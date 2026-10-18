"""LLM 工厂与 Settings 适配单元测试（文档 2 第 3.7 节 + 裁决 §8.4 配置切换）。"""

import pytest

from config.settings import Settings
from llm.api_client import APIClient
from llm.factory import create_llm_client, llm_config_from_settings
from llm.local_client import LocalClient

API_CONFIG = {
    "llm": {
        "mode": "api",
        "api": {
            "provider": "ant-ling",
            "api_key": "sk-test-not-real",
            "model": "Ling-3.1-flash",
            "base_url": "https://api.ant-ling.com/v1/",
        },
    }
}

LOCAL_CONFIG = {
    "llm": {
        "mode": "local",
        "local": {
            "base_url": "http://localhost:8000/v1",
            "model": "qwen3.6-35b-a3b",
        },
    }
}


class TestCreateLlmClient:
    def test_api_mode_returns_api_client(self):
        client = create_llm_client(API_CONFIG)
        assert isinstance(client, APIClient)
        assert client.provider == "ant-ling"
        assert client.model == "Ling-3.1-flash"

    def test_local_mode_returns_local_client(self):
        client = create_llm_client(LOCAL_CONFIG)
        assert isinstance(client, LocalClient)
        assert client.model == "qwen3.6-35b-a3b"

    def test_base_url_is_optional(self):
        """文档 2 第 3.7 节：base_url 用 .get 取，缺省时为 None。"""
        config = {"llm": {"mode": "api",
                          "api": {"provider": "openai", "api_key": "k",
                                  "model": "gpt-4o"}}}
        client = create_llm_client(config)
        assert isinstance(client, APIClient)

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            create_llm_client({"llm": {"mode": "bogus"}})

    def test_both_modes_implement_same_interface(self):
        """裁决 §8.4：两种模式对本项目行为一致（同接口、同方法集）。"""
        api_client = create_llm_client(API_CONFIG)
        local_client = create_llm_client(LOCAL_CONFIG)
        for name in ("generate", "generate_stream"):
            assert callable(getattr(api_client, name))
            assert callable(getattr(local_client, name))


class TestLlmConfigFromSettings:
    def test_builds_documented_shape(self):
        settings = Settings(_env_file=None)
        config = llm_config_from_settings(settings)
        assert config["llm"]["mode"] == "api"
        assert config["llm"]["api"]["provider"] == "openai"
        assert config["llm"]["api"]["base_url"] == "https://api.ant-ling.com/v1/"
        assert config["llm"]["local"]["base_url"] == "http://localhost:8000/v1"
        assert config["llm"]["local"]["model"] == "qwen3.6-35b-a3b"

    def test_reflects_settings_values(self):
        settings = Settings(_env_file=None, LLM_MODE="local",
                            LLM_API_PROVIDER="ant-ling",
                            LLM_API_MODEL="Ling-3.1-flash")
        config = llm_config_from_settings(settings)
        assert config["llm"]["mode"] == "local"
        assert config["llm"]["api"]["model"] == "Ling-3.1-flash"
