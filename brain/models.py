"""对局状态与动作的数据模型 —— 感知层、决策层、执行层之间的统一契约。"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

MAX_CARDS_PER_LOCATION = 4
NUM_LOCATIONS = 3


class Card(BaseModel):
    id: str                      # 卡牌唯一标识，对应 data/cards.json 的 key
    name: str = ""               # 显示名（中文）
    cost: int
    power: int
    hand_index: int | None = None  # 在手牌中的位置（从左到右，0 起）；执行层用来定位
    entity: int | None = None      # 游戏内实体 id（区分同名卡）
    turn_played: int | None = None  # 场上牌：打出（揭示）的回合；由 brain/board.py 跟踪填写


class Location(BaseModel):
    idx: int = Field(ge=0, lt=NUM_LOCATIONS)
    name: str = ""
    effect: str = ""
    revealed: bool = True
    my_power: int = 0
    opp_power: int = 0
    my_cards: list[Card] = Field(default_factory=list)
    opp_card_count: int = 0
    opp_cards: list[Card] = Field(default_factory=list)  # 对手在此地点已揭示的牌
    playable: bool = True        # 某些地点会禁止出牌

    @property
    def my_slots_free(self) -> int:
        return MAX_CARDS_PER_LOCATION - len(self.my_cards)


class GameState(BaseModel):
    turn: int
    max_turn: int = 6
    energy: int
    cube_stake: int = 1
    turn_timer: bool = True          # 本局是否有回合计时（Game.NoTurnTimer 取反）
    can_snap: bool = True            # 我方本局还没 Snap（每局一次）
    opp_snapped: bool = False        # 对手已 Snap
    opp_snap_turn: int | None = None # 对手 Snap 的回合（撤退判断：对手刚 Snap 的那一回合）
    cube_next: int | None = None     # 下回合生效的赌注（Snap 后翻倍）
    turn_seconds_left: float | None = None   # 本回合倒计时剩余秒数（内存 TurnTimer；读不到为 None）
    turn_seconds_total: float | None = None
    timer_read_at: float | None = None       # 读取时刻（time.monotonic），用于折算决策开始时的剩余时间
    hand: list[Card]
    deck_remaining: list[str] = Field(default_factory=list)   # 我方剩余牌库（卡组 − 已见牌，无序）
    my_deck_count: int | None = None
    my_graveyard: list[str] = Field(default_factory=list)
    opp_hand_count: int | None = None
    opp_deck_count: int | None = None
    locations: list[Location]
    history: list[str] = Field(default_factory=list)
    forbidden: list[tuple[str, int]] = Field(default_factory=list)  # 运行时发现的 (card_id, 地点) 禁止组合
    board_notes: list[str] = Field(default_factory=list)  # 场上最新要点（brain/board.py 每回合重建，旧信息自动失效）


class ActionType(str, Enum):
    PLAY = "play"


class PlayAction(BaseModel):
    type: Literal["play"] = "play"
    card: str                    # Card.id
    location: int = Field(ge=0, lt=NUM_LOCATIONS)


class TurnDecision(BaseModel):
    actions: list[PlayAction] = Field(default_factory=list)  # 按执行顺序（影响揭示顺序）
    snap: bool = False
    retreat: bool = False
    win_probability: float | None = None   # 大模型估计的最终胜率（0–1），用于撤退期望计算
    reason: str = ""
    source: str = "unknown"      # claude / rule_based，便于复盘
