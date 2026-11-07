"""P0-v2：对局期间的实时文件事件监控。

用 ReadDirectoryChangesW（watchdog）监听文件系统事件，任何写入、新建、改名
（包括临时文件 + 原子替换）都会记录，带毫秒时间戳。比 probe_state_files.py 的 2 秒轮询更全面。

GameState.json 一旦变化就立即解析，打印：回合、ChangeCount、三个地点的战力。
用于确认：对局进行中，到底有没有任何本地文件按回合更新。

用法：
    python tools/watch_live.py --minutes 15
    python tools/watch_live.py --minutes 15 --all   # 不过滤噪声目录（浏览器缓存等）
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_state_files import find_install_dir, unity_data_dir  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

HOME = Path(os.environ["USERPROFILE"])
NOISE = re.compile(
    r"\\(Google|Microsoft|Mozilla|BraveSoftware|Edge|Packages|NVIDIA|AMD|Claude|claude|Code|npm|pip|"
    r"Temp\\claude|ConnectedDevicesPlatform|D3DSCache|CrashDumps|Steam\\htmlcache|Windows|"
    r"Yinxiang Biji|webviewdata|Tencent|Lenovo|JetBrains|\.vscode)\\",
    re.I,
)
REPORT_DIR = Path(__file__).resolve().parent.parent / "logs"


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def summarize_gamestate(path: Path) -> str:
    """解析 GameState.json，返回一行摘要。"""
    for _ in range(3):  # 文件可能正在写，重试
        try:
            d = json.loads(path.read_text("utf-8-sig"))
            break
        except (OSError, json.JSONDecodeError):
            time.sleep(0.2)
    else:
        return "  (解析失败)"
    parts = []
    for side in ("LocalGame", "RemoteGame"):
        g = (d.get(side) or {}).get("GameState") or {}
        if not g:
            continue
        byid: dict = {}

        def idx(o):
            if isinstance(o, dict):
                if "$id" in o:
                    byid[o["$id"]] = o
                for v in o.values():
                    idx(v)
            elif isinstance(o, list):
                for v in o:
                    idx(v)

        idx(g)
        locs = [byid.get(l.get("$ref"), l) for l in g.get("_locations", []) if isinstance(l, dict)]
        loc_s = " ".join(f"{l.get('LocationDefId')}:{l.get('CurPlayer1Power')}/{l.get('CurPlayer2Power')}" for l in locs)
        cpi = (d.get(side) or {}).get("ClientPlayerInfo") or {}
        parts.append(
            f"  {side}: id={str(g.get('Id'))[:8]} turn={g.get('Turn')}/{g.get('TotalTurns')} "
            f"changes={cpi.get('ChangeCountLatest')} winner={'Y' if g.get('Winner') else '-'} | {loc_s}"
        )
    return "\n".join(parts)


class Handler(FileSystemEventHandler):
    def __init__(self, show_all: bool):
        self.show_all = show_all
        self.counts: collections.Counter[str] = collections.Counter()
        self.first_seen: dict[str, str] = {}
        self.lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type == "opened" or event.event_type == "closed_no_write":
            return
        path = getattr(event, "dest_path", "") or event.src_path
        if not self.show_all and NOISE.search(path):
            return
        if path.endswith(("watch_live.txt",)) or "Auto-Snap\\logs" in path:
            return
        with self.lock:
            self.counts[path] += 1
            self.first_seen.setdefault(path, ts())
        extra = f"  (from {event.src_path})" if event.event_type == "moved" else ""
        print(f"[{ts()}] {event.event_type:<8} {path}{extra}")
        if path.endswith("GameState.json") and event.event_type in ("modified", "created", "moved"):
            print(summarize_gamestate(Path(path)))


def poll_data_dir(data_dir: Path, handler: Handler, interval: float = 0.5) -> None:
    """兜底：0.5s 轮询游戏数据目录的 mtime，防止事件丢失。"""
    def snap():
        out = {}
        for p in data_dir.rglob("*"):
            try:
                if p.is_file():
                    out[str(p)] = p.stat().st_mtime
            except OSError:
                pass
        return out

    prev = snap()
    while True:
        time.sleep(interval)
        cur = snap()
        for p, m in cur.items():
            if prev.get(p) != m:
                with handler.lock:
                    handler.counts[p + " [poll]"] += 1
                    handler.first_seen.setdefault(p + " [poll]", ts())
                print(f"[{ts()}] poll-mod {p}")
                if p.endswith("GameState.json"):
                    print(summarize_gamestate(Path(p)))
        prev = cur


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--all", action="store_true", help="不过滤噪声目录")
    args = ap.parse_args()

    install = find_install_dir()
    roots = [HOME / "AppData", HOME / "Documents"]
    if install:
        roots.append(install)
    tmp = Path(os.environ.get("TEMP", ""))
    if tmp.exists() and not str(tmp).startswith(str(HOME / "AppData")):
        roots.append(tmp)

    handler = Handler(args.all)
    obs = Observer()
    data_dir = unity_data_dir(install)
    # 游戏数据目录单独一个监听句柄：AppData 事件量大，共用句柄时缓冲区溢出会静默丢事件
    if data_dir.exists():
        obs.schedule(handler, str(data_dir), recursive=True)
        print(f"监听(专用): {data_dir}")
    for r in roots:
        if r.exists():
            obs.schedule(handler, str(r), recursive=True)
            print(f"监听: {r}")
    threading.Thread(target=poll_data_dir, args=(data_dir, handler), daemon=True).start()
    gs = data_dir / "Standalone" / "States" / "prod" / "GameState.json"
    if gs.exists():
        print(f"当前 GameState.json ({datetime.fromtimestamp(gs.stat().st_mtime):%H:%M:%S}):")
        print(summarize_gamestate(gs))
    obs.start()
    print(f"[{ts()}] 开始监控 {args.minutes} 分钟。请进入对局，每回合结束时留意本窗口输出。Ctrl+C 结束。")
    try:
        time.sleep(args.minutes * 60)
    except KeyboardInterrupt:
        pass
    obs.stop()
    obs.join()

    print("=" * 70)
    print("被写入的文件（按事件次数排序，前 40）：")
    for p, n in handler.counts.most_common(40):
        print(f"  {n:>5}  first={handler.first_seen[p]}  {p}")
    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f"watch_live_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps({"counts": handler.counts, "first_seen": handler.first_seen}, ensure_ascii=False, indent=2), "utf-8")
    print(f"报告: {out}")


if __name__ == "__main__":
    main()
