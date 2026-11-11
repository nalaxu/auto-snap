"""感知层实现：只读内存（StateSource 协议）。

read_state() 只在「我方可以出牌」时返回 GameState，其余情况返回 None：
- 游戏未运行 / 不在对局 / 对局已结束
- 我方本回合已点「结束回合」（EndedTurn >= Turn），正在等待揭示或对手
- 回合 0（开局动画）
- 新回合尚未就绪：能量未补满，或本回合的抽牌还没到手（见 TurnGate）

出现异常时自动断开，下次调用重新附加（附加有冷却，避免对游戏进程反复全量扫描）。
"""
from __future__ import annotations

import logging
import time

import psutil

from brain.models import GameState

from .deck import load_current_deck
from .memory.process import ProcessNotFound
from .memory.snap import NotInGame, SnapMemoryReader, to_game_state

log = logging.getLogger(__name__)

ARENA_LEAGUE_ID = 10052   # Game.LeagueDefId 枚举值 = NetEaseArena（2026-11-09 实测；排位 / 新手局不同）


def is_arena_game(snap: dict | None) -> bool:
    return bool(snap) and snap.get("league_id") == ARENA_LEAGUE_ID


class MemoryStateSource:
    def __init__(self, client="SnapCN.exe", reattach_cooldown_s: float = 15.0):
        """client：clients.ClientProfile（推荐）或进程名。"""
        self.client = client
        self.process_name = getattr(client, "process_name", client)
        self.states_dir = getattr(client, "states_dir", None)
        self.reattach_cooldown_s = reattach_cooldown_s
        self.reader: SnapMemoryReader | None = None
        self._next_attach = 0.0
        self._deck_game_id: str | None = None
        self.deck_name = ""
        self.deck: list[str] = []
        self.last_snapshot: dict | None = None
        self.gate = TurnGate()
        self._arena = None
        self.arena_mode = False   # main.auto --game-mode arena 时置 True
        # 幽灵对局（客户端内存残留的未结束旧局）：放在这里，断开重连换了 reader 也不会重新选回去
        self.ghost_ids: dict[str, int | None] = {}
        self.ghost_cleared: set[str] = set()

    # ------------------------------------------------------------ 连接管理
    def _ensure_reader(self) -> SnapMemoryReader | None:
        if self.reader:
            if psutil.pid_exists(self.reader.p.pid):
                return self.reader
            log.info("游戏进程已退出（pid=%s），等待重新附加", self.reader.p.pid)
            self.close()
            self._next_attach = 0.0
        if time.time() < self._next_attach:
            return None
        self._next_attach = time.time() + self.reattach_cooldown_s
        try:
            self.reader = SnapMemoryReader(self.client)
            self.reader._ghost_ids, self.reader._ghost_cleared = self.ghost_ids, self.ghost_cleared
            self.reader.resolve_classes()
            log.info("已附加 %s pid=%s（只读）", self.process_name, self.reader.p.pid)
        except (ProcessNotFound, OSError, RuntimeError) as e:
            log.info("附加失败：%s", e)
            self.close()
        return self.reader

    def close(self) -> None:
        if self.reader:
            self.reader.close()
        self.reader = None

    # ------------------------------------------------------------ StateSource
    def snapshot(self) -> dict | None:
        """原始快照（含结束标志等），不做出牌阶段过滤；不在对局返回 None。"""
        reader = self._ensure_reader()
        if not reader:
            return None
        try:
            snap = reader.snapshot()
        except NotInGame:
            return None
        except Exception as e:  # noqa: BLE001  进程退出 / 内存结构变化 -> 断开重连
            log.warning("读取失败，断开重连：%r", e)
            self.close()
            return None
        if snap["game_id"] != self._deck_game_id:  # 新对局：重新读取当前卡组
            self._deck_game_id = snap["game_id"]
            # 竞技场局：LeagueDefId 识别；竞技场模式下（arena_mode）兜底——整场进行中开的局都按竞技卡组
            arena = self.arena_state() if (is_arena_game(snap) or self.arena_mode) else None
            if arena and len(arena.deck) == arena.config.deck_size:   # 竞技场：卡组来自本场组卡结果
                self.deck_name, self.deck = "竞技卡组", list(arena.deck)
            else:
                self.deck_name, self.deck = load_current_deck(self.states_dir) if self.states_dir else load_current_deck()
            log.info("新对局 %s，卡组「%s」%d 张", snap["game_id"][:8], self.deck_name, len(self.deck))
        self.last_snapshot = snap
        return snap

    def mark_ghost(self, game_id: str | None, reason: str) -> bool:
        """上层（画面交叉验证）确认界面不在这局里：判为幽灵对局，不再选。返回是否生效。"""
        reader = self.reader
        if not reader or not reader.mark_ghost(game_id, reason):
            return False
        self.last_snapshot = None
        return True

    def arena_state(self):
        """竞技场状态（perception.arena.ArenaState）；没附加 / 竞技场界面从未打开过返回 None。"""
        reader = self._ensure_reader()
        if not reader:
            return None
        try:
            if self._arena is None or self._arena.p is not reader.p:
                from .arena import ArenaReader
                self._arena = ArenaReader(reader)
            return self._arena.read()
        except Exception as e:  # noqa: BLE001
            log.warning("读取竞技场状态失败：%r", e)
            return None

    def tutorial_running(self) -> bool:
        reader = self._ensure_reader()
        if not reader:
            return False
        try:
            return bool(reader.tutorial_running())
        except Exception as e:  # noqa: BLE001
            log.warning("读取新手指引状态失败：%r", e)
            return False

    def read_state(self) -> GameState | None:
        snap = self.snapshot()
        if not snap or not self.gate.ready(snap):
            return None
        return to_game_state(snap, self.deck)


