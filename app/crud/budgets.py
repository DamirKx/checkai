"""Лимиты расходов на месяц и их выполнение."""
from typing import List, Optional, Dict
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload
from app import models
from app import schemas
from app.categories import CategoryError, get_user_category
from app.currencies import DEFAULT_CURRENCY
from app.crud.receipts import attach_base_rates
from app.crud.users import get_user_by_id


def get_user_budgets(db: Session, user_id: int) -> List[models.Budget]:
    """Лимиты пользователя: общий первым, затем в порядке категорий (position)."""
    return (
        db.query(models.Budget)
        .outerjoin(models.Category, models.Budget.category_id == models.Category.id)
        .options(joinedload(models.Budget.category_rel))
        .filter(models.Budget.user_id == user_id)
        .order_by(models.Budget.category_id.isnot(None), models.Category.position, models.Budget.id)
        .all()
    )


def set_user_budgets(db: Session, user_id: int, budgets_data: List[schemas.BudgetItem]) -> List[models.Budget]:
    """Заменяет весь список лимитов пользователя в одной транзакции. Чужая категория — CategoryError(422)."""
    for item in budgets_data:
        if item.category_id is not None and get_user_category(db, user_id, item.category_id) is None:
            raise CategoryError(422, "category_not_found")

    db.query(models.Budget).filter(models.Budget.user_id == user_id).delete(synchronize_session=False)

    new_budgets = [
        models.Budget(
            user_id=user_id,
            category_id=item.category_id,
            limit=item.limit,
        )
        for item in budgets_data
    ]
    if new_budgets:
        db.add_all(new_budgets)

    db.commit()
    return get_user_budgets(db, user_id)


def get_budget_status(
    db: Session,
    user_id: int,
    month: str,
    base_currency: Optional[str] = None,
) -> schemas.BudgetStatusOut:
    """Расчёт фактических расходов за месяц month (YYYY-MM) в сравнении с бюджетами в основной валюте."""
    if not base_currency:
        user = get_user_by_id(db, user_id)
        base_currency = user.base_currency if user else DEFAULT_CURRENCY

    budgets = get_user_budgets(db, user_id)

    receipts = (
        db.query(models.Receipt)
        .options(selectinload(models.Receipt.items))
        .filter(
            models.Receipt.user_id == user_id,
            models.Receipt.date.isnot(None),
            func.substr(models.Receipt.date, 1, 7) == month,
        )
        .all()
    )

    attach_base_rates(db, receipts, base_currency)

    total_spent = 0.0
    spending: Dict[Optional[int], float] = {}
    unconverted_counts: Dict[str, int] = {}

    for r in receipts:
        if r.rate_to_base is None:
            cur = r.currency or DEFAULT_CURRENCY
            unconverted_counts[cur] = unconverted_counts.get(cur, 0) + 1
            continue

        rate = r.rate_to_base
        rec_total = (r.total or 0.0) * rate
        total_spent += rec_total

        if r.items:
            for item in r.items:
                spending[item.category_id] = spending.get(item.category_id, 0.0) + (item.total_price or 0.0) * rate
        else:
            spending[r.category_id] = spending.get(r.category_id, 0.0) + rec_total

    general_budget = next((b for b in budgets if b.category_id is None), None)
    total_budget_limit = general_budget.limit if general_budget else None

    status_items = []
    for b in budgets:
        if b.category_id is None:
            spent = round(total_spent, 2)
        else:
            spent = round(spending.get(b.category_id, 0.0), 2)

        remaining = round(b.limit - spent, 2)
        percent = round((spent / b.limit) * 100, 1) if b.limit > 0 else 0.0
        is_over = spent > b.limit

        status_items.append(
            schemas.BudgetStatusItem(
                category_id=b.category_id,
                category=b.category,
                limit=round(b.limit, 2),
                spent=spent,
                remaining=remaining,
                percent=percent,
                percentage=percent,
                is_over_budget=is_over,
            )
        )

    unconverted = [
        {"currency": cur, "receipts": count}
        for cur, count in sorted(unconverted_counts.items())
    ]

    return schemas.BudgetStatusOut(
        month=month,
        total_budget=round(total_budget_limit, 2) if total_budget_limit is not None else None,
        total_spent=round(total_spent, 2),
        budgets=status_items,
        base_currency=base_currency,
        unconverted=unconverted,
    )
