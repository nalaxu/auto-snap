"""P0 调研：检查本机国服《漫威终极逆转》是否会写出可读取的对局状态文件。

国际服 Marvel Snap 会在
    %USERPROFILE%/AppData/LocalLow/Second Dinner/SNAP/Standalone/States/nvprod/*.json
写 GameState.json 等文件（记牌器就靠它）。本脚本检查国服是否有等价物。

用法：
    python tools/probe_state_files.py                 # 静态扫描一次
    python tools/probe_state_files.py --watch 900     # 监控 15 分钟：期间登录并打一局，记录哪些文件被写入

只读文件，不触碰游戏进程。仅依赖标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import winreg
from datetime import datetime
from pathlib import Path

APP_ID = "2955370"
PROCESS = "SnapCN.exe"
HOME = Path(os.environ["USERPROFILE"])
LOCALLOW = HOME / "AppData" / "LocalLow"
INTL_STATES = LOCALLOW / "Second Dinner" / "SNAP"
REPORT_DIR = Path(__file__).resolve().parent.parent / "logs"

INTERESTING_NAME = re.compile(r"state|game|play|match|battle|deck|collection|card|turn|snap", re.I)
LOG_KEYWORDS = re.compile(r"\b(Card|Location|Turn|Energy|Hand|Deck|Cube|Snap|Reveal|GameState)\b|回合|能量|手牌|地点")
SKIP_DIRS = {"StreamingAssets", "il2cpp_data", "webviewsupport.cef904430", "Plugins", "Resources"}

sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------- 定位游戏
def steam_root() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        return None


def find_install_dir() -> Path | None:
    root = steam_root()
    if not root:
        return None
    vdf = root / "steamapps" / "libraryfolders.vdf"
    libs = [root]
    if vdf.exists():
        libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text("utf-8", "ignore"))]
    for lib in libs:
        acf = lib / "steamapps" / f"appmanifest_{APP_ID}.acf"
        if acf.exists():
            m = re.search(r'"installdir"\s+"([^"]+)"', acf.read_text("utf-8", "ignore"))
            if m:
                return lib / "steamapps" / "common" / m.group(1)
    return None


def unity_data_dir(install: Path | None) -> Path | None:
    """Unity persistentDataPath = LocalLow/<CompanyName>/<ProductName>，取自 *_Data/app.info。"""
    if install:
        for info in install.glob("*_Data/app.info"):
            lines = info.read_text("utf-8", "ignore").splitlines()
            if len(lines) >= 2:
                return LOCALLOW / lines[0].strip() / lines[1].strip()
    return LOCALLOW / "NetEase" / "SnapCN"


def process_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {PROCESS}"], capture_output=True, text=True, errors="ignore").stdout
    return PROCESS.lower() in out.lower()


# ---------------------------------------------------------------- 扫描
def walk(root: Path, max_depth: int = 8):
    if not root.exists():
        return
    base = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if n not in SKIP_DIRS and len(d.parts) - base < max_depth]
        for f in filenames:
            yield d / f


def snapshot(roots: list[Path]) -> dict[str, tuple[int, float]]:
    snap = {}
    for r in roots:
        for p in walk(r):
            try:
                st = p.stat()
                snap[str(p)] = (st.st_size, st.st_mtime)
            except OSError:
                pass
    return snap


def describe(path: Path) -> dict:
    info: dict = {"path": str(path)}
    try:
        st = path.stat()
        info["size"] = st.st_size
        info["mtime"] = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    except OSError:
        return info
    if path.suffix.lower() == ".json" and info["size"] < 50_000_000:
        try:
            data = json.loads(path.read_text("utf-8-sig", "ignore"))
            info["json_top_keys"] = list(data.keys())[:30] if isinstance(data, dict) else f"<{type(data).__name__} len={len(data)}>"
        except Exception as e:  # noqa: BLE001
            info["json_error"] = str(e)[:120]
    elif path.suffix.lower() in {".log", ".txt"}:
        try:
            text = path.read_text("utf-8", "ignore")
            hits = [ln.strip()[:160] for ln in text.splitlines() if LOG_KEYWORDS.search(ln)]
            info["keyword_lines"] = len(hits)
            info["keyword_samples"] = hits[:8]
        except OSError:
            pass
    return info


def static_scan(install: Path | None, data_dir: Path, recent_hours: float) -> dict:
    report: dict = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "process_running": process_running(),
        "install_dir": str(install) if install else None,
        "data_dir": str(data_dir),
        "data_dir_exists": data_dir.exists(),
        "intl_states_exists": INTL_STATES.exists(),
    }

    data_files = [describe(p) for p in walk(data_dir)]
    report["data_dir_files"] = data_files
    report["states_dirs"] = [str(p) for p in data_dir.rglob("*") if p.is_dir() and re.search(r"state", p.name, re.I)] if data_dir.exists() else []

    recent = []
    if install and install.exists():
        cutoff = time.time() - recent_hours * 3600
        for p in walk(install, max_depth=3):
            try:
                if p.stat().st_mtime >= cutoff and (INTERESTING_NAME.search(p.name) or p.suffix.lower() in {".json", ".log", ".txt"}):
                    recent.append(describe(p))
            except OSError:
                pass
    report["install_dir_recent_files"] = recent

    candidates = [f for f in data_files if f.get("json_top_keys") or INTERESTING_NAME.search(Path(f["path"]).name)]
    candidates = [f for f in candidates if not re.search(r"Analytics|ShaderVariant", f["path"])]
    report["state_file_candidates"] = candidates
    return report


def print_report(r: dict) -> None:
    print("=" * 70)
    print(f"游戏进程 {PROCESS} 运行中: {r['process_running']}")
    print(f"安装目录: {r['install_dir']}")
    print(f"数据目录: {r['data_dir']}  存在: {r['data_dir_exists']}")
    print(f"国际服 States 目录存在: {r['intl_states_exists']}")
    print("-" * 70)
    print(f"数据目录文件 ({len(r['data_dir_files'])}):")
    for f in r["data_dir_files"]:
        extra = ""
        if "json_top_keys" in f:
            extra = f"  keys={f['json_top_keys']}"
        elif f.get("keyword_lines"):
            extra = f"  对局关键词行数={f['keyword_lines']}"
        print(f"  {f.get('size', '?'):>10}  {f.get('mtime', '')}  {f['path']}{extra}")
    if r["install_dir_recent_files"]:
        print("安装目录中近期被写入的文件:")
        for f in r["install_dir_recent_files"]:
            print(f"  {f.get('size', '?'):>10}  {f.get('mtime', '')}  {f['path']}")
    print("-" * 70)
    if r["states_dirs"] or r["state_file_candidates"]:
        print("✅ 发现疑似状态文件/目录：")
        for d in r["states_dirs"]:
            print(f"  [dir] {d}")
        for f in r["state_file_candidates"]:
            print(f"  [file] {f['path']}")
    else:
        print("❌ 未发现状态文件。若尚未登录/未进入对局，请用 --watch 模式在打一局的同时监控。")


# ---------------------------------------------------------------- 监控
def watch(roots: list[Path], seconds: int, interval: float) -> dict:
    print(f"开始监控 {seconds}s（每 {interval}s 采样）。现在请登录并打一局，Ctrl+C 可提前结束。")
    for r in roots:
        print(f"  监控: {r}")
    prev = snapshot(roots)
    changes: dict[str, dict] = {}
    end = time.time() + seconds
    try:
        while time.time() < end:
            time.sleep(interval)
            cur = snapshot(roots)
            ts = datetime.now().strftime("%H:%M:%S")
            for p, (size, mtime) in cur.items():
                old = prev.get(p)
                if old is None or old[1] != mtime:
                    kind = "NEW" if old is None else "MOD"
                    c = changes.setdefault(p, {"path": p, "writes": 0, "first": ts, "max_size": 0})
                    c["writes"] += 1
                    c["last"] = ts
                    c["max_size"] = max(c["max_size"], size)
                    print(f"  [{ts}] {kind} {size:>10}  {p}")
            for p in prev.keys() - cur.keys():
                print(f"  [{ts}] DEL             {p}")
            prev = cur
    except KeyboardInterrupt:
        print("提前结束监控。")

    result = sorted(changes.values(), key=lambda c: -c["writes"])
    print("-" * 70)
    print("监控期间被写入的文件（按写入次数排序）：")
    for c in result:
        print(f"  writes={c['writes']:>4}  max_size={c['max_size']:>10}  {c['path']}")
    for c in result:
        c.update({k: v for k, v in describe(Path(c["path"])).items() if k not in c})
    return {"watched_roots": [str(r) for r in roots], "changed_files": result}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", type=int, default=0, metavar="SECONDS", help="监控文件写入的时长（秒）")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--recent-hours", type=float, default=24.0, help="安装目录中视为“近期写入”的时间窗")
    args = ap.parse_args()

    install = find_install_dir()
    data_dir = unity_data_dir(install)
    report = static_scan(install, data_dir, args.recent_hours)
    print_report(report)

    if args.watch:
        roots = [LOCALLOW / data_dir.parent.name]  # 整个 LocalLow/NetEase
        if install:
            roots.append(install)
        report["watch"] = watch(roots, args.watch, args.interval)

    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f"probe_report_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n报告已保存: {out}")


if __name__ == "__main__":
    main()
