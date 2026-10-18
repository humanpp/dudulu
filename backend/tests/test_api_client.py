"""APIClient 单元测试：假传输层覆盖 HTTP 调用体（无网络、无第三方 mock 库）。

联调用例（test_llm_api_client.py）负责真实 API 验证；本文件用假 AsyncOpenAI
覆盖 generate / generate_stream 的分支，使核心模块行覆盖达标。
环境无 pytest-asyncio（不在 requirements.txt），异步用例统一用 asyncio.run 驱动。
"""

import asyncio
from types import SimpleNamespace

import pytest

from llm.api_client import APIClient


class FakeCompletions:
    """假 /chat/completions：按脚本返回非流式响应或流式 chunk。"""

    def __init__(self):
        self.calls: list[dict] = []

    async def create(self, model, messages, stream=False, **kwargs):
        self.calls.append({"model": model, "messages": messages,
                           "stream": stream, **kwargs})
        if stream:
            return self._stream()
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="完整回复"))])

    async def _stream(self):
        for token in ("你", "好", "呀"):
            yield SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content=token))])


class FakeAsyncOpenAI:
    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.completions = FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.fixture
def fake_openai(monkeypatch):
    """把 llm.api_client.AsyncOpenAI 换成假实现，返回最后创建的实例。"""
    holder = {}

    def factory(**kwargs):
        instance = FakeAsyncOpenAI(**kwargs)
        holder["instance"] = instance
        return instance

    monkeypatch.setattr("llm.api_client.AsyncOpenAI", factory)
    return holder


class TestGenerate:
    def test_returns_content(self, fake_openai):
        client = APIClient("ant-ling", "sk-test", "m", "https://x/v1/")
        reply = asyncio.run(client.generate("你好"))
        assert reply == "完整回复"
        call = fake_openai["instance"].completions.calls[0]
        assert call["model"] == "m"
        assert call["messages"] == [{"role": "user", "content": "你好"}]
        assert call["stream"] is False

    def test_none_content_becomes_empty_string(self, fake_openai):
        client = APIClient("ant-ling", "sk-test", "m")
        instance = fake_openai["instance"]

        async def create(model, messages, stream=False, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=None))])

        instance.completions.create = create
        assert asyncio.run(client.generate("你好")) == ""

    def test_kwargs_passthrough(self, fake_openai):
        client = APIClient("ant-ling", "sk-test", "m")
        asyncio.run(client.generate("你好", temperature=0.5))
        assert fake_openai["instance"].completions.calls[0]["temperature"] == 0.5


class TestGenerateStream:
    def test_yields_tokens_in_order(self, fake_openai):
        client = APIClient("ant-ling", "sk-test", "m", "https://x/v1/")

        async def collect():
            return [token async for token in client.generate_stream("你好")]

        assert asyncio.run(collect()) == ["你", "好", "呀"]
        assert fake_openai["instance"].completions.calls[0]["stream"] is True

    def test_empty_choices_skipped(self, fake_openai):
        client = APIClient("ant-ling", "sk-test", "m")
        instance = fake_openai["instance"]

        async def create(model, messages, stream=False, **kwargs):
            async def gen():
                yield SimpleNamespace(choices=[])
                yield SimpleNamespace(choices=[SimpleNamespace(
                    delta=SimpleNamespace(content="有"))])
            return gen()

        instance.completions.create = create

        async def collect():
            return [token async for token in client.generate_stream("你好")]

        assert asyncio.run(collect()) == ["有"]

    def test_error_propagates(self, fake_openai):
        """异常向上抛，由 SSE 端点转 error 事件（文档 2 第 6.6 节）。"""
        client = APIClient("ant-ling", "sk-test", "m")
        instance = fake_openai["instance"]

        async def create(model, messages, stream=False, **kwargs):
            raise RuntimeError("upstream timeout")

        instance.completions.create = create

        async def collect():
            return [token async for token in client.generate_stream("你好")]

        with pytest.raises(RuntimeError, match="upstream timeout"):
            asyncio.run(collect())


class TestInit:
    def test_passes_base_url_and_key_to_sdk(self, fake_openai):
        APIClient("ant-ling", "sk-test", "m", "https://api.ant-ling.com/v1/")
        assert fake_openai["instance"].init_kwargs == {
            "api_key": "sk-test", "base_url": "https://api.ant-ling.com/v1/"}

    def test_base_url_optional(self, fake_openai):
        APIClient("openai", "sk-test", "gpt-4o")
        assert fake_openai["instance"].init_kwargs["base_url"] is None
