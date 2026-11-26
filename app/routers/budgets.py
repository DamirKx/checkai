"""Бюджеты и лимиты расходов (только для авторизованных)."""
import re
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import auth, crud, database, models, schemas
from app.i18n import AppError

router = APIRouter(tags=["Budgets"])

_MONTH_REGEX = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@router.get("/api/budgets", response_model=List[schemas.BudgetOut])
def get_budgets(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Список лимитов текущего пользователя. Если лимитов нет, возвращает пустой список []."""
    return crud.get_user_budgets(db, user_id=current_user.id)


@router.put("/api/budgets", response_model=List[schemas.BudgetOut])
def set_budgets(
    data: schemas.BudgetListUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Установка списка лимитов пользователя в одной транзакции (полная замена списка)."""
    return crud.set_user_budgets(db, user_id=current_user.id, budgets_data=data.budgets)


@router.get("/api/budgets/status", response_model=schemas.BudgetStatusOut)
def get_budgets_status(
    month: Optional[str] = Query(None, description="Месяц в формате YYYY-MM (по умолчанию текущий)"),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Расчёт расходов по бюджетам текущего пользователя за указанный месяц."""
    if not month:
        target_month = datetime.now(timezone.utc).strftime("%Y-%m")
    else:
        target_month = month.strip()
        if not _MONTH_REGEX.match(target_month):
            raise AppError(422, "invalid_month")
    return crud.get_budget_status(db, user_id=current_user.id, month=target_month, base_currency=current_user.base_currency)
