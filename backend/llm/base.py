"""LLM 统一接口定义（文档 2 第 3.7 节）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class LLMClient(ABC):
    """统一接口。无论底层是 API 还是本地模型，都实现这个方法。"""

    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> str:
        """非流式生成。

        输入：prompt（完整提示词）；输出：完整回复文本；副作用：一次上游调用。
        """
        ...

    @abstractmethod
    async def generate_stream(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        """流式生成，逐 token 返回。用于 SSE。

        输入：prompt；输出：token 异步迭代器；副作用：一次上游流式调用。
        """
        ...

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """把文本编码为向量，用于记忆检索的语义相似度（裁决：接口契约保留）。

        APIClient / LocalClient 当前抛 NotImplementedError——ant-ling 无
        /embeddings 端点，语义检索由 YantrikDB 内置 embedder 提供；
        Phase 10 本地服务若暴露 /v1/embeddings 再实现。
        """
        ...
