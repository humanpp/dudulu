"""Settings 单元测试（文档 2 第 8.1 节 + 裁决 5 的 LLM_API_BASE_URL）。"""

import pytest

from config.settings import ENV_FILE, Settings, get_settings


class TestSettings:
    def test_documented_defaults(self):
        """_env_file=None 绕过 .env，验证文档 8.1 节的字段缺省值。"""
        settings = Settings(_env_file=None)
        assert settings.PROJECT_NAME == "dudulu"
        assert settings.SQLITE_PATH == "data/dudulu.db"
        assert settings.YANTRIK_PATH == "data/yantrik/dudulu_memory.db"
        assert settings.SECRET_KEY == "change-me-in-production"
        assert settings.TOKEN_EXPIRE_DAYS == 30
        assert settings.LLM_MODE == "api"
        assert settings.LLM_API_PROVIDER == "openai"
        assert settings.LLM_API_MODEL == "gpt-4o"
        assert settings.LLM_API_BASE_URL == "https://api.ant-ling.com/v1/"
        assert settings.LLM_LOCAL_BASE_URL == "http://localhost:8000/v1"
        assert settings.LLM_LOCAL_MODEL == "qwen3.6-35b-a3b"
        assert settings.PROACTIVE_INTERVAL_HOURS == 4.5
        assert settings.PROACTIVE_SILENCE_THRESHOLD == 3
        assert settings.PROACTIVE_MAX_DAILY == 3
        assert settings.PROACTIVE_QUIET_HOURS == (23, 8)
        assert settings.EMOTION_CONFIG_PATH == "config/dudulu_config.yaml"
        assert settings.SCHEDULER_ENABLED is True

    def test_env_prefix_mapping(self, monkeypatch):
        """DUDULU_ 前缀映射到字段（文档 0 第六章命名规范）。"""
        monkeypatch.setenv("DUDULU_LLM_API_MODEL", "env-model")
        monkeypatch.setenv("DUDULU_TOKEN_EXPIRE_DAYS", "7")
        settings = Settings(_env_file=None)
        assert settings.LLM_API_MODEL == "env-model"
        assert settings.TOKEN_EXPIRE_DAYS == 7

    def test_env_file_points_to_project_root(self):
        """裁决：.env 在项目根目录，settings 按文件位置定位，与 cwd 无关。"""
        assert ENV_FILE.name == ".env"
        assert ENV_FILE.parent.name == "dudulu"

    def test_dotenv_loaded(self):
        """项目根 .env（联调配置）能被读入；.env 不在时跳过而非报错。"""
        if not ENV_FILE.exists():
            pytest.skip("项目根 .env 不存在（联调配置未落盘）")
        settings = Settings()
        assert settings.LLM_API_PROVIDER == "ant-ling"
        assert settings.LLM_API_MODEL == "Ling-3.1-flash"
        assert settings.LLM_API_KEY != ""

    def test_get_settings_is_singleton(self):
        first = get_settings()
        second = get_settings()
        assert first is second
