"""Категории, перенос позиций, правила «товар → категория» и автокатегория для названий без фото.

Всё — только для авторизованных, кроме стандартного списка для гостя.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import auth, crud, database, models, ocr, schemas
from app.categories import DEFAULT_CATEGORIES, apply_rules
from app.i18n import AppError, Lang
from app.rate_limit import CATEGORIZE_RATE, limiter

logger = logging.getLogger("checkai")

router = APIRouter(tags=["Categories"])


def _own_category(db: Session, user: models.User, category_id: int) -> models.Category:
    category = crud.get_user_category(db, user.id, category_id)
    if category is None:
        raise AppError(404, "category_not_found")
    return category


@router.get("/api/categories", response_model=List[schemas.CategoryOut])
def list_categories(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Категории пользователя в порядке position."""
    return crud.get_categories(db, current_user.id)


@router.get("/api/categories/defaults", response_model=List[schemas.CategoryDefaultOut])
def list_default_categories():
    """Стандартные категории — для гостя (без авторизации). name и description — русские, перевод — по default_key."""
    return [{**d, "default_key": d["key"]} for d in DEFAULT_CATEGORIES]


@router.post("/api/categories", response_model=schemas.CategoryOut, status_code=201)
def create_category(
    data: schemas.CategoryCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Новая категория в конце списка. Повтор названия без учёта регистра — 409."""
    return crud.create_category(db, current_user.id, data)


@router.put("/api/categories/order", response_model=List[schemas.CategoryOut])
def reorder_categories(
    data: schemas.CategoryOrder,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Новый порядок: все id пользователя, каждый один раз."""
    return crud.reorder_categories(db, current_user.id, data.ids)


@router.patch("/api/categories/{category_id}", response_model=schemas.CategoryOut)
def update_category(
    category_id: int,
    data: schemas.CategoryUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Переименование, иконка, описание — любой, в том числе стандартной категории."""
    category = _own_category(db, current_user, category_id)
    return crud.update_category(db, category, data)


@router.delete("/api/categories/{category_id}", response_model=schemas.CategoryDeleteOut)
def delete_category(
    category_id: int,
    move_to: Optional[int] = Query(None, description="Куда перенести позиции, чеки и правила"),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Удаление категории. Без move_to позиции остаются без категории. Лимит бюджета удаляется всегда."""
    category = _own_category(db, current_user, category_id)
    return crud.delete_category(db, current_user.id, category, move_to)


@router.post("/api/items/move", response_model=schemas.ItemsMoveOut)
def move_items(
    data: schemas.ItemsMove,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Перенос позиций в категорию (null — «Без категории»); remember — запомнить правило."""
    return crud.move_items(db, current_user.id, data.item_ids, data.category_id, data.remember)


@router.get("/api/category-rules", response_model=List[schemas.CategoryRuleOut])
def list_category_rules(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Запомненные правила «товар → категория»."""
    return crud.get_rules(db, current_user.id)


@router.delete("/api/category-rules/{rule_id}")
def delete_category_rule(
    rule_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Удаление своего правила (чужое — 404)."""
    if not crud.delete_rule(db, current_user.id, rule_id):
        raise AppError(404, "rule_not_found")
    return {"success": True}


# --- Автокатегория для товаров, вписанных руками ---

@router.post("/api/categorize", response_model=schemas.CategorizeOut)
def categorize(
    data: schemas.CategorizeIn,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
    lang: str = Lang,
):
    """Категории для названий без фото: правила пользователя, затем ML (ИИ или ключевые слова).

    Ответ — по элементу на каждое название в том же порядке; не нашли — category_id, category
    и category_source равны null. ИИ недоступен — то, что нашлось по правилам и ключевым словам, а не 500.
    """
    allowed, retry_after = limiter.check_limit(f"categorize:{current_user.id}", *CATEGORIZE_RATE)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content=AppError(429, "too_many_requests").body(lang),
            headers={"Retry-After": str(retry_after)},
        )
    items = [
        {"name": it.name.strip(), "category_id": None, "category": None, "category_source": None}
        for it in data.items
    ]
    named = [it for it in items if it["name"]]
    apply_rules(db, current_user.id, named)
    rest = [it for it in named if it["category_source"] != "rule"]
    if rest:
        try:
            from ml.categorize import categorize_items
            categorize_items(rest, ocr.categories_for(current_user))
        except Exception as e:
            logger.warning("Автокатегория не сработала (%s): %s", type(e).__name__, e)
            for it in rest:
                it.update(category_id=None, category=None, category_source=None)
    return {"items": items}
