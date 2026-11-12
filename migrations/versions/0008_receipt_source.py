"""Источник чека: фото или ручной ввод (NEXT_WEEK_TASKS.md, A.2.1)

Revision ID: 0008_receipt_source
Revises: 0007_item_source_merchant
Create Date: 2026-11-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_receipt_source"
down_revision = "0007_item_source_merchant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("receipts")}
    if "source" in columns:
        return
    op.add_column("receipts", sa.Column("source", sa.String(10), nullable=False, server_default="photo"))
    # Старые чеки без фото считаем введёнными вручную
    op.execute("UPDATE receipts SET source = 'manual' WHERE image_path IS NULL OR image_path = ''")


def downgrade() -> None:
    with op.batch_alter_table("receipts") as batch:
        batch.drop_column("source")
