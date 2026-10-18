"""本地模式实现（文档 2 第 3.7 节）：llama.cpp / vLLM / Ollama 等。

与 APIClient 的差别只在是否传 api_key（本地部署通常不需要，裁决 §8.3），
其余逻辑一致，故复用 APIClient 的实现，仅替换构造方式。
api_key 缺省时用 "EMPTY" 占位（OpenAI SDK 要求非空，本地服务忽略该值，
vLLM 惯例）；显式传入时透传——用于 ant-ling 这类需要 Key 的 OpenAI
兼容远程服务（裁决：2026-10-14，实测 ant-ling 拒绝无 Key 请求）。
"""

from __future__ import annotations

from llm.api_client import APIClient


class LocalClient(APIClient):
    """本地模式：协议同样是 OpenAI 兼容的 /chat/completions。"""

    def __init__(self, base_url: str, model: str, api_key: str | None = None):
        """初始化本地客户端。

        输入：
            base_url: 服务地址（如 http://localhost:8000/v1）
            model: 模型名
            api_key: 可选。缺省 → "EMPTY" 占位；显式传入 → 透传
        副作用：构造底层 AsyncOpenAI 连接池（不发请求）。
        """
        super().__init__(provider="local", base_url=base_url, model=model,
                         api_key=api_key or "EMPTY")
