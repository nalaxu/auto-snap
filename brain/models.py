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
    activated: bool | None = None   # 我方场上牌：激活（Activate）能力是否已用；非场上牌为 None
    slot: int | None = None         # 我方场上牌在该地点的格子（0 左上、1 右上、2 左下、3 右下）
    screen_activatable: bool = False  # 画面上这张牌有「Press & Hold to Activate」提示（资料库没写激活也算，如复制来的能力）


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
    deck_name: str = ""                     # 我方当前卡组名（卡组知识库索引）
    opp_seen: list[str] = Field(default_factory=list)   # 本局对手揭示过的牌（含已离场），用于识别对手卡组原型
    played_here_since_reveal: dict[int, int] = Field(default_factory=dict)   # 地点 -> 揭示后我方往那里打过几张（场地信号是否已触发）
    played_last_turn: bool | None = None   # 我方上回合是否出过牌（Infinaut 类限制）；读不到为 None
    opp_destroyed: int | None = None        # 对手本局摧毁过的牌数（摧毁 combo 触发次数）
    opp_discarded: int | None = None        # 对手本局弃过的牌数（弃牌 combo 触发次数）
    opp_energy_by_turn: dict[int, int] = Field(default_factory=dict)   # 对手每回合打出的已揭示牌总费用（brain/board.py 统计）
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
    arena: dict | None = None   # 竞技场局的整场背景（血量 / 积分 / 档位 / Snap 门槛，brain/arena_strategy.py），排位为 None


class ActionType(str, Enum):
    PLAY = "play"


class PlayAction(BaseModel):
    type: Literal["play"] = "play"
    card: str                    # Card.id
    location: int = Field(ge=0, lt=NUM_LOCATIONS)


class ActivateAction(BaseModel):
    card: str                    # 我方场上牌的 id
    location: int = Field(ge=0, lt=NUM_LOCATIONS)


class MoveAction(BaseModel):
    card: str
    from_location: int = Field(ge=0, lt=NUM_LOCATIONS)
    to_location: int = Field(ge=0, lt=NUM_LOCATIONS)


class TurnDecision(BaseModel):
    actions: list[PlayAction] = Field(default_factory=list)  # 按执行顺序（影响揭示顺序）
    activate: list[ActivateAction] = Field(default_factory=list)
    moves: list[MoveAction] = Field(default_factory=list)          # 本回合移动的场上牌（激活后、出牌前执行）  # 本回合要激活的场上牌（先于出牌执行）
    snap: bool = False
    retreat: bool = False
    win_probability: float | None = None   # 校准后的最终胜率（0–1），用于撤退期望计算
    win_probability_raw: float | None = None   # 大模型原始预估（校准样本用）
    reason: str = ""
    source: str = "unknown"      # claude / rule_based，便于复盘
