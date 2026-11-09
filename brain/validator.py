"""规则校验器：AI 的输出必须先通过这里才能交给执行层。"""
from __future__ import annotations

from .location_rules import blocked_reason
from .models import GameState, TurnDecision


def validate(state: GameState, decision: TurnDecision) -> list[str]:
    """返回错误列表；空列表表示决策合法。"""
    errors: list[str] = []
    # 同 id 可有多张（如 Sentinel 复制）：按手牌顺序依次消耗，与执行层「取手牌中第一张同 id」一致
    copies: dict[str, list] = {}
    for c in sorted(state.hand, key=lambda c: (c.hand_index is None, c.hand_index or 0)):
        copies.setdefault(c.id, []).append(c)
    used: dict[str, int] = {}
    energy_left = state.energy
    slots = {loc.idx: loc.my_slots_free for loc in state.locations}
    playable = {loc.idx: loc.playable for loc in state.locations}

    # 执行顺序 激活 → 移动 → 出牌：先校验移动并更新空位（移走的牌空出格子，本回合可以出牌）
    from .location_rules import move_targets
    for mv in decision.moves:
        if not 0 <= mv.from_location < len(state.locations):
            errors.append(f"移动 {mv.card}：地点{mv.from_location} 不存在")
            continue
        loc = state.locations[mv.from_location]
        card = next((c for c in loc.my_cards if c.id == mv.card), None)
        if card is None:
            errors.append(f"移动 {mv.card}：地点{mv.from_location} 没有这张我方牌")
            continue
        if mv.to_location not in move_targets(state, card, mv.from_location):
            errors.append(f"移动 {mv.card}：本回合不能从地点{mv.from_location} 移到地点{mv.to_location}")
            continue
        if slots.get(mv.to_location, 0) <= 0:
            errors.append(f"移动 {mv.card}：地点{mv.to_location} 已满")
            continue
        slots[mv.from_location] += 1
        slots[mv.to_location] -= 1

    for i, act in enumerate(decision.actions):
        if act.card not in copies:
            errors.append(f"动作{i}: 手牌中没有 {act.card}")
            continue
        n = used.get(act.card, 0)
        if n >= len(copies[act.card]):
            errors.append(f"动作{i}: {act.card} 手牌中只有 {n} 张，不能再打出")
            continue
        card = copies[act.card][n]
        if act.location not in slots:
            errors.append(f"动作{i}: 地点 {act.location} 不存在")
            continue
        if not playable[act.location]:
            errors.append(f"动作{i}: 地点 {act.location} 当前不可出牌")
            continue
        why = blocked_reason(state, card, act.location)
        if why:
            errors.append(f"动作{i}: {act.card} 不能打在地点 {act.location}（{why}）")
            continue
        if slots[act.location] <= 0:
            errors.append(f"动作{i}: 地点 {act.location} 已满")
            continue
        if card.cost > energy_left:
            errors.append(f"动作{i}: {act.card} 费用 {card.cost} 超过剩余能量 {energy_left}")
            continue
        used[act.card] = n + 1
        energy_left -= card.cost
        slots[act.location] -= 1

    if decision.snap and not state.can_snap:
        errors.append("当前不能 Snap")
    avail = {(c.id, l.idx) for l in state.locations for c in activatable(state, l)}
    for a in decision.activate:
        if (a.card, a.location) not in avail:
            errors.append(f"激活 {a.card}@地点{a.location} 不可用（不是我方场上可激活的牌、本回合刚打出或已激活过）")
    return errors


def activatable(state: GameState, loc) -> list:
    """该地点我方可激活的场上牌：能力含 Activate、不是本回合打出、尚未激活。"""
    from .knowledge import default_db

    db = default_db()
    out = []
    for c in loc.my_cards:
        if c.activated is False and (c.screen_activatable or (
                c.turn_played is not None and 0 < c.turn_played < state.turn and "Activate:" in (db.ability(c.id) or ""))):
            out.append(c)
    return out
