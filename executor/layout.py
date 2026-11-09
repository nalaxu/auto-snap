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


HINT_DY = -0.088   # 激活提示（Press & Hold to Activate/Deactivate）在牌中心上方的偏移（实测 2 张截图）


def hint_to_slot(hint: tuple[float, float], cards: list[tuple[int, int]], tol: float = 0.05) -> tuple[int, int] | None:
    """提示中心 -> 最近的我方场上牌 (location, slot)；cards 为候选 [(location, slot)]。"""
    best, dist = None, tol
    for loc, slot in cards:
        x, y = board_slot_pos(loc, slot)
        d = max(abs(hint[0] - x), abs(hint[1] - (y + HINT_DY)))
        if d < dist:
            best, dist = (loc, slot), d
    return best


# ---------------------------------------------------------------- 竞技场（tests/fixtures/arena/，2000×1125）
ARENA_DRAFT_SELECT = [(0.4215, 0.391), (0.500, 0.391), (0.5785, 0.391)]   # 组卡三张候选的 Select
ARENA_SWAP_SELECT = [(0.4825, 0.352), (0.5375, 0.352), (0.593, 0.352)]    # 整场界面换牌候选的 Select
ARENA_SWAP_REFRESH = (0.6075, 0.182)                                       # 换牌面板右上刷新（放弃本次换牌）
ARENA_CHEST = (0.500, 0.550)                                               # 整场结束：点宝箱打开
ARENA_BUY_CANCEL = (0.447, 0.560)                                          # 买票弹窗 CANCEL
# 卡组 2 行 × 6 列。界面顺序 = 内存 PickedCards 顺序（按费用排序；2026-11-09 实战两次换牌都换下了目标牌）
ARENA_DECK_X = (0.3965, 0.4385, 0.479, 0.520, 0.561, 0.602)
ARENA_DECK_Y = (0.524, 0.627)


def arena_deck_pos(index: int) -> tuple[float, float]:
    if not 0 <= index < 12:
        raise IndexError(f"arena deck index {index} out of range")
    return (ARENA_DECK_X[index % 6], ARENA_DECK_Y[index // 6])


HAND_CENTER_X = 0.4963
HAND_SPACING = 0.0731
HAND_Y = 0.876


def hand_card_pos(index: int, hand_size: int) -> tuple[float, float]:
    """手牌在底部居中排列，牌数变化时整体平移。index 0 = 最左。"""
    if not 0 <= index < hand_size:
        raise IndexError(f"hand index {index} out of range for hand size {hand_size}")
    return (HAND_CENTER_X + (index - (hand_size - 1) / 2) * HAND_SPACING, HAND_Y)
