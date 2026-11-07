"""Claude 决策器（P4 实现）。

设计要点：
- 用 Messages API 的 tool use 强制输出 TurnDecision 结构
- 规则说明 / 卡牌库 / 卡组 放在 system 前缀并开启 prompt caching
- 超时（默认 10s）或校验失败两次 -> 交给 rule_based 兜底
"""
from __future__ import annotations

from . import rule_based
from .models import GameState, TurnDecision
from .validator import validate


class ClaudeDecider:
    def __init__(self, model: str, timeout_s: float = 10.0):
        self.model = model
        self.timeout_s = timeout_s

    def decide(self, state: GameState) -> TurnDecision:
        try:
            decision = self._ask_claude(state)
            if not validate(state, decision):
                return decision
        except NotImplementedError:
            pass
        return rule_based.decide(state)

    def _ask_claude(self, state: GameState) -> TurnDecision:
        raise NotImplementedError("P4: 接入 anthropic SDK")
