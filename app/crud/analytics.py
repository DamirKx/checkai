"""Аналитика расходов и облегчённые выборки чеков для сводок (профиль, ИИ-анализ)."""
from types import SimpleNamespace
from typing import List, Optional, Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session
from app import models
from app.categories import UNCATEGORIZED_NAME
from app.currencies import DEFAULT_CURRENCY
from app.crud.receipts import _receipts_order, attach_base_rates
from app.crud.users import get_user_by_id


def _light_receipts(db: Session, user_id: int) -> List[SimpleNamespace]:
    """Чеки пользователя только нужными для сводок колонками, без позиций, с rate_to_base. От новых к старым."""
    R = models.Receipt
    rows = (
        db.query(R.id, R.store, R.merchant, R.date, R.created_at, R.total, R.currency, R.rate_to_kzt, R.category_id)
        .filter(R.user_id == user_id)
        .order_by(*_receipts_order())
    )
    return [SimpleNamespace(**row._asdict()) for row in rows]


def _item_sums(db: Session, user_id: int) -> List[tuple]:
    """(receipt_id, category_id, сумма позиций) — группировка в SQL, а не загрузка всех позиций."""
    I = models.ReceiptItem
    return (
        db.query(I.receipt_id, I.category_id, func.sum(I.total_price))
        .join(models.Receipt)
        .filter(models.Receipt.user_id == user_id)
        .group_by(I.receipt_id, I.category_id)
        .all()
    )


def get_analytics(db: Session, user_id: int, base_currency: Optional[str] = None) -> Dict[str, Any]:
    """Сводка аналитики с пересчётом всех сумм в основную валюту пользователя.

    Чеки без курса не суммируются, а группируются в unconverted. Позиции целиком не загружаются:
    суммы по категориям считает SQL, а для recent_items читаются позиции только последних чеков.
    """
    if not base_currency:
        user = get_user_by_id(db, user_id)
        base_currency = user.base_currency if user else DEFAULT_CURRENCY

    receipts = _light_receipts(db, user_id)
    if not receipts:
        return {
            "total_spent": 0.0,
            "receipts_count": 0,
            "converted_count": 0,
            "avg_receipt": 0.0,
            "top_store": None,
            "by_category": {},
            "by_store": {},
            "by_month": {},
            "recent_items": [],
            "base_currency": base_currency,
            "unconverted": [],
        }

    attach_base_rates(db, receipts, base_currency)
    names = dict(db.query(models.Category.id, models.Category.name).filter(models.Category.user_id == user_id))
    rate_by_id = {r.id: r.rate_to_base for r in receipts}

    def category_name(category_id: Optional[int]) -> str:
        return names.get(category_id) or UNCATEGORIZED_NAME

    total_spent = 0.0
    counted_receipts = 0
    by_store: Dict[str, float] = {}
    by_month: Dict[str, float] = {}
    by_category: Dict[str, float] = {}
    unconverted_counts: Dict[str, int] = {}

    with_items = set()
    for receipt_id, category_id, amount in _item_sums(db, user_id):
        with_items.add(receipt_id)
        rate = rate_by_id.get(receipt_id)
        if rate is not None:
            cat = category_name(category_id)
            by_category[cat] = by_category.get(cat, 0.0) + (amount or 0.0) * rate

    for r in receipts:
        if r.rate_to_base is None:
            cur = r.currency or DEFAULT_CURRENCY
            unconverted_counts[cur] = unconverted_counts.get(cur, 0) + 1
            continue

        rec_total = (r.total or 0.0) * r.rate_to_base
        total_spent += rec_total
        counted_receipts += 1

        store_name = r.store or "Неизвестно"
        by_store[store_name] = by_store.get(store_name, 0.0) + rec_total

        if r.date:
            m = r.date[:7]
            by_month[m] = by_month.get(m, 0.0) + rec_total

        if r.id not in with_items:
            cat = category_name(r.category_id)
            by_category[cat] = by_category.get(cat, 0.0) + rec_total

    top_store = max(by_store, key=by_store.get) if by_store else None

    # Пять последних позиций: у каждого чека с позициями их хотя бы одна, так что хватит пяти чеков.
    # Позиции чеков без курса не показываем: их цены нельзя выразить в основной валюте
    recent_receipts = [r for r in receipts if r.rate_to_base is not None and r.id in with_items][:5]
    recent_items: List[Dict[str, Any]] = []
    if recent_receipts:
        items_by_receipt: Dict[int, List[models.ReceiptItem]] = {}
        for item in (
            db.query(models.ReceiptItem)
            .filter(models.ReceiptItem.receipt_id.in_([r.id for r in recent_receipts]))
            .order_by(models.ReceiptItem.id)
        ):
            items_by_receipt.setdefault(item.receipt_id, []).append(item)
        for r in recent_receipts:
            for item in items_by_receipt.get(r.id, []):
                recent_items.append({
                    "name": item.name,
                    "price": round((item.total_price or 0.0) * r.rate_to_base, 2),
                    "store": r.store,
                })
                if len(recent_items) == 5:
                    break
            if len(recent_items) == 5:
                break

    unconverted = [
        {"currency": cur, "receipts": count}
        for cur, count in sorted(unconverted_counts.items())
    ]

    return {
        "total_spent": round(total_spent, 2),
        "receipts_count": len(receipts),
        "converted_count": counted_receipts,
        "avg_receipt": round(total_spent / counted_receipts, 2) if counted_receipts else 0.0,
        "top_store": top_store,
        "by_category": {k: round(v, 2) for k, v in by_category.items()},
        "by_store": {k: round(v, 2) for k, v in by_store.items()},
        "by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
        "recent_items": recent_items,
        "base_currency": base_currency,
        "unconverted": unconverted,
    }
