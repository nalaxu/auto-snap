"""竞技场评分表（知识库）：data/arena/ratings.json。

每张卡：rating（1–10，竞技场单卡强度）、tags、note（一句话要点）、source（auto = 大模型初值 / manual = 人工复核）。
tags 固定词表：
- standalone：不依赖其他牌也能打出战力
- needs:<体系>：需要组件才成立（如 needs:discard）
- enabler:<体系>：给某体系提供组件（如 enabler:discard）
- tech：针对卡（针对特定对手打法）
体系见 THEMES。初值由 tools/build_arena_ratings.py 生成，面板复核后改为 manual。
"""
from __future__ import annotations

from pathlib import Path

from .kbfile import read_json, update_json

PATH = Path(__file__).resolve().parent.parent / "data" / "arena" / "ratings.json"
THEMES = ("move", "discard", "destroy", "ongoing", "onreveal", "summon", "lowcost", "highcost", "ramp", "buff",
          "debuff", "bounce", "deck", "junk", "location", "energy", "activate", "graveyard")
TAGS = ("standalone", "tech", *[f"needs:{t}" for t in THEMES], *[f"enabler:{t}" for t in THEMES])


class Ratings:
    def __init__(self, path: Path = PATH):
        self.path = path
        self.items: dict[str, dict] = (read_json(path, {}) or {}).get("items", {})

    def get(self, cid: str) -> dict | None:
        return self.items.get(cid)

    def rating(self, cid: str) -> float | None:
        e = self.items.get(cid)
        return float(e["rating"]) if e and e.get("rating") is not None else None

    def tags(self, cid: str) -> list[str]:
        return list((self.items.get(cid) or {}).get("tags") or [])

    def put_many(self, entries: dict[str, dict], overwrite_manual: bool = False) -> int:
        """写入（原子替换）。人工复核过的条目默认不覆盖。返回写入条数。"""
        n = 0

        def fn(d: dict) -> None:
            nonlocal n
            d.setdefault("_meta", {"doc": "竞技场评分表，见 brain/arena_ratings.py"})
            items = d.setdefault("items", {})
            for cid, e in entries.items():
                if not overwrite_manual and (items.get(cid) or {}).get("source") == "manual":
                    continue
                items[cid] = e
                n += 1

        self.path.parent.mkdir(parents=True, exist_ok=True)
        update_json(self.path, fn, default={})
        self.items = (read_json(self.path, {}) or {}).get("items", {})
        return n


_default: Ratings | None = None


def default_ratings() -> Ratings:
    global _default
    if _default is None:
        _default = Ratings()
    return _default


def set_default_ratings(r: Ratings | None) -> None:
    global _default
    _default = r


def card_pool(db=None) -> list[str]:
    """竞技场卡池：国服客户端里有、且国际服资料为已发布可收集的卡（排除衍生物 / 增益 / 教程卡）。"""
    from .knowledge import default_db

    db = db or default_db()
    out = []
    for cid, v in db.cards_raw.items():
        if v.get("source") != "cn":
            continue
        if v.get("status") == "released" and v.get("series") and v.get("series") != "None":
            out.append(cid)
    return sorted(out)
