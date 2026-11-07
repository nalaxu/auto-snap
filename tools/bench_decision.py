"""逐回合决策的模型/参数对比：用历史对局的真实局面重放（不连接游戏），比较速度与决策质量。

    python tools/bench_decision.py                       # 全部预设配置
    python tools/bench_decision.py --only sol-low ds-flash
    python tools/bench_decision.py --games 3ac649a8 69777b34 --reps 2

质量指标：合法率（通过 validator）、能量利用率、关键回合是否打出已知最优（KEY_CHECKS）。
同一中转上的配置串行跑（避免互相排队影响耗时），不同厂商之间并行。结果写 logs/reports/bench_decision_*.json。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from brain.board import BoardTracker  # noqa: E402
from brain.llm import load_dotenv  # noqa: E402
from brain.llm.base import TaskConfig  # noqa: E402
from brain.llm.decider import SCHEMA, SYSTEM, build_prompt  # noqa: E402
from brain.llm.openai_client import OpenAIClient  # noqa: E402
from brain.models import GameState, PlayAction, TurnDecision  # noqa: E402
from brain.validator import validate  # noqa: E402

DEFAULT_GAMES = ["ca64a04a", "69777b34", "3ac649a8"]
# (对局, 回合) -> 判定函数(actions) ；来自实战复盘
KEY_CHECKS = {
    ("3ac649a8", 3): ("IronMan→右路（Hawkeye 跟牌 + 翻倍）", lambda acts: ("IronMan", 2) in acts),
    ("3ac649a8", 4): ("Medusa→中路", lambda acts: ("Medusa", 1) in acts),
}


@dataclass
class Preset:
    name: str
    provider: str          # relay | deepseek（同 provider 串行）
    model: str
    effort: str = "low"
    api: str = "responses"
    verbosity: str | None = None
    json_mode: str = "schema"
    extra_body: dict = field(default_factory=dict)


NO_THINK = {"thinking": {"type": "disabled"}}
THINK = {"thinking": {"type": "enabled"}}
PRESETS = [
    Preset("sol-low", "relay", "gpt-6.1-sol"),
    Preset("sol-low-vlow", "relay", "gpt-6.1-sol", verbosity="low"),
    Preset("terra-low-vlow", "relay", "gpt-6.1-terra", verbosity="low"),
    Preset("luna6-low-vlow", "relay", "gpt-6-luna", verbosity="low"),
    Preset("ds-flash", "deepseek", "deepseek-flash", effort="off", api="chat", json_mode="object", extra_body=NO_THINK),
    Preset("ds-pro", "deepseek", "deepseek-v4-pro", effort="off", api="chat", json_mode="object", extra_body=NO_THINK),
    Preset("ds-flash-think", "deepseek", "deepseek-flash", effort="low", api="chat", json_mode="object", extra_body=THINK),
    Preset("ds-pro-think", "deepseek", "deepseek-v4-pro", effort="low", api="chat", json_mode="object", extra_body=THINK),
]


def client_for(p: Preset) -> OpenAIClient:
    if p.provider == "deepseek":
        key, url = os.environ["DEEPSEEK_API_KEY"], os.environ.get("DEEPSEEK_API_URL") or "https://api.deepseek.com"
    else:
        key, url = os.environ["OPENAI_API_KEY"], os.environ.get("OPENAI_BASE_URL") or None
    return OpenAIClient(api_key=key, base_url=url, api=p.api, timeout_s=90, json_mode=p.json_mode,
                        verbosity=p.verbosity, extra_body=p.extra_body or None)


def load_turns(games: list[str]) -> list[tuple[str, GameState]]:
    out = []
    for g in games:
        tr, seen = BoardTracker(), set()
        for line in open(glob.glob(str(ROOT / "logs" / "games" / f"*{g}*.jsonl"))[0], encoding="utf-8"):
            r = json.loads(line)
            if r["type"] != "turn" or r["turn"] in seen:
                continue
            seen.add(r["turn"])
            st = GameState(**r["state"])
            tr.update(g, st)
            out.append((g, st))
    return out


def run_preset(p: Preset, turns, reps: int) -> dict:
    client = client_for(p)
    task = TaskConfig(model=p.model, effort=p.effort, max_output_tokens=8000, timeout_s=60, max_retries=0)
    rows = []
    for g, st in turns:
        prompt = build_prompt(st)
        for _ in range(reps):
            t0 = time.time()
            row = {"game": g, "turn": st.turn}
            try:
                d = client.complete_json(task, SYSTEM, prompt, SCHEMA, "turn_decision")
                acts = [(a["card"], int(a["location"])) for a in d["actions"]]
                dec = TurnDecision(actions=[PlayAction(card=c, location=l) for c, l in acts])
                errs = validate(st, dec)
                cost = {}
                for c in st.hand:
                    cost.setdefault(c.id, []).append(c.cost)
                spent = sum(cost.get(c, [0]).pop(0) if cost.get(c) else 0 for c, _ in acts) if not errs else 0
                row.update(ok=True, s=round(time.time() - t0, 2), acts=acts, valid=not errs, errors=errs,
                           energy=st.energy, spent=spent, reason=d.get("reason", ""))
                if (g, st.turn) in KEY_CHECKS:
                    row["key"] = bool(KEY_CHECKS[(g, st.turn)][1](acts))
            except Exception as e:  # noqa: BLE001
                row.update(ok=False, s=round(time.time() - t0, 2), error=repr(e)[:200])
            rows.append(row)
            flag = "" if row.get("ok") else " ERR " + row["error"][:90]
            print(f"[{p.name}] {g} T{st.turn} {row['s']:5.1f}s {row.get('acts', '')}"
                  f"{'' if row.get('valid', True) else ' 不合法'}{flag}")
    return {"preset": p.__dict__, "rows": rows}


def summarize(res: dict) -> str:
    rows = res["rows"]
    ok = [r for r in rows if r["ok"]]
    ts = sorted(r["s"] for r in ok) or [float("nan")]
    q = lambda f: ts[min(len(ts) - 1, int(len(ts) * f))]
    valid = sum(r["valid"] for r in ok)
    eff = sum(r["spent"] for r in ok if r["valid"]) / max(1, sum(r["energy"] for r in ok if r["valid"]))
    keys = [r["key"] for r in ok if "key" in r]
    within = lambda lim: sum(1 for r in ok if r["s"] <= lim) / max(1, len(rows))
    return (f"{res['preset']['name']:<18} n={len(rows):<3} 失败={len(rows) - len(ok):<2} 中位={statistics.median(ts):5.1f}s "
            f"p90={q(0.9):5.1f}s 最大={ts[-1]:5.1f}s ≤12s={within(12):4.0%} ≤16s={within(16):4.0%} "
            f"合法={valid}/{len(ok)} 能量利用={eff:4.0%} 关键回合={sum(keys)}/{len(keys)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--games", nargs="*", default=DEFAULT_GAMES)
    ap.add_argument("--reps", type=int, default=1)
    a = ap.parse_args()
    load_dotenv()
    SETTINGS = tomllib.loads((ROOT / "config" / "settings.toml").read_text("utf-8"))  # noqa: F841
    presets = [p for p in PRESETS if not a.only or p.name in a.only]
    turns = load_turns(a.games)
    by_provider: dict[str, list[Preset]] = {}
    for p in presets:
        by_provider.setdefault(p.provider, []).append(p)
    results: list[dict] = []

    def run_group(ps):
        return [run_preset(p, turns, a.reps) for p in ps]

    with ThreadPoolExecutor(len(by_provider)) as ex:
        for group in ex.map(run_group, by_provider.values()):
            results.extend(group)
    print("\n==== 汇总 ====")
    lines = [summarize(r) for r in results]
    print("\n".join(lines))
    out = ROOT / "logs" / "reports" / f"bench_decision_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": lines, "results": results}, ensure_ascii=False, indent=1), "utf-8")
    print(f"结果：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
