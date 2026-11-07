"""对局状态机（P3 实现）。"""
from __future__ import annotations

from enum import Enum, auto


class Phase(Enum):
    UNKNOWN = auto()
    MAIN_MENU = auto()
    MATCHMAKING = auto()
    MY_TURN = auto()          # 可出牌
    WAITING = auto()          # 已结束回合，等待揭示/对手
    MATCH_END = auto()
    REWARDS = auto()
    ERROR_DIALOG = auto()


STUCK_TIMEOUT_S = 90  # 同一 Phase 停留超过该时间 -> 截图存档 + 尝试恢复
