"""卡牌 / 场地资料库：按游戏内 id（CardDefId / LocationDefId）查询详细信息。

两层数据，人工维护优先：
1. data/cards.json、data/locations.json —— 人工确认（国服实测能力、标签、bias、notes）
2. data/card_db.json、data/location_db.json —— tools/update_card_db.py 抓取的全量资料（能力文字、基础费用/战力）

另外把能力文字里常见的「出牌限制」解析成规则（Goose、The Big House、Kyln…），供 validator / 规则 AI 使用，
这样新卡/新场地不必等撞上一次被拒才手工补。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CARD_DB_PATH = DATA_DIR / "card_db.json"
LOCATION_DB_PATH = DATA_DIR / "location_db.json"
CURATED_CARDS_PATH = DATA_DIR / "cards.json"


def _load_items(path: Path) -> dict:
    if not path.exists():
        return {}
    d = json.loads(path.read_text("utf-8"))
    items = d.get("items", d)
    return {k: v for k, v in items.items() if not k.startswith("_") and isinstance(v, dict)}


class GameDB:
    def __init__(self, card_db: Path = CARD_DB_PATH, location_db: Path = LOCATION_DB_PATH,
                 curated_cards: Path = CURATED_CARDS_PATH):
        self.cards_raw = _load_items(card_db)
        self.locations_raw = _load_items(location_db)
        curated = _load_items(curated_cards)
        self.cards: dict[str, dict] = {}
        for cid in set(self.cards_raw) | set(curated):
            merged = {"cost": None, "power": None, "ability": "", "type": "special", "tags": []}
            merged.update({k: v for k, v in self.cards_raw.get(cid, {}).items() if v not in (None, "")})
            merged.update(curated.get(cid, {}))
            if not merged.get("ability") and cid in self.cards_raw:
                merged["type"] = "vanilla"
            merged["tags"] = sorted(set(merged.get("tags") or []) | ability_tags(merged.get("ability", "")))
            self.cards[cid] = merged

    def card(self, cid: str) -> dict:
        return self.cards.get(cid, {})

    def ability(self, cid: str) -> str:
        return self.cards.get(cid, {}).get("ability", "")

    def location(self, lid: str) -> dict | None:
        return self.locations_raw.get(lid)

    def location_effect(self, lid: str) -> str:
        return (self.locations_raw.get(lid) or {}).get("effect", "")


_default: GameDB | None = None


def default_db() -> GameDB:
    global _default
    if _default is None:
        _default = GameDB()
    return _default


def set_default_db(db: GameDB | None) -> None:
    global _default
    _default = db


# ---------------------------------------------------------------------------
# 能力文字 -> 决策标签（规则 AI 与场上要点共用；新卡无需手工打标签）

FOLLOWUP_RE = re.compile(r"[Ii]f you play a card (?:here|at this location) next turn, \+(\d+) Power")


def ability_tags(text: str) -> set[str]:
    tags: set[str] = set()
    if not text:
        return tags
    if text.startswith("Ongoing"):
        tags.add("ongoing")
    if FOLLOWUP_RE.search(text):
        tags.add("wants_followup_here")
    if re.search(r"If this is at the middle location", text):
        tags.add("prefers_middle")
    if re.search(r"If your opponent played a card here this turn", text):
        tags.add("wants_opp_play_here")
    return tags


def followup_bonus(text: str) -> int | None:
    m = FOLLOWUP_RE.search(text or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# 出牌限制解析
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlayCheck:
    """turn, card(cost, power) -> 是否禁止。who: 'all' 双方受限 / 'owner' 仅控制者受限。"""
    pred: object
    who: str = "all"
    text: str = ""

    def blocks(self, turn: int, cost: int, power: int) -> bool:
        return bool(self.pred(turn, cost, power))


def _cost_pred(n: int, mod: str | None):
    if mod == "or less":
        return lambda t, c, p: c <= n
    if mod == "or more":
        return lambda t, c, p: c >= n
    return lambda t, c, p: c == n


def parse_restrictions(text: str) -> list[PlayCheck]:
    """从场地效果 / 卡牌能力文字中解析「这里不能出哪些牌」。只认常见句式，认不出就不限制（由运行时被拒兜底）。"""
    out: list[PlayCheck] = []
    if not text:
        return out
    t = text.replace("’", "'")
    for m in re.finditer(r"[Cc]ards that cost (\d+)(?:, (\d+),? or (\d+))?( or less| or more)? can't be played here", t):
        if m.group(2):
            ns = {int(m.group(1)), int(m.group(2)), int(m.group(3))}
            out.append(PlayCheck(lambda tu, c, p, ns=ns: c in ns, text=m.group(0)))
        else:
            out.append(PlayCheck(_cost_pred(int(m.group(1)), (m.group(4) or "").strip() or None), text=m.group(0)))
    for m in re.finditer(r"Nobody can play cards that cost (\d+)( or less| or more)? here", t):
        out.append(PlayCheck(_cost_pred(int(m.group(1)), (m.group(2) or "").strip() or None), text=m.group(0)))
    for m in re.finditer(r"Cards with (\d+) or more Power can't be played here", t):
        n = int(m.group(1))
        out.append(PlayCheck(lambda tu, c, p, n=n: p >= n, text=m.group(0)))
    for m in re.finditer(r"You can't play cards here after turn (\d+)", t):
        n = int(m.group(1))
        out.append(PlayCheck(lambda tu, c, p, n=n: tu > n, text=m.group(0)))
    for m in re.finditer(r"On turn (\d+), cards can't be played here", t):
        n = int(m.group(1))
        out.append(PlayCheck(lambda tu, c, p, n=n: tu == n, text=m.group(0)))
    if re.search(r"(?:^|\. )Cards can't be played here", t):
        out.append(PlayCheck(lambda tu, c, p: True, text="Cards can't be played here"))
    if re.search(r"Ongoing: You can't play cards here", t):
        out.append(PlayCheck(lambda tu, c, p: True, who="owner", text="You can't play cards here"))
    return out


def parse_forced_turn(text: str) -> int | None:
    """'On turn 5, all cards must be played here.' -> 5"""
    m = re.search(r"On turn (\d+), all cards must be played here", text or "")
    return int(m.group(1)) if m else None


@dataclass(frozen=True)
class SelfRestriction:
    """卡牌自身的出牌位置限制（Giganto 只能左、Sentry 不能右、Crossbones 只能领先处…）。"""
    only_idx: int | None = None
    not_idx: int | None = None
    only_winning: bool = False
    only_losing: bool = False
    max_turn: int | None = None
    not_after_playing: bool = False   # Infinaut：上回合出过牌就不能打


_POS = {"left": 0, "middle": 1, "right": 2}

_MOVE_TO = re.compile(r"(?:On turn (\d+), )?(?:you can move cards to this location|cards are Moveable to (?:here|this location))", re.I)


def parse_move_to(effect: str) -> tuple[bool, int | None]:
    """场地是否允许把牌移到这里 -> (允许, 仅限的回合)。如 New York：On turn 6, cards are Moveable to here。"""
    m = _MOVE_TO.search(effect or "")
    if not m:
        return False, None
    return True, int(m.group(1)) if m.group(1) else None


def parse_self_restriction(text: str) -> SelfRestriction | None:
    if not text:
        return None
    t = text.replace("’", "'")
    kw = {}
    if m := re.search(r"You can only play this at the (left|middle|right) location", t):
        kw["only_idx"] = _POS[m.group(1)]
    if m := re.search(r"You can't play this at the (left|middle|right) location", t):
        kw["not_idx"] = _POS[m.group(1)]
    if re.search(r"You can only play this at (?:locations|a location) where you(?:'re| are) winning", t):
        kw["only_winning"] = True
    if re.search(r"You can only play this at a location you(?:'re| are) losing", t):
        kw["only_losing"] = True
    if m := re.search(r"You can't play this after turn (\d+)", t):
        kw["max_turn"] = int(m.group(1))
    if re.search(r"If you played a card last turn, you can't play this", t):
        kw["not_after_playing"] = True
    return SelfRestriction(**kw) if kw else None
