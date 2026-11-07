"""大模型场地分析（与厂商无关）：判断场地在各回合阶段是否利好放牌，结果写回知识库。"""
from __future__ import annotations

import logging
from typing import Any

from ..locations import LocationInfo
from .base import LLMClient, LLMError, TaskConfig

log = logging.getLogger(__name__)

SYSTEM = """你是《漫威终极逆转》（Marvel Snap 国服）的场地分析专家。
给定一个场地（LocationDefId，与国际服英文 ID 相同），说明它的效果，并评估「在不同回合阶段往这个场地放牌对我方是否有利」。

评分 bias 取 -3..+3（正数=利好往这里放牌，负数=不利，0=中性），分三个阶段：
- early：第 1-3 回合
- mid：第 4-5 回合
- final：最后一回合（通常第 6 回合）

判断原则（玩家经验）：
- 每回合给这里的牌加战力之类「随时间累积收益」的场地：越早放越好（early 高）。
- 会把我方打出的牌复制给对手、或随时间累积负面效果的场地：前期不利，留到最后一回合再放，把负面影响降到最低（early/mid 为负，final 接近 0 或正）。
- 只影响特定类型卡牌（如 Ongoing）的场地：bias 给中性，在 tags 里注明（如 "ongoing_double"），由卡牌规则另行加权。
- 纯出牌限制（如某回合禁止/强制出牌）：bias 中性，tags 加 "restriction"。
- 如果你不确定这个场地的效果，effect 写 "unknown"，bias 全部给 0，confidence 给 low。"""

_BIAS = {"type": "integer", "enum": [-3, -2, -1, 0, 1, 2, 3]}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "effect": {"type": "string"},
        "bias": {"type": "object", "properties": {"early": _BIAS, "mid": _BIAS, "final": _BIAS},
                 "required": ["early", "mid", "final"], "additionalProperties": False},
        "tags": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string"},
    },
    "required": ["effect", "bias", "tags", "confidence", "notes"],
    "additionalProperties": False,
}


class LocationAnalyst:
    def __init__(self, client: LLMClient, task: TaskConfig):
        self.client = client
        self.task = task

    def analyze(self, name: str, effect_hint: str = "") -> LocationInfo | None:
        user = f"场地 LocationDefId：{name}" + (f"\n画面上显示的效果文字：{effect_hint}" if effect_hint else "")
        try:
            d = self.client.complete_json(self.task, SYSTEM, user, SCHEMA, "location_assessment")
        except LLMError as e:
            log.warning("场地 %s 分析失败：%s", name, e)
            return None
        if d.get("confidence") == "low" or d.get("effect") == "unknown":
            log.info("模型对场地 %s 把握不足，保持未知", name)
            return None
        return LocationInfo(name=name, effect=d["effect"], source=f"llm:{self.client.provider}/{self.task.model}",
                            bias=d["bias"], tags=d.get("tags", []), notes=d.get("notes", ""))
