"""SQLite 连接与建表（文档 2 第 2 章 db/sqlite.py）。

连接参数来自 Settings（SQLITE_PATH，默认 data/dudulu.db，相对 backend/ 运行目录）。
开发/测试用 Base.metadata.create_all 建表；生产变更走 Alembic
（db/migrations，初始迁移由同一批模型生成，两处同源）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from fastapi import Depends
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import get_settings
from db.models import Base

logger = logging.getLogger(__name__)


def database_url(sqlite_path: str) -> str:
    """把文件路径转成 SQLAlchemy URL（as_posix 保证 Windows 路径用正斜杠）。"""
    return f"sqlite:///{Path(sqlite_path).as_posix()}"


def create_engine_for(sqlite_path: str) -> Engine:
    """按路径创建 Engine，并确保父目录存在。"""
    if sqlite_path != ":memory:":
        parent = Path(sqlite_path).parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)
    return create_engine(database_url(sqlite_path), future=True)


def create_session_factory(sqlite_path: str) -> sessionmaker[Session]:
    """按路径创建 session 工厂（expire_on_commit=False，提交后属性仍可读）。"""
    return sessionmaker(bind=create_engine_for(sqlite_path),
                        autoflush=False, expire_on_commit=False, future=True)


def init_db(engine: Engine) -> None:
    """建表（幂等，只建缺失的表）。生产 schema 变更请走 Alembic 迁移。"""
    Base.metadata.create_all(engine)


# 进程级默认引擎与会话工厂（应用启动与 get_db 使用）
_engine: Engine = create_engine_for(get_settings().SQLITE_PATH)
SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=_engine, autoflush=False, expire_on_commit=False, future=True)


def get_session_factory() -> sessionmaker[Session]:
    """FastAPI 依赖：会话工厂（测试可覆盖此依赖切换临时库）。"""
    return SessionLocal


def get_db(
    factory: sessionmaker[Session] = Depends(get_session_factory),
) -> Iterator[Session]:
    """FastAPI 依赖：请求级 Session，用完即关（提交由各路由显式进行）。"""
    db = factory()
    try:
        yield db
    finally:
        db.close()
