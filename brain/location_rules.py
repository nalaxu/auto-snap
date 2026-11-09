"""出牌限制。

游戏客户端不会预先计算「哪张牌不能打在哪」（Card._locationsThisCardCanNotPlay 实测为空），
拖拽时才由规则拒绝。因此：
1. 从资料库（brain/knowledge.py）解析限制，决策时提前避开：
   - 场地效果（The Big House、Kyln、Vault…）
   - 场上已揭示的牌（Goose：双方都不能在此出 4 费以上；Ebony Maw：控制者不能在此出牌）
   - 卡牌自身（Giganto 只能左、Sentry 不能右、Crossbones 只能出在领先处…）
2. 解析不了的限制由执行层在拖拽被拒后上报，写入 GameState.forbidden，重新决策（见 main.auto）。

_CANT_PLAY / _MUST_PLAY_ON_TURN 为国服实测的人工覆盖，优先于资料库文字。
"""
from __future__ import annotations

import re

from collections.abc import Callable

from . import knowledge
from .models import Card, GameState

# 人工覆盖：(turn, card) -> 该地点是否禁止打出这张牌
_CANT_PLAY: dict[str, Callable[[int, Card], bool]] = {
    "HellfireClub": lambda turn, c: c.cost == 1,               # Cards that cost 1 can't be played here.（实机截图）
}
# 国服实测与资料库文字不符的场地：不按文字限制
_IGNORE_DB_TEXT = {"SanctumSanctorum"}                         # 国服教程局我方在此出过 4 张牌

# 人工覆盖：某回合所有牌必须打在这里
_MUST_PLAY_ON_TURN: dict[str, int] = {
    "AvengersCompound": 5,                                     # On turn 5, all cards must be played here.（实机截图）
}


def _location_blocks(name: str, turn: int, card: Card, db: knowledge.GameDB) -> bool:
    rule = _CANT_PLAY.get(name)
    if rule is not None:
        return rule(turn, card)
    if name in _IGNORE_DB_TEXT:
        return False
    return any(r.blocks(turn, card.cost, card.power) for r in knowledge.parse_restrictions(db.location_effect(name)))


def _board_blocks(state: GameState, card: Card, loc_idx: int, db: knowledge.GameDB) -> bool:
    loc = state.locations[loc_idx]
    for side, cards in (("mine", loc.my_cards), ("opp", loc.opp_cards)):
        for c in cards:
            for r in knowledge.parse_restrictions(db.ability(c.id)):
                if r.who == "owner" and side != "mine":
                    continue
                if r.blocks(state.turn, card.cost, card.power):
                    return True
    return False


def _self_blocks(state: GameState, card: Card, loc_idx: int, db: knowledge.GameDB) -> bool:
    sr = knowledge.parse_self_restriction(db.ability(card.id))
    if sr is None:
        return False
    loc = state.locations[loc_idx]
    return ((sr.only_idx is not None and loc_idx != sr.only_idx)
            or (sr.not_idx is not None and loc_idx == sr.not_idx)
            or (sr.only_winning and loc.my_power <= loc.opp_power)
            or (sr.only_losing and loc.my_power >= loc.opp_power)
            or (sr.max_turn is not None and state.turn > sr.max_turn)
            or (sr.not_after_playing and bool(state.played_last_turn)))


def hand_control_blocked(state: GameState, db: knowledge.GameDB) -> str | None:
    """手牌里有接管出牌的牌（Agatha Harkness）：本回合我方不能自己出牌。
    国际服：偶数回合由她出牌（on even turns）；国服：每回合都由她出牌（on all turns）。"""
    for c in state.hand:
        ab = (db.ability(c.id) or "").lower()
        if "controls your cards on all turns" in ab:
            return f"{c.id} 在手：每回合由她替你出牌"
        if "controls your cards on even turns" in ab and state.turn % 2 == 0:
            return f"{c.id} 在手：偶数回合由她替你出牌"
    return None


def forced_turn(name: str, db: knowledge.GameDB | None = None) -> int | None:
    if name in _MUST_PLAY_ON_TURN:
        return _MUST_PLAY_ON_TURN[name]
    return knowledge.parse_forced_turn((db or knowledge.default_db()).location_effect(name))


