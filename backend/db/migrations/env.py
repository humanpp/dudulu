"""Alembic 迁移环境：URL 取自 Settings，元数据取自 db.models.Base。"""

import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import get_settings  # noqa: E402
from db.models import Base  # noqa: E402
from db.sqlite import database_url  # noqa: E402

config = context.config
target_metadata = Base.metadata

DATABASE_URL = database_url(get_settings().SQLITE_PATH)


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL 脚本，不连库。"""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行。"""
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
