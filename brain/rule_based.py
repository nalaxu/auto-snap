"""本地规则 AI：在所有合法出牌组合中搜索总价值最高的方案。

每张牌的价值 = 费用 × (ENERGY_WEIGHT + 规则得分 / SCORE_SCALE)：按费用加权，避免「多出几张小牌」压过「一张关键大牌」，
同时鼓励用满能量。

规则见 brain/strategy.py（争夺价值、场地利弊、卡牌-场地配合、Hawkeye 跟牌、Star-Lord 触发条件，可扩展）。
逐张贪心会出现「高费牌先占满中路，Medusa 只能去别处」之类的问题，所以对整手牌做搜索（手牌 ≤ 8 张，能量剪枝后很快）。
某张牌在所有场地都很不利（得分 < HOLD_THRESHOLD）时宁可留牌不出。
"""
from __future__ import annotations

from . import strategy
from .location_rules import can_play
from .models import GameState, PlayAction, TurnDecision

HOLD_THRESHOLD = -3.0
ENERGY_WEIGHT = 0.5          # 每用 1 点能量的基础价值：鼓励用满能量
SCORE_SCALE = 3.0
MAX_NODES = 60000            # 搜索上限（防御性）


def decide(state: GameState, advisor=None, rules=None) -> TurnDecision:
    ctx = strategy.Context(state=state, advisor=advisor or strategy.default_advisor(), cards_db=strategy.cards_db(),
                           diff={loc.idx: loc.my_power - loc.opp_power for loc in state.locations})
    cards = sorted(state.hand, key=lambda c: (-c.cost, -c.power))
    slots = {loc.idx: loc.my_slots_free for loc in state.locations if loc.playable}
    legal = {id(c): [i for i in slots if can_play(state, c, i)] for c in cards}

    best: dict = {"value": float("-inf"), "plan": []}
    nodes = 0

    def dfs(k: int, energy: int, value: float, plan: list) -> None:
        nonlocal nodes
        nodes += 1
        if k == len(cards) or nodes > MAX_NODES:
            if value > best["value"] + 1e-9:
                best["value"], best["plan"] = value, list(plan)
            return
        card = cards[k]
        if card.cost <= energy:
            for loc in legal[id(card)]:
                if slots[loc] <= 0:
                    continue
                score, why = strategy.score_placement(ctx, card, loc, rules)
                if score < HOLD_THRESHOLD:
                    continue
                slots[loc] -= 1
                ctx.diff[loc] += card.power
                ctx.planned[loc] = ctx.planned.get(loc, 0) + 1
                plan.append((card, loc, score, why))
                dfs(k + 1, energy - card.cost, value + max(card.cost, 1) * (ENERGY_WEIGHT + score / SCORE_SCALE), plan)
                plan.pop()
                ctx.planned[loc] -= 1
                ctx.diff[loc] -= card.power
                slots[loc] += 1
        dfs(k + 1, energy, value, plan)

    dfs(0, state.energy, 0.0, [])
    actions = [PlayAction(card=c.id, location=loc) for c, loc, _, _ in best["plan"]]
    notes = [f"{c.id}→地点{loc}[{s:+.1f}: {'; '.join(w) or '-'}]" for c, loc, s, w in best["plan"]]
    return TurnDecision(actions=actions, reason=" | ".join(notes) or "无可出的牌", source="rule_based")
