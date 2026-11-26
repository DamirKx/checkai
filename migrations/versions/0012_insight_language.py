"""Язык ИИ-рекомендаций

Существующие анализы написаны по-русски — получают ru.

Revision ID: 0012_insight_language
Revises: 0011_category_default_key
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_insight_language"
down_revision = "0011_category_default_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("insights")}
    if "language" not in columns:
        op.add_column("insights", sa.Column("language", sa.String(2), nullable=False, server_default="ru"))


def downgrade() -> None:
    with op.batch_alter_table("insights") as batch:
        batch.drop_column("language")
