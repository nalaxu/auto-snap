"""抓取最新卡牌 / 场地资料，生成本地资料库 data/card_db.json、data/location_db.json。

数据源：Marvel Snap Zone 公开数据接口（官方网站 marvelsnap.com / marvelsnap.163.com 没有可抓取的卡牌图鉴）。
其 `carddefid` 与游戏内存里的 CardDefId / LocationDefId 一致，可直接按 id 关联。

用法：
    python tools/update_card_db.py            # 抓取并写入 data/
    python tools/update_card_db.py --from-dir D:/dump   # 用已下载的 cards.json / locs.json 生成（离线）

注意：这是国际服数据；国服版本可能有改动（实战中手牌的费用/战力以内存读数为准，资料库只提供能力文字与基础值）。
人工维护的 data/cards.json、data/locations.json 优先级更高（标签、bias、notes），本工具不会改动它们。
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BASE = "https://marvelsnapzone.com/getinfo/?searchtype={kind}&searchcardstype=true"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Auto-Snap card-db updater"


def clean(text: str | None) -> str:
    text = re.sub(r"<br\s*/?>", " ", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch(kind: str) -> list[dict]:
    req = urllib.request.Request(BASE.format(kind=kind), headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return extract(json.loads(r.read().decode("utf-8")), kind)


def extract(payload: dict, kind: str) -> list[dict]:
    body = payload.get("success", payload)
    if isinstance(body, dict):
        body = body.get(kind) or body.get("cards") or next(iter(body.values()))
    if not isinstance(body, list) or not body:
        raise ValueError(f"{kind}: 接口返回格式变了，未找到列表")
    return body


def card_kind(ability: str) -> str:
    """vanilla(无能力) / ongoing / on_reveal / special —— 与 data/cards.json 的 type 含义一致。"""
    if not ability:
        return "vanilla"
    if ability.startswith("Ongoing"):
        return "ongoing"
    if ability.startswith("On Reveal"):
        return "on_reveal"
    return "special"


def build_cards(raw: list[dict]) -> dict:
    out = {}
    for c in raw:
        cid = c.get("carddefid")
        if not cid or c.get("type") not in (None, "Character"):
            continue
        ability = clean(c.get("ability"))
        out[cid] = {
            "name": c.get("name", cid), "cost": c.get("cost"), "power": c.get("power"),
            "ability": ability, "type": card_kind(ability),
            "keywords": sorted({t["tag"] for t in c.get("tags") or [] if t.get("tag")}),
            "status": c.get("status", ""), "series": c.get("source", ""),
        }
    return dict(sorted(out.items()))


def build_locations(raw: list[dict]) -> dict:
    out = {}
    for l in raw:
        lid = l.get("carddefid")
        if not lid:
            continue
        out[lid] = {"name": l.get("name", lid), "effect": clean(l.get("ability")), "status": l.get("status", ""),
                    "rarity": l.get("rarity", "")}
    return dict(sorted(out.items()))


def write(path: Path, items: dict, kind: str) -> None:
    meta = {"source": BASE.format(kind=kind), "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(items), "released": sum(1 for v in items.values() if v["status"] == "released")}
    old = json.loads(path.read_text("utf-8")).get("items", {}) if path.exists() else {}
    added = sorted(set(items) - set(old))
    changed = sorted(k for k in set(items) & set(old) if items[k] != old[k])
    path.write_text(json.dumps({"_meta": meta, "items": items}, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"{path.name}: {meta['count']} 条（已上线 {meta['released']}），新增 {len(added)}，变更 {len(changed)}")
    for k in (added + changed)[:20]:
        print(f"   {'+' if k in added else '~'} {k}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-dir", type=Path, help="离线：目录下的 cards.json / locs.json（接口原始返回）")
    ap.add_argument("--out", type=Path, default=DATA)
    a = ap.parse_args()
    if a.from_dir:
        cards = extract(json.loads((a.from_dir / "cards.json").read_text("utf-8")), "cards")
        locs = extract(json.loads((a.from_dir / "locs.json").read_text("utf-8")), "locations")
    else:
        cards, locs = fetch("cards"), fetch("locations")
    a.out.mkdir(parents=True, exist_ok=True)
    write(a.out / "card_db.json", build_cards(cards), "cards")
    write(a.out / "location_db.json", build_locations(locs), "locations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
