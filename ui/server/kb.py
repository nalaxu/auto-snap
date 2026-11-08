"""知识库管理（外部面板 M3）：待复核队列、卡组（我方打法 / 对手原型）、放牌顺序信号、卡牌与场地的人工覆盖。

所有写入都走 brain.kbfile.update_json（读最新 -> 改 -> 原子替换），写之前把原文件备份到 logs/kb_backup/（每个文件保留最近 30 份）。
机器人运行中会把知识库读进内存：这里的修改对正在运行的机器人下次启动才完全生效（赛后积累会在最新文件上追加，不会覆盖修改）。
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from brain.kbfile import read_json, update_json

ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
SCOPES = ("here", "global_here", "global")
POLARITIES = ("good", "bad")
KEEP_BACKUPS = 30


def _check_id(x: str, what: str = "id") -> str:
    if not ID_RE.match(x or ""):
        raise HTTPException(400, f"{what} 只能包含字母、数字、下划线和连字符")
    return x


def pending_signal(sig: dict) -> bool:
    """自动识别、还没人复核过的信号。"""
    src = str(sig.get("source", ""))
    return src.startswith("auto") and "复核" not in src


class KnowledgeBase:
    def __init__(self, data_dir: Path, backup_dir: Path, games_dir: Path | None = None):
        self.data = data_dir
        self.backup_dir = backup_dir
        self.games_dir = games_dir
        self.p_signals = data_dir / "signals.json"
        self.p_mine = data_dir / "decks" / "mine.json"
        self.p_opp = data_dir / "decks" / "opponents.json"
        self.p_unmatched = data_dir / "decks" / "unmatched.jsonl"
        self.p_cards = data_dir / "cards.json"
        self.p_locs = data_dir / "locations.json"
        self.p_unknown = data_dir / "unknown_locations.json"
        self.p_card_db = data_dir / "card_db.json"
        self.p_loc_db = data_dir / "location_db.json"

    # ------------------------------------------------------------ 基础
    def _backup(self, path: Path) -> None:
        if not path.exists():
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        shutil.copy2(path, self.backup_dir / f"{path.name}.{stamp}")
        olds = sorted(self.backup_dir.glob(f"{path.name}.*"))
        for old in olds[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)

    def _update(self, path: Path, fn) -> dict:
        self._backup(path)
        return update_json(path, fn)

    def _items(self, path: Path) -> dict:
        d = read_json(path)
        items = d.get("items", d) if isinstance(d, dict) else {}
        return {k: v for k, v in items.items() if not k.startswith("_") and isinstance(v, dict)}

    def card_db(self) -> dict:
        return self._items(self.p_card_db)

    def loc_db(self) -> dict:
        return self._items(self.p_loc_db)

    def card_name(self, cid: str, db: dict | None = None) -> str:
        db = db if db is not None else self.card_db()
        return (db.get(cid) or {}).get("name") or cid

    # ------------------------------------------------------------ 待复核
    def overview(self) -> dict:
        sigs = self.signals()
        unmatched = self.unmatched()
        unknown = self.unknown_locations()
        return {
            "pending_signals": [s for s in sigs if s["pending"]],
            "unmatched": unmatched,
            "unknown_locations": unknown,
            "counts": {"signals": sum(s["pending"] for s in sigs), "unmatched": len(unmatched), "locations": len(unknown)},
        }

    # ------------------------------------------------------------ 信号
    def signals(self) -> list[dict]:
        data = read_json(self.p_signals)
        cdb, ldb = self.card_db(), self.loc_db()
        out = []
        for kind, db, field in (("locations", ldb, "effect"), ("cards", cdb, "ability")):
            for sid, sig in (data.get(kind) or {}).items():
                info = db.get(sid) or {}
                out.append({"kind": kind, "id": sid, "name": info.get("name") or sid, "text": info.get(field, ""),
                            **{k: sig.get(k) for k in ("scope", "polarity", "note", "source")}, "pending": pending_signal(sig)})
        return sorted(out, key=lambda s: (not s["pending"], s["kind"], s["name"]))

    def put_signal(self, kind: str, sid: str, body: dict) -> None:
        if kind not in ("locations", "cards"):
            raise HTTPException(400, "kind 只能是 locations 或 cards")
        _check_id(sid)
        if body.get("scope") not in SCOPES or body.get("polarity") not in POLARITIES:
            raise HTTPException(400, "scope / polarity 取值不对")
        entry = {"scope": body["scope"], "polarity": body["polarity"], "note": str(body.get("note") or "").strip(),
                 "source": str(body.get("source") or f"面板编辑 {date.today().isoformat()}")}

        def fn(d):
            d.setdefault("locations", {})
            d.setdefault("cards", {})
            d[kind][sid] = entry
        self._update(self.p_signals, fn)

    def approve_signal(self, kind: str, sid: str) -> None:
        def fn(d):
            sig = (d.get(kind) or {}).get(sid)
            if sig is None:
                raise HTTPException(404, "没有这条信号")
            sig["source"] = f"auto（已复核 {date.today().isoformat()}）"
        self._update(self.p_signals, fn)

    def delete_signal(self, kind: str, sid: str) -> None:
        def fn(d):
            if sid not in (d.get(kind) or {}):
                raise HTTPException(404, "没有这条信号")
            del d[kind][sid]
        self._update(self.p_signals, fn)

    # ------------------------------------------------------------ 对手卡组候选
    def _unmatched_rows(self) -> list[dict]:
        if not self.p_unmatched.exists():
            return []
        rows = []
        for line in self.p_unmatched.read_text("utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def unmatched(self) -> list[dict]:
        opp = read_json(self.p_opp)
        cdb = self.card_db()
        out = []
        for r in self._unmatched_rows():
            cards = r.get("cards") or []
            best = []
            for aid, a in opp.items():   # 和已有原型重合的牌数：给「归入已有原型」做建议
                sig = {a.get("core"), *a.get("signature", []), *a.get("seen_cards", {})} - {None}
                n = len(sig & set(cards))
                if n:
                    best.append({"id": aid, "name": a.get("name", aid), "overlap": n})
            best.sort(key=lambda x: -x["overlap"])
            out.append({**r, "card_names": {c: self.card_name(c, cdb) for c in cards}, "suggest": best[:3]})
        return list(reversed(out))   # 新的在前

    def _remove_unmatched(self, game_id: str) -> dict:
        rows = self._unmatched_rows()
        hit = next((r for r in rows if r.get("game_id") == game_id), None)
        if hit is None:
            raise HTTPException(404, "没有这条候选")
        self._backup(self.p_unmatched)
        keep = [r for r in rows if r is not hit]
        self.p_unmatched.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), "utf-8")
        return hit

    @staticmethod
    def _add_sample(a: dict, row: dict) -> None:
        sample = {k: row.get(k) for k in ("game_id", "date", "result", "cards")}
        a["samples"] = [s for s in a.get("samples", []) if s.get("game_id") != sample["game_id"]] + [sample]
        seen = a.setdefault("seen_cards", {})
        for c in row.get("cards") or []:
            seen[c] = seen.get(c, 0) + 1

    def assign_unmatched(self, game_id: str, aid: str) -> None:
        opp = read_json(self.p_opp)
        if aid not in opp:
            raise HTTPException(404, "没有这个原型")
        row = self._remove_unmatched(game_id)
        self._update(self.p_opp, lambda d: self._add_sample(d[aid], row) if aid in d else None)

    def dismiss_unmatched(self, game_id: str) -> None:
        self._remove_unmatched(game_id)

    # ------------------------------------------------------------ 卡组
    def decks(self) -> dict:
        cdb = self.card_db()
        opp = read_json(self.p_opp)
        opps = []
        for aid, a in opp.items():
            samples = a.get("samples", [])
            opps.append({"id": aid, **{k: a.get(k) for k in ("name", "core", "signature", "plan", "counter")},
                         "samples": len(samples), "wins": sum(s.get("result") == "win" for s in samples),
                         "seen_cards": sorted((a.get("seen_cards") or {}).items(), key=lambda x: -x[1])[:16],
                         "card_names": {c: self.card_name(c, cdb) for c in {a.get("core"), *a.get("signature", []), *(a.get("seen_cards") or {})} - {None}}})
        mine = read_json(self.p_mine)
        return {"mine": [{"id": k, **v, "card_names": {c: self.card_name(c, cdb) for c in v.get("cards", {})}} for k, v in mine.items()],
                "opponents": opps}

    def put_mine(self, name: str, body: dict) -> None:
        _check_id(name, "卡组 id")
        cards = {(_check_id(k, "卡牌 id")): str(v) for k, v in (body.get("cards") or {}).items() if str(v).strip()}
        entry = {"name": str(body.get("name") or name), "plan": str(body.get("plan") or ""), "cards": cards}
        if body.get("lessons"):
            entry["lessons"] = [str(x) for x in body["lessons"] if str(x).strip()]

        def fn(d):
            old = d.get(name, {})
            d[name] = {**{k: v for k, v in old.items() if k not in entry and k != "lessons"}, **entry}
        self._update(self.p_mine, fn)

    def put_opponent(self, aid: str, body: dict, create: bool = False, from_game: str | None = None) -> None:
        _check_id(aid, "原型 id")
        signature = [_check_id(c, "卡牌 id") for c in body.get("signature") or []]
        core = body.get("core") or None
        if core:
            _check_id(core, "核心牌 id")
        if not str(body.get("name") or "").strip():
            raise HTTPException(400, "原型要有名字")
        fields = {"name": str(body["name"]).strip(), "core": core, "signature": signature,
                  "plan": str(body.get("plan") or ""), "counter": str(body.get("counter") or "")}
        if create and aid in read_json(self.p_opp):
            raise HTTPException(409, "这个原型 id 已存在")
        if not create and aid not in read_json(self.p_opp):
            raise HTTPException(404, "没有这个原型")
        row = self._remove_unmatched(from_game) if from_game else None

        def fn(d):
            a = d.setdefault(aid, {"samples": [], "seen_cards": {}})
            a.update(fields)
            if row:
                self._add_sample(a, row)
        self._update(self.p_opp, fn)

    def delete_opponent(self, aid: str) -> None:
        def fn(d):
            if aid not in d:
                raise HTTPException(404, "没有这个原型")
            del d[aid]
        self._update(self.p_opp, fn)

    # ------------------------------------------------------------ 卡牌
    def search_cards(self, q: str = "", only_curated: bool = False, limit: int = 60) -> list[dict]:
        db, cur = self.card_db(), self._items(self.p_cards)
        q = q.strip().lower()
        out = []
        for cid in sorted(set(db) | set(cur)):
            d, c = db.get(cid, {}), cur.get(cid)
            if only_curated and not c:
                continue
            name = d.get("name") or cid
            if q and q not in cid.lower() and q not in name.lower() and q not in (d.get("ability") or "").lower():
                continue
            m = {**d, **(c or {})}
            out.append({"id": cid, "name": name, "cost": m.get("cost"), "power": m.get("power"), "ability": m.get("ability", ""),
                        "curated": bool(c), "status": d.get("status")})
            if len(out) >= limit:
                break
        return out

    def card(self, cid: str) -> dict:
        db, cur = self.card_db(), self._items(self.p_cards)
        if cid not in db and cid not in cur:
            raise HTTPException(404, "没有这张牌")
        return {"id": cid, "name": (db.get(cid) or {}).get("name") or cid, "db": db.get(cid), "curated": cur.get(cid)}

    def put_card(self, cid: str, body: dict) -> None:
        _check_id(cid, "卡牌 id")
        if cid not in self.card_db() and cid not in self._items(self.p_cards):
            raise HTTPException(404, "没有这张牌")
        entry: dict = {}
        for k in ("cost", "power"):
            if body.get(k) not in (None, ""):
                try:
                    entry[k] = int(body[k])
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{k} 要是整数") from None
        for k in ("ability", "notes"):
            if str(body.get(k) or "").strip():
                entry[k] = str(body[k]).strip()
        if body.get("tags"):
            entry["tags"] = [str(t).strip() for t in body["tags"] if str(t).strip()]
        if not entry:
            raise HTTPException(400, "至少填一项覆盖（费用 / 战力 / 能力 / 备注 / 标签）")

        def fn(d):
            old = d.get(cid, {}) if isinstance(d.get(cid), dict) else {}
            d[cid] = {**({"type": old["type"]} if "type" in old else {}), **entry}
        self._update(self.p_cards, fn)

    def delete_card(self, cid: str) -> None:
        def fn(d):
            if cid not in d:
                raise HTTPException(404, "这张牌没有人工覆盖")
            del d[cid]
        self._update(self.p_cards, fn)

    # ------------------------------------------------------------ 场地
    def unknown_locations(self) -> list[dict]:
        seen = read_json(self.p_unknown)
        cur, ldb = self._items(self.p_locs), self.loc_db()
        out = []
        for lid, s in seen.items():
            if lid in cur or not isinstance(s, dict):
                continue
            out.append({"id": lid, "name": (ldb.get(lid) or {}).get("name") or lid, "effect": (ldb.get(lid) or {}).get("effect", ""),
                        "seen": s.get("seen", 0), "last_seen": s.get("last_seen")})
        return sorted(out, key=lambda x: -x["seen"])

    def llm_notes(self, lid: str, limit: int = 6) -> list[dict]:
        """对局记录里大模型对这个场地的判断（最近的在前），供人工写 bias 参考。"""
        if self.games_dir is None or not self.games_dir.exists():
            return []
        from gamelog.stats import read_records

        out = []
        for f in sorted(self.games_dir.glob("2*.jsonl"), reverse=True):
            for r in read_records(f):
                if r.get("type") != "turn":
                    continue
                locs = [l.get("name") for l in (r.get("state") or {}).get("locations", [])]
                for a in (r.get("llm") or {}).get("location_assessment") or []:
                    i = a.get("location") if isinstance(a, dict) else None
                    if isinstance(i, int) and 0 <= i < len(locs) and locs[i] == lid:
                        out.append({"game": f.stem[-8:], "turn": r.get("turn"), "favorable_now": a.get("favorable_now"),
                                    "why": a.get("why", "")})
            if len(out) >= limit:
                break
        return out[:limit]

    def search_locations(self, q: str = "", only_curated: bool = False, limit: int = 60) -> list[dict]:
        db, cur = self.loc_db(), self._items(self.p_locs)
        q = q.strip().lower()
        out = []
        for lid in sorted(set(db) | set(cur)):
            d, c = db.get(lid, {}), cur.get(lid)
            if only_curated and not c:
                continue
            name = d.get("name") or lid
            effect = (c or {}).get("effect") or d.get("effect", "")
            if q and q not in lid.lower() and q not in name.lower() and q not in effect.lower():
                continue
            out.append({"id": lid, "name": name, "effect": effect, "curated": bool(c), "bias": (c or {}).get("bias"),
                        "source": (c or {}).get("source")})
            if len(out) >= limit:
                break
        return out

    def location(self, lid: str) -> dict:
        db, cur = self.loc_db(), self._items(self.p_locs)
        if lid not in db and lid not in cur:
            raise HTTPException(404, "没有这个场地")
        return {"id": lid, "name": (db.get(lid) or {}).get("name") or lid, "db": db.get(lid), "curated": cur.get(lid),
                "llm_notes": self.llm_notes(lid)}

    def put_location(self, lid: str, body: dict) -> None:
        _check_id(lid, "场地 id")
        if lid not in self.loc_db() and lid not in self._items(self.p_locs):
            raise HTTPException(404, "没有这个场地")
        bias = {}
        for k in ("early", "mid", "final"):
            try:
                v = int((body.get("bias") or {}).get(k, 0))
            except (TypeError, ValueError):
                raise HTTPException(400, "bias 要是 -3..3 的整数") from None
            if not -3 <= v <= 3:
                raise HTTPException(400, "bias 要在 -3..3 之间")
            bias[k] = v
        entry = {"effect": str(body.get("effect") or "").strip(), "source": f"面板编辑 {date.today().isoformat()}",
                 "bias": bias, "tags": [str(t).strip() for t in body.get("tags") or [] if str(t).strip()],
                 "notes": str(body.get("notes") or "").strip()}
        self._update(self.p_locs, lambda d: d.__setitem__(lid, entry))

    def delete_location(self, lid: str) -> None:
        def fn(d):
            if lid not in d:
                raise HTTPException(404, "这个场地没有人工条目")
            del d[lid]
        self._update(self.p_locs, fn)


# ---------------------------------------------------------------- 路由

class SignalBody(BaseModel):
    scope: str
    polarity: str
    note: str = ""


class AssignBody(BaseModel):
    archetype: str


class OpponentBody(BaseModel):
    name: str
    core: str | None = None
    signature: list[str] = Field(default_factory=list)
    plan: str = ""
    counter: str = ""
    from_game: str | None = None


class MineBody(BaseModel):
    name: str = ""
    plan: str = ""
    cards: dict[str, str] = Field(default_factory=dict)
    lessons: list[str] = Field(default_factory=list)


class CardBody(BaseModel):
    cost: int | None = None
    power: int | None = None
    ability: str = ""
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class LocationBody(BaseModel):
    effect: str = ""
    bias: dict[str, int] = Field(default_factory=dict)
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


def create_router(kb: KnowledgeBase) -> APIRouter:
    r = APIRouter(prefix="/api/kb")
    ok = {"ok": True}

    r.get("/overview")(kb.overview)
    r.get("/signals")(kb.signals)

    @r.put("/signals/{kind}/{sid}")
    def put_signal(kind: str, sid: str, body: SignalBody):
        kb.put_signal(kind, sid, body.model_dump())
        return ok

    @r.post("/signals/{kind}/{sid}/approve")
    def approve_signal(kind: str, sid: str):
        kb.approve_signal(kind, sid)
        return ok

    @r.delete("/signals/{kind}/{sid}")
    def delete_signal(kind: str, sid: str):
        kb.delete_signal(kind, sid)
        return ok

    r.get("/unmatched")(kb.unmatched)

    @r.post("/unmatched/{game_id}/assign")
    def assign(game_id: str, body: AssignBody):
        kb.assign_unmatched(game_id, body.archetype)
        return ok

    @r.delete("/unmatched/{game_id}")
    def dismiss(game_id: str):
        kb.dismiss_unmatched(game_id)
        return ok

    r.get("/decks")(kb.decks)

    @r.put("/mine/{name}")
    def put_mine(name: str, body: MineBody):
        kb.put_mine(name, body.model_dump())
        return ok

    @r.post("/opponents/{aid}")
    def create_opponent(aid: str, body: OpponentBody):
        kb.put_opponent(aid, body.model_dump(), create=True, from_game=body.from_game)
        return ok

    @r.put("/opponents/{aid}")
    def put_opponent(aid: str, body: OpponentBody):
        kb.put_opponent(aid, body.model_dump())
        return ok

    @r.delete("/opponents/{aid}")
    def delete_opponent(aid: str):
        kb.delete_opponent(aid)
        return ok

    @r.get("/cards")
    def cards(q: str = "", curated: bool = False):
        return kb.search_cards(q, curated)

    r.get("/cards/{cid}")(kb.card)

    @r.put("/cards/{cid}")
    def put_card(cid: str, body: CardBody):
        kb.put_card(cid, body.model_dump())
        return ok

    @r.delete("/cards/{cid}")
    def delete_card(cid: str):
        kb.delete_card(cid)
        return ok

    @r.get("/locations")
    def locations(q: str = "", curated: bool = False):
        return kb.search_locations(q, curated)

    r.get("/locations/{lid}")(kb.location)

    @r.put("/locations/{lid}")
    def put_location(lid: str, body: LocationBody):
        kb.put_location(lid, body.model_dump())
        return ok

    @r.delete("/locations/{lid}")
    def delete_location(lid: str):
        kb.delete_location(lid)
        return ok

    return r
