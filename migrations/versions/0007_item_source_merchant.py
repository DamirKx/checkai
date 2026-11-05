"""Источник категории позиции и каноническое имя магазина

Revision ID: 0007_item_source_merchant
Revises: 0006_profile
Create Date: 2026-11-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_item_source_merchant"
down_revision = "0006_profile"
branch_labels = None
depends_on = None


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "category_source" not in _columns("receipt_items"):
        op.add_column("receipt_items", sa.Column("category_source", sa.String(16), nullable=True))
    if "merchant" not in _columns("receipts"):
        op.add_column("receipts", sa.Column("merchant", sa.String(255), nullable=True))
        op.create_index("ix_receipts_merchant", "receipts", ["merchant"])
    # Старые чеки получают merchant при следующем редактировании; /api/profile/stats до этого
    # вычисляет имя сети для них на лету (crud.get_profile_stats)


def downgrade() -> None:
    op.drop_index("ix_receipts_merchant", table_name="receipts")
    with op.batch_alter_table("receipts") as batch:
        batch.drop_column("merchant")
    with op.batch_alter_table("receipt_items") as batch:
        batch.drop_column("category_source")
