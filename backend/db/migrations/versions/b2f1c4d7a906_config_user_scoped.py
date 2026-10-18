"""config 表按 user 分区（Phase 10 额外问题 1 的修复）

全局单例配置在多用户系统下是安全漏洞（一个用户改 Key 影响所有人）。
改为：主键 (user_id, key)；user_id = '__global__' 的行是全局默认，
用户级 PUT 写当前 user_id 的覆盖行，GET 返回「全局默认 + 用户覆盖」合并视图。

迁移策略（SQLite 不便改主键）：建新表 → 旧行以 __global__ 拷入 → 换表。
Revision ID: b2f1c4d7a906
Revises: 8d0463ebacee
Create Date: 2026-10-17
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b2f1c4d7a906"
down_revision = "8d0463ebacee"
branch_labels = None
depends_on = None

GLOBAL_USER = "__global__"


def upgrade() -> None:
    op.create_table(
        "config_user_scoped",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"),
                  nullable=False),
        sa.PrimaryKeyConstraint("user_id", "key"),
    )
    # 旧行全部迁移为全局默认（Phase 9 的全局配置保留为 __global__）
    op.execute(
        f"INSERT INTO config_user_scoped (user_id, key, value, updated_at) "
        f"SELECT '{GLOBAL_USER}', key, value, updated_at FROM config"
    )
    op.drop_table("config")
    op.rename_table("config_user_scoped", "config")


def downgrade() -> None:
    op.create_table(
        "config_global",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"),
                  nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.execute(
        "INSERT INTO config_global (key, value, updated_at) "
        f"SELECT key, value, updated_at FROM config WHERE user_id = '{GLOBAL_USER}'"
    )
    op.drop_table("config")
    op.rename_table("config_global", "config")
