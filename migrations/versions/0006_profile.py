"""Профиль: фото, настройки оформления, последний вход, код восстановления

Revision ID: 0006_profile
Revises: 0005_categories
Create Date: 2026-11-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_profile"
down_revision = "0005_categories"
branch_labels = None
depends_on = None

COLUMNS = [
    sa.Column("avatar_path", sa.String(500), nullable=True),
    sa.Column("preferences", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("last_login_at", sa.DateTime(), nullable=True),
    sa.Column("recovery_code_hash", sa.String(255), nullable=True),
]


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    for column in COLUMNS:
        if column.name not in existing:
            op.add_column("users", column)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        for column in reversed(COLUMNS):
            batch.drop_column(column.name)
