"""竞技场组卡 / 换牌决策。

M1：规则版占位（低费曲线 + 身材），保证流程能跑通；M2 换成「竞技场评分表 + 大模型」（docs/arena-mode-plan.md）。

用户确认的原则：好卡先拿 > 低费曲线（1 费 1–3、2 费 3–4、3 费 2–3、4 费 1–2、5 费 1–2、6 费 0–1，不是硬约束）；
前几轮看单卡强度，后几轮曲线权重上升；需要组件的牌有组件时才加分；针对卡打折。
"""
from __future__ import annotations

from dataclasses import dataclass

from .knowledge import default_db

# 费用 -> (最少, 最多) 张；6 费及以上合并
CURVE = {1: (1, 3), 2: (3, 4), 3: (2, 3), 4: (1, 2), 5: (1, 2), 6: (0, 1)}


@dataclass
class Pick:
    index: int          # 选第几张（0/1/2）
    card: str
    reason: str
    scores: list[float]


@dataclass
class Swap:
    out_index: int | None   # 卡组里换下第几张；None = 不换（刷新放弃）
    out_card: str | None
    in_index: int | None    # 换进候选第几张
    in_card: str | None
    reason: str


def _bucket(cost) -> int:
    try:
        c = int(cost)
    except (TypeError, ValueError):
        return 3
    return max(1, min(6, c)) if c > 0 else 1


def card_value(cid: str) -> float:
    """单卡强度（占位）：身材相对同费白板的超出量；有能力的牌给基础分。M2 改为评分表。"""
    c = default_db().card(cid)
    if not c:
        return 0.0
    cost, power = c.get("cost"), c.get("power")
    if cost is None or power is None:
        return 1.0
    vanilla = {0: 1, 1: 2, 2: 3, 3: 4, 4: 6, 5: 8, 6: 10}.get(min(int(cost), 6), 12)
    v = (int(power) - vanilla) / 2
    if c.get("ability"):
        v += 1.5
    return v


def curve_need(deck: list[str], cid: str) -> float:
    """把这张牌加入卡组后的曲线得分：补到最少张数 +，超过最多张数 −。"""
    db = default_db()
    counts: dict[int, int] = {}
    for d in deck:
        b = _bucket(db.card(d).get("cost"))
        counts[b] = counts.get(b, 0) + 1
    b = _bucket(db.card(cid).get("cost"))
    lo, hi = CURVE[b]
    n = counts.get(b, 0)
    if n < lo:
        return 1.5
    if n >= hi:
        return -2.0 * (n - hi + 1)
    return 0.3


def choose_pick(deck: list[str], candidates: list[str], deck_size: int = 12) -> Pick:
    """组卡三选一。前几轮看单卡强度，越往后曲线权重越高。"""
    w = 0.3 + 0.7 * len(deck) / max(1, deck_size - 1)
    scores = [card_value(c) + w * curve_need(deck, c) for c in candidates]
    i = max(range(len(candidates)), key=lambda k: scores[k])
    return Pick(i, candidates[i], f"规则占位：强度+曲线（曲线权重 {w:.2f}）", scores)


def choose_swap(deck: list[str], candidates: list[str], threshold: float = 1.0) -> Swap:
    """换牌：最好的候选 vs 卡组里边际价值最低的一张，提升超过阈值才换。"""
    if not deck or not candidates:
        return Swap(None, None, None, None, "没有可换的牌")

    def deck_score(cards: list[str]) -> float:
        total, rest = 0.0, []
        for c in cards:
            total += card_value(c) + 0.5 * curve_need(rest, c)
            rest.append(c)
        return total

    base = deck_score(deck)
    best = (0.0, None, None)
    for i, out in enumerate(deck):
        for j, inc in enumerate(candidates):
            new = deck[:i] + [inc] + deck[i + 1:]
            gain = deck_score(new) - base
            if gain > best[0]:
                best = (gain, i, j)
    gain, i, j = best
    if i is None or gain < threshold:
        return Swap(None, None, None, None, f"最佳提升 {gain:.1f} < 阈值 {threshold}，放弃")
    return Swap(i, deck[i], j, candidates[j], f"规则占位：提升 {gain:.1f}")
