"""Таблица бюджетов и лимитов категорий

Revision ID: 0003_budgets
Revises: 0002_item_category_iso_dates
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_budgets"
down_revision = "0002_item_category_iso_dates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "budgets" not in tables:
        op.create_table(
            "budgets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("category", sa.String(100), nullable=True),
            sa.Column("limit", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_budgets_id", "budgets", ["id"])
        op.create_index("ix_budgets_user_id", "budgets", ["user_id"])


def downgrade() -> None:
    op.drop_table("budgets")
