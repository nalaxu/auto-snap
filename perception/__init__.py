from __future__ import annotations


def make_state_source(settings: dict, client=None):
    """按 config/settings.toml 的 [perception].source 创建感知层实现。client：clients.ClientProfile（默认按配置解析）。"""
    import clients

    source = settings.get("perception", {}).get("source", "memory")
    client = client or clients.resolve(settings)
    if source == "memory":
        from .memory_source import MemoryStateSource

        return MemoryStateSource(client)
    if source == "screen":
        from .screen_source import ScreenStateSource

        return ScreenStateSource()
    raise ValueError(f"未知的 perception.source: {source}")
