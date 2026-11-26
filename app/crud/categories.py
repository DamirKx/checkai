"""Категории пользователя: создание, правка, удаление, порядок, перенос товаров, запомненные правила."""
from typing import List, Optional, Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session
from app import models
from app import schemas
from app.categories import (
    CategoryError, DEFAULT_ICON, MAX_CATEGORIES, find_category_by_name, get_user_category,
    main_category_id, normalize_item_name,
)
from app.i18n import CategoryName


def get_categories(db: Session, user_id: int) -> List[models.Category]:
    return (
        db.query(models.Category)
        .filter(models.Category.user_id == user_id)
        .order_by(models.Category.position, models.Category.id)
        .all()
    )


def _check_name_free(db: Session, user_id: int, name: str, exclude_id: Optional[int] = None) -> None:
    existing = find_category_by_name(db, user_id, name)
    if existing is not None and existing.id != exclude_id:
        raise CategoryError(409, "category_exists", name=CategoryName(existing.name, existing.default_key))


def create_category(db: Session, user_id: int, data: schemas.CategoryCreate) -> models.Category:
    count = db.query(func.count(models.Category.id)).filter(models.Category.user_id == user_id).scalar() or 0
    if count >= MAX_CATEGORIES:
        raise CategoryError(422, "categories_limit", max=MAX_CATEGORIES)
    _check_name_free(db, user_id, data.name)

    last = db.query(func.max(models.Category.position)).filter(models.Category.user_id == user_id).scalar()
    category = models.Category(
        user_id=user_id,
        name=data.name,
        description=data.description,
        icon=data.icon or DEFAULT_ICON,
        position=(last + 1) if last is not None else 0,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def update_category(db: Session, category: models.Category, data: schemas.CategoryUpdate) -> models.Category:
    """Меняет только поля, пришедшие в запросе. Позиции ссылаются по id — новое название видно сразу везде."""
    fields = data.model_fields_set
    # Новое название или описание — категория больше не стандартная, перевода у неё нет.
    # Иконка и порядок, а также то же самое значение ключ не сбрасывают
    if "name" in fields and data.name != category.name:
        category.default_key = None
    if "description" in fields and data.description != category.description:
        category.default_key = None
    if "name" in fields:
        _check_name_free(db, category.user_id, data.name, exclude_id=category.id)
        category.name = data.name
    if "description" in fields:
        category.description = data.description
    if "icon" in fields:
        category.icon = data.icon or DEFAULT_ICON
    db.commit()
    db.refresh(category)
    return category


def _recalc_receipt_categories(receipts: List[models.Receipt]) -> None:
    """Категория чека после переноса позиций. У чека без позиций она не меняется."""
    for receipt in receipts:
        if receipt.items:
            receipt.category_id = main_category_id(receipt.items)


def delete_category(
    db: Session, user_id: int, category: models.Category, move_to: Optional[int] = None
) -> Dict[str, Any]:
    """Удаляет категорию в одной транзакции.

    С move_to позиции, чеки и правила переходят в неё; без — позиции и чеки остаются
    без категории, правила удаляются. Лимит бюджета удаляется в любом случае.
    """
    if move_to is not None:
        if move_to == category.id:
            raise CategoryError(422, "move_to_deleted_category")
        if get_user_category(db, user_id, move_to) is None:
            raise CategoryError(422, "category_not_found")

    try:
        items = (
            db.query(models.ReceiptItem)
            .join(models.Receipt)
            .filter(models.Receipt.user_id == user_id, models.ReceiptItem.category_id == category.id)
            .all()
        )
        for item in items:
            item.category_id = move_to
            # Перенос при удалении категории выбрал пользователь; без move_to категории больше нет
            item.category_source = "user" if move_to is not None else None
        affected = {item.receipt_id for item in items}

        for receipt in (
            db.query(models.Receipt)
            .filter(models.Receipt.user_id == user_id, models.Receipt.category_id == category.id)
            .all()
        ):
            receipt.category_id = move_to
            affected.add(receipt.id)

        rules = db.query(models.CategoryRule).filter(
            models.CategoryRule.user_id == user_id, models.CategoryRule.category_id == category.id
        )
        if move_to is not None:
            rules.update({models.CategoryRule.category_id: move_to}, synchronize_session=False)
        else:
            rules.delete(synchronize_session=False)

        deleted_budget = db.query(models.Budget).filter(
            models.Budget.user_id == user_id, models.Budget.category_id == category.id
        ).delete(synchronize_session=False) > 0

        db.flush()
        if affected:
            _recalc_receipt_categories(
                db.query(models.Receipt).filter(models.Receipt.id.in_(affected)).all()
            )
        db.delete(category)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"success": True, "moved_items": len(items), "deleted_budget": deleted_budget}


