"""全局配置（环境变量、路径）——文档 2 第 8.1 节。

环境变量前缀 DUDULU_，并自动加载项目根目录的 .env（联调配置，不入库）。
注意：文档示例中的 `class Config: env_prefix` 是 pydantic v1 写法，
本项目硬约束 Pydantic v2 + pydantic-settings，故用语义等价的 SettingsConfigDict。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录的 .env（dudulu/.env）。后端进程从 backend/ 启动，
# 这里按本文件位置定位，保证任意 cwd 下都读同一份配置。
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    # 项目
    PROJECT_NAME: str = "dudulu"

    # 数据库
    SQLITE_PATH: str = "data/dudulu.db"
    YANTRIK_PATH: str = "data/yantrik/dudulu_memory.db"

    # JWT
    SECRET_KEY: str = "change-me-in-production"
    TOKEN_EXPIRE_DAYS: int = 30

    # LLM
    LLM_MODE: str = "api"
    LLM_API_PROVIDER: str = "openai"
    LLM_API_KEY: str = ""
    LLM_API_MODEL: str = "gpt-4o"
    LLM_API_BASE_URL: str = "https://api.ant-ling.com/v1/"
    LLM_LOCAL_BASE_URL: str = "http://localhost:8000/v1"
    LLM_LOCAL_MODEL: str = "qwen3.6-35b-a3b"

    # 主动行为
    PROACTIVE_INTERVAL_HOURS: float = 4.5
    PROACTIVE_SILENCE_THRESHOLD: int = 3
    PROACTIVE_MAX_DAILY: int = 3
    PROACTIVE_QUIET_HOURS: tuple = (23, 8)

    # 情感引擎
    EMOTION_CONFIG_PATH: str = "config/dudulu_config.yaml"

    # 记忆库（YantrikDB）。YANTRIK_PATH 见上方数据库节。
    # 嵌入器模式："default" = YantrikDB.with_default 捆绑模型（生产；
    # 首次启动需联网下载 ~28MB，离线降级 64 维且维度永久锁定）；
    # "fake" = 确定性假嵌入器，仅开发调试与单元测试用，无语义能力
    YANTRIK_EMBEDDER: str = "default"

    # 调度器（生产下 Web worker 里置 false，由独立进程跑，见 10.2）
    SCHEDULER_ENABLED: bool = True

    # 主动行为心跳间隔（分钟）。文档未定义"心跳本身多快"（第十六章待调优），
    # 默认 15 分钟；E2E 与调试可用环境变量缩短。
    PROACTIVE_TICK_MINUTES: float = 15

    model_config = SettingsConfigDict(env_prefix="DUDULU_", env_file=ENV_FILE)


_settings: Settings | None = None


def get_settings() -> Settings:
    """进程内单例读取配置。

    输入：无；输出：Settings 实例；副作用：首次调用时读取环境变量与 .env。
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
