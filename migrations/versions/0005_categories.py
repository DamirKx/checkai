"""Свои категории: таблицы categories и category_rules, category_id вместо строки category

Revision ID: 0005_categories
Revises: 0004_currency
Create Date: 2026-10-30
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0005_categories"
down_revision = "0004_currency"
branch_labels = None
depends_on = None

# Копия стандартного списка на 30.10.2026: миграция не должна зависеть от будущих правок app/categories.py
DEFAULT_CATEGORIES = [
    ("Продукты", "🥕", "Еда и напитки для дома: молочные продукты, хлеб, мясо, овощи, фрукты, крупы, сладости"),
    ("Одежда", "🧥", "Одежда, обувь, аксессуары"),
    ("Дом и быт", "🏠", "Бытовая химия, хозтовары, посуда, мелочи для дома"),
    ("Транспорт", "🚌", "Топливо, такси, проезд, парковка"),
    ("Кафе", "☕", "Готовая еда и напитки вне дома: кафе, рестораны, кофейни, доставка еды"),
    ("Здоровье", "✚", "Аптека, лекарства, витамины, медицинские услуги"),
    ("Развлечения", "🎟", "Кино, театр, концерты, игры, подписки"),
    ("Другое", "◌", "Всё, что не подошло в другие категории"),
]
UNCATEGORIZED = "без категории"
# Таблица → поведение category_id при удалении категории
CATEGORY_FK = {"receipt_items": "SET NULL", "receipts": "SET NULL", "budgets": "CASCADE"}

categories_t = sa.table(
    "categories",
    sa.column("id", sa.Integer), sa.column("user_id", sa.Integer), sa.column("name", sa.String),
    sa.column("description", sa.String), sa.column("icon", sa.String), sa.column("position", sa.Integer),
    sa.column("created_at", sa.DateTime),
)
users_t = sa.table("users", sa.column("id", sa.Integer))
receipts_t = sa.table(
    "receipts", sa.column("id", sa.Integer), sa.column("user_id", sa.Integer),
    sa.column("category", sa.String), sa.column("category_id", sa.Integer),
)
items_t = sa.table(
    "receipt_items", sa.column("id", sa.Integer), sa.column("receipt_id", sa.Integer),
    sa.column("category", sa.String), sa.column("category_id", sa.Integer),
)
budgets_t = sa.table(
    "budgets", sa.column("id", sa.Integer), sa.column("user_id", sa.Integer),
    sa.column("category", sa.String), sa.column("category_id", sa.Integer),
)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean(raw):
    """Строка категории из старых данных → название новой категории или None."""
    name = (raw or "").strip()
    if not name or name.lower() == UNCATEGORIZED:
        return None
    if name.lower() == "прочее":  # так писал сервер, фронт — «Другое»
        return "Другое"
    return name[:50].strip()


def _columns(inspector, table):
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # 1. Новые таблицы
    if "categories" not in tables:
        op.create_table(
            "categories",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(50), nullable=False),
            sa.Column("description", sa.String(300), nullable=True),
            sa.Column("icon", sa.String(8), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_categories_id", "categories", ["id"])
        op.create_index("ix_categories_user_id", "categories", ["user_id"])
    if "category_rules" not in tables:
        op.create_table(
            "category_rules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("pattern", sa.String(200), nullable=False),
            sa.Column(
                "category_id", sa.Integer(),
                sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("user_id", "pattern", name="uq_category_rules_user_pattern"),
        )
        op.create_index("ix_category_rules_id", "category_rules", ["id"])
        op.create_index("ix_category_rules_user_id", "category_rules", ["user_id"])

    # 2. category_id рядом со старой строкой
    for table, ondelete in CATEGORY_FK.items():
        if "category_id" in _columns(inspector, table):
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table}_category_id", "categories", ["category_id"], ["id"], ondelete=ondelete,
            )
            batch_op.create_index(f"ix_{table}_category_id", ["category_id"])

    # 3. Данные: каждому пользователю стандартные категории плюс те, что встречаются в его чеках и бюджетах
    if all("category" in _columns(inspector, t) for t in CATEGORY_FK):
        _migrate_data(bind)

    # 4. Старые строковые колонки больше не нужны
    for table in CATEGORY_FK:
        if "category" in _columns(sa.inspect(bind), table):
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column("category")


def _migrate_data(bind) -> None:
    now = _utcnow()
    user_ids = [row.id for row in bind.execute(sa.select(users_t.c.id))]
    for user_id in user_ids:
        existing = bind.execute(
            sa.select(categories_t.c.id, categories_t.c.name, categories_t.c.position)
            .where(categories_t.c.user_id == user_id)
        ).fetchall()
        by_lower = {row.name.lower(): row.id for row in existing}
        next_position = max((row.position for row in existing), default=-1) + 1

        def ensure(name, icon="◌", description=None):
            nonlocal next_position
            key = name.lower()
            if key not in by_lower:
                by_lower[key] = bind.execute(
                    categories_t.insert().values(
                        user_id=user_id, name=name, description=description, icon=icon,
                        position=next_position, created_at=now,
                    ).returning(categories_t.c.id)
                ).scalar_one()
                next_position += 1
            return by_lower[key]

        for name, icon, description in DEFAULT_CATEGORIES:
            ensure(name, icon, description)

        # Раньше позиция без категории попадала в аналитике в категорию чека — сохраняем это
        item_rows = bind.execute(
            sa.select(items_t.c.id, items_t.c.category, receipts_t.c.category.label("receipt_category"))
            .select_from(items_t.join(receipts_t, items_t.c.receipt_id == receipts_t.c.id))
            .where(receipts_t.c.user_id == user_id)
        ).fetchall()
        raw_items = {}
        for row in item_rows:
            raw = row.category if (row.category or "").strip() else row.receipt_category
            raw_items.setdefault(raw, []).append(row.id)

        raw_receipts = {
            row.category for row in bind.execute(
                sa.select(receipts_t.c.category).where(receipts_t.c.user_id == user_id).distinct()
            )
        }
        budget_rows = bind.execute(
            sa.select(budgets_t.c.id, budgets_t.c.category)
            .where(budgets_t.c.user_id == user_id)
            .order_by(budgets_t.c.id)
        ).fetchall()

        # Свои категории — по алфавиту, после стандартных
        names = {_clean(raw) for raw in (*raw_items, *raw_receipts, *(b.category for b in budget_rows))}
        for name in sorted(n for n in names if n):
            ensure(name)

        def target(raw):
            name = _clean(raw)
            return by_lower[name.lower()] if name else None

        for raw, ids in raw_items.items():
            category_id = target(raw)
            if category_id is not None:
                for chunk in range(0, len(ids), 500):
                    bind.execute(
                        items_t.update().where(items_t.c.id.in_(ids[chunk:chunk + 500])).values(category_id=category_id)
                    )
        for raw in raw_receipts:
            category_id = target(raw)
            if category_id is not None:
                bind.execute(
                    receipts_t.update()
                    .where(receipts_t.c.user_id == user_id, receipts_t.c.category == raw)
                    .values(category_id=category_id)
                )

        seen = set()
        for b in budget_rows:
            category_id = target(b.category)
            # Лимит, который не удалось сопоставить, иначе молча стал бы общим (category_id = NULL)
            unmapped = b.category is not None and category_id is None
            # «Прочее» и «Другое» сливаются в одну категорию — оставляем первый лимит
            if unmapped or category_id in seen:
                bind.execute(budgets_t.delete().where(budgets_t.c.id == b.id))
                continue
            seen.add(category_id)
            bind.execute(budgets_t.update().where(budgets_t.c.id == b.id).values(category_id=category_id))
    # Позиции и чеки без владельца (до появления аккаунтов) остаются без категории


def downgrade() -> None:
    bind = op.get_bind()
    for table in CATEGORY_FK:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("category", sa.String(100), nullable=True))

    for table in (items_t, receipts_t, budgets_t):
        name = (
            sa.select(categories_t.c.name)
            .where(categories_t.c.id == table.c.category_id)
            .scalar_subquery()
        )
        bind.execute(table.update().where(table.c.category_id.isnot(None)).values(category=name))

    for table in CATEGORY_FK:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_category_id")
            batch_op.drop_constraint(f"fk_{table}_category_id", type_="foreignkey")
            batch_op.drop_column("category_id")

    op.drop_table("category_rules")
    op.drop_table("categories")
