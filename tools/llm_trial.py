"""大模型试验：用历史对局数据离线评估（不连接游戏、默认不写知识库）。

    python tools/llm_trial.py locations [--save]                 # 分析历史中出现过的场地
    python tools/llm_trial.py turns logs/mem_explore_xxx.json     # 逐回合：玩家实际 vs 规则AI vs 大模型
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from brain import llm, rule_based  # noqa: E402
from brain.llm.decider import LLMDecider  # noqa: E402
from brain.locations import LocationKB  # noqa: E402
from perception.deck import load_current_deck  # noqa: E402
from perception.memory.snap import to_game_state  # noqa: E402

SETTINGS = tomllib.loads((ROOT / "config" / "settings.toml").read_text("utf-8"))
PLACEHOLDER = ("RevealOn", "Unrevealed", "Hidden")


def history_snapshots() -> list[dict]:
    out = []
    for f in sorted(glob.glob(str(ROOT / "logs" / "mem_explore_*.json"))):
        out += json.loads(Path(f).read_text("utf-8")).get("snapshots", [])
    return out


def seen_locations() -> list[str]:
    names = set()
    for s in history_snapshots():
        for l in s["locations"]:
            n = l.get("id") or l.get("raw_id")
            if n and not n.startswith(PLACEHOLDER):
                names.add(n)
    gs = Path.home() / "AppData/LocalLow/NetEase/SnapCN/Standalone/States/prod/GameState.json"
    if gs.exists():  # 赛后文件里的终局场地
        d = json.loads(gs.read_text("utf-8-sig"))
        for side in ("LocalGame", "RemoteGame"):
            res = ((d.get(side) or {}).get("GameState") or {}).get("ClientResultMessage") or {}
            names.update(n for n in res.get("LocationDefIdsAtEndOfGame", []) or [] if isinstance(n, str))
    return sorted(names)


def cmd_locations(save: bool) -> None:
    client = llm.make_client(SETTINGS)
    task = llm.task_config(SETTINGS, "review")
    analyst = llm.LocationAnalyst(client, task)
    kb = LocationKB()
    names = seen_locations()
    print(f"历史中出现过 {len(names)} 个场地；模型 {task.model} effort={task.effort}\n")
    results = {}
    for n in names:
        known = kb.get(n)
        t = time.time()
        info = analyst.analyze(n)
        dt = time.time() - t
        results[n] = info
        tag = f"[知识库已有:{known.source}]" if known else "[未知]"
        if info:
            print(f"- {n} {tag} ({dt:.1f}s)\n    效果：{info.effect}\n    bias 前/中/末 = {info.bias['early']}/{info.bias['mid']}/{info.bias['final']}"
                  f"  tags={info.tags}\n    {info.notes}")
            if known and known.source == "screenshot":
                print(f"    ↔ 截图确认：{known.effect}  bias={known.bias}")
        else:
            print(f"- {n} {tag} ({dt:.1f}s) 模型把握不足/失败")
        if save and info and not known:
            kb.put(info)
    if save:
        print("\n已把知识库中原本没有的场地写入 data/locations.json（截图确认的条目不覆盖）")


def decision_points(snaps: list[dict], game_id: str) -> list[tuple[dict, dict]]:
    """每回合：决策时刻（能量补满、本回合最后一个手牌最多的快照）+ 回合结束前最后一个快照（看玩家实际出了什么）。"""
    by_turn: dict[int, list[dict]] = {}
    for s in snaps:
        if s["game_id"].startswith(game_id) and not s.get("finished"):
            by_turn.setdefault(s["turn"], []).append(s)
    out = []
    for turn, ss in sorted(by_turn.items()):
        full = [s for s in ss if s["me"].get("energy") == s["me"].get("max_energy") == turn]
        if not full:
            continue
        point = max(full, key=lambda s: len(s["me"]["hand"]))
        out.append((point, ss[-1]))
    return out


def human_plays(before: dict, after: dict) -> list[str]:
    seen = {c["entity"] for l in before["locations"] for c in l["my_cards"]}
    return [f"{c.get('id')}→地点{l['idx']}" for l in after["locations"] for c in l["my_cards"] if c["entity"] not in seen]


def cmd_turns(path: str, game_id: str | None) -> None:
    snaps = json.loads(Path(path).read_text("utf-8"))["snapshots"]
    if not game_id:  # 默认取快照最多的一局
        from collections import Counter
        game_id = Counter(s["game_id"] for s in snaps if not s.get("finished")).most_common(1)[0][0]
    client = llm.make_client(SETTINGS)
    task = llm.task_config(SETTINGS, "decision")
    decider = LLMDecider(client, task)
    _, deck = load_current_deck()
    print(f"对局 {game_id[:8]}；大模型 {task.model} effort={task.effort}\n")
    rows = []
    for point, end in decision_points(snaps, game_id):
        st = to_game_state(point, deck)
        fmt = lambda d: ", ".join(f"{a.card}→地点{a.location}" for a in d.actions) or "不出牌"
        rb = rule_based.decide(st)
        t = time.time()
        ld = decider.decide(st)
        dt = time.time() - t
        locs = " | ".join(f"{l.idx}:{l.name or '?'} {l.my_power}:{l.opp_power}" for l in st.locations)
        human = human_plays(point, end)
        print(f"=== T{st.turn} 能量{st.energy} 手牌 {[f'{c.id}({c.cost}/{c.power})' for c in st.hand]}\n    场地 {locs}")
        print(f"    玩家实际：{', '.join(human) or '不出牌'}")
        print(f"    规则AI  ：{fmt(rb)}\n              {rb.reason}")
        print(f"    大模型  ：{fmt(ld)}  ({dt:.1f}s, {ld.source})\n              {ld.reason}")
        if decider.last_raw:
            for a in decider.last_raw.get("location_assessment", []):
                print(f"              场地{a['location']} [{a.get('effect', '')}] 本回合利弊 {a['favorable_now']:+d}：{a['why']}")
            print(f"              争夺目标 {decider.last_raw.get('target_locations')}；规划：{decider.last_raw.get('plan')}")
        print()
        rows.append({"turn": st.turn, "human": human, "rule": fmt(rb), "llm": fmt(ld), "llm_s": round(dt, 1),
                     "llm_reason": ld.reason, "llm_raw": decider.last_raw})
    out = ROOT / "logs" / "reports" / f"llm_trial_{game_id[:8]}_{datetime.now():%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    print(f"结果已保存：{out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("locations")
    p1.add_argument("--save", action="store_true")
    p2 = sub.add_parser("turns")
    p2.add_argument("file")
    p2.add_argument("--game")
    args = ap.parse_args()
    if llm.make_client(SETTINGS) is None:
        sys.exit("未配置大模型密钥（.env）")
    if args.cmd == "locations":
        cmd_locations(args.save)
    else:
        cmd_turns(args.file, args.game)


if __name__ == "__main__":
    main()
