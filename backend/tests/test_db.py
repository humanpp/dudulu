"""db 层单元测试：建表幂等 / 表集合 / 默认工厂（文档 2 第 5.2 节 schema）。"""

import pytest
from sqlalchemy import inspect

from helpers import make_db_session_factory

EXPECTED_TABLES = {
    "users", "characters", "character_user_state", "character_versions",
    "sessions", "messages", "state_changes", "config", "proactive_log",
    "proactive_state",
}


class TestInitDb:
    def test_all_documented_tables_created(self, tmp_path):
        factory = make_db_session_factory(tmp_path / "dudulu.db")
        tables = set(inspect(factory.kw["bind"]).get_table_names())
        assert tables == EXPECTED_TABLES

    def test_init_is_idempotent(self, tmp_path):
        """create_all 幂等：重复调用不报错、表数不变。"""
        from db.sqlite import init_db

        factory = make_db_session_factory(tmp_path / "dudulu.db")
        init_db(factory.kw["bind"])
        init_db(factory.kw["bind"])
        tables = set(inspect(factory.kw["bind"]).get_table_names())
        assert tables == EXPECTED_TABLES

    def test_created_at_default_is_sql_current_timestamp(self, tmp_path):
        """server_default 必须是 SQL 表达式而非字符串字面量（踩坑回归）。"""
        from sqlalchemy.schema import CreateTable
        from sqlalchemy import create_engine

        from db.models import User

        ddl = str(CreateTable(User.__table__).compile(
            create_engine("sqlite://")))
        assert "DEFAULT CURRENT_TIMESTAMP" in ddl
        assert "'CURRENT_TIMESTAMP'" not in ddl

    def test_parent_directory_created(self, tmp_path):
        from db.sqlite import create_engine_for

        nested = tmp_path / "a" / "b" / "dudulu.db"
        create_engine_for(str(nested))
        assert nested.parent.is_dir()

    def test_database_url_windows_path(self):
        """Windows 绝对路径转正斜杠 URL。"""
        from db.sqlite import database_url

        assert database_url("data/dudulu.db") == "sqlite:///data/dudulu.db"
        assert database_url(r"C:\Temp\x.db") == "sqlite:///C:/Temp/x.db"


class TestDefaultSessionFactory:
    def test_get_session_factory_returns_singleton(self):
        from db.sqlite import SessionLocal, get_session_factory

        assert get_session_factory() is SessionLocal

    def test_get_db_yields_bound_session(self):
        """get_db 产出绑定了引擎的会话（直调需显式传工厂；Depends 路径由
        路由集成测试覆盖）。"""
        from db.sqlite import SessionLocal, get_db

        db = next(get_db(SessionLocal))
        try:
            assert db.bind is not None
        finally:
            db.close()
