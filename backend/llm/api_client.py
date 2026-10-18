"""API 模式实现（文档 2 第 3.7 节）：OpenAI 兼容协议（/chat/completions）。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from llm.base import LLMClient

logger = logging.getLogger(__name__)


class APIClient(LLMClient):
    """API 模式：OpenAI / Claude / DeepSeek 等。"""

    def __init__(self, provider: str, api_key: str, model: str,
                 base_url: str | None = None):
        """初始化 API 客户端。

        输入：
            provider: 提供方名称（仅记录用，协议统一走 OpenAI 兼容）
            api_key: API Key（来自 .env / 配置，禁止硬编码或打日志）
            model: 模型名
            base_url: 可选，自定义端点（裁决 5：ant-ling 等兼容服务的实际用途）
        副作用：构造底层 AsyncOpenAI 连接池（不发请求）。
        """
        self.provider = provider
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate(self, prompt: str, **kwargs) -> str:
        """非流式生成。输入 prompt；输出完整文本；副作用：一次上游 HTTP 调用。"""
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return response.choices[0].message.content or ""

    async def embed(self, text: str) -> list[float]:
        """文本嵌入（接口契约保留，当前无可用端点）。

        ant-ling 不支持 /embeddings（实测 404 route_not_found）；语义检索由
        YantrikDB 内置 embedder 提供，本方法暂无调用方，故显式抛
        NotImplementedError 而非留死代码。Phase 10 本地服务若暴露
        /v1/embeddings 再实现。LocalClient 继承本行为。
        """
        raise NotImplementedError(
            "APIClient.embed 未实现：当前 ant-ling 不支持 /embeddings；"
            "语义检索由 YantrikDB 内置 embedder 提供。"
            "Phase 10 若本地服务暴露 /v1/embeddings 再实现。")

    async def generate_stream(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        """流式生成，逐 token yield。

        输入 prompt；输出 token 异步迭代器；副作用：一次上游 HTTP 流式调用。
        异常直接向上抛，由调用方（SSE 端点）转成 error 事件（文档 2 第 6.6 节）。
        """
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            **kwargs,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            content = chunk.choices[0].delta.content
            if content:
                yield content
