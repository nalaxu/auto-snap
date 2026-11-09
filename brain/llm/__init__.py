"""多厂商大模型接入。配置见 config/settings.toml 的 [llm]，密钥放在项目根目录 .env（不入库）。

    from brain import llm
    client = llm.make_client(settings)            # 未配置密钥返回 None
    task = llm.task_config(settings, "location")  # 每个任务可以用不同模型
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import LLMClient, LLMError, LLMRefusal, TaskConfig
from .location_analyst import LocationAnalyst

ROOT = Path(__file__).resolve().parent.parent.parent
PROVIDERS = ("openai", "anthropic")

# decision：逐回合决策（含场地分析，一次调用）；review：赛后复盘 / 离线维护场地知识库
DEFAULT_TASKS = {
    "decision": TaskConfig(model="gpt-6.1-sol", effort="medium", max_output_tokens=8000, timeout_s=45, max_retries=0),
    "review": TaskConfig(model="gpt-6.1-sol", effort="high", timeout_s=300),
    # arena：竞技场组卡 / 换牌（无时间限制）
    "arena": TaskConfig(model="gpt-6.1-sol", effort="medium", timeout_s=120, max_retries=1),
}

__all__ = ["LLMClient", "LLMError", "LLMRefusal", "TaskConfig", "LocationAnalyst",
           "load_dotenv", "make_client", "task_config", "make_location_analyst"]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """读取 .env（KEY=VALUE），不覆盖已存在的环境变量。"""
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _cfg(settings: dict) -> dict:
    """[llm] 配置；llm.profile 指定时用 [llm.profiles.<名字>] 覆盖（一键切换中转 / DeepSeek 等）。"""
    cfg = settings.get("llm", {}) or {}
    name = os.environ.get("LLM_PROFILE") or cfg.get("profile")
    if name:
        prof = (cfg.get("profiles", {}) or {}).get(name)
        if prof is None:
            raise ValueError(f"未知的 llm.profile: {name}（可选 {list((cfg.get('profiles') or {}).keys())}）")
        cfg = _merge({k: v for k, v in cfg.items() if k != "profiles"}, prof)
    return cfg


def make_client(settings: dict) -> LLMClient | None:
    load_dotenv()
    cfg = _cfg(settings)
    provider = cfg.get("provider", "openai")
    if provider not in PROVIDERS:
        raise ValueError(f"未知的 llm.provider: {provider}（支持 {PROVIDERS}）")
    key_env = cfg.get("api_key_env") or ("OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY")
    url_env = cfg.get("base_url_env") or ("OPENAI_BASE_URL" if provider == "openai" else "")
    api_key = os.environ.get(key_env, "").strip()
    base_url = (os.environ.get(url_env, "").strip() if url_env else "") or cfg.get("base_url") or None
    if not api_key:
        return None
    timeout = float(cfg.get("timeout_s", 60))
    if provider == "openai":
        from .openai_client import OpenAIClient

        return OpenAIClient(api_key=api_key, base_url=base_url, api=cfg.get("api", "auto"), timeout_s=timeout,
                            json_mode=cfg.get("json_mode", "schema"), verbosity=cfg.get("verbosity") or None,
                            extra_body=cfg.get("extra_body") or None)
    from .anthropic_client import AnthropicClient

    return AnthropicClient(api_key=api_key, base_url=base_url, timeout_s=timeout)


def task_config(settings: dict, task: str) -> TaskConfig:
    base = DEFAULT_TASKS[task]
    t = (_cfg(settings).get("tasks", {}) or {}).get(task, {}) or {}
    return TaskConfig(model=t.get("model", base.model), effort=t.get("effort", base.effort),
                      max_output_tokens=int(t.get("max_output_tokens", base.max_output_tokens)),
                      timeout_s=float(t.get("timeout_s", base.timeout_s)),
                      max_retries=int(t.get("max_retries", base.max_retries)),
                      verbosity=t.get("verbosity", base.verbosity) or None)


def make_location_analyst(settings: dict) -> LocationAnalyst | None:
    """离线维护场地知识库用（对局中场地分析已并入逐回合决策）。"""
    client = make_client(settings)
    return LocationAnalyst(client, task_config(settings, "review")) if client else None


def make_decider(settings: dict):
    """大模型逐回合决策器；未配置密钥返回 None。"""
    from .decider import LLMDecider

    client = make_client(settings)
    if not client:
        return None
    t = ((_cfg(settings).get("tasks", {}) or {}).get("decision", {}) or {})
    return LLMDecider(client, task_config(settings, "decision"),
                      effort_timed=t.get("effort_timed", "low"),
                      deadline_s=float(t.get("deadline_s", 40)),
                      deadline_timed_s=float(t.get("deadline_timed_s", 12)),
                      deadline_late_s=float(t.get("deadline_late_s", t.get("deadline_timed_s", 12))),
                      late_from_turn=int(t.get("late_from_turn", 5)),
                      timer_reserve_s=float(t.get("timer_reserve_s", 7)),
                      min_llm_s=float(t.get("min_llm_s", 5)))


def make_arena_decider(settings: dict):
    """竞技场组卡 / 换牌大模型决策；未配置密钥返回 None（流程改用规则打分）。"""
    from .arena_decider import ArenaDecider

    client = make_client(settings)
    if not client:
        return None
    return ArenaDecider(client, task_config(settings, "arena"),
                        swap_threshold=float((settings.get("arena", {}) or {}).get("swap_threshold", 1.0)))
