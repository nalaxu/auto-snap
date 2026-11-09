"""竞技场组卡 / 换牌离线试跑：随机三选一模拟 12 轮组卡，对比大模型与规则的选择；可选读当前游戏里的换牌候选。

    python tools/arena_draft_trial.py --seed 1            # 模拟一次组卡（大模型 + 规则对照）
    python tools/arena_draft_trial.py --rule-only         # 只用规则
    python tools/arena_draft_trial.py --live-swap         # 读游戏内存里的当前卡组与换牌候选，给出换牌建议（不操作）
    python tools/arena_draft_trial.py --replay-swaps 6a5974d8   # 按 logs/arena/runs.jsonl 重建卡组，重新决策当时的每次换牌
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from brain import arena_draft as rules  # noqa: E402
from brain import llm  # noqa: E402
from brain.arena_ratings import card_pool, default_ratings  # noqa: E402
from brain.knowledge import default_db  # noqa: E402


def label(cid: str) -> str:
    c = default_db().card(cid)
    r = default_ratings().rating(cid)
    return f"{cid}({c.get('cost')}/{c.get('power')}{'' if r is None else f' ★{r:g}'})"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--rule-only", action="store_true")
    ap.add_argument("--live-swap", action="store_true")
    ap.add_argument("--replay-swaps", metavar="RUN_ID", help="整场 id（前缀即可）")
    args = ap.parse_args()
    settings = tomllib.loads((ROOT / "config" / "settings.toml").read_text("utf-8"))
    decider = None if args.rule_only else llm.make_arena_decider(settings)

    if args.replay_swaps:
        replay_swaps(args.replay_swaps, decider)
        return

    if args.live_swap:
        from perception.arena import ArenaReader
        from perception.memory.snap import SnapMemoryReader

        st = ArenaReader(SnapMemoryReader("SnapCN.exe")).read()
        if not st or st.phase != "run":
            sys.exit(f"当前没有进行中的整场（phase={st.phase if st else None}）")
        print("卡组：", ", ".join(label(c) for c in st.deck))
        print("候选：", ", ".join(label(c) for c in st.candidates))
        ctx = {"score": st.score, "hp": st.hp, "games": st.games, "swaps_left": st.swaps_left}
        r = rules.choose_swap(st.deck, st.candidates[:3])
        print(f"规则：{r.out_card} → {r.in_card}（{r.reason}）")
        if decider:
            d = decider.swap(st.deck, st.candidates[:3], ctx)
            print(f"大模型：{d.out_card} → {d.in_card}（{d.reason}）")
        return

    rnd = random.Random(args.seed)
    pool = card_pool()
    deck_llm: list[str] = []
    deck_rule: list[str] = []
    for rd in range(1, 13):
        cands = rnd.sample(pool, 3)
        r = rules.choose_pick(deck_rule, cands)
        deck_rule.append(r.card)
        line = f"{rd:2d}. {' | '.join(label(c) for c in cands)}\n    规则 → {r.card}"
        if decider:
            p = decider.pick(deck_llm, cands)
            deck_llm.append(p.card)
            line += f"   大模型 → {p.card}（{p.reason}）"
        print(line)
    for name, deck in (("规则", deck_rule), ("大模型", deck_llm)):
        if deck:
            print(f"{name}卡组 曲线 {rules.curve_counts(deck)} 得分 {rules.deck_score(deck):.1f}：{', '.join(deck)}")


def replay_swaps(run_prefix: str, decider) -> None:
    """按整场记录重建卡组（组卡 + 实际换牌），对每次换牌重新决策并与当时的选择对照；之后仍按当时的选择更新卡组。"""
    deck: list[str] = []
    ctx = {"score": 0, "hp": 10, "games": 0}
    for line in (ROOT / "logs" / "arena" / "runs.jsonl").read_text("utf-8").splitlines():
        e = json.loads(line)
        if not str(e.get("run_id", "")).startswith(run_prefix):
            continue
        if e["kind"] == "pick":
            deck.append(e["pick"])
        elif e["kind"] == "game":
            ctx = {"score": e["score_after"], "hp": e["hp_after"], "games": ctx["games"] + 1}
        elif e["kind"] == "swap":
            cands = e["candidates"][:3]
            print(f"— 卡组：{', '.join(deck)}")
            print(f"  候选：{' | '.join(label(c) for c in cands)}")
            print(f"  当时：{e.get('out')} → {e.get('inc')}（{e.get('reason')}）")
            r = rules.choose_swap(deck, cands)
            print(f"  规则：{r.out_card} → {r.in_card}（{r.reason}）")
            if decider:
                d = decider.swap(deck, cands, ctx)
                print(f"  现在：{d.out_card} → {d.in_card}（{d.reason}）")
            if e.get("out") in deck:
                deck[deck.index(e["out"])] = e["inc"]
    if not deck:
        print(f"没有找到整场 {run_prefix}")


if __name__ == "__main__":
    main()
