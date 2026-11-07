"""检测本机的《漫威终极逆转》/ Marvel Snap 客户端：正在运行的进程、安装目录、渠道标志文件、Unity 数据目录。

用法：
    python tools/detect_client.py                 # 列出运行中的客户端及其匹配的配置档
    python tools/detect_client.py --dir E:/FeverApps/MarvelSnap   # 检查一个安装目录（不需要运行）

只读文件与进程列表，不读取游戏内存。新装一个客户端后用它确认 config/settings.toml 的 [game.clients.*] 是否正确。
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import clients  # noqa: E402

MARKERS = ["NtUniSdkSteam.dll", "steam_api64.dll", "steam_appid.txt", "mpay.dll", "FeverGameLauncher",
           "cube_key.dll", "GameAssembly.dll"]


def inspect_dir(folder: Path, profiles: dict) -> None:
    exes = [p for p in folder.glob("*.exe") if (p.parent / f"{p.stem}_Data").is_dir()]
    if not exes:
        print(f"  {folder}：没有找到 Unity 游戏 exe（<名字>.exe + <名字>_Data）")
        return
    for exe in exes:
        print(f"  exe: {exe}")
        info = exe.parent / f"{exe.stem}_Data" / "app.info"
        if info.exists():
            company, product = (info.read_text("utf-8", "ignore").splitlines() + ["", ""])[:2]
            print(f"  Unity 数据目录: {clients.LOCALLOW / company.strip() / product.strip()}")
        for m in MARKERS:
            f = folder / m
            if f.exists():
                print(f"    ✓ {m}" + (f"  {f.stat().st_size:,} B" if f.is_file() else ""))
        hits = [k for k, p in profiles.items() if p.matches_exe(exe.name, str(exe))]
        print(f"  匹配的配置档: {', '.join(hits) or '无（需要在 config/settings.toml 新增或调整 [game.clients.*]）'}")
        if len(hits) > 1:
            print("  ⚠ 匹配了多个配置档：给它们加 require_files / exclude_files 或 install_dir 区分")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, help="检查指定安装目录")
    args = ap.parse_args()
    settings = tomllib.loads((ROOT / "config" / "settings.toml").read_text("utf-8"))
    profiles = clients.profiles(settings)
    print(f"当前选择: {clients.selected_key(settings)}")
    if args.dir:
        inspect_dir(args.dir, profiles)
        return
    names = {p.process_name.lower() for p in profiles.values()}
    found = False
    for proc in psutil.process_iter(["name", "exe", "pid"]):
        name = (proc.info.get("name") or "")
        if name.lower() not in names and "snap" not in name.lower():
            continue
        found = True
        exe = proc.info.get("exe")
        print(f"\n进程 {name} pid={proc.info['pid']}  {exe or '<路径不可读>'}")
        if exe:
            inspect_dir(Path(exe).parent, profiles)
    if not found:
        print("没有发现正在运行的客户端；可用 --dir 指定安装目录检查")


if __name__ == "__main__":
    main()
