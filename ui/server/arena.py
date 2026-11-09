"""面板接口：竞技场评分表复核（data/arena/ratings.json）。

    GET  /api/arena/ratings?q=&tag=&source=&limit=   列表（按评分降序；q 搜 id / 名字 / 能力 / 要点）
    PUT  /api/arena/ratings/{cid}                    人工修改 → source=manual（之后重新生成不会覆盖）
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from brain.arena_links import default_links
from brain.arena_ratings import TAGS, Ratings, card_pool
from brain.knowledge import default_db


class RatingBody(BaseModel):
    rating: float = Field(ge=1, le=10)
    tags: list[str] = []
    note: str = ""


def create_router(path=None) -> APIRouter:
    r = APIRouter(prefix="/api/arena")

    def ratings() -> Ratings:
        return Ratings(path) if path else Ratings()

    @r.get("/ratings")
    def list_ratings(q: str = "", tag: str = "", source: str = "", limit: int = 120) -> dict:
        rt = ratings()
        db = default_db()
        ql = q.strip().lower()
        pool = set(card_pool()) | set(rt.items)
        rows = []
        for cid in pool:
            c = db.card(cid)
            e = rt.get(cid) or {}
            if tag and tag not in (e.get("tags") or []):
                continue
            if source == "missing" and e:
                continue
            if source in ("auto", "manual") and e.get("source") != source:
                continue
            name = c.get("name") or cid
            if ql and not any(ql in str(x).lower() for x in (cid, name, c.get("ability") or "", e.get("note") or "")):
                continue
            rows.append({"id": cid, "name": name, "cost": c.get("cost"), "power": c.get("power"),
                         "ability": c.get("ability") or "", "rating": e.get("rating"), "tags": e.get("tags") or [],
                         "note": e.get("note") or "", "source": e.get("source"),
                         "creates": default_links().token_info(cid)})
        rows.sort(key=lambda x: (-(x["rating"] if x["rating"] is not None else -1), x["id"]))
        counts = {"total": len(pool), "rated": len(rt.items),
                  "manual": sum(1 for e in rt.items.values() if e.get("source") == "manual")}
        return {"rows": rows[:max(1, min(limit, 600))], "matched": len(rows), "counts": counts, "tags": list(TAGS)}

    @r.put("/ratings/{cid}")
    def put_rating(cid: str, body: RatingBody) -> dict:
        bad = [t for t in body.tags if t not in TAGS]
        if bad:
            raise HTTPException(400, f"未知标签：{bad}")
        rt = ratings()
        rt.put_many({cid: {"rating": round(body.rating * 2) / 2, "tags": sorted(set(body.tags)), "note": body.note[:60],
                           "source": "manual"}}, overwrite_manual=True)
        return {"ok": True, "entry": rt.get(cid)}

    return r
