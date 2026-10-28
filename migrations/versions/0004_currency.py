"""Поддержка валют: валюта чека, курс к тенге, основная валюта пользователя, таблица курсов

Revision ID: 0004_currency
Revises: 0003_budgets
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_currency"
down_revision = "0003_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # 1. Добавляем base_currency в users
    if "users" in tables:
        user_columns = {c["name"] for c in inspector.get_columns("users")}
        if "base_currency" not in user_columns:
            with op.batch_alter_table("users") as batch_op:
                batch_op.add_column(
                    sa.Column("base_currency", sa.String(3), nullable=False, server_default="KZT")
                )

    # 2. Добавляем currency и rate_to_kzt в receipts
    if "receipts" in tables:
        receipt_columns = {c["name"] for c in inspector.get_columns("receipts")}
        with op.batch_alter_table("receipts") as batch_op:
            if "currency" not in receipt_columns:
                batch_op.add_column(
                    sa.Column("currency", sa.String(3), nullable=False, server_default="KZT")
                )
            if "rate_to_kzt" not in receipt_columns:
                batch_op.add_column(
                    sa.Column("rate_to_kzt", sa.Float(), nullable=True)
                )

        # Старым чекам в KZT проставляем курс 1.0 к тенге
        receipts = sa.table(
            "receipts",
            sa.column("id", sa.Integer),
            sa.column("currency", sa.String),
            sa.column("rate_to_kzt", sa.Float),
        )
        bind.execute(
            receipts.update()
            .where((receipts.c.currency == "KZT") | (receipts.c.currency.is_(None)))
            .values(rate_to_kzt=1.0, currency="KZT")
        )

    # 3. Создаём таблицу exchange_rates
    if "exchange_rates" not in tables:
        op.create_table(
            "exchange_rates",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("date", sa.String(10), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False),
            sa.Column("rate_to_kzt", sa.Float(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("date", "currency", name="uq_exchange_rates_date_currency"),
        )
        op.create_index("ix_exchange_rates_id", "exchange_rates", ["id"])
        op.create_index("ix_exchange_rates_date", "exchange_rates", ["date"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "exchange_rates" in tables:
        op.drop_table("exchange_rates")

    if "receipts" in tables:
        receipt_columns = {c["name"] for c in inspector.get_columns("receipts")}
        with op.batch_alter_table("receipts") as batch_op:
            if "rate_to_kzt" in receipt_columns:
                batch_op.drop_column("rate_to_kzt")
            if "currency" in receipt_columns:
                batch_op.drop_column("currency")

    if "users" in tables:
        user_columns = {c["name"] for c in inspector.get_columns("users")}
        if "base_currency" in user_columns:
            with op.batch_alter_table("users") as batch_op:
                batch_op.drop_column("base_currency")
