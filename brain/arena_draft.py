"""竞技场组卡 / 换牌：规则打分（大模型决策的参考分与兜底），见 docs/arena-mode-plan.md「M2」。

用户确认的原则：好卡先拿 > 低费曲线（1 费 1–3、2 费 3–4、3 费 2–3、4 费 1–2、5 费 1–2、6 费 0–1，不是硬约束）；
前几轮看单卡强度，后几轮曲线权重上升；需要组件的牌有组件时才加分；针对卡打折（评分表里已体现）。

单卡强度来自评分表 data/arena/ratings.json（1–10）；没有评分的卡按身材估算。
"""
from __future__ import annotations

from dataclasses import dataclass

from .arena_ratings import default_ratings
from .knowledge import default_db

# 费用 -> (最少, 最多) 张；6 费及以上合并、0 费算 1 费
CURVE = {1: (1, 3), 2: (3, 4), 3: (2, 3), 4: (1, 2), 5: (1, 2), 6: (0, 1)}
SYNERGY_NEEDS = 0.7      # needs:X 的牌，卡组里每有一张 X 组件（enabler:X 或同体系牌）加分，最多算 3 张
SYNERGY_ENABLER = 0.4    # enabler:X 的牌，卡组里每有一张 needs:X 加分


@dataclass
class Pick:
    index: int          # 选第几张（0/1/2）
    card: str
    reason: str
    scores: list[float]
    source: str = "rule"


@dataclass
class Swap:
    out_index: int | None   # 卡组里换下第几张；None = 不换（刷新放弃）
    out_card: str | None
    in_index: int | None    # 换进候选第几张
    in_card: str | None
    reason: str
    gain: float = 0.0
    source: str = "rule"


def bucket(cost) -> int:
    try:
        c = int(cost)
    except (TypeError, ValueError):
        return 3
    return max(1, min(6, c))


def cost_of(cid: str):
    return default_db().card(cid).get("cost")


def card_value(cid: str) -> float:
    """单卡强度（1–10）：评分表优先，没有评分按身材估算。"""
    r = default_ratings().rating(cid)
    if r is not None:
        return r
    c = default_db().card(cid)
    if not c or c.get("cost") is None or c.get("power") is None:
        return 5.0
    vanilla = {0: 1, 1: 2, 2: 3, 3: 4, 4: 6, 5: 8, 6: 10}.get(min(int(c["cost"]), 6), 12)
    v = 5.0 + (int(c["power"]) - vanilla) / 2 + (1.0 if c.get("ability") else 0.0)
    return max(1.0, min(10.0, v))


def curve_counts(deck: list[str]) -> dict[int, int]:
    out = {b: 0 for b in CURVE}
    for d in deck:
        out[bucket(cost_of(d))] += 1
    return out


def curve_need(deck: list[str], cid: str) -> float:
    """把这张牌加入卡组的曲线得分：补到最少张数 +1.5，在范围内 +0.3，超过最多张数每多一张 −2。"""
    b = bucket(cost_of(cid))
    lo, hi = CURVE[b]
    n = curve_counts(deck)[b]
    if n < lo:
        return 1.5
    if n >= hi:
        return -2.0 * (n - hi + 1)
    return 0.3


def curve_penalty(deck: list[str]) -> float:
    """整副卡组的曲线偏差（≤ 0）。"""
    p = 0.0
    for b, n in curve_counts(deck).items():
        lo, hi = CURVE[b]
        p -= 1.5 * max(0, lo - n) + 2.0 * max(0, n - hi)
    return p


def _themes(cid: str, kind: str) -> set[str]:
    return {t.split(":", 1)[1] for t in default_ratings().tags(cid) if t.startswith(kind + ":")}


def synergy(deck: list[str], cid: str) -> float:
    """这张牌和卡组其他牌的配合分。"""
    needs, enables = _themes(cid, "needs"), _themes(cid, "enabler")
    s = 0.0
    for t in needs:
        k = sum(1 for d in deck if t in _themes(d, "enabler") or t in _themes(d, "needs"))
        s += SYNERGY_NEEDS * min(k, 3)
    for t in enables:
        k = sum(1 for d in deck if t in _themes(d, "needs"))
        s += SYNERGY_ENABLER * min(k, 3)
    return s


def pick_score(deck: list[str], cid: str, deck_size: int = 12) -> float:
    w = 0.3 + 0.7 * len(deck) / max(1, deck_size - 1)    # 曲线权重：首轮 0.3 → 末轮 1.0
    return card_value(cid) + w * curve_need(deck, cid) + synergy(deck, cid)


def choose_pick(deck: list[str], candidates: list[str], deck_size: int = 12) -> Pick:
    scores = [pick_score(deck, c, deck_size) for c in candidates]
    i = max(range(len(candidates)), key=lambda k: scores[k])
    return Pick(i, candidates[i], f"规则：评分 {card_value(candidates[i]):.1f} + 曲线 / 配合 = {scores[i]:.1f}", scores)


def deck_score(deck: list[str]) -> float:
    total = sum(card_value(c) for c in deck) + curve_penalty(deck)
    for i, c in enumerate(deck):
        total += synergy(deck[:i] + deck[i + 1:], c) / 2    # 两两配合算两次，各取一半
    return total


def choose_swap(deck: list[str], candidates: list[str], threshold: float = 1.0) -> Swap:
    """换牌：枚举「换下卡组某张 × 换进某个候选」，整副卡组得分提升最大且超过阈值才换。"""
    if not deck or not candidates:
        return Swap(None, None, None, None, "没有可换的牌")
    base = deck_score(deck)
    best = (0.0, None, None)
    for i in range(len(deck)):
        for j, inc in enumerate(candidates):
            gain = deck_score(deck[:i] + [inc] + deck[i + 1:]) - base
            if gain > best[0]:
                best = (gain, i, j)
    gain, i, j = best
    if i is None or gain < threshold:
        return Swap(None, None, None, None, f"最佳提升 {gain:.1f} < 阈值 {threshold}，放弃", gain)
    return Swap(i, deck[i], j, candidates[j], f"规则：{deck[i]} → {candidates[j]}，卡组得分 +{gain:.1f}", gain)
