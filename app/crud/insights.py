"""ИИ-рекомендации: сводка расходов для модели, её хеш и сохранённые анализы."""
import hashlib
import json
from datetime import date, datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from app import models
from app import rates
from app.currencies import DEFAULT_CURRENCY
from app.i18n import category_display_name
from app.crud.analytics import _item_sums, _light_receipts
from app.crud.budgets import get_user_budgets
from app.crud.categories import get_categories
from app.crud.profile import _month_shift
from app.crud.receipts import attach_base_rates, canonical_merchant


SNAPSHOT_MONTHS = 6
# С каким числом прошлых месяцев сравнивается текущий
SNAPSHOT_PREV_MONTHS = 3
SNAPSHOT_TOP_STORES = 5
SNAPSHOT_SMALL_GROUPS = 5
# «Мелкая покупка» — чек дешевле этой суммы в тенге (детектор frequent_small)
SMALL_PURCHASE_KZT = 2000
# Названия магазинов приходят из OCR или от пользователя — в сводку только обрезанными
SNAPSHOT_NAME_MAX = 60


def _snapshot_day(r: Any) -> str:
    """День чека для сводки: дата покупки, без неё — день сохранения (UTC)."""
    return r.date[:10] if r.date else r.created_at.strftime("%Y-%m-%d")


def _store_name(r: Any) -> str:
    name = (r.merchant or canonical_merchant(r.store) or r.store or "").strip()
    return name[:SNAPSHOT_NAME_MAX]


def build_finance_snapshot(
    db: Session, user: models.User, months: int = SNAPSHOT_MONTHS, today: Optional[date] = None,
    lang: Optional[str] = None,
) -> Dict[str, Any]:
    """Обезличенная сводка расходов для ИИ: только агрегаты в основной валюте, без email, имени и id пользователя.

    Пересчёт валют — attach_base_rates, как в get_analytics; чеки без курса только перечисляются в unconverted.
    С lang в сводке есть language, а у категорий — display_name: название на этом языке, им ML
    пишет текст. name остаётся русским, как в базе.
    """
    base = user.base_currency or DEFAULT_CURRENCY
    today = today or datetime.now(timezone.utc).date()
    month = today.isoformat()[:7]
    window = [_month_shift(month, -i) for i in range(months - 1, -1, -1)]
    window_set = set(window)
    prev = [_month_shift(month, -i) for i in range(1, SNAPSHOT_PREV_MONTHS + 1)]

    receipts = [r for r in _light_receipts(db, user.id) if _snapshot_day(r)[:7] in window_set]
    attach_base_rates(db, receipts, base)
    by_id = {r.id: r for r in receipts}

    month_totals = {m: {"month": m, "total": 0.0, "receipts": 0} for m in window}
    unconverted: Dict[str, int] = {}
    stores: Dict[str, Dict[str, Any]] = {}
    # Мелкие покупки этого месяца по (категория чека, магазин)
    kzt_per_base = 1.0 if base == "KZT" else rates.rate_to_kzt(db, base, today.isoformat(), fetch=False)
    small_limit = SMALL_PURCHASE_KZT / kzt_per_base if kzt_per_base else None
    small: Dict[tuple, Dict[str, Any]] = {}
    for r in receipts:
        if r.rate_to_base is None:
            cur = r.currency or DEFAULT_CURRENCY
            unconverted[cur] = unconverted.get(cur, 0) + 1
            continue
        value = (r.total or 0.0) * r.rate_to_base
        m = _snapshot_day(r)[:7]
        month_totals[m]["total"] += value
        month_totals[m]["receipts"] += 1
        name = _store_name(r)
        if name:
            entry = stores.setdefault(name.lower(), {"name": name, "total": 0.0, "count": 0})
            entry["total"] += value
            entry["count"] += 1
        if m == month and small_limit is not None and 0 < value < small_limit:
            group = small.setdefault((r.category_id, name), {
                "category_id": r.category_id, "store": name or None, "count": 0, "total": 0.0,
            })
            group["count"] += 1
            group["total"] += value

    # Траты по категориям и месяцам: позиции — по сумме позиций, чек без позиций — целиком (как в аналитике)
    spent: Dict[tuple, float] = {}
    receipts_in_category: Dict[Optional[int], set] = {}
    with_items = set()

    def add(category_id: Optional[int], r: Any, amount: float) -> None:
        m = _snapshot_day(r)[:7]
        spent[(category_id, m)] = spent.get((category_id, m), 0.0) + amount
        if m == month:
            receipts_in_category.setdefault(category_id, set()).add(r.id)

    for receipt_id, category_id, amount in _item_sums(db, user.id):
        r = by_id.get(receipt_id)
        if r is None:
            continue
        with_items.add(receipt_id)
        if r.rate_to_base is not None:
            add(category_id, r, (amount or 0.0) * r.rate_to_base)
    for r in receipts:
        if r.id not in with_items and r.rate_to_base is not None:
            add(r.category_id, r, (r.total or 0.0) * r.rate_to_base)

    limits = {b.category_id: b.limit for b in get_user_budgets(db, user.id)}
    categories = []
    for c in get_categories(db, user.id):
        row = {
            "id": c.id,
            "name": c.name,
            "this_month": round(spent.get((c.id, month), 0.0), 2),
            "prev_months": [round(spent.get((c.id, m), 0.0), 2) for m in prev],
            "limit": limits.get(c.id),
            "receipts": len(receipts_in_category.get(c.id, ())),
        }
        if lang is not None:
            row["display_name"] = category_display_name(c.default_key, c.name, lang)
        # Категории без трат за полгода и без лимита ИИ не нужны
        if row["limit"] is not None or any(v for (cid, _), v in spent.items() if cid == c.id):
            categories.append(row)

    categorized_total = sum(spent.values())
    uncategorized = sum(v for (cid, _), v in spent.items() if cid is None)

    snapshot = {
        "base_currency": base,
        "today": today.isoformat(),
        "months": [{**m, "total": round(m["total"], 2)} for m in month_totals.values()],
        "categories": categories,
        "total_limit": limits.get(None),
        "top_stores": [
            {**s, "total": round(s["total"], 2)}
            for s in sorted(stores.values(), key=lambda s: s["total"], reverse=True)[:SNAPSHOT_TOP_STORES]
        ],
        "small_purchases": [
            {**g, "total": round(g["total"], 2)}
            for g in sorted(small.values(), key=lambda g: g["count"], reverse=True)[:SNAPSHOT_SMALL_GROUPS]
        ],
        "uncategorized_share": round(uncategorized / categorized_total, 3) if categorized_total > 0 else 0.0,
        "unconverted": [{"currency": c, "count": n} for c, n in sorted(unconverted.items())],
    }
    if lang is not None:
        snapshot["language"] = lang
    return snapshot


