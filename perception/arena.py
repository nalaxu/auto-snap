"""竞技场（国服 Arena Mode）状态：只读内存。

入口是单例 `Netease.Code.App.GameMode.Arena.ArenaModeManager`（ScriptableObject，地址在进程内不变）：
- 管理器自带规则参数：GoldCost / PickedCardMaxCount / MaxHealthPoints / RewardChangeHealthPoints /
  MaxWinStakesCount / RewardChangeStakesCount
- `_cacheState`（NetEaseArenaModeState）：TicketsCount、AccumulatedStakesCount（头像进度）、DataVersion、Data
- `Data`（ArenaModeData）：Finished、TotalGameCount、WinStakesCount（积分）、HealthPoints、ChangeCardCount（剩余换牌）、
  EverChangedCardCount、LastRewardChangeCardFrom{HealthPoints,WinStakes}、PickableCards（三张候选）、PickedCards（卡组）、
  AppearedCards、ArenaDeckId

注意：继承来的 `_state` 字段按泛型定义解析的偏移不对，只用 `_cacheState`。见 docs/arena-mode-plan.md「M0 结论」。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

MANAGER_CLASS = ("ArenaModeManager", "Netease.Code.App.GameMode.Arena")
CACHE_DIR = Path(__file__).resolve().parents[1] / "logs" / ".cache"


@dataclass
class ArenaConfig:
    gold_cost: int = 150
    deck_size: int = 12
    max_hp: int = 10
    hp_per_swap: int = 2          # 每掉 N 血给一次换牌
    max_score: int = 35
    score_per_swap: int = 4       # 每赢 N 魔方给一次换牌


@dataclass
class ArenaState:
    tickets: int = 0
    avatar_progress: int = 0          # AccumulatedStakesCount：Infinity Avatar 进度
    version: int = 0                  # DataVersion：服务器每次更新 +1，用来确认操作生效
    run_id: str = ""
    finished: bool = True             # 没有进行中的整场时为 True
    games: int = 0
    score: int = 0                    # 积分（本场赢的魔方累计，可为负）
    hp: int = 0
    swaps_left: int = 0
    swaps_used: int = 0
    hp_mark: int = 0                  # 上次发放换牌时的血量
    score_mark: int = 0               # 上次发放换牌时的积分
    candidates: list[str] = field(default_factory=list)   # 组卡：本轮三选一；组完：下一次换牌的候选
    deck: list[str] = field(default_factory=list)         # 已选卡组（界面顺序）
    appeared: dict[str, int] = field(default_factory=dict)
    pool: str = ""                    # ArenaDeckId（卡池定义）
    config: ArenaConfig = field(default_factory=ArenaConfig)

    @property
    def phase(self) -> str:
        """none：没有进行中的整场（需要用门票开始）；draft：组卡中；run：组完牌，打对局 / 换牌。"""
        if self.finished or not self.run_id:
            return "none"
        return "draft" if len(self.deck) < self.config.deck_size else "run"

    @property
    def draft_round(self) -> int:
        """组卡第几轮（1 起）。"""
        return len(self.deck) + 1

    @property
    def swap_points(self) -> int:
        """换牌进度（满 score_per_swap 点给一次）：赢 1 魔方记 1 点、掉 1 血记 score_per_swap/hp_per_swap 点。

        进度从上次发放的标记点算起（LastRewardChangeCardFrom*），两种来源混合累计。
        """
        c = self.config
        won = max(0, self.score - self.score_mark)
        lost = max(0, self.hp_mark - self.hp)
        return won + lost * c.score_per_swap // max(1, c.hp_per_swap)

    def summary(self) -> dict:
        return {"phase": self.phase, "tickets": self.tickets, "score": self.score, "hp": self.hp, "games": self.games,
                "swaps_left": self.swaps_left, "swap_points": self.swap_points, "candidates": self.candidates,
                "deck": self.deck, "avatar_progress": self.avatar_progress, "version": self.version}


class ArenaReader:
    """在 SnapMemoryReader 的进程 / IL2CPP 解析之上读取竞技场状态。管理器地址按进程缓存到 logs/.cache/。"""

    def __init__(self, reader):
        self.p = reader.p
        self.il = reader.il
        self.manager = 0

    # ------------------------------------------------------------ 定位
    def _cache_path(self) -> Path | None:
        try:
            created = int(psutil.Process(self.p.pid).create_time())
        except (psutil.Error, OSError):
            return None
        return CACHE_DIR / f"arena_{self.p.pid}_{created}.json"

    def _valid_manager(self, m: int) -> bool:
        il = self.il
        if il.class_name(self.p.ptr(m)) != (MANAGER_CLASS[1], MANAGER_CLASS[0]):
            return False
        hp = il.get_int(m, "<MaxHealthPoints>k__BackingField")
        size = il.get_int(m, "<PickedCardMaxCount>k__BackingField")
        return bool(hp and 0 < hp <= 100 and size and 0 < size <= 40)

    def locate(self) -> int:
        """找到 ArenaModeManager 实例（约 4-10s，之后读缓存毫秒级）。找不到返回 0。"""
        if self.manager and self._valid_manager(self.manager):
            return self.manager
        path = self._cache_path()
        if path and path.exists():
            try:
                m = int(json.loads(path.read_text("utf-8"))["manager"])
                if self._valid_manager(m):
                    self.manager = m
                    return m
            except (OSError, ValueError, KeyError, TypeError):
                pass
        ks = self.il.find_classes([MANAGER_CLASS]).get(MANAGER_CLASS) or []
        hits = self.p.find_u64(ks) if ks else {}
        for k in ks:
            for m in hits.get(k, []):
                if self._valid_manager(m):
                    self.manager = m
                    if path:
                        try:
                            path.parent.mkdir(parents=True, exist_ok=True)
                            for old in path.parent.glob("arena_*.json"):
                                if old != path:
                                    old.unlink(missing_ok=True)
                            path.write_text(json.dumps({"manager": m}), "utf-8")
                        except OSError:
                            pass
                    return m
        return 0

    # ------------------------------------------------------------ 读取
    def _cards(self, lst: int) -> list[str]:
        il = self.il
        out = []
        for it in il.list_ptrs(lst, limit=64):
            c = il.get_ptr(it, "<Card>k__BackingField")
            cid = il.get_str(c, "<CardDefId>k__BackingField") if c else None
            if cid:
                out.append(cid)
        return out

    def read(self) -> ArenaState | None:
        """当前竞技场状态；管理器还没加载（从未打开过竞技场界面）返回 None。"""
        m = self.locate()
        if not m:
            return None
        il = self.il
        g = lambda o, f: il.get_int(o, f"<{f}>k__BackingField")   # noqa: E731
        cfg = ArenaConfig(gold_cost=g(m, "GoldCost") or 150, deck_size=g(m, "PickedCardMaxCount") or 12,
                          max_hp=g(m, "MaxHealthPoints") or 10, hp_per_swap=g(m, "RewardChangeHealthPoints") or 2,
                          max_score=g(m, "MaxWinStakesCount") or 35, score_per_swap=g(m, "RewardChangeStakesCount") or 4)
        st = il.get_ptr(m, "_cacheState")
        if not st or il.object_class_name(st) != "NetEaseArenaModeState":
            return None
        s = ArenaState(config=cfg, tickets=g(st, "TicketsCount") or 0, avatar_progress=g(st, "AccumulatedStakesCount") or 0,
                       version=g(st, "DataVersion") or 0)
        d = il.get_ptr(st, "<Data>k__BackingField")
        if not d or il.object_class_name(d) != "ArenaModeData":
            return s
        s.run_id = il.get_str(d, "<Id>k__BackingField") or ""
        s.finished = bool(il.get_bool(d, "<Finished>k__BackingField"))
        s.games = g(d, "TotalGameCount") or 0
        s.score = g(d, "WinStakesCount") or 0
        s.hp = g(d, "HealthPoints") or 0
        s.swaps_left = g(d, "ChangeCardCount") or 0
        s.swaps_used = g(d, "EverChangedCardCount") or 0
        s.hp_mark = g(d, "LastRewardChangeCardFromHealthPoints") or 0
        s.score_mark = g(d, "LastRewardChangeCardFromWinStakes") or 0
        s.candidates = self._cards(il.get_ptr(d, "<PickableCards>k__BackingField"))
        s.deck = self._cards(il.get_ptr(d, "<PickedCards>k__BackingField"))
        ap = il.get_ptr(d, "<AppearedCards>k__BackingField")
        for x in il.list_ptrs(ap, limit=400) if ap else []:
            cid = il.get_str(x, "<CardDefId>k__BackingField")
            if cid:
                s.appeared[cid] = il.get_int(x, "<AppearedTimes>k__BackingField") or 0
        s.pool = il.get_str(d, "<ArenaDeckId>k__BackingField") or ""
        return s
