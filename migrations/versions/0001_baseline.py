"""Исходная схема (как её создавал create_all до перехода на Alembic)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("store", sa.String(255), nullable=True),
        sa.Column("date", sa.String(20), nullable=True),
        sa.Column("time", sa.String(10), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("total", sa.Float(), nullable=True),
        sa.Column("image_path", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_receipts_id", "receipts", ["id"])
    op.create_index("ix_receipts_user_id", "receipts", ["user_id"])

    op.create_table(
        "receipt_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("receipt_id", sa.Integer(), sa.ForeignKey("receipts.id"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("price_per_unit", sa.Float(), nullable=True),
        sa.Column("total_price", sa.Float(), nullable=True),
    )
    op.create_index("ix_receipt_items_id", "receipt_items", ["id"])


def downgrade() -> None:
    op.drop_table("receipt_items")
    op.drop_table("receipts")
    op.drop_table("users")
