from __future__ import annotations


def make_state_source(settings: dict):
    """按 config/settings.toml 的 [perception].source 创建感知层实现。"""
    source = settings.get("perception", {}).get("source", "memory")
    process = settings.get("game", {}).get("process_name", "SnapCN.exe")
    if source == "memory":
        from .memory_source import MemoryStateSource

        return MemoryStateSource(process)
    if source == "screen":
        from .screen_source import ScreenStateSource

        return ScreenStateSource()
    raise ValueError(f"未知的 perception.source: {source}")
