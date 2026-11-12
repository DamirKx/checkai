"""ИИ-рекомендации по расходам (NEXT_WEEK_TASKS.md, B.2.3)

Revision ID: 0009_insights
Revises: 0008_receipt_source
Create Date: 2026-11-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_insights"
down_revision = "0008_receipt_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "insights" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "insights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_insights_id", "insights", ["id"])
    op.create_index("ix_insights_user_id", "insights", ["user_id"])


def downgrade() -> None:
    op.drop_table("insights")