def snapshot_hash(snapshot: Dict[str, Any]) -> str:
    """Хеш сводки без точного дня: в пределах месяца те же данные — тот же анализ.

    Язык в хеш не входит: анализ сравнивается только с анализом на том же языке (get_latest_insight).
    display_name входит, только если отличается от name, — поэтому у русских анализов хеш тот же,
    что до перевода, и после выкладки они не становятся stale.
    """
    stable = {k: v for k, v in snapshot.items() if k != "language"}
    stable["today"] = str(snapshot.get("today", ""))[:7]
    if "categories" in snapshot:
        stable["categories"] = [
            {k: v for k, v in c.items() if not (k == "display_name" and v == c.get("name"))}
            for c in snapshot["categories"] or []
        ]
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# --- Сохранённые анализы ---

INSIGHTS_KEEP = 3


def get_latest_insight(db: Session, user_id: int, language: str = "ru") -> Optional[models.Insight]:
    """Последний анализ на языке language."""
    return (
        db.query(models.Insight)
        .filter(models.Insight.user_id == user_id, models.Insight.language == language)
        .order_by(models.Insight.created_at.desc(), models.Insight.id.desc())
        .first()
    )


def save_insight(
    db: Session, user_id: int, model: str, data: Dict[str, Any], digest: str, language: str = "ru",
) -> models.Insight:
    """Новый анализ; на каждом языке хранятся последние INSIGHTS_KEEP, старше удаляются."""
    insight = models.Insight(user_id=user_id, model=model[:100], data=data, snapshot_hash=digest, language=language)
    db.add(insight)
    db.flush()
    old = (
        db.query(models.Insight.id)
        .filter(models.Insight.user_id == user_id, models.Insight.language == language)
        .order_by(models.Insight.created_at.desc(), models.Insight.id.desc())
        .offset(INSIGHTS_KEEP)
        .all()
    )
    if old:
        db.query(models.Insight).filter(models.Insight.id.in_([row.id for row in old])).delete(synchronize_session=False)
    db.commit()
    db.refresh(insight)
    return insight
