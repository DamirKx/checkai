"""Категория у товара, индекс по дате, даты чеков в формате YYYY-MM-DD

Revision ID: 0002_item_category_iso_dates
Revises: 0001_baseline
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

from app.dates import normalize_date_time

revision = "0002_item_category_iso_dates"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    item_columns = {c["name"] for c in inspector.get_columns("receipt_items")}
    if "category" not in item_columns:
        op.add_column("receipt_items", sa.Column("category", sa.String(100), nullable=True))

    receipt_indexes = {ix["name"] for ix in inspector.get_indexes("receipts")}
    if "ix_receipts_date" not in receipt_indexes:
        op.create_index("ix_receipts_date", "receipts", ["date"])

    # Переводим уже сохранённые даты («29.07.2025», «13.05.19 21:17», …) в YYYY-MM-DD
    receipts = sa.table(
        "receipts",
        sa.column("id", sa.Integer),
        sa.column("date", sa.String),
        sa.column("time", sa.String),
    )
    rows = bind.execute(sa.select(receipts.c.id, receipts.c.date, receipts.c.time)).fetchall()
    for row in rows:
        new_date, new_time = normalize_date_time(row.date, row.time)
        if new_date != row.date or new_time != row.time:
            bind.execute(
                receipts.update()
                .where(receipts.c.id == row.id)
                .values(date=new_date, time=new_time)
            )


def downgrade() -> None:
    # Обратное преобразование дат не выполняется: формат YYYY-MM-DD понимают все версии фронта.
    op.drop_index("ix_receipts_date", table_name="receipts")
    with op.batch_alter_table("receipt_items") as batch_op:
        batch_op.drop_column("category")
