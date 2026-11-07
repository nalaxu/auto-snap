"""Anthropic Claude 客户端。

- 结构化输出：output_config.format = json_schema；推理强度：output_config.effort
- 开启服务端 refusal fallbacks（fallbacks: "default"，beta server-side-fallback-2026-07-01）
- 通过 extra_body / extra_headers 传参，兼容当前安装的旧版 anthropic SDK
"""
from __future__ import annotations

import json
from typing import Any

from .base import LLMError, LLMRefusal, TaskConfig


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout_s: float = 60.0, client: Any = None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key or None, base_url=base_url or None, timeout=timeout_s)
        self.client = client

    def complete_json(self, task: TaskConfig, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        c = self.client
        c = c.with_options(timeout=task.timeout_s, max_retries=task.max_retries) if hasattr(c, "with_options") else c
        resp = c.messages.create(
            model=task.model,
            max_tokens=task.max_output_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={
                "output_config": {"effort": task.effort, "format": {"type": "json_schema", "schema": schema}},
                "fallbacks": "default",
            },
        )
        if resp.stop_reason == "refusal":
            raise LLMRefusal("Claude 拒绝回答")
        if resp.stop_reason != "end_turn":
            raise LLMError(f"Claude 未正常结束：stop_reason={resp.stop_reason}")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            raise LLMError("Claude 没有返回文本")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"返回不是合法 JSON：{text[:200]!r}") from e
