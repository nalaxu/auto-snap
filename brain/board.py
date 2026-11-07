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
        self.opp_spent: dict[int, int] = {}

    def update(self, game_id: str, state: GameState) -> GameState:
        db = self.db or knowledge.default_db()
        if game_id != self.game_id:
            self.game_id, self.first_turn, self.played, self.opp_spent = game_id, state.turn, {}, {}
        for loc in state.locations:
            for side, cards in (("mine", loc.my_cards), ("opp", loc.opp_cards)):
                for n, c in enumerate(cards):
                    key = (side, c.entity) if c.entity is not None else (side, loc.idx, c.id, n)
                    if key not in self.played and side == "opp":
                        t = c.turn_played or max(0, state.turn - 1)
                        if t >= 1:   # 对手每张牌第一次出现时按打出回合累计费用（之后被摧毁也算在内）
                            self.opp_spent[t] = self.opp_spent.get(t, 0) + c.cost
                    if key not in self.played:
                        # 中途接管时，首个局面里已在场上的牌回合未知
                        self.played[key] = None if state.turn == self.first_turn and state.turn > 1 else max(0, state.turn - 1)
                    if c.turn_played is None:   # 内存读到的揭示回合（TurnRevealed）优先
                        c.turn_played = self.played[key]
        state.opp_energy_by_turn = dict(self.opp_spent)
        state.board_notes = build_notes(state, db)
        return state


_NEXT_CARD = re.compile(r"\b(?:your )?next card(?: you play)?\b", re.I)
_NEXT_ANY = re.compile(r"\bnext (?:turn|card|time)\b", re.I)


def next_effect_note(where: str, card_id: str, ability: str, loc_name: str) -> str | None:
    """上回合打出、效果落在「本回合 / 下一张牌」的我方牌 -> 本回合要点（通用：Shuri、Forge、Stick、Zero、Rescue、Jessica Jones…）。

    「下一张牌」类效果只作用于本回合打出的第一张牌：提醒排序（actions 顺序 = 出牌顺序）。
    """
    if not ability or ability.startswith("Ongoing") or not _NEXT_ANY.search(ability):
        return None
    if _NEXT_CARD.search(ability):
        here = re.search(r"at this location|here", ability, re.I)
        order = f"actions 第一张必须打在 {where}" if here else "actions 第一张就是受影响的牌"
        return f"【本回合有效·出牌顺序】{where} 我方 {card_id}：{ability} → {order}"
    return f"【本回合有效】{where} 我方 {card_id}：{ability}"


def reorder_for_next_card(state: GameState, decision, db: knowledge.GameDB | None = None) -> str | None:
    """「下一张牌打在这里才生效」（如 Shuri 加倍）：本回合计划里有打到该地点的牌，就把其中战力最高的一张挪到第一张。

    返回调整说明；无需调整返回 None。只处理指定地点的增益类（here / at this location），其余交给模型按要点排序。
    """
    db = db or knowledge.default_db()
    last = state.turn - 1
    if last < 1 or not decision.actions:
        return None
    for loc in state.locations:
        for c in loc.my_cards:
            ab = db.ability(c.id) or ""
            if c.turn_played != last or not _NEXT_CARD.search(ab) or not re.search(r"at this location|here", ab, re.I):
                continue
            here = [i for i, a in enumerate(decision.actions) if a.location == loc.idx]
            if not here:
                return None
            power = {h.id: h.power for h in state.hand}
            best = max(here, key=lambda i: power.get(decision.actions[i].card, 0))
            if best == 0:
                return None
            a = decision.actions.pop(best)
            decision.actions.insert(0, a)
            return f"{c.id} 的「下一张牌」效果：{a.card} 调到第一张出（地点{loc.idx}）"
    return None


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
                if not bonus and side == "mine" and c.turn_played == last and last >= 1:
                    if note := next_effect_note(where, c.id, ability, loc.name):
                        pending.append(note)
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
    actionable = _actionable_notes(state, db)
    tempo = _opp_tempo_note(state)
    notes = actionable + pending + ongoing + opp_new + tempo + loc_events
    return notes[:MAX_NOTES]


def _opp_tempo_note(state: GameState) -> list[str]:
    """对手节奏：近 3 回合能量利用（已揭示牌费用 / 回合能量）与摧毁 combo 次数，用来判断对手起手好坏、估计胜率。"""
    turns = [t for t in range(max(1, state.turn - 3), state.turn)]
    if not turns:
        return []
    # 效果直接放到场上的牌（如 Jubilee）也会被计入：按回合能量封顶，避免出现 7/4
    used = ", ".join(f"T{t} {min(state.opp_energy_by_turn.get(t, 0), t)}/{t}" for t in turns)
    combo = "".join(f"；{name} combo {n} 次" for name, n in (("摧毁", state.opp_destroyed), ("弃牌", state.opp_discarded)) if n)
    return [f"【对手节奏】能量利用 {used}（只计已揭示的牌）{combo}"]


def _actionable_notes(state: GameState, db: knowledge.GameDB) -> list[str]:
    """本回合可以主动做的事（不花能量，不做就浪费）：可激活的场上牌、场地允许的移动。"""
    from .location_rules import move_targets
    from .validator import activatable

    final = state.turn >= state.max_turn
    out = []
    for loc in state.locations:
        where = _where(loc.idx, loc.name)
        for c in activatable(state, loc):
            out.append(f"【可激活{'·最后机会' if final else ''}】{where} 我方 {c.id}：{db.ability(c.id)}"
                       + ("（不激活就浪费）" if final else ""))
    moves: dict[int, list[str]] = {}
    for loc in state.locations:
        for c in loc.my_cards:
            for to in move_targets(state, c, loc.idx):
                moves.setdefault(to, []).append(f"{c.id}@地点{loc.idx}")
    for to, cards in moves.items():
        out.append(f"【可移动·本回合】可把我方牌移到 {_where(to, state.locations[to].name)}：{', '.join(cards)}（写进 moves）")
    return out
