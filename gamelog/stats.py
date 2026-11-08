"""战绩与复盘数据：从 logs/games/ 的对局记录汇总（外部面板 M2 的「战绩」「复盘」页使用）。

- list_games()       每局一行摘要（结果、魔方、客户端、卡组、场地、Snap、决策耗时、兜底 / 被拒 / 浪费能量次数）
- aggregate(games)   总览：战绩、魔方走势、魔方分布、按客户端 / 卡组 / 场地、Snap 与撤退、大模型决策质量
- game_detail(file)  单局逐回合：局面、决策、执行结果、大模型场地评估、问题标记、终局

对局文件只追加不修改，按 (路径, 修改时间, 大小) 缓存解析结果。
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from .live import decision_payload, state_payload
from .recorder import GAMES_DIR

SIGN = {"win": 1, "loss": -1}


def _db():
    from brain.knowledge import default_db
    return default_db()


def _loc_name(lid: str | None) -> str:
    if not lid:
        return "?"
    return (_db().location(lid) or {}).get("name") or lid


def read_index(root: Path = GAMES_DIR) -> list[dict]:
    index = root / "index.jsonl"
    if not index.exists():
        return []
    rows = []
    for line in index.read_text("utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


@lru_cache(maxsize=256)
def _read_records(path: str, mtime: float, size: int) -> tuple[dict, ...]:
    out = []
    for line in Path(path).read_text("utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return tuple(out)


def read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    st = path.stat()
    return list(_read_records(str(path), st.st_mtime, st.st_size))


def _spent(turn: dict) -> int:
    hand = {c["id"]: c.get("cost") or 0 for c in turn["state"].get("hand", [])}
    return sum(hand.get(x["card"], 0) for x in turn.get("executed", []) if x.get("ok") and "card" in x)


def turn_flags(turn: dict) -> list[str]:
    """一个回合的问题 / 关键事件标记：fallback（规则 AI 兜底）、rejected（出牌被拒）、waste（能量没用完）、
    snap、retreat、failed（执行失败）、activate、move。"""
    flags = []
    dec = turn.get("decision") or {}
    src = str(dec.get("source", ""))
    if src.startswith("rule"):
        flags.append("fallback")
    ex = turn.get("executed") or []
    if any(not x.get("ok") and "card" in x for x in ex):
        flags.append("rejected")
    if str(turn.get("outcome", "")).startswith("failed"):
        flags.append("failed")
    if turn.get("outcome") == "done":   # 剩下的能量还够打手里某张没打的牌（不考虑地点限制，仅作提示）
        left = (turn["state"].get("energy") or 0) - _spent(turn)
        played = {x["card"] for x in ex if x.get("ok") and "card" in x}
        if left > 0 and any(c["id"] not in played and c.get("cost") is not None and c["cost"] <= left
                            for c in turn["state"].get("hand", [])):
            flags.append("waste")
    if any(x.get("snap") and x.get("ok") for x in ex):
        flags.append("snap")
    if any(x.get("retreat") and x.get("ok") for x in ex):
        flags.append("retreat")
    if any(x.get("activate") and x.get("ok") for x in ex):
        flags.append("activate")
    if any(x.get("move") and x.get("ok") for x in ex):
        flags.append("move")
    return flags


def summarize(row: dict, records: list[dict]) -> dict:
    start = next((r for r in records if r.get("type") == "game_start"), {})
    end = next((r for r in records if r.get("type") == "game_end"), {})
    turns = [r for r in records if r.get("type") == "turn"]
    result = row.get("result") or end.get("result") or "?"
    cube = row.get("cube_value") or end.get("cube_value") or 0
    llm_times = [t["decide_s"] for t in turns if t.get("decide_s") and str(t["decision"].get("source", "")).startswith("llm")]
    flags = Counter(f for t in turns for f in turn_flags(t))
    snap_turn = next((t["turn"] for t in turns if "snap" in turn_flags(t)), None)
    win_probs = [t["decision"].get("win_probability") for t in turns if t["decision"].get("win_probability") is not None]
    return {
        "file": row.get("file"),
        "game_id": row.get("game_id") or (records[0].get("game_id") if records else None),
        "started": row.get("started"),
        "ended": row.get("ended"),
        "result": result,
        "retreat": row.get("retreat", end.get("retreat")),
        "cube_value": cube,
        "cubes": SIGN.get(result, 0) * (cube or 0),
        "client": start.get("client") or "",
        "deck": start.get("deck_name") or "",
        "locations": [{"id": l, "name": _loc_name(l)} for l in (row.get("locations") or [])],
        "my_locations_won": row.get("my_locations_won", end.get("my_locations_won")),
        "opp_locations_won": row.get("opp_locations_won", end.get("opp_locations_won")),
        "turns": len(turns),
        "snap_turn": snap_turn,
        "decide_median": round(statistics.median(llm_times), 1) if llm_times else None,
        "fallbacks": flags["fallback"],
        "rejected": flags["rejected"],
        "waste": flags["waste"],
        "final_win_prob": win_probs[-1] if win_probs else None,
    }


def list_games(root: Path = GAMES_DIR) -> list[dict]:
    """从旧到新。"""
    out = []
    for row in read_index(root):
        path = root / str(row.get("file", ""))
        out.append(summarize(row, read_records(path)))
    return out


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 1)


def aggregate(games: list[dict], root: Path = GAMES_DIR) -> dict:
    n = len(games)
    res = Counter(g["result"] for g in games)
    net = sum(g["cubes"] for g in games)
    wins = [g["cube_value"] for g in games if g["result"] == "win"]
    losses = [g["cube_value"] for g in games if g["result"] == "loss"]

    acc, trend = 0, []
    for g in games:
        acc += g["cubes"]
        trend.append({"file": g["file"], "result": g["result"], "cubes": g["cubes"], "total": acc, "started": g["started"]})

    streak, best = 0, 0
    for g in games:
        streak = streak + 1 if g["result"] == "win" else 0
        best = max(best, streak)
    cur = 0
    for g in reversed(games):
        if g["result"] != games[-1]["result"]:
            break
        cur += 1

    dist = defaultdict(lambda: {"win": 0, "loss": 0})
    for g in games:
        if g["result"] in ("win", "loss"):
            dist[g["cube_value"]][g["result"]] += 1

    def group(key: str) -> list[dict]:
        bucket = defaultdict(list)
        for g in games:
            bucket[g[key] or "?"].append(g)
        return sorted(({"key": k, "games": len(v), "wins": sum(x["result"] == "win" for x in v),
                        "cubes": sum(x["cubes"] for x in v)} for k, v in bucket.items()), key=lambda x: -x["games"])

    # 场地：按终局该场地我方赢 / 输
    locs: dict[str, Counter] = defaultdict(Counter)
    llm_times, by_turn, sources = [], defaultdict(list), Counter()
    flags = Counter()
    for g in games:
        records = read_records(root / str(g["file"]))
        end = next((r for r in records if r.get("type") == "game_end"), {})
        for l in end.get("final", []):
            c = locs[l.get("name") or "?"]
            c["games"] += 1
            c["won" if l["my_power"] > l["opp_power"] else "lost" if l["my_power"] < l["opp_power"] else "tied"] += 1
            c["game_wins"] += g["result"] == "win"
        for t in (r for r in records if r.get("type") == "turn"):
            src = str(t["decision"].get("source", "")).split(":")[0]
            sources[src] += 1
            flags.update(turn_flags(t))
            if src == "llm" and t.get("decide_s"):
                llm_times.append(t["decide_s"])
                by_turn[t["turn"]].append(t["decide_s"])
    loc_rows = sorted(({"id": k, "name": _loc_name(k), **v} for k, v in locs.items()), key=lambda x: (-x["games"], x["name"]))

    snapped = [g for g in games if g["snap_turn"] is not None]
    decisions = sum(sources.values())
    return {
        "games": n,
        "wins": res["win"], "losses": res["loss"], "draws": res["draw"],
        "win_rate": round(res["win"] / n, 3) if n else None,
        "net_cubes": net,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "cubes_per_game": round(net / n, 2) if n else None,
        "streak": {"current": cur, "current_result": games[-1]["result"] if games else None, "best_win": best},
        "trend": trend,
        "cube_dist": [{"cube": k, **v} for k, v in sorted(dist.items(), key=lambda x: -x[0])],
        "by_client": group("client"),
        "by_deck": group("deck"),
        "locations": loc_rows,
        "snap": {"games": len(snapped), "wins": sum(g["result"] == "win" for g in snapped), "cubes": sum(g["cubes"] for g in snapped)},
        "retreat": {"me": sum(g["retreat"] == "me" for g in games), "opp": sum(g["retreat"] == "opp" for g in games)},
        "decisions": {
            "total": decisions,
            "llm": sources["llm"],
            "fallback": sources["rule_based"],
            "skipped": sources["skip"],
            "fallback_rate": round(sources["rule_based"] / decisions, 3) if decisions else None,
            "median_s": _pct(llm_times, 0.5),
            "p90_s": _pct(llm_times, 0.9),
            "by_turn": [{"turn": k, "median_s": _pct(v, 0.5), "p90_s": _pct(v, 0.9), "n": len(v)} for k, v in sorted(by_turn.items())],
            "rejected": flags["rejected"],
            "waste": flags["waste"],
            "failed": flags["failed"],
        },
    }


def game_detail(file: str, root: Path = GAMES_DIR) -> dict | None:
    path = (root / file).resolve()
    if path.parent != root.resolve() or not path.exists():
        return None
    records = read_records(path)
    row = next((r for r in read_index(root) if r.get("file") == file), {"file": file})
    db = _db()
    turns = []
    pending_events: list[dict] = []
    for r in records:
        if r.get("type") == "event":
            pending_events.append({"kind": r.get("kind"), **{k: v for k, v in r.items() if k not in ("type", "kind", "game_id")}})
            continue
        if r.get("type") != "turn":
            continue
        st = r.get("state") or {}
        llm = r.get("llm") or {}
        loc_ids = [l.get("name") for l in st.get("locations") or []]
        assessment = [{**a, "name": _loc_name(loc_ids[a["location"]] if isinstance(a.get("location"), int)
                                              and 0 <= a["location"] < len(loc_ids) else None)}
                      for a in llm.get("location_assessment") or [] if isinstance(a, dict)]
        turns.append({
            "turn": r.get("turn"),
            "t": r.get("t"),
            "state": state_payload(st, r.get("locations"), db),
            "decision": decision_payload(r.get("decision") or {}, st, llm, r.get("decide_s"), db=db),
            "executed": r.get("executed") or [],
            "outcome": r.get("outcome"),
            "flags": turn_flags(r),
            "assessment": assessment,
            "targets": llm.get("target_locations") or [],
            "events": pending_events,
        })
        pending_events = []
    end = next((r for r in records if r.get("type") == "game_end"), None)
    final = None
    if end:
        final = [{**l, "display": _loc_name(l.get("name")),
                  "my_cards": [{"id": c, "name": (db.card(c) or {}).get("name") or c} for c in l.get("my_cards", []) if c],
                  "opp_cards": [{"id": c, "name": (db.card(c) or {}).get("name") or c} for c in l.get("opp_cards", []) if c]}
                 for l in end.get("final", [])]
    return {"summary": summarize(row, records), "turns": turns, "final": final, "trailing_events": pending_events}