def known_card(card_id) -> bool:
    return bool(card_id) and card_id != "None"


def is_my_turn(snap: dict) -> bool:
    turn = snap.get("turn") or 0
    if snap.get("finished") or turn < 1:
        return False
    ended = snap["me"].get("ended_turn")
    return ended is None or ended < turn


class TurnGate:
    """判断「本回合可以决策了」：防止在能量补满前 / 抽牌到手前就做决定。

    就绪条件（全部满足）：
      1. is_my_turn：未结束、回合 >= 1、我方本回合尚未结束回合
      2. 能量已补满：max_energy >= turn（新回合切换瞬间 max_energy 还是上回合的值）
      3. 已抽牌：牌库张数比回合开始时少；或牌库空 / 手牌满 7 张；或能量补满后超过 draw_timeout_s（兜底）
      4. 快照稳定 settle_s 秒不变（避免读到半更新状态）
    """

    def __init__(self, draw_timeout_s: float = 5.0, settle_s: float = 0.3, clock=time.monotonic):
        self.draw_timeout_s = draw_timeout_s
        self.settle_s = settle_s
        self.clock = clock
        self._turn_key: tuple | None = None
        self._deck_at_start: int | None = None
        self._energy_ready_at: float | None = None
        self._last_key: str | None = None
        self._stable_since = 0.0

    def ready(self, snap: dict) -> bool:
        now = self.clock()
        key = repr((snap.get("turn"), snap["me"], snap["locations"]))
        if key != self._last_key:
            self._last_key, self._stable_since = key, now
        if not is_my_turn(snap):
            return False
        me, turn = snap["me"], snap["turn"]
        tk = (snap["game_id"], turn)
        if tk != self._turn_key:  # 第一次看到这个回合：记下回合开始时的牌库张数
            self._turn_key, self._deck_at_start, self._energy_ready_at = tk, me.get("deck_count"), None
        if (me.get("max_energy") or 0) < turn:
            return False
        # 开局时手牌对象已在，但 CardDefId 还是占位 "None"（官网版实测 T1 出现）：等真实卡牌到位
        if any(not known_card(c.get("id")) for c in me.get("hand", [])):
            return False
        if self._energy_ready_at is None:
            self._energy_ready_at = now
        deck = me.get("deck_count")
        drawn = (
            (deck is not None and self._deck_at_start is not None and deck < self._deck_at_start)
            or deck == 0
            or len(me.get("hand", [])) >= 7
            or (turn == 1 and len(me.get("hand", [])) >= 4)   # T1 开局手牌已发好，看不到牌库减少
            or now - self._energy_ready_at >= self.draw_timeout_s
        )
        return drawn and now - self._stable_since >= self.settle_s
