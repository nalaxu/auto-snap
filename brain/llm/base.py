"""与厂商无关的大模型接口：上层只关心「给 system + user，拿回符合 JSON Schema 的结果」。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(RuntimeError):
    pass


class LLMRefusal(LLMError):
    """模型拒绝回答（安全策略）。"""


@dataclass(frozen=True)
class TaskConfig:
    """一个任务（场地分析 / 出牌决策 / 赛后复盘）使用的模型与推理强度。"""
    model: str
    effort: str = "medium"
    max_output_tokens: int = 16000
    timeout_s: float = 60.0      # 单次请求超时
    max_retries: int = 2         # SDK 层自动重试次数（逐回合决策设 0：超时直接交给规则 AI）
    verbosity: str | None = None  # Responses text.verbosity：low 回答更短更快（逐回合决策用）


class LLMClient(Protocol):
    provider: str

    def complete_json(self, task: TaskConfig, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        """返回解析后的 JSON（严格符合 schema）。失败抛 LLMError / LLMRefusal。"""
        ...
