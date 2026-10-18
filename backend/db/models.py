"""SQLite ORM 模型（文档 2 第 5.2 节全部建表 SQL，逐表对拍）。

SQLAlchemy 2.0 声明式。所有查询显式进行，不用隐式行为（懒加载等）。
JSON 列以 TEXT 存储（expression_state / internal_state / affective_state /
profile_json / delta_json / state_after_json / generation_meta_json）。
时间列统一存 naive UTC（SQLite CURRENT_TIMESTAMP 即 UTC）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """当前 UTC 时间的 naive datetime（与 SQLite CURRENT_TIMESTAMP 同基准）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """声明式基类。"""


class User(Base):
    """用户表（文档 2 第 5.2 节）。

    proactive_enabled：用户级主动行为开关（v1.0）；
    timezone：IANA 时区名，NULL 按 Asia/Shanghai（UTC+8）兜底（见 3.11）。
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    proactive_enabled: Mapped[int] = mapped_column(Integer, default=1)
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"))


class Character(Base):
    """角色档案表（文档 2 第 5.2 节）。profile_json 含 theme / visibility。"""

    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    profile_json: Mapped[str] = mapped_column(Text, nullable=False)
    generation_meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_path: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    is_public: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    is_archived: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"))


class CharacterUserState(Base):
    """用户-角色关系状态表，双键隔离（文档 2 第 5.2 节）。

    relationship_stage 存 stage_code（S0~S4）；affective_state JSON 里
    只存 AFF/TRS/ATT 三个可衰减维，stage 字段不入库（判定值）。
    """

    __tablename__ = "character_user_state"

    character_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    expression_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    affective_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    relationship_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    last_interaction: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"))


class CharacterVersion(Base):
    """角色版本表（文档 2 第 5.2 节；每角色保留最近 20 版，见 5.3）。"""

    __tablename__ = "character_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[str] = mapped_column(String, nullable=False)
    profile_json: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"))
    change_reason: Mapped[str | None] = mapped_column(String, nullable=True)


class Session(Base):
    """会话表（文档 2 第 5.2 节，v1.0）。status：active / ended。"""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    character_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="active")


class Message(Base):
    """消息索引表（文档 2 第 5.2 节）。

    正文不在本表：memory_id 指向 YantrikDB，正文按 memory_id 回读（见 6.7）。
    本表无 content / valence / is_proactive 列，与 DDL 一致。
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    character_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    memory_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"))


class StateChange(Base):
    """状态变更日志（文档 2 第 5.2 节）。只增不删，治理策略见 5.3。"""

    __tablename__ = "state_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"))
    event_label: Mapped[str | None] = mapped_column(String, nullable=True)
    event_intensity: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_after_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class Config(Base):
    """配置表（文档 2 第 5.2 节；Phase 10 起按 user 分区）。

    主键 (user_id, key)：user_id = GLOBAL_CONFIG_USER（'__global__'）的
    行是全局默认；用户级 PUT 写当前 user_id 的覆盖行。GET 返回
    「全局默认 + 当前用户覆盖」的合并视图（多用户隔离，额外问题 1）。
    """

    __tablename__ = "config"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"))


# 全局默认配置的归属 user_id（迁移与合并视图共用）
GLOBAL_CONFIG_USER = "__global__"


class ProactiveLog(Base):
    """主动行为日志（文档 2 第 5.2 节；保留 180 天，见 5.3）。"""

    __tablename__ = "proactive_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_responded: Mapped[int] = mapped_column(Integer, default=0)
    response_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProactiveState(Base):
    """主动行为退缩状态（文档 2 第 5.2 节，v1.0）。

    busy_until：用户近期表示"最近很忙"，到期前不主动发起（v1.3）。
    """

    __tablename__ = "proactive_state"

    character_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    consecutive_no_reply: Mapped[int] = mapped_column(Integer, default=0)
    last_proactive_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    silenced_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    busy_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"))
