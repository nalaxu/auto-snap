"""赛后复盘：汇总 logs/games/ 下的对局记录，找出可改进点。

    python tools/analyze_games.py            # 打印报告并保存到 logs/reports/
    python tools/analyze_games.py --last 5   # 只看最近 5 局

报告内容：
- 战绩：胜/负/平、胜率、方块净得
- 场地：每个场地出现次数、我方在该场地的胜负、知识库评估（bias/来源）
- 待分析：知识库里没有的场地 -> 交给大模型（Claude API 或 Claude Code）分析后写入 data/locations.json
- 决策问题：未用完能量的回合、被拒出牌、新手指引/提示事件
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from brain.locations import LocationKB  # noqa: E402

GAMES = ROOT / "logs" / "games"


def load_games(last: int = 0) -> list[dict]:
    index = GAMES / "index.jsonl"
    if not index.exists():
        return []
    rows = [json.loads(x) for x in index.read_text("utf-8").splitlines() if x.strip()]
    rows = rows[-last:] if last else rows
    for r in rows:
        f = GAMES / r["file"]
        r["events"] = [json.loads(x) for x in f.read_text("utf-8").splitlines()] if f.exists() else []
    return rows


def report(games: list[dict]) -> str:
    kb = LocationKB()
    out: list[str] = [f"# 对局复盘（{datetime.now():%Y-%m-%d %H:%M}，{len(games)} 局）", ""]
    if not games:
        return "\n".join(out + ["还没有对局记录。"])

    res = Counter(g["result"] for g in games)
    cubes = sum((g.get("cube_value") or 1) * {"win": 1, "loss": -1}.get(g["result"], 0) for g in games)
    out += ["## 战绩", f"- 胜 {res['win']} / 负 {res['loss']} / 平 {res['draw']}，胜率 {res['win'] / len(games):.0%}，方块净得 {cubes:+d}", ""]

    loc_stats: dict[str, Counter] = defaultdict(Counter)
    loc_cards: dict[str, Counter] = defaultdict(Counter)
    for g in games:
        end = next((e for e in g["events"] if e["type"] == "game_end"), None)
        for l in (end or {}).get("final", []):
            name = l["name"] or "?"
            s = loc_stats[name]
            s["games"] += 1
            s["won" if l["my_power"] > l["opp_power"] else "lost" if l["my_power"] < l["opp_power"] else "tied"] += 1
            for c in l["my_cards"]:
                loc_cards[name][c] += 1
    out += ["## 场地", "| 场地 | 出现 | 我方赢/输/平 | 知识库 bias(前/中/末) | 来源 | 我方常放 |", "|---|---|---|---|---|---|"]
    unknown = []
    for name, s in sorted(loc_stats.items(), key=lambda x: -x[1]["games"]):
        info = kb.get(name)
        if not info:
            unknown.append(name)
        b = info.bias if info else {}
        out.append(f"| {name} | {s['games']} | {s['won']}/{s['lost']}/{s['tied']} | "
                   f"{b.get('early', '-')}/{b.get('mid', '-')}/{b.get('final', '-')} | {info.source if info else '**未知**'} | "
                   f"{', '.join(f'{c}×{n}' for c, n in loc_cards[name].most_common(3))} |")
    out.append("")

    waste, rejected, events = [], [], Counter()
    for g in games:
        for e in g["events"]:
            if e["type"] == "turn":
                spent = sum(next((c["cost"] for c in e["state"]["hand"] if c["id"] == x["card"]), 0)
                            for x in e["executed"] if x.get("ok") and "card" in x)
                left = e["state"]["energy"] - spent
                if e["outcome"] == "done" and left > 0 and e["state"]["hand"]:
                    waste.append(f"{g['game_id'][:8]} T{e['turn']}：剩 {left} 能量，手牌 "
                                 f"{[c['id'] + '(' + str(c['cost']) + ')' for c in e['state']['hand']]}")
                rejected += [f"{g['game_id'][:8]} T{e['turn']}：{x['card']}→地点{x['location']}"
                             for x in e["executed"] if not x.get("ok") and "card" in x]   # 只统计出牌（不含 Snap/激活/移动/撤退）
            elif e["type"] == "event":
                events[e["kind"] + (f":{e['name']}" if e.get("name") else "")] += 1
    out += ["## 决策问题",
            f"- 未用完能量的回合（{len(waste)}）：", *[f"  - {w}" for w in waste[:15]],
            f"- 被拒出牌（{len(rejected)}）：", *[f"  - {r}" for r in rejected[:15]],
            f"- 提示/指引事件：{dict(events) or '无'}", ""]

    out += ["## 待大模型分析的场地",
            *(f"- {n}（出现 {loc_stats[n]['games']} 次）" for n in unknown),
            "" if unknown else "- 无",
            "分析方式：对局中大模型已逐回合给出场地判断（见对局日志 turn.llm.location_assessment）；赛后由复盘模型或 Claude Code 汇总，"
            "由其判断各场地在前期/中期/最后回合是否利好放牌，写入 data/locations.json。"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--last", type=int, default=0)
    args = ap.parse_args()
    text = report(load_games(args.last))
    print(text)
    out = ROOT / "logs" / "reports" / f"report_{datetime.now():%Y%m%d_%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", "utf-8")
    print(f"\n报告已保存：{out}")


if __name__ == "__main__":
    main()
