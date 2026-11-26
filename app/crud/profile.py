"""Сводка профиля: цифры, активность по дням, привычки."""
from datetime import datetime, timedelta, timezone, tzinfo
from typing import List, Optional, Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session
from app import models
from app.currencies import DEFAULT_CURRENCY
from app.crud.analytics import _item_sums, _light_receipts
from app.crud.receipts import attach_base_rates, canonical_merchant


ACTIVITY_DAYS = 182  # 26 недель


def _stats_day(r: Any, tz: tzinfo) -> str:
    """Дата чека для профиля: дата покупки, без неё — created_at в часовом поясе пользователя."""
    if r.date:
        return r.date[:10]
    return r.created_at.replace(tzinfo=timezone.utc).astimezone(tz).strftime("%Y-%m-%d")


def _month_shift(month: str, delta: int) -> str:
    year, mon = int(month[:4]), int(month[5:7]) - 1 + delta
    return f"{year + mon // 12:04d}-{mon % 12 + 1:02d}"


def get_profile_stats(db: Session, user: models.User, tz: tzinfo) -> Dict[str, Any]:
    """Цифры, активность и привычки для профиля одним запросом.

    Чеки читаются только нужными колонками (без позиций), суммы позиций по категориям считает SQL.
    Пересчёт в основную валюту — attach_base_rates, как в /api/analytics; чеки без курса не входят
    в суммы и перечисляются в unconverted.
    """
    base = user.base_currency or DEFAULT_CURRENCY
    receipts = _light_receipts(db, user.id)
    attach_base_rates(db, receipts, base)

    items_count = (
        db.query(func.count(models.ReceiptItem.id)).join(models.Receipt)
        .filter(models.Receipt.user_id == user.id).scalar() or 0
    )
    item_sums = _item_sums(db, user.id)

    today = datetime.now(tz).date()
    window_start = (today - timedelta(days=ACTIVITY_DAYS - 1)).isoformat()
    today_iso = today.isoformat()
    month = today_iso[:7]
    prev_month = _month_shift(month, -1)

    total_spent = 0.0
    converted = 0
    month_total = prev_month_total = 0.0
    unconverted: Dict[str, int] = {}
    activity: Dict[str, Dict[str, Any]] = {}
    stores: Dict[str, Dict[str, Any]] = {}
    days: List[str] = []
    biggest = None
    rate_by_id: Dict[int, Optional[float]] = {}

    for r in receipts:
        rate_by_id[r.id] = r.rate_to_base
        day = _stats_day(r, tz)
        days.append(day)
        value = (r.total or 0.0) * r.rate_to_base if r.rate_to_base is not None else None

        if value is None:
            cur = r.currency or DEFAULT_CURRENCY
            unconverted[cur] = unconverted.get(cur, 0) + 1
        else:
            total_spent += value
            converted += 1
            if day[:7] == month:
                month_total += value
            elif day[:7] == prev_month:
                prev_month_total += value
            if value > 0 and (biggest is None or value > biggest[1]):
                biggest = (r, value)

        if window_start <= day <= today_iso:
            cell = activity.setdefault(day, {"date": day, "count": 0, "total": 0.0})
            cell["count"] += 1
            cell["total"] += value or 0.0

        # Без учёта регистра и пробелов по краям; сеть (merchant) важнее строки с чека.
        # У чеков, сохранённых до появления merchant, имя сети вычисляется на лету.
        # Чеки идут от новых к старым — показываем самое свежее написание
        name = (r.merchant or canonical_merchant(r.store) or r.store or "").strip()
        if name:
            entry = stores.setdefault(name.lower(), {"name": name, "count": 0})
            entry["count"] += 1

    # Категории: позиции — по сумме позиций, чек без позиций — целиком по своей категории (как в аналитике)
    by_category: Dict[Optional[int], float] = {}
    with_items = set()
    for receipt_id, category_id, amount in item_sums:
        with_items.add(receipt_id)
        rate = rate_by_id.get(receipt_id)
        if rate is not None:
            by_category[category_id] = by_category.get(category_id, 0.0) + (amount or 0.0) * rate
    for r in receipts:
        if r.id not in with_items and r.rate_to_base is not None:
            by_category[r.category_id] = by_category.get(r.category_id, 0.0) + (r.total or 0.0) * r.rate_to_base
    # «Без категории» в топ не попадает
    top_category = max(
        ((cid, s) for cid, s in by_category.items() if cid is not None and s > 0),
        key=lambda pair: pair[1], default=None,
    )

    # Рекорд подряд и любимый день недели — за то же окно, что и тепловая карта
    longest = run = 0
    weekdays = [0] * 7
    day_cursor = today - timedelta(days=ACTIVITY_DAYS - 1)
    while day_cursor <= today:
        count = activity.get(day_cursor.isoformat(), {}).get("count", 0)
        run = run + 1 if count else 0
        longest = max(longest, run)
        # 0 = воскресенье … 6 = суббота, как Date.getDay() в JS
        weekdays[(day_cursor.weekday() + 1) % 7] += count
        day_cursor += timedelta(days=1)
    top_weekday = weekdays.index(max(weekdays)) if any(weekdays) else None

    top_store = max(stores.values(), key=lambda s: s["count"], default=None)

    return {
        "base_currency": base,
        "receipts_count": len(receipts),
        "items_count": items_count,
        "total_spent": round(total_spent, 2),
        "average_receipt": round(total_spent / converted, 2) if converted else 0.0,
        "unconverted": [{"currency": c, "count": n} for c, n in sorted(unconverted.items())],
        "first_receipt_at": min(days) if days else None,
        "last_receipt_at": max(days) if days else None,
        "activity": [
            {**cell, "total": round(cell["total"], 2)} for _, cell in sorted(activity.items())
        ],
        "longest_streak": longest,
        "top_weekday": top_weekday,
        "top_store": top_store,
        "top_category": {"id": top_category[0], "total": round(top_category[1], 2)} if top_category else None,
        "biggest_receipt": (
            {"id": biggest[0].id, "total": round(biggest[1], 2), "store": biggest[0].merchant or biggest[0].store}
            if biggest else None
        ),
        "month_total": round(month_total, 2),
        "prev_month_total": round(prev_month_total, 2),
    }
