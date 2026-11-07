"""场上最新要点（消息栈）：每回合按当前局面重建，只保留仍然有效、对本回合决策有用的信息，过期的自动消失。

BoardTracker.update(game_id, state)：
1. 跟踪每张场上牌的打出回合（按实体 id，首次出现在第 N 回合的局面里 = 第 N-1 回合打出并揭示）
2. 生成 state.board_notes（按优先级，最多 MAX_NOTES 条）：
   - 本回合待触发：我方/对手上回合打出的「下回合在此出牌 +N」牌（Hawkeye 类），过了本回合即失效
   - 持续效果：场上双方已揭示的 Ongoing 牌（限制、加成），牌离场即消失
   - 对手上回合新打出的牌（揭示效果已结算，提示其意图），只保留一回合
   - 场地的回合事件：效果里提到的回合还没过（如「第 6 回合开始交换手牌」），过了即删除
"""
from __future__ import annotations

import re

from . import knowledge
from .models import GameState

MAX_NOTES = 10
_SIDE = {"mine": "我方", "opp": "对手"}
_POS = ["左", "中", "右"]


def _where(idx: int, name: str) -> str:
    return f"地点{idx}({_POS[idx] if idx < 3 else idx}{'·' + name if name else ''})"


class BoardTracker:
    def __init__(self, db: knowledge.GameDB | None = None):
        self.db = db
        self.game_id: str | None = None
        self.first_turn: int | None = None
        self.played: dict[tuple, int | None] = {}

    def update(self, game_id: str, state: GameState) -> GameState:
        db = self.db or knowledge.default_db()
        if game_id != self.game_id:
            self.game_id, self.first_turn, self.played = game_id, state.turn, {}
        for loc in state.locations:
            for side, cards in (("mine", loc.my_cards), ("opp", loc.opp_cards)):
                for n, c in enumerate(cards):
                    key = (side, c.entity) if c.entity is not None else (side, loc.idx, c.id, n)
                    if key not in self.played:
                        # 中途接管时，首个局面里已在场上的牌回合未知
                        self.played[key] = None if state.turn == self.first_turn and state.turn > 1 else max(0, state.turn - 1)
                    c.turn_played = self.played[key]
        state.board_notes = build_notes(state, db)
        return state


def _turn_numbers(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"turn (\d+)", text or "", flags=re.I)]


def build_notes(state: GameState, db: knowledge.GameDB) -> list[str]:
    pending, ongoing, opp_new, loc_events = [], [], [], []
    last = state.turn - 1
    for loc in state.locations:
        where = _where(loc.idx, loc.name)
        for side, cards in (("mine", loc.my_cards), ("opp", loc.opp_cards)):
            for c in cards:
                ability = db.ability(c.id)
                bonus = knowledge.followup_bonus(ability)
                if bonus and c.turn_played == last and last >= 1:
                    who = "我方" if side == "mine" else "对手"
                    pending.append(f"【本回合有效】{where} {_SIDE[side]} {c.id} 上回合打出：{who}本回合在此再出一张牌，它 +{bonus} 战力"
                                   + ("（尽量在此跟牌）" if side == "mine" else ""))
                if ability.startswith("Ongoing"):
                    ongoing.append(f"【持续】{where} {_SIDE[side]} {c.id}：{ability}")
                if side == "opp" and c.turn_played == last and last >= 1 and not ability.startswith("Ongoing"):
                    opp_new.append(f"【对手上回合】{where} 打出 {c.id}（{c.cost}费{c.power}力）：{ability or '无能力'}")
        if loc.revealed and loc.name:
            eff = db.location_effect(loc.name)
            turns = _turn_numbers(eff)
            if turns and max(turns) >= state.turn:
                left = min(t for t in turns if t >= state.turn) - state.turn
                when = "本回合" if left == 0 else f"{left} 回合后"
                loc_events.append(f"【场地事件·{when}】{where}：{eff}")
    notes = pending + ongoing + opp_new + loc_events
    return notes[:MAX_NOTES]
