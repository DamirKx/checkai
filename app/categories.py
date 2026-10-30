"""Категории расходов пользователя: стандартный набор, нормализация названий товаров, правила.

Контракт — CATEGORIES_TASKS.md, раздел 0. Фронт и ML берут стандартный список из API,
а normalize_item_name импортируют отсюда, а не копируют.
"""
import re
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models

UNCATEGORIZED_NAME = "Без категории"
DEFAULT_ICON = "◌"
MAX_CATEGORIES = 50
NAME_MAX_LENGTH = 50
DESCRIPTION_MAX_LENGTH = 300
ICON_MAX_LENGTH = 8

# Порядок списка = position. Описание — не только для человека: по нему ML раскладывает товары.
DEFAULT_CATEGORIES: List[Dict[str, str]] = [
    {"name": "Продукты", "icon": "🥕",
     "description": "Еда и напитки для дома: молочные продукты, хлеб, мясо, овощи, фрукты, крупы, сладости"},
    {"name": "Одежда", "icon": "🧥", "description": "Одежда, обувь, аксессуары"},
    {"name": "Дом и быт", "icon": "🏠", "description": "Бытовая химия, хозтовары, посуда, мелочи для дома"},
    {"name": "Транспорт", "icon": "🚌", "description": "Топливо, такси, проезд, парковка"},
    {"name": "Кафе", "icon": "☕",
     "description": "Готовая еда и напитки вне дома: кафе, рестораны, кофейни, доставка еды"},
    {"name": "Здоровье", "icon": "✚", "description": "Аптека, лекарства, витамины, медицинские услуги"},
    {"name": "Развлечения", "icon": "🎟", "description": "Кино, театр, концерты, игры, подписки"},
    {"name": "Другое", "icon": "◌", "description": "Всё, что не подошло в другие категории"},
]


class CategoryError(Exception):
    """Ошибка операции с категориями: server.py превращает её в ответ {"detail": ...} с этим кодом."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def normalize_item_name(name: str) -> str:
    """'Крем NIVEA 50мл' → 'крем nivea'. Пустая строка — правило не создаётся."""
    s = str(name or "").lower().replace("ё", "е")
    # числа с единицами; \b — чтобы «2 груши» не превратилось в «уши»
    s = re.sub(r"\d+(?:[.,]\d+)?\s*(?:(?:кг|гр|г|мл|л|шт|уп)\b|%)?", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)                                     # знаки препинания
    s = re.sub(r"\s+", " ", s).strip()
    return s[:200]


def create_default_categories(db: Session, user: models.User) -> None:
    """Добавляет пользователю стандартные категории. Коммит — за вызывающим."""
    for position, d in enumerate(DEFAULT_CATEGORIES):
        user.categories.append(models.Category(
            name=d["name"], description=d["description"], icon=d["icon"], position=position,
        ))


def get_user_category(db: Session, user_id: int, category_id: int) -> Optional[models.Category]:
    return (
        db.query(models.Category)
        .filter(models.Category.id == category_id, models.Category.user_id == user_id)
        .first()
    )


def find_category_by_name(db: Session, user_id: int, name: str) -> Optional[models.Category]:
    """Поиск без учёта регистра; в SQLite lower() подменена на версию, понимающую кириллицу."""
    return (
        db.query(models.Category)
        .filter(models.Category.user_id == user_id, func.lower(models.Category.name) == name.strip().lower())
        .first()
    )


def resolve_category_id(
    db: Session, user_id: int, category_id: Optional[int], name: Optional[str]
) -> Optional[int]:
    """Категория позиции при сохранении чека: сначала id, иначе название (гость вошёл и сохранил).

    Чужой или несуществующий id — ошибка 422. Название, которого у пользователя нет, — None:
    новую категорию молча не создаём.
    """
    if category_id is not None:
        if get_user_category(db, user_id, category_id) is None:
            raise CategoryError(422, "Категория не найдена")
        return category_id
    if name and name.strip():
        category = find_category_by_name(db, user_id, name)
        return category.id if category else None
    return None


def main_category_id(items: Iterable[Any], fallback_id: Optional[int] = None) -> Optional[int]:
    """Категория чека: категория самой дорогой позиции, при равенстве — первой из них.

    Позиции «Без категории» участвуют как None. Без позиций — fallback_id.
    """
    best = None
    for item in items:
        if best is None or (item.total_price or 0.0) > (best.total_price or 0.0):
            best = item
    return best.category_id if best is not None else fallback_id


def apply_rules(db: Session, user_id: int, items: List[Dict[str, Any]]) -> None:
    """Правила пользователя важнее ML: проставляет category_id, category и category_source = "rule"."""
    patterns = {normalize_item_name(it.get("name")) for it in items} - {""}
    if not patterns:
        return
    rules = (
        db.query(models.CategoryRule, models.Category)
        .join(models.Category, models.CategoryRule.category_id == models.Category.id)
        .filter(models.CategoryRule.user_id == user_id, models.CategoryRule.pattern.in_(patterns))
        .all()
    )
    by_pattern = {rule.pattern: category for rule, category in rules}
    for it in items:
        category = by_pattern.get(normalize_item_name(it.get("name")))
        if category is not None:
            it["category_id"] = category.id
            it["category"] = category.name
            it["category_source"] = "rule"
