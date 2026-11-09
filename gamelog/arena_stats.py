"""竞技场战绩：从 logs/arena/runs.jsonl（整场事件）与竞技场对局记录汇总，供面板「战绩 → 竞技场」使用。

- runs：每场一行（开始时间、积分、血量、局数、档位、是否结束、组卡 / 换牌次数、卡组）
- 总览：场数、平均积分、档位分布、对局胜负与魔方、按开局血量分组的 Snap / 撤退 / 胜率
- forfeits：血量不足抵扣时的没收观测（超出量 → 实际没收），用来确认折半的取整规则
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .arena_log import read_events

TIERS = (-8, 0, 5, 10, 15, 20, 25, 30, 35)


def tier_of(score: int | None) -> int | None:
    if score is None:
        return None
    t = None
    for th in TIERS:
        if score >= th:
            t = th
    return t


def runs_from_events(events: list[dict]) -> list[dict]:
    runs: dict[str, dict] = {}
    order: list[str] = []
    for e in events:
        rid = e.get("run_id") or "?"
        if rid not in runs:
            runs[rid] = {"run_id": rid, "started": e.get("t"), "ended": None, "finished": False, "score": None, "hp": None,
                         "games": 0, "wins": 0, "losses": 0, "picks": 0, "swaps": 0, "declined_swaps": 0, "deck": []}
            order.append(rid)
        r = runs[rid]
        k = e.get("kind")
        if k == "pick":
            r["picks"] += 1
            r["deck"].append(e.get("pick"))
        elif k == "swap":
            if e.get("out"):
                r["swaps"] += 1
                if e["out"] in r["deck"]:
                    r["deck"][r["deck"].index(e["out"])] = e.get("inc")
            else:
                r["declined_swaps"] += 1
        elif k == "game":
            r["games"] += 1
            r["wins"] += e.get("result") == "win"
            r["losses"] += e.get("result") == "loss"
            r["score"], r["hp"] = e.get("score_after"), e.get("hp_after")
        elif k == "run_end":
            r.update(finished=True, ended=e.get("t"), score=e.get("score"), hp=e.get("hp"))
            if e.get("deck"):
                r["deck"] = list(e["deck"])
    out = [runs[r] for r in order]
    for r in out:
        r["tier"] = tier_of(r["score"])
    return out


def hp_bucket(hp: int | None) -> str:
    if hp is None:
        return "?"
    return "7–10" if hp >= 7 else "4–6" if hp >= 4 else "1–3"


def aggregate(events: list[dict] | None = None, arena_games: list[dict] | None = None) -> dict:
    """arena_games：gamelog.stats.list_games() 里 mode == arena 的对局（带 arena_hp / snap_turn / retreat）。"""
    events = read_events() if events is None else events
    games = arena_games or []
    runs = runs_from_events(events)
    done = [r for r in runs if r["finished"] and r["score"] is not None]
    res = Counter(g["result"] for g in games)
    by_hp: dict[str, Counter] = defaultdict(Counter)
    for g in games:
        c = by_hp[hp_bucket(g.get("arena_hp"))]
        c["games"] += 1
        c["wins"] += g["result"] == "win"
        c["snaps"] += g.get("snap_turn") is not None
        c["retreats"] += g.get("retreat") == "me"
        c["cubes"] += g.get("cubes") or 0
    forfeits = [{"run_id": e.get("run_id"), "hp_before": e.get("hp_before"), "cube_value": e.get("cube_value"),
                 "overflow": e.get("overflow"), "forfeit": e.get("forfeit")}
                for e in events if e.get("kind") == "game" and e.get("forfeit") is not None]
    return {
        "runs": list(reversed(runs)),
        "runs_total": len(runs),
        "runs_finished": len(done),
        "avg_score": round(sum(r["score"] for r in done) / len(done), 1) if done else None,
        "tier_dist": sorted(Counter(r["tier"] for r in done).items(), key=lambda kv: (kv[0] is None, kv[0])),
        "games": len(games),
        "results": dict(res),
        "cubes": sum(g.get("cubes") or 0 for g in games),
        "by_hp": [{"hp": k, **v} for k, v in sorted(by_hp.items())],
        "forfeits": forfeits,
        "picks": sum(r["picks"] for r in runs),
        "swaps": sum(r["swaps"] for r in runs),
        "declined_swaps": sum(r["declined_swaps"] for r in runs),
    }
