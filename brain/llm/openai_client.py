"""OpenAI（及兼容 OpenAI 协议的中转 / 其他厂商）客户端。

- api="responses"：官方推荐的 Responses API（text.format = json_schema，reasoning.effort）
- api="chat"：Chat Completions（response_format = json_schema，reasoning_effort），第三方中转普遍支持
- api="auto"：先试 Responses，若端点不支持（404/405/400 未知路径）则回退 Chat，并记住结果

提速/兼容选项：
- effort="off"：不发送推理参数（不支持推理强度的模型，或 DeepSeek 用 extra_body 关闭思考时）
- verbosity="low"：Responses 的 text.verbosity，回答更短（实测输出 token 约减半）
- json_mode="object"：只支持 json_object 的厂商（DeepSeek），Schema 写进系统提示，返回后由上层校验
- extra_body：厂商私有参数，如 DeepSeek 关闭思考 {"thinking": {"type": "disabled"}}
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .base import LLMError, LLMRefusal, TaskConfig

log = logging.getLogger(__name__)


class OpenAIClient:
    provider = "openai"

    def __init__(self, api_key: str, base_url: str | None = None, api: str = "auto", timeout_s: float = 60.0, client: Any = None,
                 json_mode: str = "schema", verbosity: str | None = None, extra_body: dict | None = None):
        self.api = api
        self.json_mode = json_mode
        self.verbosity = verbosity
        self.extra_body = extra_body or None
        if client is None:
            import httpx
            import openai

            # 实测（2026-11-07，中转）：新建连接（TLS 握手）1.4-5.1s，偶尔超过 SDK 默认 5s 连接超时；复用连接 0.3-0.7s。
            # httpx 默认空闲 5s 就关闭连接，而回合间隔 20-30s，导致每回合都重新握手 -> 保活 120s，连接超时放宽到 15s。
            http = httpx.Client(timeout=httpx.Timeout(timeout_s, connect=15.0),
                                limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120.0))
            client = openai.OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout_s, max_retries=2,
                                   http_client=http)
        self.client = client
        self._last_warm = 0.0

    def warmup(self, min_interval_s: float = 20.0) -> bool:
        """轻量请求（GET /models）建立/保持连接，让下一次决策复用已握手的连接。距上次预热不足 min_interval_s 则跳过。"""
        import time

        if time.time() - self._last_warm < min_interval_s:
            return False
        self._last_warm = time.time()
        try:
            self.client.with_options(timeout=15, max_retries=0).models.list()
            return True
        except Exception as e:  # noqa: BLE001
            log.debug("预热连接失败：%r", e)
            return False

    def complete_json(self, task: TaskConfig, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        import time

        try:
            return self._complete_json(task, system, user, schema, schema_name)
        finally:
            self._last_warm = time.time()   # 刚用过的连接本身就是热的

    def _complete_json(self, task, system, user, schema, schema_name):
        if self.api in ("responses", "auto"):
            try:
                return self._responses(task, system, user, schema, schema_name)
            except LLMError:
                raise
            except Exception as e:  # noqa: BLE001
                if self.api == "responses" or not _endpoint_unsupported(e):
                    raise LLMError(f"Responses API 调用失败：{e!r}") from e
                log.info("端点不支持 Responses API，改用 Chat Completions：%r", e)
                self.api = "chat"
        try:
            return self._chat(task, system, user, schema, schema_name)
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"Chat Completions 调用失败：{e!r}") from e

    def _api(self, task):
        c = self.client
        return c.with_options(timeout=task.timeout_s, max_retries=task.max_retries) if hasattr(c, "with_options") else c

    def _responses(self, task, system, user, schema, schema_name):
        text: dict[str, Any] = {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}}
        if task.verbosity or self.verbosity:
            text["verbosity"] = task.verbosity or self.verbosity
        kw: dict[str, Any] = {"reasoning": {"effort": task.effort}} if _effort(task) else {}
        if self.extra_body:
            kw["extra_body"] = self.extra_body
        resp = self._api(task).responses.create(
            model=task.model, instructions=system, input=user,
            max_output_tokens=task.max_output_tokens, text=text, **kw,
        )
        for item in getattr(resp, "output", None) or []:
            for c in getattr(item, "content", None) or []:
                if getattr(c, "type", "") == "refusal":
                    raise LLMRefusal(getattr(c, "refusal", "refused"))
        status = getattr(resp, "status", "completed")
        if status != "completed":
            raise LLMError(f"Responses 未完成：status={status} {getattr(resp, 'incomplete_details', '')}")
        return _parse(resp.output_text)

    def _chat(self, task, system, user, schema, schema_name):
        kw: dict[str, Any] = {"reasoning_effort": task.effort} if _effort(task) else {}
        if self.extra_body:
            kw["extra_body"] = self.extra_body
        if self.json_mode == "object":
            system = (system + "\n\n输出必须是一个 JSON 对象，严格符合以下 JSON Schema（不要输出其他文字）：\n"
                      + json.dumps(schema, ensure_ascii=False))
            fmt: dict[str, Any] = {"type": "json_object"}
        else:
            fmt = {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
        resp = self._api(task).chat.completions.create(
            model=task.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_completion_tokens=task.max_output_tokens, response_format=fmt, **kw,
        )
        msg = resp.choices[0].message
        if getattr(msg, "refusal", None):
            raise LLMRefusal(msg.refusal)
        if resp.choices[0].finish_reason not in ("stop", None):
            raise LLMError(f"Chat 未正常结束：finish_reason={resp.choices[0].finish_reason}")
        return _parse(msg.content)


def _effort(task: TaskConfig) -> bool:
    return bool(task.effort) and task.effort not in ("off", "none_param")


def _endpoint_unsupported(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    text = str(e).lower()
    if "model_not_found" in text or "model" in text and "not available" in text:
        return False  # 模型不存在也是 404，但与端点是否支持 Responses 无关
    return status in (404, 405) or (status == 400 and ("responses" in text or "not found" in text or "unknown" in text))


def _parse(text: str | None) -> dict[str, Any]:
    if not text:
        raise LLMError("模型没有返回内容")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"返回不是合法 JSON：{text[:200]!r}") from e
