"""从正在运行的国服客户端读取卡牌定义，生成国服卡牌库 data/card_db_cn.json。

    python tools/build_cn_card_db.py                 # 需要游戏在运行（停在主界面或对局中都行），约 1-2 分钟
    python tools/build_cn_card_db.py --process SnapCN.exe

只读内存（OpenProcess VM_READ），不注入、不写内存。数据来源与可信度：
- 名字 / 费用 / 战力 / 能力模板：直接取自客户端 CardDef，就是国服当前版本。
- 能力里的数值（模板占位符，如 +{card.AddedPower}）：游戏把它写在技能代码里，内存没有数据表。
  按优先级取：① 客户端渲染过的文本反推（params_source = "client"）
              ② 模板与国际服文字结构一致时取国际服数值（"global"，数值可能和国服不同）
              ③ 都没有就写成 ?（"unknown"）
- 文字语言跟随客户端语言设置（英文客户端读出英文）。

生成时与国际服 data/card_db.json 对比，把费用、战力、能力不同的卡写进文件的 diff 字段，面板「知识库」页可以查看。
资料优先级：人工维护 data/cards.json > 国服库 > 国际服库。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from perception.memory.carddefs import (PLACEHOLDER, CardDefReader, CardDefRow, clean_text, is_flavor,  # noqa: E402
                                        is_placeholder, keywords, match_params, render)
from tools.update_card_db import card_kind  # noqa: E402

DATA = ROOT / "data"
OUT = DATA / "card_db_cn.json"
GLOBAL = DATA / "card_db.json"
PATCH = Path.home() / "AppData" / "LocalLow" / "NetEase" / "SnapCN" / "prod" / "data-patch.json"


def _cmp(text: str) -> str:
    return re.sub(r"[^a-z0-9+\-?]", "", (text or "").lower().replace("’", "'"))


def build_items(rows: dict[str, CardDefRow], rendered: set[str], global_items: dict) -> tuple[dict, dict]:
    """-> (items, stats)。纯函数：不碰游戏进程，便于测试。"""
    items: dict[str, dict] = {}
    stats = {"client": 0, "global": 0, "unknown": 0, "plain": 0, "skipped": 0}
    for cid, r in sorted(rows.items()):
        tpl = r.template
        if is_placeholder(tpl):   # 未上线的占位定义：不收录，资料仍由国际服库提供
            stats["skipped"] += 1
            continue
        flavor = clean_text(tpl) if is_flavor(tpl) else ""
        if flavor:
            tpl = ""
        params, source = {}, "plain"
        if PLACEHOLDER.search(tpl):
            params = match_params(tpl, rendered)
            source = "client"
            if params is None:
                g = (global_items.get(cid) or {}).get("ability")
                params = match_params(clean_text(tpl), [g]) if g else None
                source = "global" if params is not None else "unknown"
        stats[source] += 1
        ability = clean_text(render(tpl, params))
        entry = {"name": r.name, "cost": r.cost, "power": r.power, "ability": ability, "type": card_kind(ability),
                 "keywords": keywords(tpl), "params_source": source}
        if PLACEHOLDER.search(tpl):
            entry["template"] = clean_text(tpl)
            entry["params"] = params or {}
        if flavor:
            entry["flavor"] = flavor
        if r.abilities:
            entry["abilities"] = r.abilities
        items[cid] = entry
    return items, stats


def diff_global(items: dict, global_items: dict) -> list[dict]:
    """国服与国际服不同的卡：费用 / 战力 / 能力文字。"""
    out = []
    for cid in sorted(set(items) & set(global_items)):
        cn, gl = items[cid], global_items[cid]
        changes = {}
        for k in ("cost", "power"):
            if gl.get(k) is not None and cn.get(k) != gl.get(k):
                changes[k] = [gl.get(k), cn.get(k)]
        if cn["params_source"] != "unknown" and _cmp(cn["ability"]) != _cmp(gl.get("ability", "")):
            changes["ability"] = [gl.get("ability", ""), cn["ability"]]
        if cn.get("keywords") != sorted(set(gl.get("keywords") or [])) and "ability" in changes:
            changes["keywords"] = [gl.get("keywords") or [], cn.get("keywords") or []]
        if changes:
            out.append({"id": cid, "name": cn["name"], "changes": changes})
    return out


def client_version() -> str:
    try:
        v = json.loads(PATCH.read_text("utf-8")).get("ApplicationVersion") or {}
        return f"{v.get('Major')}.{v.get('Minor')}" if v else ""
    except (OSError, ValueError):
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--process", default="SnapCN.exe")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()

    t0 = time.time()
    try:
        reader = CardDefReader(a.process)
    except Exception as e:   # noqa: BLE001
        print(f"连不上游戏进程 {a.process}：{e}。请先启动国服客户端。")
        return 2
    try:
        print("读取卡牌定义…")
        rows = reader.read_defs()
        print(f"  {len(rows)} 张（{time.time() - t0:.0f}s）")
        print("扫描客户端已渲染的卡牌文本（用来反推能力数值）…")
        rendered = reader.rendered_texts()
        print(f"  {len(rendered)} 条（{time.time() - t0:.0f}s）")
    finally:
        reader.close()
    if len(rows) < 100:
        print("读到的卡太少，可能游戏还在加载；进入主界面后再试。未写入文件。")
        return 1

    gl = json.loads(GLOBAL.read_text("utf-8")).get("items", {}) if GLOBAL.exists() else {}
    items, stats = build_items(rows, rendered, gl)
    diff = diff_global(items, gl)
    old = json.loads(a.out.read_text("utf-8")).get("items", {}) if a.out.exists() else {}
    meta = {"source": f"{a.process} 内存（CubeDef.CardDef）", "client_version": client_version(),
            "fetched_at": datetime.now().isoformat(timespec="seconds"), "count": len(items),
            "params": stats, "diff_count": len(diff)}
    a.out.write_text(json.dumps({"_meta": meta, "items": items, "diff": diff}, ensure_ascii=False, indent=1) + "\n", "utf-8")

    added = sorted(set(items) - set(old))
    changed = sorted(k for k in set(items) & set(old) if items[k] != old[k])
    print(f"\n{a.out.name}：{len(items)} 张，客户端版本 {meta['client_version'] or '未知'}；本次新增 {len(added)}，变更 {len(changed)}")
    print(f"能力数值：客户端反推 {stats['client']}，国际服兜底 {stats['global']}，未知 {stats['unknown']}，无数值 {stats['plain']}；跳过占位定义 {stats['skipped']}")
    print(f"与国际服不同：{len(diff)} 张")
    for d in diff[:40]:
        parts = []
        for k, (g, c) in d["changes"].items():
            if k == "ability":
                parts.append(f"能力\n      国际服：{g}\n      国服：  {c}")
            elif k != "keywords":
                parts.append(f"{'费用' if k == 'cost' else '战力'} {g}→{c}")
        print(f"  ~ {d['name']}（{d['id']}）" + "；".join(p for p in parts if not p.startswith("能力"))
              + "".join("\n    " + p for p in parts if p.startswith("能力")))
    if len(diff) > 40:
        print(f"  …其余 {len(diff) - 40} 张见文件 diff 字段 / 面板知识库页")
    print(f"\n用时 {time.time() - t0:.0f}s。运行中的机器人下次启动生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
