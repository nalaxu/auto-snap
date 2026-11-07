"""决策规则：给「把某张牌放到某个场地」打分，规则可叠加、可扩展。

每条规则返回 (分数, 理由)；规则 AI 选总分最高的合法场地，并把理由写进决策日志，便于赛后复盘。
新增技巧 = 新增一个 Rule 并加入 DEFAULT_RULES。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .locations import LocationAdvisor, load_cards, phase
from .models import Card, GameState


@dataclass
class Context:
    state: GameState
    advisor: LocationAdvisor
    cards_db: dict
    diff: dict[int, int] = field(default_factory=dict)   # 我方-敌方 战力差（含本回合已计划的牌）
    planned: dict[int, int] = field(default_factory=dict)  # 本回合已计划放到各地点的牌数


class Rule(Protocol):
    name: str

    def score(self, ctx: Context, card: Card, loc: int) -> tuple[float, str | None]: ...


class ContestRule:
    """争夺价值：能追平/反超的落后场地最优先；已大幅领先的场地不再浪费战力。"""
    name = "contest"

    def score(self, ctx, card, loc):
        d = ctx.diff[loc]
        if d < 0 and d + card.power >= 0:
            return 3.0, f"落后{-d}可追平"
        if 0 <= d < 5:
            return 2.0, f"小幅领先{d}需巩固"
        if d < 0:
            return 0.5, f"落后{-d}较多"
        return -1.0, f"已领先{d}"


class LocationBiasRule:
    """玩家技巧：分析场地效果，判断本回合阶段往这里放牌是利好还是不利。"""
    name = "location"
    weight = 1.5

    def score(self, ctx, card, loc):
        l = ctx.state.locations[loc]
        if not l.revealed or not l.name:
            return 0.0, None
        info = ctx.advisor.info(l.name)
        b = info.bias_at(ctx.state.turn, ctx.state.max_turn)
        if b == 0:
            return 0.0, None
        p = phase(ctx.state.turn, ctx.state.max_turn)
        return b * self.weight, f"{l.name}在{p}阶段{'利好' if b > 0 else '不利'}{b:+d}"


class CardLocationSynergyRule:
    """卡牌与场地的配合：Ongoing 卡放到「持续效果翻倍」场地；Medusa 放中间；白板牌放「无能力 +3」场地。"""
    name = "synergy"

    def score(self, ctx, card, loc):
        meta = ctx.cards_db.get(card.id, {})
        l = ctx.state.locations[loc]
        if meta.get("type") == "ongoing" and l.revealed and "ongoing_double" in ctx.advisor.info(l.name).tags:
            return 3.0, f"{card.id}持续效果在{l.name}翻倍"
        if "prefers_middle" in meta.get("tags", []) and loc == 1:
            return 2.0, f"{card.id}在中间+3"
        if meta.get("type") == "vanilla" and l.revealed and "no_ability_buff" in ctx.advisor.info(l.name).tags:
            return 2.0, f"{card.id}是白板牌，在{l.name}+3"
        return 0.0, None


class FollowupRule:
    """Hawkeye 类：我方上回合打出「下回合在此出牌 +N」的牌，本回合在那里出的第一张牌触发加成。"""
    name = "followup"

    def score(self, ctx, card, loc):
        if ctx.planned.get(loc, 0):
            return 0.0, None
        for c in ctx.state.locations[loc].my_cards:
            meta = ctx.cards_db.get(c.id, {})
            if "wants_followup_here" in meta.get("tags", []) and c.turn_played == ctx.state.turn - 1:
                from .knowledge import followup_bonus
                n = followup_bonus(meta.get("ability", "")) or 3
                return 3.0, f"触发{c.id}跟牌+{n}"
        return 0.0, None


class OppPlayHereRule:
    """Star-Lord / Gamora 类：需要对手本回合也在此出牌。对手此处已满则不可能触发。"""
    name = "opp_play_here"

    def score(self, ctx, card, loc):
        if "wants_opp_play_here" not in ctx.cards_db.get(card.id, {}).get("tags", []):
            return 0.0, None
        if ctx.state.locations[loc].opp_card_count >= 4:
            return -2.0, f"对手在此已满，{card.id}无法触发"
        return 0.0, None


class DoublerRule:
    """Iron Man 类（翻倍我方在此总战力）：放到我方已有战力越高的地点收益越大。"""
    name = "doubler"

    def score(self, ctx, card, loc):
        meta = ctx.cards_db.get(card.id, {})
        if "doubles_location" not in meta.get("tags", []) and "Your total Power is doubled" not in meta.get("ability", ""):
            return 0.0, None
        mine = ctx.state.locations[loc].my_power + ctx.diff[loc] - (ctx.state.locations[loc].my_power - ctx.state.locations[loc].opp_power)
        s = min(3.0, max(0, mine) / 4)
        return (s, f"{card.id}翻倍此处{mine}战力") if s else (0.0, None)


DEFAULT_RULES: list[Rule] = [ContestRule(), LocationBiasRule(), CardLocationSynergyRule(), FollowupRule(), OppPlayHereRule(),
                             DoublerRule()]


def score_placement(ctx: Context, card: Card, loc: int, rules: list[Rule] | None = None) -> tuple[float, list[str]]:
    total, reasons = 0.0, []
    for r in rules or DEFAULT_RULES:
        s, why = r.score(ctx, card, loc)
        total += s
        if why:
            reasons.append(f"{why}({s:+.1f})")
    return total, reasons


_default_advisor: LocationAdvisor | None = None
_cards_db: dict | None = None


def default_advisor() -> LocationAdvisor:
    global _default_advisor
    if _default_advisor is None:
        _default_advisor = LocationAdvisor()
    return _default_advisor


def set_default_advisor(advisor: LocationAdvisor) -> None:
    global _default_advisor
    _default_advisor = advisor


def cards_db() -> dict:
    global _cards_db
    if _cards_db is None:
        _cards_db = load_cards()
    return _cards_db
