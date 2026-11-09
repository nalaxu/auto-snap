"""从游戏进程内存（只读）读取实时对局状态。

可见性原则（与 Snapper 相同）：只输出玩家在画面上本来就能看到的信息。
- 对手：未翻开的牌只给数量；仅已揭示 / 进入弃牌或放逐区 / 明确对我方可见的牌才读取 CardDefId、费用、战力
- 我方牌库：只给剩余卡牌的**无序**集合（玩家本就知道自己卡组），不暴露抽牌顺序
- 未揭示的地点不读取 LocationDefId
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import psutil

from brain.models import Card, GameState, Location

from .il2cpp import Il2Cpp
from .process import Process

UNREVEALED_PREFIXES = ("RevealOn", "Unrevealed", "Hidden")
ZONES = ["Invalid", "Deck", "Hand", "Location", "Graveyard", "SetAside", "Banished"]
CLASSES = [
    ("Game", "CubeGame"),
    ("ClientGameInfo", "CubeUnity.App.State"),
    ("Card", "CubeGame"),
    ("Player", "CubeGame"),
    ("Location", "CubeGame"),
    ("GameManager", None),
    ("TutorialManager", "CubeUnity.App.Tutorials"),
    ("TurnTimer", "CubeUnity.App.Game"),
]


log = logging.getLogger(__name__)


CACHE_DIR = Path(__file__).resolve().parents[2] / "logs" / ".cache"


class NotInGame(RuntimeError):
    pass


class SnapMemoryReader:
    def __init__(self, process_name="SnapCN.exe", rescan_interval: float = 5.0):
        """process_name：进程名或 clients.ClientProfile。"""
        self.p = Process(process_name)
        self.il = Il2Cpp(self.p)
        self.classes: dict = {}
        self.game = 0
        self.game_id: str | None = None
        self.local_id: int | None = None
        self._local_confirmed = False   # 已用手牌证据确认我方（否则每次快照重试）
        self.finished_hold_s = 30.0     # 对局刚结束的这段时间不重扫（全堆扫描 8-10s，会拖慢撤退后的领奖励流程）
        self._finished_since: float | None = None
        self._seen_live = False         # 当前对局是否被看到过「进行中」：只有亲眼看到结束的对局才 hold
        self.rescan_interval = rescan_interval
        self._last_scan = 0.0
        self._stale_ids: set[str] | None = None
        self._tutorial_mgr = 0
        self._tutorial_scan = 0.0
        self._hot: set[int] = set()     # 曾经找到过 Game 对象的内存区域（托管堆），快速扫描只扫这些
        self._last_fast = 0.0
        self._timer = 0                 # CubeUnity.App.Game.TurnTimer 实例（END TURN 按钮外圈的倒计时）
        self._timer_scan = 0.0
        self._timer_thread: threading.Thread | None = None

    def close(self) -> None:
        self.p.close()

    # ------------------------------------------------------------ 定位
    def _cache_path(self) -> Path | None:
        try:
            created = int(psutil.Process(self.p.pid).create_time())
        except (psutil.Error, OSError):
            return None
        return CACHE_DIR / f"classes_{self.p.pid}_{created}.json"

    def resolve_classes(self) -> dict:
        """按名字定位 IL2CPP 类（约 7-12s）。结果按「进程 ID + 启动时间」缓存：同一游戏进程再次启动程序时直接复用。"""
        path = self._cache_path()
        if path and path.exists():
            try:
                raw = json.loads(path.read_text("utf-8"))
                found = {(n, ns): ks for n, ns, ks in raw["classes"]}
                if set(found) == set(CLASSES) and self.il.check_classes(found):
                    self.il._fields_slot = raw.get("fields_slot")
                    self._hot = set(raw.get("hot", []))
                    self._tutorial_mgr = int(raw.get("tutorial_mgr") or 0)
                    self.classes = found
                    log.info("类定位：使用缓存 %s", path.name)
                    return self.classes
            except (OSError, ValueError, KeyError, TypeError):
                pass
        self.classes = self.il.find_classes(CLASSES)
        self._save_cache()
        return self.classes

    def _save_cache(self) -> None:
        path = self._cache_path()
        if not path or not self.classes:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            for old in path.parent.glob("classes_*.json"):   # 只保留当前进程的缓存
                if old != path:
                    old.unlink(missing_ok=True)
            path.write_text(json.dumps({"classes": [[n, ns, ks] for (n, ns), ks in self.classes.items()],
                                        "fields_slot": self.il._fields_slot, "hot": sorted(self._hot),
                                        "tutorial_mgr": self._tutorial_mgr}), "utf-8")
        except OSError as e:
            log.debug("写类缓存失败：%r", e)

    def _game_klasses(self) -> list[int]:
        if not self.classes:
            self.resolve_classes()
        ks = self.classes.get(("Game", "CubeGame")) or []
        if not ks:
            raise RuntimeError("未定位到 CubeGame.Game 类（是否已进入过对局？）")
        return ks

    def _is_game(self, g: int) -> bool:
        il = self.il
        turn = il.get_int(g, "Turn")
        players = il.list_ptrs(il.get_ptr(g, "_players"), limit=8)
        gid = il.get_str(g, "Id")
        return turn is not None and 0 <= turn <= 20 and len(players) == 2 and bool(gid) and len(gid) >= 32

    def find_games(self, hot_only: bool = False) -> list[dict]:
        """扫描堆中所有 Game 实例，返回摘要列表（调试/选择用）。

        hot_only：只扫曾经出现过 Game 对象的内存区域（约 80MB，全堆约 6.5GB），用于快速发现新对局。
        """
        il = self.il
        out = []
        if hot_only and not self._hot:
            return out
        hits = self.p.find_u64(self._game_klasses(), bases=self._hot if hot_only else None)
        for k, addrs in hits.items():
            for g in addrs:
                if not self._is_game(g):
                    continue
                base = self.p.region_base(g)
                if base is not None and base not in self._hot:
                    self._hot.add(base)
                    self._save_cache()
                out.append({
                    "addr": g,
                    "id": il.get_str(g, "Id"),
                    "turn": il.get_int(g, "Turn"),
                    "total_turns": il.get_int(g, "TotalTurns"),
                    "client_state": il.get_bool(g, "ClientState"),
                    "finished": self._finished(g),
                    "league": il.get_str(g, "LeagueDefId"),
                    "scenario": il.get_str(g, "ScenarioDefId"),
                })
        return out

    def _local_player_id(self, player_ids: set[int]) -> int | None:
        ks = self.classes.get(("ClientGameInfo", "CubeUnity.App.State")) or []
        votes: dict[int, int] = {}
        for k, addrs in (self.p.find_u64(ks).items() if ks else []):
            for a in addrs:
                lid = self.il.get_int(a, "LocalPlayerEntityId")
                if lid in player_ids:
                    votes[lid] = votes.get(lid, 0) + 1
        return max(votes, key=votes.get) if votes else None

    def _finished(self, g: int) -> bool:
        # 注意：Winner 在对局中就会被赋值（当前领先方），不能用来判断结束
        return bool(self.il.get_ptr(g, "ClientResultMessage") or self.il.get_bool(g, "_resultCreated"))

    def _start_ticks(self, g: int) -> int:
        f = self.il.field(g, "StartTimestamp")  # DateTime 值类型，8 字节
        return (self.p.i64(g + f.offset) or 0) if f else 0

    def select_game(self, force: bool = False) -> bool:
        """选当前对局：未结束 > 客户端状态 > 开局时间新 > 回合数大。

        当前对局有效且未结束 -> 直接复用（不扫描）；已结束 -> 每 rescan_interval 秒重扫一次找新对局。
        """
        valid = bool(self.game) and self._is_game(self.game) and self.il.get_str(self.game, "Id") == self.game_id
        now = time.time()
        if valid and not force and not self._finished(self.game):
            self._finished_since, self._seen_live = None, True
            return True
        if valid and not force:
            if not self._finished(self.game):
                self._finished_since, self._seen_live = None, True
                return True
            # 刚看着它结束（如对手撤退）：继续返回终局快照（毫秒级），让上层立刻去领奖励。
            # 一开始就已结束的旧对局不 hold，否则会延误发现新对局（实测晚 30s，T1 几乎超时）
            if self._seen_live:
                if self._finished_since is None:
                    self._finished_since = now
                if now - self._finished_since < self.finished_hold_s:
                    return True

        games = None
        if not force and self._hot and now - self._last_fast >= 1.0:
            # 快速扫描热区域（几十毫秒级）：新对局通常分配在同一批托管堆区域里
            self._last_fast = now
            fast = self.find_games(hot_only=True)
            if any(not g["finished"] and g["id"] not in (self._stale_ids or set()) and g["id"] != self.game_id
                   for g in fast):
                games = fast
                log.info("快速扫描发现新对局")
        if games is None:
            if not force and now - self._last_scan < self.rescan_interval:
                return valid
            self._last_scan = now
            games = self.find_games()
        self._finished_since, self._seen_live = None, False
        if not games:
            self.game = 0
            return False
        if self._stale_ids is None:  # 首次扫描时已结束的对局 = 历史残留，之后不再选
            self._stale_ids = {g["id"] for g in games if g["finished"]}
        fresh = [g for g in games if g["id"] not in self._stale_ids] or games
        fresh.sort(
            key=lambda g: (not g["finished"], g["id"] == self.game_id, bool(g["client_state"]), g["turn"] or 0),
            reverse=True,
        )
        best = fresh[0]
        self.game, self.game_id = best["addr"], best["id"]
        self._resolve_local()
        return True

    def _known_hand(self, pl: int) -> int:
        """该玩家手牌中 CardDefId 已知的张数。客户端只知道我方手牌，对手手牌读出来是 "None"。"""
        il = self.il
        cards = il.list_ptrs(il.get_ptr(il.get_ptr(pl, "Hand"), "_cards"), limit=16)
        return sum(1 for c in cards if (il.get_str(c, "CardDefId") or "None") != "None")

    def _resolve_local(self, vote: bool = True) -> None:
        """确定我方 EntityId：优先用手牌证据（已知牌更多的一方）；没有证据时退回 ClientGameInfo 投票。

        投票会被堆里残留的旧对局 ClientGameInfo 带偏（EntityId 在每局之间复用），官网版实测出现过把对手当成我方。
        """
        players = self.il.list_ptrs(self.il.get_ptr(self.game, "_players"))
        ids = [self.il.get_int(p, "EntityId") for p in players]
        known = [self._known_hand(p) for p in players]
        if len(players) == 2 and known[0] != known[1] and max(known) > 0:
            lid = ids[known.index(max(known))]
            if lid != self.local_id and self.local_id is not None:
                log.warning("我方玩家修正：EntityId %s -> %s（手牌已知张数 %s）", self.local_id, lid, dict(zip(ids, known)))
            self.local_id, self._local_confirmed = lid, True
        elif vote:  # 扫描整个堆，开销大：只在选定对局时做一次
            self.local_id, self._local_confirmed = self._local_player_id(set(ids)), False

    # ------------------------------------------------------------ 新手指引
    # ------------------------------------------------------------ 回合计时
    def _timer_alive(self, t: int) -> bool:
        """是真正的 TurnTimer 对象：Unity 原生指针有效，且 _endTurnButton 指向 EndTurnButton 对象。"""
        il = self.il
        cached = il.get_ptr(t, "m_CachedPtr")        # 销毁后为 0
        if not cached or cached == 0xFFFFFFFF:
            return False
        btn = il.get_ptr(t, "_endTurnButton")
        return bool(btn) and il.object_class_name(btn) == "EndTurnButton"

    def _timer_values(self, t: int) -> dict:
        il, p = self.il, self.p
        f_rem, f_start = il.field(t, "_timeRemaining"), il.field(t, "_startingSeconds")
        rem = p.f32(t + f_rem.offset) if f_rem else None
        start = p.f32(t + f_start.offset) if f_start else None
        active = bool(il.get_bool(t, "Active"))
        ok = rem is not None and start is not None and 1 <= start <= 600 and -1 <= rem <= start + 15
        return {"remaining": round(rem, 2) if ok else None, "total": round(start, 2) if ok else None,
                "active": active and ok, "no_timer": bool(il.get_bool(t, "_noTimer")),
                "waiting_for_opponent": bool(il.get_bool(t, "_waitingForOpponent"))}

    def _scan_timer(self) -> None:
        ks = self.classes.get(("TurnTimer", "CubeUnity.App.Game")) or []
        found: list[int] = []
        for bases in ([self._hot] if self._hot else []) + [None]:   # 先扫热区域（~0.2s），找不到再全堆（~7s）
            hits = self.p.find_u64(ks, bases=bases) if ks else {}
            found = [a for _, addrs in hits.items() for a in addrs if self._timer_alive(a)]
            if found:
                break
        for a in found:
            base = self.p.region_base(a)
            if base is not None:
                self._hot.add(base)
        self._timer = next((a for a in found if self._timer_values(a)["active"]), found[0] if found else 0)
        log.info("回合计时器%s", f"定位于 {self._timer:#x}（候选 {len(found)}）" if self._timer else "未找到（可能不在对局中）")

    def turn_timer(self) -> dict | None:
        """本回合倒计时 {remaining, total, active, no_timer, waiting_for_opponent}；还没定位到返回 None。

        定位要全堆扫描（约 7s），放在后台线程，不阻塞出牌；之后每次读取只是几次 ReadProcessMemory。
        """
        if not self.classes:
            return None
        if self._timer:
            if self._timer_alive(self._timer):
                return self._timer_values(self._timer)   # 不在计时（对手回合 / 揭示中）也保留实例
            self._timer = 0   # 对象已销毁（换局 / 回主界面）
        busy = self._timer_thread is not None and self._timer_thread.is_alive()
        if not busy and time.time() - self._timer_scan > 10:
            self._timer_scan = time.time()
            self._timer_thread = threading.Thread(target=self._scan_timer, name="turn-timer-scan", daemon=True)
            self._timer_thread.start()
        return None

    def _is_tutorial_mgr(self, m: int) -> bool:
        return self.il.object_class_name(self.il.get_ptr(m, "IsRunningTutorial")).startswith("ObservableProperty")

    def tutorial_running(self) -> bool | None:
        """新手指引是否正在进行（指引期间游戏会屏蔽拖牌等操作）。读不到返回 None。

        TutorialManager.IsRunningTutorial (ObservableProperty<bool>._value) 或 _runningTutorials 非空。
        """
        if not self.classes:
            self.resolve_classes()
        il = self.il
        if not (self._tutorial_mgr and self._is_tutorial_mgr(self._tutorial_mgr)):
            self._tutorial_mgr = 0
            if time.time() - self._tutorial_scan < 10:
                return None
            self._tutorial_scan = time.time()
            ks = self.classes.get(("TutorialManager", "CubeUnity.App.Tutorials")) or []
            for _, addrs in (self.p.find_u64(ks).items() if ks else []):
                for a in addrs:
                    if self._is_tutorial_mgr(a):
                        self._tutorial_mgr = a
                        break
            if not self._tutorial_mgr:
                return None
            self._save_cache()   # 单例，整个游戏进程内地址不变
        m = self._tutorial_mgr
        flag = il.get_bool(il.get_ptr(m, "IsRunningTutorial"), "_value")
        running = il.list_ptrs(il.get_ptr(m, "_runningTutorials"), limit=64)
        return bool(flag) or bool(running)

    # ------------------------------------------------------------ 读取
    def _stat(self, obj: int) -> int | None:
        return self.il.get_int(obj, "Value") if obj else None

    def _zone_name(self, zone: int) -> str:
        z = self.il.get_int(zone, "ZoneId") if zone else None
        return ZONES[z] if z is not None and 0 <= z < len(ZONES) else f"Zone{z}"

    def _card(self, c: int) -> dict:
        il = self.il
        owner = il.get_int(il.get_ptr(c, "Owner"), "EntityId")
        zone = self._zone_name(il.get_ptr(c, "_zone"))
        revealed = il.get_bool(c, "Revealed", "IsRevealed")
        if revealed is None:
            tr = il.get_int(c, "TurnRevealed")
            revealed = bool(tr and tr > 0)
        visible_to = il.list_ints(il.get_ptr(c, "_visibleToPlayerEntityIds"), limit=8)
        mine = owner == self.local_id
        visible = mine or revealed or zone in ("Graveyard", "Banished") or (self.local_id in visible_to)
        out = {"entity": il.get_int(c, "EntityId"), "owner": "me" if mine else "opp", "zone": zone,
               "revealed": revealed, "pos": il.get_int(c, "ZonePosition"), "turn_revealed": il.get_int(c, "TurnRevealed")}
        if visible:
            out.update(
                id=il.get_str(c, "CardDefId"),
                cost=self._stat(il.get_ptr(c, "Cost")),
                power=self._stat(il.get_ptr(c, "Power")),
            )
        return out

    def snapshot(self) -> dict:
        if not self.select_game():
            raise NotInGame("当前不在对局中")
        if not self._local_confirmed:
            self._resolve_local(vote=False)
        il, g = self.il, self.game
        players = il.list_ptrs(il.get_ptr(g, "_players"))
        pid = [il.get_int(p, "EntityId") for p in players]
        if self.local_id not in pid:
            raise NotInGame("无法确定我方玩家")
        me_idx = pid.index(self.local_id)
        turn, finished = il.get_int(g, "Turn"), self._finished(g)
        me, opp = players[me_idx], players[1 - me_idx]

        def zone_cards(pl: int, zone: str) -> list[int]:
            return il.list_ptrs(il.get_ptr(il.get_ptr(pl, zone), "_cards"), limit=64)

        hand = sorted((self._card(c) for c in zone_cards(me, "Hand")), key=lambda c: c["pos"] if c["pos"] is not None else 99)

        def seen_ids(zone: str) -> list[str]:
            return [c["id"] for c in (self._card(x) for x in zone_cards(me, zone)) if c.get("id")]
        # 客户端不知道我方牌库具体是哪些牌（CardDefId 为 "None"），只给数量；剩余牌 = 卡组 − 已见牌，由上层计算
        deck_count = len(zone_cards(me, "Deck"))
        locs = []
        for i, loc in enumerate(il.list_ptrs(il.get_ptr(g, "_locations"), limit=3)):
            p1 = il.get_int(loc, "CurPlayer1Power") or 0
            p2 = il.get_int(loc, "CurPlayer2Power") or 0
            # 客户端在揭示前的 LocationDefId 本身就是占位名（如 "RevealOn2"），即画面所见；
            # LocationDefIdToReveal（未揭示地点的真身）永不读取。
            loc_id = il.get_str(loc, "LocationDefId")
            loc_revealed = bool(loc_id) and not loc_id.startswith(UNREVEALED_PREFIXES)
            cards = [self._card(c) for c in il.list_ptrs(il.get_ptr(loc, "_cards"), limit=16)]
            locs.append({
                "idx": i,
                "id": loc_id if loc_revealed else None,
                "raw_id": loc_id,
                "revealed": loc_revealed,
                "my_power": p1 if me_idx == 0 else p2,
                "opp_power": p2 if me_idx == 0 else p1,
                "my_cards": [c for c in cards if c["owner"] == "me"],
                "opp_cards": [c for c in cards if c["owner"] == "opp"],
            })
        return {
            "game_id": self.game_id,
            "turn": turn,
            "total_turns": il.get_int(g, "TotalTurns"),
            # LeagueDefId 在内存里是枚举值（结算文件里才是字符串）：竞技场 "NetEaseArena" = 10052（2026-11-09 实测）
            "league_id": il.get_int(g, "LeagueDefId"),
            "cube_value": il.get_int(g, "CubeValue"),
            "timer": self.turn_timer() if not self._finished(g) else None,
            "read_at": time.monotonic(),
            # 赌注：Snap 后 CubeValueNextTurn 翻倍（下回合生效）；每名玩家一局只能 Snap 一次，Snap 后 CanRaiseStakes=False
            "cube_value_next": il.get_int(g, "CubeValueNextTurn"),
            "stakes_raised": il.get_int(g, "StakesRaisedCount"),
            "no_turn_timer": il.get_bool(g, "NoTurnTimer"),
            "finished": self._finished(g),
            "me": {
                "entity": self.local_id,
                "energy": il.get_int(me, "CurrentEnergy"),
                "max_energy": il.get_int(me, "MaxEnergy"),
                "ended_turn": il.get_int(me, "EndedTurn"),
                "can_raise_stakes": il.get_bool(me, "CanRaiseStakes"),
                # 该玩家主动 Snap 的回合列表（非空 = 已 Snap）。StakesRaisedCount 会把终局自动翻倍也算进去，
                # 对手的 CanRaiseStakes 在对方没 Snap 时也可能是 False，二者都不能用来判断对手是否 Snap
                "snap_turns": il.list_ints(il.get_ptr(me, "_turnsOnStakesRaiseRequested"), limit=8),
                "conceded": bool(il.get_int(me, "Conceded")),
                # 本局我方激活次数（Card.Activated 对所有牌都是 True，不能判断某张牌是否激活过；用这个计数确认激活生效）
                "activations": il.get_int(me, "NumberOfCardsActivatedByPlayerThisGame"),
                "hand": hand,
                "deck_count": deck_count,
                "graveyard": seen_ids("Graveyard"),
                "banished": seen_ids("Banished"),
            },
            "opp": {
                "entity": pid[1 - me_idx],
                "hand_count": len(zone_cards(opp, "Hand")),
                "can_raise_stakes": il.get_bool(opp, "CanRaiseStakes"),
                "snap_turns": il.list_ints(il.get_ptr(opp, "_turnsOnStakesRaiseRequested"), limit=8),
                "destroyed_by": il.get_int(opp, "NumCardsDestroyedByPlayerThisGame"),   # 对手摧毁 combo 次数（画面可见事件）
                "discarded_by": il.get_int(opp, "NumCardsDiscardedByPlayerThisGame"),   # 对手弃牌 combo 次数
                "conceded": bool(il.get_int(opp, "Conceded")),
                "deck_count": len(zone_cards(opp, "Deck")),
            },
            "locations": locs,
        }


def _timer_fields(snap: dict) -> dict:
    t = snap.get("timer") or {}
    if not t or t.get("no_timer") or not t.get("active"):
        return {}
    return {"turn_seconds_left": t["remaining"], "turn_seconds_total": t["total"], "timer_read_at": snap.get("read_at")}


def _model_card(c: dict, **kw) -> Card:
    return Card(id=c.get("id") or f"unknown_{c['entity']}", cost=c.get("cost") or 0, power=c.get("power") or 0,
                entity=c.get("entity"), **kw)


def remaining_deck(deck: list[str], snap: dict) -> list[str]:
    """剩余牌库 = 卡组 − 已见过的我方牌（手牌、场上、弃牌、放逐）。效果生成的额外牌不在卡组里，自然忽略。"""
    left = list(deck)
    seen = [c.get("id") for c in snap["me"]["hand"]]
    seen += [c.get("id") for l in snap["locations"] for c in l["my_cards"]]
    seen += snap["me"].get("graveyard", []) + snap["me"].get("banished", [])
    for cid in seen:
        if cid in left:
            left.remove(cid)
    return sorted(left)


def to_game_state(snap: dict, deck: list[str] | None = None) -> GameState:
    """内存快照 -> 决策层的 GameState。deck：当前卡组的 CardDefId 列表（用于推算剩余牌库）。"""
    hand = [_model_card(c, hand_index=i) for i, c in enumerate(snap["me"]["hand"])]
    locations = [
        Location(
            idx=l["idx"],
            name=l["id"] or "",
            revealed=bool(l["revealed"] is not False),
            my_power=l["my_power"],
            opp_power=l["opp_power"],
            my_cards=[_model_card(c, slot=c["pos"] if c.get("pos") in (0, 1, 2, 3) else i, activated=False,
                                  turn_played=c.get("turn_revealed") or None)
                      for i, c in enumerate(l["my_cards"])],
            opp_card_count=len(l["opp_cards"]),
            opp_cards=[_model_card(c) for c in l["opp_cards"] if c.get("id")],  # 仅已揭示的
        )
        for l in snap["locations"]
    ]
    return GameState(
        turn=snap["turn"] or 0,
        max_turn=snap["total_turns"] or 6,
        energy=snap["me"]["energy"] or 0,
        cube_stake=snap["cube_value"] or 1,
        cube_next=snap.get("cube_value_next") or snap["cube_value"] or 1,
        **_timer_fields(snap),
        can_snap=bool(snap["me"].get("can_raise_stakes")) and not snap["me"].get("snap_turns"),
        opp_snapped=bool((snap.get("opp") or {}).get("snap_turns")),
        played_last_turn=any(c.get("turn_revealed") == (snap["turn"] or 0) - 1
                             for l in snap["locations"] for c in l["my_cards"]),
        opp_destroyed=(snap.get("opp") or {}).get("destroyed_by"),
        opp_discarded=(snap.get("opp") or {}).get("discarded_by"),
        opp_snap_turn=max((snap.get("opp") or {}).get("snap_turns") or [0]) or None,
        turn_timer=not snap.get("no_turn_timer", False),
        hand=hand,
        deck_remaining=remaining_deck(deck, snap) if deck else [],
        my_deck_count=snap["me"].get("deck_count"),
        my_graveyard=list(snap["me"].get("graveyard", [])),
        opp_hand_count=(snap.get("opp") or {}).get("hand_count"),
        opp_deck_count=(snap.get("opp") or {}).get("deck_count"),
        locations=locations,
    )
