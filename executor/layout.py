"""界面坐标：一律用相对游戏窗口客户区的比例 (0~1)，运行时换算成像素。

标定来源：2026-11-07 三张对局截图（2000×1125，16:9），客户区实测 2537×1427（同为 16:9）。
- 手牌：5 张时中心 x≈700/848/993/1138/1285，4 张时 773/920/1065/1212（px@2000 宽）
  -> 中心 0.4963，间距 0.0731，两种张数交叉验证误差 < 5px
- 地点：Ruins/LosDiablos/SewerSystem 中心 x≈740/1000/1260；我方出牌区在地点牌下方 y≈650-700
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    def to_px(self, rx: float, ry: float) -> tuple[int, int]:
        return (self.left + round(rx * self.width), self.top + round(ry * self.height))


END_TURN_BUTTON = (0.907, 0.924)
RETREAT_BUTTON = (0.088, 0.920)
SNAP_BUTTON = (0.792, 0.640)        # 右侧赌注立方体
LOCATION_DROP = [(0.370, 0.600), (0.500, 0.600), (0.630, 0.600)]  # 我方一侧落点
LOCATION_CENTER = [(0.370, 0.413), (0.500, 0.400), (0.630, 0.413)]

# 我方场上牌格子（相对地点中心 x 的偏移；两列两行，按打出顺序 0 左上、1 右上、2 左下、3 右下）
# 标定：2026-11-07 对局截图（2000×1125）右路 IronPatriot/WhiteQueen/GreenGoblin、中路 Valentina/Mirage
SLOT_DX = (-0.024, 0.027)
SLOT_Y = (0.595, 0.715)


def board_slot_pos(location: int, slot: int) -> tuple[float, float]:
    x = LOCATION_DROP[location][0] + SLOT_DX[slot % 2]
    return (x, SLOT_Y[min(slot // 2, 1)])


HAND_CENTER_X = 0.4963
HAND_SPACING = 0.0731
HAND_Y = 0.876


def hand_card_pos(index: int, hand_size: int) -> tuple[float, float]:
    """手牌在底部居中排列，牌数变化时整体平移。index 0 = 最左。"""
    if not 0 <= index < hand_size:
        raise IndexError(f"hand index {index} out of range for hand size {hand_size}")
    return (HAND_CENTER_X + (index - (hand_size - 1) / 2) * HAND_SPACING, HAND_Y)
