"""标定检查（不动鼠标）：截取游戏窗口客户区，把执行层会用到的坐标画上去。

    python tools/calibrate.py               # 手牌张数取自内存（在对局中），否则按 --hand 画
    python tools/calibrate.py --hand 7

输出 logs/calibration.png：绿圈 = 手牌抓取点，蓝圈 = 地点落点，黄 = 备用落点，红 = 结束回合，紫 = SNAP。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from PIL import ImageDraw, ImageFont, ImageGrab

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from executor import mouse  # noqa: E402
from executor.actions import DROP_Y_FALLBACK  # noqa: E402
from executor.capture import capture_window  # noqa: E402
from executor.layout import END_TURN_BUTTON, LOCATION_DROP, SNAP_BUTTON, hand_card_pos  # noqa: E402
from executor.window import client_rect, find_window  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hand", type=int, default=0, help="手牌张数（默认从内存读取）")
    ap.add_argument("--no-memory", action="store_true")
    ap.add_argument("--client", help="cn_steam / cn_official / global / auto（默认 config [game].client）")
    args = ap.parse_args()

    import tomllib

    import clients
    settings = tomllib.loads((Path(__file__).resolve().parent.parent / "config" / "settings.toml").read_text("utf-8"))
    client = clients.resolve(settings, args.client)
    print(f"客户端：{client.describe()}")
    mouse.enable_dpi_awareness()
    hwnd = find_window(client)
    r = client_rect(hwnd)
    print(f"客户区: {r}")
    hand_ids: list[str] = []
    if not args.hand and not args.no_memory:
        try:
            from perception.memory_source import MemoryStateSource

            src = MemoryStateSource(client)
            s = src.snapshot()
            src.close()
            if s:
                hand_ids = [c.get("id") or "?" for c in s["me"]["hand"]]
                print(f"内存手牌（T{s['turn']}）: {hand_ids}")
        except Exception as e:  # noqa: BLE001
            print(f"读取内存失败：{e}")
    n = args.hand or len(hand_ids) or 5

    img = capture_window(hwnd, r.width, r.height) or ImageGrab.grab(
        bbox=(r.left, r.top, r.left + r.width, r.top + r.height), all_screens=True)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("msyh.ttc", max(16, r.height // 50))
    except OSError:
        font = ImageFont.load_default()
    rad = max(8, r.height // 90)

    def mark(rel, color, label):
        x, y = round(rel[0] * r.width), round(rel[1] * r.height)
        d.ellipse((x - rad, y - rad, x + rad, y + rad), outline=color, width=4)
        d.line((x - rad * 2, y, x + rad * 2, y), fill=color, width=2)
        d.line((x, y - rad * 2, x, y + rad * 2), fill=color, width=2)
        d.text((x + rad + 4, y - rad * 3), label, fill=color, font=font)

    for i in range(n):
        mark(hand_card_pos(i, n), "lime", f"[{i}]{hand_ids[i] if i < len(hand_ids) else ''}")
    for i, p in enumerate(LOCATION_DROP):
        mark(p, "deepskyblue", f"地点{i}")
        mark((p[0], DROP_Y_FALLBACK), "yellow", "备用")
    mark(END_TURN_BUTTON, "red", "结束回合")
    mark(SNAP_BUTTON, "violet", "SNAP")

    out = ROOT / "logs" / "calibration.png"
    img.save(out)
    img.save(ROOT / "logs" / f"calibration_{datetime.now():%H%M%S}.png")
    print(f"已保存 {out}（{img.size[0]}x{img.size[1]}，手牌 {n} 张）")


if __name__ == "__main__":
    main()
