"""卡牌之间的点名联系：衍生牌（生成的 token）与点名引用，供竞技场组卡 / 换牌考虑卡组内配合。

2026-11-09 第二场竞技：大模型换掉了 Sam Wilson，卡组随之失去 Cap's Shield（Game Start 生成、能反复移动叠加成），
因为提示里只有 Sam Wilson 自己的文字和评分，看不到衍生牌做什么。这里从资料库能力文字里通用地解析：
- creates：牌面点名的非卡池牌（衍生物，如 Cap's Shield、Mjolnir、Rock），附它的费用 / 战力 / 能力；
- 点名引用：牌面（或它的衍生牌）点名了卡组里的另一张卡池牌（如 Mjolnir 衍生的 Thor 系、Vampiric Spear → Killmonger）。
只按卡名精确匹配（区分大小写、整词），不猜昵称（「your Cap」这类交给大模型看衍生牌文字自己判断）。
"""
from __future__ import annotations

import re
from functools import lru_cache

from .arena_ratings import card_pool
from .knowledge import GameDB, default_db


class CardLinks:
    def __init__(self, db: GameDB | None = None, pool: set[str] | None = None):
        self.db = db or default_db()
        self.pool = pool if pool is not None else set(card_pool(self.db))
        names: dict[str, str] = {}
        for cid, v in self.db.cards.items():
            nm = v.get("name") or cid
            if len(nm) < 3:
                continue
            old = names.get(nm)
            if old is None or self._prefer(cid, nm) > self._prefer(old, nm):
                names[nm] = cid
        self.names = names
        alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        self._pat = re.compile(rf"(?<![\w'’])({alts})(?![\w])") if alts else None

    def _prefer(self, cid: str, name: str) -> tuple:
        """同名多张时选哪张：卡池牌 > id 与名字一致 > 非教程牌。"""
        return (cid in self.pool, re.sub(r"\W", "", name) == cid, "Tutorial" not in cid)

    @lru_cache(maxsize=4096)
    def mentions(self, cid: str) -> tuple[str, ...]:
        """能力文字里点名的其他卡（去重、保持顺序）。"""
        if self._pat is None:
            return ()
        out: list[str] = []
        for m in self._pat.findall(self.db.ability(cid) or ""):
            k = self.names[m]
            if k != cid and k not in out:
                out.append(k)
        return tuple(out)

    def tokens(self, cid: str) -> list[str]:
        """这张牌生成的衍生牌（点名的非卡池牌）。"""
        return [k for k in self.mentions(cid) if k not in self.pool]

    def reach(self, cid: str) -> set[str]:
        """这张牌（含它的衍生牌）点名的卡池牌。"""
        out = {k for k in self.mentions(cid) if k in self.pool}
        for t in self.tokens(cid):
            out |= {k for k in self.mentions(t) if k in self.pool and k != cid}
        return out

    def token_info(self, cid: str) -> list[dict]:
        out = []
        for t in self.tokens(cid):
            c = self.db.card(t)
            out.append({"name": c.get("name") or t, "cost": c.get("cost"), "power": c.get("power"),
                        "ability": c.get("ability") or "无能力"})
        return out

    def link_pairs(self, deck: list[str], cid: str) -> list[tuple[str, str]]:
        """cid 与卡组其他牌的点名联系：[(对方 id, "out" 点名对方 | "in" 被对方点名)]，同一张牌只算一次。"""
        out: list[tuple[str, str]] = []
        for d in dict.fromkeys(deck):
            if d == cid:
                continue
            if d in self.reach(cid):
                out.append((d, "out"))
            if cid in self.reach(d):
                out.append((d, "in"))
        return out

    def deck_links(self, deck: list[str], cid: str) -> list[str]:
        """cid 与卡组其他牌的点名联系（双向），用于提示。"""
        return [f"点名卡组里的 {d}" if k == "out" else f"被卡组里的 {d} 点名" for d, k in self.link_pairs(deck, cid)]

    def linked(self, a: str, b: str) -> bool:
        return a != b and (b in self.reach(a) or a in self.reach(b))


_default: CardLinks | None = None


def default_links() -> CardLinks:
    global _default
    if _default is None:
        _default = CardLinks()
    return _default


def set_default_links(links: CardLinks | None) -> None:
    global _default
    _default = links
