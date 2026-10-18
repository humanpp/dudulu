"""LLM embed 接口契约测试（裁决④：接口保留，实现抛 NotImplementedError）。"""

import asyncio

import pytest

from llm.api_client import APIClient
from llm.base import LLMClient
from llm.local_client import LocalClient


class TestEmbedInterface:
    def test_base_declares_abstract_embed(self):
        """LLMClient 保留 embed 抽象方法（与 generate / generate_stream 并列）。"""
        assert hasattr(LLMClient, "embed")
        assert getattr(LLMClient.embed, "__isabstractmethod__", False)

    def test_api_client_embed_raises_not_implemented(self):
        client = APIClient("ant-ling", "sk-test", "m")
        with pytest.raises(NotImplementedError, match="/embeddings"):
            asyncio.run(client.embed("你好"))

    def test_local_client_inherits_not_implemented(self):
        """LocalClient 继承 APIClient，一并继承该行为。"""
        client = LocalClient(base_url="http://localhost:8000/v1", model="m")
        with pytest.raises(NotImplementedError):
            asyncio.run(client.embed("你好"))

    def test_local_client_with_api_key_still_raises(self):
        client = LocalClient(base_url="https://api.ant-ling.com/v1/",
                             model="Ling-3.1-flash", api_key="sk-test")
        with pytest.raises(NotImplementedError):
            asyncio.run(client.embed("你好"))
