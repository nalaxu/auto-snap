"""只读内存探测：验证运行时解析能否拿到实时对局数据。

    python tools/mem_explore.py              # 定位类 + 字段表 + Game 实例 + 一次快照
    python tools/mem_explore.py --watch 600  # 每 0.5s 读快照，状态变化时打印（对局中运行）

仅 OpenProcess(VM_READ|QUERY_INFORMATION) + ReadProcessMemory，不写内存、不注入。
输出已做可见性过滤（对手未揭示的牌只有数量）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from perception.memory.snap import NotInGame, SnapMemoryReader, to_game_state  # noqa: E402

FIELD_TABLES = [("Game", "CubeGame"), ("Player", "CubeGame"), ("Card", "CubeGame"), ("Location", "CubeGame"),
                ("ClientGameInfo", "CubeUnity.App.State")]


def _cards(cards: list[dict]) -> str:
    return ", ".join(f"{c['id']}({c.get('cost')}/{c.get('power')})" if c.get("id") else "[未揭示]" for c in cards) or "-"


def brief(s: dict) -> str:
    me = s["me"]
    lines = [
        f"T{s['turn']}/{s['total_turns']} 能量 {me['energy']}/{me['max_energy']} 赌注 {s['cube_value']}"
        + (" [已结束]" if s["finished"] else ""),
        f"    手牌({len(me['hand'])}): {_cards(me['hand'])}",
    ]
    for l in s["locations"]:
        name = l["id"] if l["revealed"] else "<未揭示>"
        lines.append(f"    地点{l['idx']} {name}  我 {l['my_power']} : {l['opp_power']} 敌")
        lines.append(f"        我方: {_cards(l['my_cards'])}")
        lines.append(f"        敌方: {_cards(l['opp_cards'])}")
    lines.append(f"    对手: 手牌 {s['opp']['hand_count']} 张, 牌库 {s['opp']['deck_count']} 张 | "
                 f"我方牌库 {me['deck_count']} 张")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", type=int, default=0, metavar="SECONDS")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--client", help="cn_steam / cn_official / global / auto（默认 config [game].client）")
    args = ap.parse_args()

    import tomllib

    import clients
    settings = tomllib.loads((ROOT / "config" / "settings.toml").read_text("utf-8"))
    client = clients.resolve(settings, args.client)
    print(f"客户端：{client.describe()}")
    t0 = time.time()
    r = SnapMemoryReader(client)
    print(f"已附加 pid={r.p.pid}（只读）")
    strings = r.il.metadata_strings()
    print(f"metadata 字符串表 @0x{strings[0]:x} size={strings[1]}")
    classes = r.resolve_classes()
    print(f"类定位耗时 {time.time() - t0:.1f}s")
    for key, ks in classes.items():
        print(f"  {key[1] or ''}.{key[0]}: {[hex(k) for k in ks]}")
    print(f"字段表槽位: {hex(r.il._fields_slot) if r.il._fields_slot else None}")

    for key in FIELD_TABLES:
        ks = classes.get(key) or []
        if not ks:
            continue
        fs = sorted((f for f in r.il.fields(ks[0]).values() if not f.is_static), key=lambda f: f.offset)
        print(f"\n[{key[1]}.{key[0]}] {len(fs)} 个实例字段")
        print("   " + "  ".join(f"{f.name}@{f.offset:#x}" for f in fs))

    t1 = time.time()
    games = r.find_games()
    print(f"\nGame 实例 {len(games)} 个（扫描 {time.time() - t1:.1f}s）:")
    for g in games:
        print(f"  @{g['addr']:#x} id={str(g['id'])[:8]} turn={g['turn']}/{g['total_turns']} client={g['client_state']} "
              f"finished={g['finished']} league={g['league']} scenario={g['scenario']}")

    report: dict = {"time": datetime.now().isoformat(timespec="seconds"), "games": games, "snapshots": []}
    try:
        r.select_game(force=True)
        print(f"\n选中对局 {str(r.game_id)[:8]}，我方 EntityId={r.local_id}")
        s = r.snapshot()
        print(brief(s))
        report["snapshots"].append(s)
        gs = to_game_state(s)
        print(f"    -> GameState OK: 手牌 {len(gs.hand)} 张, 能量 {gs.energy}")
    except NotInGame as e:
        print(f"\n{e}")

    if args.watch:
        print(f"\n开始实时读取 {args.watch}s（每 {args.interval}s）...")
        last = None
        end = time.time() + args.watch
        try:
            while time.time() < end:
                t = time.time()
                try:
                    s = r.snapshot()
                    key = json.dumps({k: s[k] for k in ("game_id", "turn", "finished", "me", "locations", "opp")}, sort_keys=True)
                    if key != last:
                        last = key
                        print(f"[{datetime.now():%H:%M:%S.%f}"[:-3] + f"] (读取 {1000 * (time.time() - t):.0f}ms) " + brief(s))
                        report["snapshots"].append(s)
                except NotInGame as e:
                    if last != "NIG":
                        print(f"[{datetime.now():%H:%M:%S}] {e}")
                        last = "NIG"
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass

    r.close()
    out = ROOT / "logs" / f"mem_explore_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), "utf-8")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
