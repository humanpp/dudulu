"""客户端工厂：按配置在 API / 本地模式间切换（文档 2 第 3.7 节）。"""

from __future__ import annotations

import logging

from llm.api_client import APIClient
from llm.base import LLMClient
from llm.local_client import LocalClient

logger = logging.getLogger(__name__)


def create_llm_client(config: dict) -> LLMClient:
    """根据配置创建客户端。架构不关心接的是什么模型（文档 2 第 3.7 节）。

    输入：config，形如 {"llm": {"mode": "api"|"local", "api": {...}, "local": {...}}}；
    输出：对应模式的 LLMClient；副作用：无（不发请求）。
    mode 既非 api 也非 local 时抛 ValueError（文档未定义， fail-fast）。
    """
    mode = config["llm"]["mode"]  # "api" or "local"
    if mode == "api":
        return APIClient(
            provider=config["llm"]["api"]["provider"],
            api_key=config["llm"]["api"]["api_key"],
            model=config["llm"]["api"]["model"],
            base_url=config["llm"]["api"].get("base_url"),
        )
    elif mode == "local":
        return LocalClient(
            base_url=config["llm"]["local"]["base_url"],
            model=config["llm"]["local"]["model"],
            api_key=config["llm"]["local"].get("api_key"),
        )
    raise ValueError(f"未知 LLM 模式: {mode}")


def llm_config_from_settings(settings) -> dict:
    """把 Settings 组装成 create_llm_client 所需的 config dict。

    输入：Settings 实例（duck-typed，只读 LLM_* 字段）；
    输出：文档 2 第 3.7 节工厂的 config 结构；副作用：无。
    local 节不带 api_key（本地部署通常不需要）；需要时可在配置中显式提供。
    """
    return {
        "llm": {
            "mode": settings.LLM_MODE,
            "api": {
                "provider": settings.LLM_API_PROVIDER,
                "api_key": settings.LLM_API_KEY,
                "model": settings.LLM_API_MODEL,
                "base_url": settings.LLM_API_BASE_URL,
            },
            "local": {
                "base_url": settings.LLM_LOCAL_BASE_URL,
                "model": settings.LLM_LOCAL_MODEL,
            },
        }
    }


def _deep_merge(parent: dict, child: dict) -> dict:
    """深合并：child 覆盖 parent，dict 递归、列表与标量整体替换。"""
    import copy

    merged = copy.deepcopy(parent)
    for key, value in child.items():
        if (key in merged and isinstance(merged[key], dict)
                and isinstance(value, dict)):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def llm_config_with_overrides(settings, overrides: dict) -> dict:
    """Settings + config 表覆盖 → 工厂 config（覆盖优先，Phase 9 设置页用）。

    输入：Settings、形如 {"mode": ..., "api": {...}, "local": {...}} 的覆盖；
    输出：create_llm_client 所需的 config dict；副作用：无。
    api_key 显式空串表示"已清除"：保持为空，由调用方决定占位（见
    api.routes_chat.get_llm_client 的 EMPTY 占位说明）。
    """
    config = llm_config_from_settings(settings)
    return {"llm": _deep_merge(config["llm"], overrides or {})}
