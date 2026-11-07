"""截图 + CV/OCR 感知（P2 实现）。

待实现：
- 定位 SnapCN.exe 窗口客户区（pywin32），dxcam/mss 截图
- 回合/能量/地点战力：固定区域 OCR（PaddleOCR）
- 手牌：在自己卡组 12 张里做模板匹配
- 界面状态：data/templates/ui 下的按钮模板匹配
"""
from __future__ import annotations

from brain.models import GameState


class ScreenStateSource:
    def read_state(self) -> GameState | None:
        raise NotImplementedError("P2")
