"""Ключ стандартной категории для перевода названия

Ключ получают только нетронутые стандартные категории: название И описание совпадают со стандартными
буква в букву. Переименованная или с изменённым описанием — NULL, она показывается как своя.

Revision ID: 0011_category_default_key
Revises: 0010_receipt_duplicates
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_category_default_key"
down_revision = "0010_receipt_duplicates"
branch_labels = None
depends_on = None

# Копия app/categories.py, DEFAULT_CATEGORIES на момент миграции: код может измениться, миграция — нет
DEFAULTS = [
    ("groceries", "Продукты",
     "Еда и напитки для дома: молочные продукты, хлеб, мясо, овощи, фрукты, крупы, сладости"),
    ("clothing", "Одежда", "Одежда, обувь, аксессуары"),
    ("home", "Дом и быт", "Бытовая химия, хозтовары, посуда, мелочи для дома"),
    ("transport", "Транспорт", "Топливо, такси, проезд, парковка"),
    ("cafe", "Кафе", "Готовая еда и напитки вне дома: кафе, рестораны, кофейни, доставка еды"),
    ("health", "Здоровье", "Аптека, лекарства, витамины, медицинские услуги"),
    ("entertainment", "Развлечения", "Кино, театр, концерты, игры, подписки"),
    ("other", "Другое", "Всё, что не подошло в другие категории"),
]


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("categories")}
    if "default_key" not in columns:
        op.add_column("categories", sa.Column("default_key", sa.String(32), nullable=True))
    for key, name, description in DEFAULTS:
        bind.execute(
            sa.text(
                "UPDATE categories SET default_key = :key "
                "WHERE name = :name AND description = :description AND default_key IS NULL"
            ),
            {"key": key, "name": name, "description": description},
        )


def downgrade() -> None:
    with op.batch_alter_table("categories") as batch:
        batch.drop_column("default_key")
