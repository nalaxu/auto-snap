"""界面识别：按钮模板匹配（模板取自 2000×1125 对局截图，匹配前把截图缩放到同宽）。

模板交叉验证（截图 2–7）：自身得分 1.00，其他界面 ≤ 0.61；阈值 0.75。
- collect_rewards：对局结束（右下 Collect Rewards）
- next：战斗奖励（右下 Next）
- play：主界面（PLAY）
- end_turn：对局中（右下 END TURN）
- never_seen：对手打出未见过的新卡，全屏展示（点任意位置跳过）
- tap_to_snap：新手指引「Tap to SNAP!」（必须点右侧魔方才能继续）
- card_detail：卡牌详情弹窗（左下「Esc Close」）：误点到卡牌时弹出，挡住棋盘导致拖牌全部失败
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "data" / "templates" / "ui"
REF_WIDTH = 2000
THRESHOLD = 0.75  # 截图交叉验证：其他界面 ≤ 0.61；实机主界面 Play 0.91
# 搜索区域（相对坐标 x0, y0, x1, y1），缩小范围提速、减少误匹配
ROIS = {
    "collect_rewards": (0.75, 0.80, 1.0, 1.0),
    "next": (0.75, 0.80, 1.0, 1.0),
    "end_turn": (0.75, 0.80, 1.0, 1.0),
    "play": (0.35, 0.72, 0.65, 0.97),
    "never_seen": (0.30, 0.00, 0.70, 0.20),
    "tap_to_snap": (0.60, 0.25, 0.95, 0.60),
    "card_detail": (0.00, 0.85, 0.20, 1.00),
}

# 已知全屏提示 -> 点击位置（相对坐标）。其余模糊遮罩（如撤退指引）不自动点，交给玩家。
OVERLAY_CLICKS = {
    "never_seen": (0.12, 0.50),     # 新卡展示：点左侧空白处跳过
    "tap_to_snap": (0.792, 0.640),  # SNAP 指引：点魔方（= layout.SNAP_BUTTON）
    "card_detail": (0.066, 0.933),  # 卡牌详情：点左下 Esc Close
}


@dataclass(frozen=True)
class Match:
    name: str
    score: float
    center: tuple[float, float]  # 相对坐标

    @property
    def found(self) -> bool:
        return self.score >= THRESHOLD


_templates: dict[str, np.ndarray] = {}
_template_dirs: list[Path] = [TEMPLATE_DIR]


def set_template_dirs(dirs: list[Path]) -> None:
    """按客户端切换界面模板：依次查找，前面的目录优先（客户端专用 → 共享）。"""
    global _template_dirs
    _template_dirs = [Path(d) for d in dirs] or [TEMPLATE_DIR]
    _templates.clear()


def _load() -> dict[str, np.ndarray]:
    if not _templates:
        for name in ROIS:
            for d in _template_dirs:
                path = d / f"{name}.png"
                # cv2.imread 不支持中文路径：用 imdecode
                t = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR) if path.exists() else None
                if t is not None:
                    _templates[name] = t
                    break
    return _templates


def detect(img) -> dict[str, Match]:
    """img：PIL.Image 或 BGR ndarray（任意 16:9 尺寸）。返回各按钮的最佳匹配。"""
    arr = np.array(img.convert("RGB"))[:, :, ::-1] if hasattr(img, "convert") else img
    h, w = arr.shape[:2]
    scale = REF_WIDTH / w
    arr = cv2.resize(arr, (REF_WIDTH, round(h * scale)), interpolation=cv2.INTER_AREA) if abs(scale - 1) > 1e-3 else arr
    H, W = arr.shape[:2]
    out: dict[str, Match] = {}
    for name, t in _load().items():
        x0, y0, x1, y1 = ROIS[name]
        roi = arr[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)]
        if roi.shape[0] < t.shape[0] or roi.shape[1] < t.shape[1]:
            continue
        r = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(r)
        cx = (int(x0 * W) + loc[0] + t.shape[1] / 2) / W
        cy = (int(y0 * H) + loc[1] + t.shape[0] / 2) / H
        out[name] = Match(name, float(score), (cx, cy))
    return out


def _template(name: str):
    for d in _template_dirs:
        path = d / f"{name}.png"
        if path.exists():
            return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    return None


def _board(img):
    arr = np.array(img.convert("RGB"))[:, :, ::-1] if hasattr(img, "convert") else img
    h, w = arr.shape[:2]
    return cv2.resize(arr, (REF_WIDTH, round(h * REF_WIDTH / w)), interpolation=cv2.INTER_AREA)


def activate_hints(img, threshold: float = THRESHOLD) -> list[tuple[float, float]]:
    """画面上所有「Press & Hold to Activate」提示的中心（相对坐标）。提示出现在可激活的我方场上牌上方，
    包括资料库里没写激活的牌（如 Iron Lad 复制到了激活能力）。"""
    t = _template("activate_hint")
    if t is None:
        return []
    arr = _board(img)
    H, W = arr.shape[:2]
    x0, y0 = int(0.25 * W), int(0.35 * H)
    roi = arr[y0:int(0.80 * H), x0:int(0.75 * W)]
    r = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
    out: list[tuple[float, float]] = []
    th, tw = t.shape[:2]
    for y, x in sorted(zip(*np.where(r >= threshold)), key=lambda p: -r[p]):
        cx, cy = (x0 + x + tw / 2) / W, (y0 + y + th / 2) / H
        if all(abs(cx - a) > 0.03 or abs(cy - b) > 0.03 for a, b in out):   # 非极大值抑制
            out.append((cx, cy))
    return out


def activation_staged(img) -> float:
    """场上牌已激活（待揭示）时，牌上方会出现「Press & Hold to Deactivate」提示。返回最佳匹配得分（≥ THRESHOLD 视为已激活）。

    激活要到揭示时才结算，内存里的激活计数在本回合内不变，只能看画面确认；确认前不能重复长按（会取消激活）。
    """
    t = None
    for d in _template_dirs:
        path = d / "deactivate_hint.png"
        if path.exists():
            t = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
            break
    if t is None:
        return 0.0
    arr = np.array(img.convert("RGB"))[:, :, ::-1] if hasattr(img, "convert") else img
    h, w = arr.shape[:2]
    arr = cv2.resize(arr, (REF_WIDTH, round(h * REF_WIDTH / w)), interpolation=cv2.INTER_AREA)
    H, W = arr.shape[:2]
    roi = arr[int(0.35 * H):int(0.80 * H), int(0.25 * W):int(0.75 * W)]   # 三个地点的我方区域
    return float(cv2.minMaxLoc(cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED))[1])


BLUR_THRESHOLD = 50.0  # 实测：指引遮罩时棋盘区清晰度 ≈ 2，正常画面 ≥ 867


def sharpness(img, box=(0.30, 0.10, 0.70, 0.70)) -> float:
    """棋盘区域拉普拉斯方差（越小越模糊）。"""
    arr = np.array(img.convert("L")) if hasattr(img, "convert") else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = arr.shape[:2]
    roi = arr[int(box[1] * H):int(box[3] * H), int(box[0] * W):int(box[2] * W)]
    roi = cv2.resize(roi, (800, round(800 * roi.shape[0] / roi.shape[1])), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(roi, cv2.CV_64F).var())


def overlay_active(img) -> bool:
    """新手指引遮罩：除高亮目标外整屏模糊（如「Tap to SNAP!」）。内存里的 IsRunningTutorial 不覆盖这类提示。"""
    return sharpness(img) < BLUR_THRESHOLD


def current(img) -> Match | None:
    """得分最高且过阈值的按钮；都没有返回 None。"""
    found = [m for m in detect(img).values() if m.found]
    return max(found, key=lambda m: m.score) if found else None
