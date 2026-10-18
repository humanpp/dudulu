"""LLM API 客户端联调测试（真实调用 ant-ling，默认跳过）。

运行方式：python -m pytest -m integration
API Key 从项目根 .env 读取，不落代码（裁决 §8.5）。
断言只验证结构（非空、类型、事件序列），不依赖模型具体回复内容（裁决 §8.4）。

实测记录（2026-10-14）：
- APIClient + ant-ling Key：非流式与流式均真实通过；
- LocalClient（按文档 2 第 3.7 节不传 api_key）打 ant-ling 返回
  401 ling_auth_not_exist（"授权无效"）——ant-ling 拒绝无 Key 请求，
  故裁决 §8.3 "base_url 指向 ant-ling 验证本地模式"的路径需要 LocalClient
  具备携带 Key 的能力，已作为待裁决项提交，本地模式实时验证留待 Phase 10。
"""

import asyncio

import pytest

from config.settings import get_settings
from llm.api_client import APIClient
from llm.factory import create_llm_client
from llm.local_client import LocalClient

pytestmark = pytest.mark.integration

PROMPT = "你好，用一句话介绍你自己"

LOCAL_LIVE_SKIP_REASON = (
    "ant-ling 拒绝无 Key 请求（401 ling_auth_not_exist）；LocalClient 按文档 2 "
    "第 3.7 节不传 api_key。实时等价验证待 LocalClient api_key 能力裁决后，"
    "于 Phase 10 补跑。"
)


@pytest.fixture(scope="module")
def settings():
    return get_settings()


@pytest.fixture(scope="module")
def api_config(settings):
    """API 模式配置：全部取自 .env（ant-ling 联调配置）。"""
    return {
        "llm": {
            "mode": "api",
            "api": {
                "provider": settings.LLM_API_PROVIDER,
                "api_key": settings.LLM_API_KEY,
                "model": settings.LLM_API_MODEL,
                "base_url": settings.LLM_API_BASE_URL,
            },
        }
    }


@pytest.fixture(scope="module")
def local_config(settings):
    """本地模式配置：base_url 同样指向 ant-ling（协议兼容，见裁决 §8.3）。"""
    return {
        "llm": {
            "mode": "local",
            "local": {
                "base_url": settings.LLM_API_BASE_URL,
                "model": settings.LLM_API_MODEL,
            },
        }
    }


async def _collect_stream(client) -> list[str]:
    return [token async for token in client.generate_stream(PROMPT)]


class TestApiClientIntegration:
    def test_generate_non_stream(self, api_config):
        """用例 1：非流式生成，断言返回非空字符串。"""
        client = create_llm_client(api_config)
        reply = asyncio.run(client.generate(PROMPT))
        assert isinstance(reply, str)
        assert reply.strip() != ""

    def test_generate_stream(self, api_config):
        """用例 2：流式生成，断言按顺序收到多个 token 且拼接后非空。"""
        client = create_llm_client(api_config)
        tokens = asyncio.run(_collect_stream(client))
        assert len(tokens) > 1
        assert all(isinstance(token, str) for token in tokens)
        assert "".join(tokens).strip() != ""


class TestModelAgnosticIntegration:
    """裁决 §8.4：create_llm_client 只靠 config 切换，两种模式行为一致。"""

    def test_config_switch_returns_correct_types(self, api_config, local_config):
        """前半：纯 config 决定客户端类型，无需改代码。"""
        api_client = create_llm_client(api_config)
        local_client = create_llm_client(local_config)
        assert isinstance(api_client, APIClient)
        assert isinstance(local_client, LocalClient)

    def test_same_prompt_contract(self, api_config, local_config):
        """前半：两种模式暴露同一接口与方法集（静态行为一致保证）。"""
        api_client = create_llm_client(api_config)
        local_client = create_llm_client(local_config)
        assert type(api_client).generate is type(local_client).generate
        assert (type(api_client).generate_stream
                is type(local_client).generate_stream)

    def test_api_mode_streams_live(self, api_config):
        """后半（API 侧实测）：API 模式真实流式返回多个 token。"""
        client = create_llm_client(api_config)
        tokens = asyncio.run(_collect_stream(client))
        assert len(tokens) > 1
        assert "".join(tokens).strip() != ""

    @pytest.mark.skip(reason=LOCAL_LIVE_SKIP_REASON)
    def test_local_mode_streams_live(self, local_config):
        """后半（本地侧实测）：LocalClient 真实流式返回多个 token。"""
        client = create_llm_client(local_config)
        tokens = asyncio.run(_collect_stream(client))
        assert len(tokens) > 1
        assert "".join(tokens).strip() != ""