def blocked_reason(state: GameState, card: Card, loc_idx: int) -> str | None:
    """不能出牌的原因；可以出返回 None。"""
    if (card.id, loc_idx) in state.forbidden:
        return "本回合拖拽已被游戏拒绝过"
    loc = state.locations[loc_idx]
    if not loc.playable:
        return "该地点当前不可出牌"
    db = knowledge.default_db()
    if reason := hand_control_blocked(state, db):
        return reason
    for other in state.locations:
        if other.idx != loc_idx and other.revealed and forced_turn(other.name, db) == state.turn:
            return f"{other.name} 的限制：本回合所有牌必须打在那里"
    if loc.revealed and loc.name and _location_blocks(loc.name, state.turn, card, db):
        return f"场地 {loc.name} 的限制"
    if _board_blocks(state, card, loc_idx, db):
        return "场上已揭示牌的限制（如 Goose）"
    if _self_blocks(state, card, loc_idx, db):
        return f"{card.id} 自身的出牌位置限制"
    return None


def can_play(state: GameState, card: Card, loc_idx: int) -> bool:
    return blocked_reason(state, card, loc_idx) is None


def self_moveable_text(ability: str) -> bool:
    """卡牌自带 Moveable。「Quickdraw: Moveable …」只有抽到当回合打出才生效（如 Aunt May），读不到是否触发，按不可移动处理。
    2026-11-09 实战：Aunt May 没触发 Quickdraw，拖动被游戏拒绝。"""
    head = ability.lower().split("quickdraw:", 1)[0]
    # 关键词形式：句首 / 冒号后的「Moveable」「Moveable twice」；排除「a Moveable card / ability」这类提及
    return bool(re.search(r"(?:^|[.:;]\s*)moveable\b", head.strip()))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def granted_movers(state: GameState) -> set[str]:
    """我方已揭示的场上牌通过 Ongoing 赋予移动能力的牌 id：如 Sam Wilson「Ongoing: You can move Cap's Shield.」。
    2026-11-09 实战：Cap's Shield 每回合都能移（移到 Cap 所在地点 +2），程序之前不认识，一次都没移。"""
    db = knowledge.default_db()
    names = {_norm(v.get("name") or k): k for k, v in db.cards.items()}
    names.update({_norm(k): k for k in db.cards})
    out: set[str] = set()
    for loc in state.locations:
        for c in loc.my_cards:
            if c.turn_played is not None and c.turn_played >= state.turn:
                continue
            for m in re.finditer(r"you can move ([^.]+?)\.", db.ability(c.id) or "", re.I):
                cid = names.get(_norm(m.group(1)))
                if cid:
                    out.add(cid)
    return out


def move_targets(state: GameState, card: Card, from_idx: int) -> list[int]:
    """我方场上牌本回合可以移到哪些地点：卡牌自带 Moveable，或目标场地本回合允许移入（如 New York 第 6 回合）。
    本回合刚打出的牌（还没揭示）不能移动；目标要有空位。"""
    if card.turn_played is None or card.turn_played >= state.turn:
        return []
    db = knowledge.default_db()
    self_moveable = self_moveable_text(db.ability(card.id) or "") or card.id in granted_movers(state)
    out = []
    for loc in state.locations:
        if loc.idx == from_idx or loc.my_slots_free <= 0:
            continue
        ok = self_moveable
        if not ok and loc.revealed and loc.name:
            allowed, turn = knowledge.parse_move_to(db.location_effect(loc.name))
            ok = allowed and (turn is None or turn == state.turn)
        if ok:
            out.append(loc.idx)
    return out


def daredevil_watch(state: GameState) -> bool:
    """我方有已揭示的 Daredevil（夜魔侠）且现在是第 5 回合：要先等对手出牌展示完（按钮显示 WAITING 时放牌会被拒）。"""
    if state.turn != 5:
        return False
    return any(c.id.startswith("Daredevil") and c.turn_played is not None and c.turn_played < 5
               for loc in state.locations for c in loc.my_cards)
