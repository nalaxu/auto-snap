"""竞技场组卡 / 换牌的大模型决策。组卡、换牌没有时间限制，用较高推理强度；失败时用规则打分兜底。

提示词只放通用原则；具体卡牌的评分 / 标签 / 要点来自评分表（知识库），只注入当前候选与卡组涉及的卡。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .. import arena_draft as rules
from ..arena_ratings import default_ratings
from ..knowledge import default_db
from .base import LLMClient, LLMError, TaskConfig

log = logging.getLogger(__name__)

SYSTEM = """你是《漫威终极逆转》（Marvel Snap 国服）竞技场模式的组卡高手。
竞技场：12 轮三选一组成 12 张卡组（卡池约等于全卡池），对手同样随机组卡；之后打对局，每赢 4 魔方或每掉 2 血获得一次换牌机会。

原则（按优先级）：
1. 好卡先拿：单卡强度最重要（参考 rating，1–10）。
2. 能量利用率：偏低费的曲线，参考 1 费 1–3、2 费 3–4、3 费 2–3、4 费 1–2、5 费 1–2、6 费 0–1 张。
   这不是硬约束，但越到后面几轮，越要补齐曲线缺口、避免某个费用堆积。
3. 配合：needs:<体系> 的牌只有卡组里已有该体系组件时才值得拿；已经形成方向时，同体系牌加分。
4. 针对卡（tech）在随机环境里价值打折。
rule_score 是程序按上述原则算出的参考分（越高越好），可以参考但要自己判断，尤其是卡牌之间的具体配合。

只输出 JSON，reason 用中文、≤ 40 字。"""

PICK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"pick": {"type": "integer", "enum": [0, 1, 2]}, "reason": {"type": "string"}},
    "required": ["pick", "reason"],
    "additionalProperties": False,
}

SWAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "swap": {"type": "boolean"},
        "out": {"type": "integer", "minimum": 0, "maximum": 11},
        "in": {"type": "integer", "enum": [0, 1, 2]},
        "reason": {"type": "string"},
    },
    "required": ["swap", "out", "in", "reason"],
    "additionalProperties": False,
}


def card_info(cid: str) -> dict:
    c = default_db().card(cid)
    r = default_ratings().get(cid) or {}
    return {"id": cid, "cost": c.get("cost"), "power": c.get("power"), "ability": c.get("ability") or "无能力",
            "rating": r.get("rating"), "tags": r.get("tags", []), "note": r.get("note", "")}


def curve_text(deck: list[str]) -> dict:
    return {f"{b}费" if b < 6 else "6+费": n for b, n in rules.curve_counts(deck).items()}


class ArenaDecider:
    def __init__(self, client: LLMClient, task: TaskConfig, swap_threshold: float = 1.0):
        self.client = client
        self.task = task
        self.swap_threshold = swap_threshold

    def pick(self, deck: list[str], candidates: list[str], deck_size: int = 12) -> rules.Pick:
        fallback = rules.choose_pick(deck, candidates, deck_size)
        data = {
            "round": f"{len(deck) + 1}/{deck_size}",
            "candidates": [dict(card_info(c), idx=i, rule_score=round(fallback.scores[i], 1)) for i, c in enumerate(candidates)],
            "deck": [card_info(c) for c in deck],
            "curve_now": curve_text(deck),
        }
        user = "组卡三选一，pick 填候选的 idx：\n" + json.dumps(data, ensure_ascii=False)
        try:
            out = self.client.complete_json(self.task, SYSTEM, user, PICK_SCHEMA, "arena_pick")
            i = int(out["pick"])
            if not 0 <= i < len(candidates):
                raise LLMError(f"pick 越界：{i}")
            return rules.Pick(i, candidates[i], out.get("reason", ""), fallback.scores, source=f"llm:{self.task.model}")
        except Exception as e:  # noqa: BLE001
            log.warning("竞技场组卡大模型失败，用规则：%r", e)
            fallback.reason += f"（大模型失败：{type(e).__name__}）"
            return fallback

    def swap(self, deck: list[str], candidates: list[str], context: dict | None = None) -> rules.Swap:
        fallback = rules.choose_swap(deck, candidates, self.swap_threshold)
        data = {
            "deck": [dict(card_info(c), idx=i) for i, c in enumerate(deck)],
            "candidates": [dict(card_info(c), idx=i) for i, c in enumerate(candidates)],
            "curve_now": curve_text(deck),
            "rule_suggestion": {"out": fallback.out_card, "in": fallback.in_card, "gain": round(fallback.gain, 1)},
            "run": context or {},
        }
        user = ("换牌：可以用一张候选替换卡组里的一张（out 填卡组 idx，in 填候选 idx），不值得换就 swap=false"
                "（放弃本次机会，候选会刷新）。\n" + json.dumps(data, ensure_ascii=False))
        try:
            out = self.client.complete_json(self.task, SYSTEM, user, SWAP_SCHEMA, "arena_swap")
            if not out.get("swap"):
                return rules.Swap(None, None, None, None, out.get("reason", ""), source=f"llm:{self.task.model}")
            i, j = int(out["out"]), int(out["in"])
            if not (0 <= i < len(deck) and 0 <= j < len(candidates)):
                raise LLMError(f"换牌下标越界：out={i} in={j}")
            return rules.Swap(i, deck[i], j, candidates[j], out.get("reason", ""), source=f"llm:{self.task.model}")
        except Exception as e:  # noqa: BLE001
            log.warning("竞技场换牌大模型失败，用规则：%r", e)
            fallback.reason += f"（大模型失败：{type(e).__name__}）"
            return fallback
