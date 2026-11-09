"""生成竞技场评分表初值（data/arena/ratings.json）：大模型按批给国服卡池打分，写入 source=auto。

    python tools/build_arena_ratings.py                 # 只补还没有评分的卡
    python tools/build_arena_ratings.py --force         # 重打所有 auto 条目（人工复核过的 manual 不覆盖）
    python tools/build_arena_ratings.py --only Leader Magneto
    python tools/build_arena_ratings.py --batch 25 --workers 4 --effort high

用 [llm.tasks.review]（离线任务，高推理强度）。结果需要在面板里人工复核。
"""
from __future__ import annotations

import argparse
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from brain import llm  # noqa: E402
from brain.arena_ratings import TAGS, THEMES, Ratings, card_pool  # noqa: E402
from brain.knowledge import default_db  # noqa: E402

SYSTEM = f"""你是《漫威终极逆转》（Marvel Snap 国服）竞技场模式的组卡专家，给卡牌打「竞技场组卡评分」。

竞技场规则：每场竞技用 12 轮三选一组成 12 张卡组，卡池约等于全卡池、与收藏无关；对手同样是随机组卡。
所以：卡组很难凑齐特定体系，对手也没有固定套路；能量利用率很重要（每回合把能量转化为战力）。

评分 rating（1–10，可用 0.5）衡量「在随机组卡环境里这张牌的期望强度」：
- 不依赖其他牌、单独就能打出高战力或高收益的牌分高（standalone）。
- 需要特定组件才成立的牌（弃牌、摧毁、移动、Ongoing 等体系核心）单独价值低，分数按「没有组件时」的表现给，
  标 needs:<体系>；能给某体系提供组件的牌标 enabler:<体系>。
- 针对特定打法的针对卡（tech）在随机环境里价值打折。
- 费用效率：低费高身材、带收益的牌加分；身材很差又没有稳定收益的牌减分。
- 负面效果（给对手加战力、削弱自己）按严重程度减分。

tags 只能从这个词表选（可多选，可为空）：{", ".join(TAGS)}
体系：{", ".join(THEMES)}（summon=召唤衍生物，lowcost=低费铺场，highcost=高费，ramp=加能量，junk=往对手牌库/手牌塞垃圾，
deck=抽牌/牌库操作，graveyard=弃牌堆/复活，activate=激活能力）。
note：一句中文要点（≤20 字），说明打法或评分理由。

每张卡都要输出，id 原样返回。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "rating": {"type": "number"},
                "tags": {"type": "array", "items": {"type": "string", "enum": list(TAGS)}},
                "note": {"type": "string"},
            },
            "required": ["id", "rating", "tags", "note"],
            "additionalProperties": False,
        }},
    },
    "required": ["cards"],
    "additionalProperties": False,
}


def card_line(cid: str) -> str:
    """一张卡一行；生成衍生牌的附上衍生牌的身材与能力（如 Sam Wilson → Cap's Shield），否则大模型只看到名字。"""
    from brain.arena_links import default_links

    c = default_db().card(cid)
    line = f"- {cid}（{c.get('name') or cid}）{c.get('cost')}费{c.get('power')}力：{c.get('ability') or '无能力'}"
    for t in default_links().token_info(cid):
        line += f"\n  · 衍生 {t['name']} {t['cost']}费{t['power']}力：{t['ability']}"
    return line


def rate_batch(client, task, ids: list[str]) -> dict[str, dict]:
    user = "给下面每张卡打分：\n" + "\n".join(card_line(c) for c in ids)
    out = client.complete_json(task, SYSTEM, user, SCHEMA, "arena_ratings")
    res = {}
    for e in out.get("cards", []):
        cid = e.get("id")
        if cid in ids:
            r = max(1.0, min(10.0, float(e.get("rating", 5))))
            res[cid] = {"rating": round(r * 2) / 2, "tags": sorted(set(e.get("tags") or [])),
                        "note": (e.get("note") or "")[:40], "source": "auto", "model": task.model}
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--effort", help="覆盖推理强度（默认 [llm.tasks.review]）")
    args = ap.parse_args()

    settings = tomllib.loads((ROOT / "config" / "settings.toml").read_text("utf-8"))
    client = llm.make_client(settings)
    if not client:
        sys.exit("未配置大模型密钥（.env）")
    task = llm.task_config(settings, "review")
    if args.effort:
        task = replace(task, effort=args.effort)
    ratings = Ratings()
    pool = args.only or card_pool()
    todo = [c for c in pool if args.force or c not in ratings.items]
    todo = [c for c in todo if (ratings.get(c) or {}).get("source") != "manual"]
    print(f"卡池 {len(pool)} 张，待评分 {len(todo)} 张；模型 {task.model}/{task.effort}，每批 {args.batch}，并发 {args.workers}")
    batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]
    done = failed = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool_ex:
        futs = {pool_ex.submit(rate_batch, client, task, b): b for b in batches}
        for f in as_completed(futs):
            b = futs[f]
            try:
                res = f.result()
            except Exception as e:  # noqa: BLE001
                failed += len(b)
                print(f"  ✗ 批次失败（{b[0]}…）：{e!r}")
                continue
            ratings.put_many(res)
            done += len(res)
            missing = [c for c in b if c not in res]
            print(f"  ✓ {done}/{len(todo)}（{time.time() - t0:.0f}s）" + (f"  缺 {missing}" if missing else ""))
    print(f"完成：写入 {done} 张，失败 {failed} 张 -> {ratings.path}")


if __name__ == "__main__":
    main()
