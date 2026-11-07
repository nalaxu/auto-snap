"""感知层接口。P0 调研结果决定用哪种实现：
- FileStateSource：读游戏写出的状态文件（若国服存在）
- ScreenStateSource：截图 + CV/OCR
"""
from __future__ import annotations

from typing import Protocol

from brain.models import GameState


class StateSource(Protocol):
    def read_state(self) -> GameState | None:
        """返回当前对局状态；不在我方出牌阶段时返回 None。"""
        ...
