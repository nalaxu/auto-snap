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
    return errors