def reorder_categories(db: Session, user_id: int, ids: List[int]) -> List[models.Category]:
    categories = get_categories(db, user_id)
    if len(ids) != len(set(ids)) or set(ids) != {c.id for c in categories}:
        raise CategoryError(422, "categories_order_invalid")
    by_id = {c.id: c for c in categories}
    for position, category_id in enumerate(ids):
        by_id[category_id].position = position
    db.commit()
    return get_categories(db, user_id)


def move_items(
    db: Session, user_id: int, item_ids: List[int], category_id: Optional[int], remember: bool
) -> Dict[str, int]:
    """Переносит позиции в категорию (None — «Без категории») в одной транзакции.

    Хотя бы одна позиция чужая или не существует — 404, не меняется ничего.
    remember — для каждого названия создать или обновить правило; rules_created — сколько
    правил создано или перенаправлено.
    """
    ids = set(item_ids)
    if category_id is not None and get_user_category(db, user_id, category_id) is None:
        raise CategoryError(422, "category_not_found")
    items = (
        db.query(models.ReceiptItem)
        .join(models.Receipt)
        .filter(models.Receipt.user_id == user_id, models.ReceiptItem.id.in_(ids))
        .all()
    )
    if len(items) != len(ids):
        raise CategoryError(404, "item_not_found")

    try:
        for item in items:
            item.category_id = category_id
            item.category_source = "user" if category_id is not None else None

        rules_saved = 0
        if remember and category_id is not None:
            patterns = {normalize_item_name(item.name) for item in items} - {""}
            existing = {
                rule.pattern: rule
                for rule in db.query(models.CategoryRule).filter(
                    models.CategoryRule.user_id == user_id, models.CategoryRule.pattern.in_(patterns)
                )
            }
            for pattern in patterns:
                rule = existing.get(pattern)
                if rule is None:
                    db.add(models.CategoryRule(user_id=user_id, pattern=pattern, category_id=category_id))
                    rules_saved += 1
                elif rule.category_id != category_id:
                    rule.category_id = category_id
                    rules_saved += 1

        db.flush()
        _recalc_receipt_categories(
            db.query(models.Receipt).filter(models.Receipt.id.in_({item.receipt_id for item in items})).all()
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"moved": len(items), "rules_created": rules_saved}


def get_rules(db: Session, user_id: int) -> List[Dict[str, Any]]:
    rows = (
        db.query(models.CategoryRule, models.Category.name)
        .join(models.Category, models.CategoryRule.category_id == models.Category.id)
        .filter(models.CategoryRule.user_id == user_id)
        .order_by(models.CategoryRule.pattern)
        .all()
    )
    return [
        {
            "id": rule.id,
            "pattern": rule.pattern,
            "category_id": rule.category_id,
            "category": name,
            "created_at": rule.created_at,
        }
        for rule, name in rows
    ]


def delete_rule(db: Session, user_id: int, rule_id: int) -> bool:
    deleted = db.query(models.CategoryRule).filter(
        models.CategoryRule.id == rule_id, models.CategoryRule.user_id == user_id
    ).delete(synchronize_session=False)
    db.commit()
    return deleted > 0
