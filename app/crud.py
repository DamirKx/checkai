from datetime import date
from typing import List, Optional, Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from app import models
from app import schemas
from app import rates
from app.currencies import DEFAULT_CURRENCY

# Все операции с чеками требуют user_id: анонимы с историей не работают
# (эндпоинты защищены через auth.get_current_user).

# --- Операции с пользователями ---

def get_user_by_email(db: Session, email: str) -> Optional[models.User]:
    """Поиск пользователя по email."""
    return db.query(models.User).filter(models.User.email == email.strip().lower()).first()

def get_user_by_id(db: Session, user_id: int) -> Optional[models.User]:
    """Поиск пользователя по id."""
    return db.query(models.User).filter(models.User.id == user_id).first()

def create_user(
    db: Session,
    email: str,
    hashed_password: str,
    full_name: Optional[str] = None,
    base_currency: str = DEFAULT_CURRENCY,
) -> models.User:
    """Создание нового пользователя."""
    user = models.User(
        email=email.strip().lower(),
        hashed_password=hashed_password,
        full_name=full_name.strip() if full_name else None,
        base_currency=base_currency or DEFAULT_CURRENCY,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def delete_user(db: Session, user: models.User) -> List[str]:
    """Удаляет пользователя и его чеки (cascade). Возвращает список image_path для очистки файлов."""
    image_paths = [r.image_path for r in user.receipts if r.image_path]
    db.delete(user)
    db.commit()
    return image_paths

# --- Операции с чеками ---

def receipt_day(r: models.Receipt) -> str:
    """Определяет день для поиска курса валюты чека."""
    if r.date:
        return r.date[:10]
    if r.created_at:
        return r.created_at.strftime("%Y-%m-%d")
    return date.today().strftime("%Y-%m-%d")


def attach_base_rates(
    db: Session,
    receipts: Any,
    base: str = DEFAULT_CURRENCY,
) -> Any:
    """Проставляет каждому чеку base_currency и rate_to_base (обычные атрибуты объекта, не колонки в БД).
    
    При чтении в сеть не ходим (fetch=False).
    """
    if receipts is None:
        return None

    is_single = isinstance(receipts, models.Receipt)
    receipt_list: List[models.Receipt] = [receipts] if is_single else list(receipts)

    base = base or DEFAULT_CURRENCY
    base_rates_cache: Dict[str, Optional[float]] = {}

    for r in receipt_list:
        cur = r.currency or DEFAULT_CURRENCY
        if r.rate_to_kzt is None and cur == "KZT":
            r.rate_to_kzt = 1.0

        r.base_currency = base
        if cur == base:
            r.rate_to_base = 1.0
        elif r.rate_to_kzt is None:
            r.rate_to_base = None
        elif base == "KZT":
            r.rate_to_base = r.rate_to_kzt
        else:
            day = receipt_day(r)
            if day not in base_rates_cache:
                base_rates_cache[day] = rates.rate_to_kzt(db, base, day, fetch=False)
            base_rate = base_rates_cache[day]
            r.rate_to_base = (r.rate_to_kzt / base_rate) if base_rate else None

    return receipt_list[0] if is_single else receipt_list


def create_receipt(
    db: Session,
    data: schemas.ReceiptCreate,
    user_id: int,
    image_path: Optional[str] = None,
) -> models.Receipt:
    cur = data.currency or DEFAULT_CURRENCY
    rate = rates.rate_to_kzt(db, cur, data.date, fetch=True)

    receipt = models.Receipt(
        user_id=user_id,
        store=data.store,
        date=data.date,  # уже YYYY-MM-DD (см. ReceiptCreate)
        time=data.time,
        category=data.category or "Продукты",
        total=data.total or 0.0,
        currency=cur,
        rate_to_kzt=rate,
        image_path=image_path,
    )
    receipt.items = [
        models.ReceiptItem(
            name=item_data.name,
            quantity=item_data.quantity or 1.0,
            price_per_unit=item_data.price_per_unit or 0.0,
            total_price=item_data.total_price or 0.0,
            category=item_data.category,
        )
        for item_data in data.items
    ]
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    return receipt


def _receipts_order():
    # Сначала свежие покупки по дате чека, чеки без даты — в конце
    return (
        models.Receipt.date.desc().nulls_last(),
        models.Receipt.time.desc().nulls_last(),
        models.Receipt.created_at.desc(),
    )


def _escape_like(value: str) -> str:
    """Символы % и _ в запросе пользователя ищем буквально, а не как шаблон LIKE."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_all_receipts(
    db: Session,
    user_id: int,
    skip: int = 0,
    limit: int = 100,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
) -> List[models.Receipt]:
    query = db.query(models.Receipt).filter(models.Receipt.user_id == user_id)

    if from_date:
        query = query.filter(models.Receipt.date >= from_date)
    if to_date:
        query = query.filter(models.Receipt.date <= to_date)
    if category and category.strip():
        # Категория чека или любой его позиции (как в аналитике)
        cat = _escape_like(category.strip())
        query = query.filter(
            models.Receipt.category.ilike(cat, escape="\\") |
            models.Receipt.items.any(models.ReceiptItem.category.ilike(cat, escape="\\"))
        )
    if q and q.strip():
        term = f"%{_escape_like(q.strip())}%"
        query = query.filter(
            models.Receipt.store.ilike(term, escape="\\") |
            models.Receipt.items.any(models.ReceiptItem.name.ilike(term, escape="\\"))
        )

    return (
        query
        .order_by(*_receipts_order())
        .offset(skip)
        .limit(limit)
        .all()
    )

def get_receipt_by_id(db: Session, receipt_id: int, user_id: int) -> Optional[models.Receipt]:
    return (
        db.query(models.Receipt)
        .filter(models.Receipt.id == receipt_id, models.Receipt.user_id == user_id)
        .first()
    )

def update_receipt(
    db: Session,
    receipt: models.Receipt,
    data: schemas.ReceiptUpdate,
) -> models.Receipt:
    cur = data.currency or DEFAULT_CURRENCY
    # Курс обновляется только если изменились валюта или дата чека
    if cur != receipt.currency or data.date != receipt.date:
        receipt.rate_to_kzt = rates.rate_to_kzt(db, cur, data.date, fetch=True)

    receipt.currency = cur
    receipt.store = data.store
    receipt.date = data.date
    receipt.time = data.time
    receipt.category = data.category or "Продукты"
    receipt.total = data.total or 0.0

    receipt.items = [
        models.ReceiptItem(
            name=item_data.name,
            quantity=item_data.quantity or 1.0,
            price_per_unit=item_data.price_per_unit or 0.0,
            total_price=item_data.total_price or 0.0,
            category=item_data.category,
        )
        for item_data in data.items
    ]
    db.commit()
    db.refresh(receipt)
    return receipt

def delete_receipt(db: Session, receipt: models.Receipt) -> None:
    db.delete(receipt)
    db.commit()

def count_receipts_with_image(db: Session, image_path: str) -> int:
    return db.query(func.count(models.Receipt.id)).filter(models.Receipt.image_path == image_path).scalar() or 0


def get_analytics(db: Session, user_id: int, base_currency: Optional[str] = None) -> Dict[str, Any]:
    """Сводка аналитики с пересчётом всех сумм в основную валюту пользователя.
    
    Чеки без курса не суммируются, а группируются в unconverted.
    """
    if not base_currency:
        user = get_user_by_id(db, user_id)
        base_currency = user.base_currency if user else DEFAULT_CURRENCY

    receipts = (
        db.query(models.Receipt)
        .options(joinedload(models.Receipt.items))
        .filter(models.Receipt.user_id == user_id)
        .order_by(*_receipts_order())
        .all()
    )

    if not receipts:
        return {
            "total_spent": 0.0,
            "receipts_count": 0,
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

    total_spent = 0.0
    counted_receipts = 0
    by_store: Dict[str, float] = {}
    by_month: Dict[str, float] = {}
    by_category: Dict[str, float] = {}
    unconverted_counts: Dict[str, int] = {}

    for r in receipts:
        if r.rate_to_base is None:
            cur = r.currency or DEFAULT_CURRENCY
            unconverted_counts[cur] = unconverted_counts.get(cur, 0) + 1
            continue

        rate = r.rate_to_base
        rec_total = (r.total or 0.0) * rate
        total_spent += rec_total
        counted_receipts += 1

        store_name = r.store or "Неизвестно"
        by_store[store_name] = by_store.get(store_name, 0.0) + rec_total

        if r.date:
            m = r.date[:7]
            by_month[m] = by_month.get(m, 0.0) + rec_total

        if r.items:
            for item in r.items:
                cat = item.category or r.category or "Прочее"
                by_category[cat] = by_category.get(cat, 0.0) + (item.total_price or 0.0) * rate
        else:
            cat = r.category or "Прочее"
            by_category[cat] = by_category.get(cat, 0.0) + rec_total

    top_store = max(by_store, key=by_store.get) if by_store else None

    recent_items: List[Dict[str, Any]] = []
    for r in receipts:
        rate = r.rate_to_base if r.rate_to_base is not None else 1.0
        for item in r.items:
            recent_items.append({
                "name": item.name,
                "price": round((item.total_price or 0.0) * rate, 2),
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
        "receipts_count": counted_receipts,
        "avg_receipt": round(total_spent / counted_receipts, 2) if counted_receipts else 0.0,
        "top_store": top_store,
        "by_category": {k: round(v, 2) for k, v in by_category.items()},
        "by_store": {k: round(v, 2) for k, v in by_store.items()},
        "by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
        "recent_items": recent_items,
        "base_currency": base_currency,
        "unconverted": unconverted,
    }


# --- Операции с бюджетами ---

def _budgets_order():
    # Общий бюджет (category is None) выводим первым, затем остальные по алфавиту
    return (
        models.Budget.category.isnot(None),
        models.Budget.category.asc(),
    )


def get_user_budgets(db: Session, user_id: int) -> List[models.Budget]:
    """Возвращает все лимиты пользователя, отсортированные: общий лимит первым, затем по алфавиту."""
    return (
        db.query(models.Budget)
        .filter(models.Budget.user_id == user_id)
        .order_by(*_budgets_order())
        .all()
    )


def set_user_budgets(db: Session, user_id: int, budgets_data: List[schemas.BudgetItem]) -> List[models.Budget]:
    """Заменяет весь список лимитов пользователя в одной транзакции."""
    db.query(models.Budget).filter(models.Budget.user_id == user_id).delete(synchronize_session=False)

    new_budgets = [
        models.Budget(
            user_id=user_id,
            category=item.category,
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
        .options(joinedload(models.Receipt.items))
        .filter(
            models.Receipt.user_id == user_id,
            models.Receipt.date.isnot(None),
            func.substr(models.Receipt.date, 1, 7) == month,
        )
        .all()
    )

    attach_base_rates(db, receipts, base_currency)

    total_spent = 0.0
    spending_lower: Dict[str, float] = {}
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
                cat = (item.category or r.category or "Прочее").strip().lower()
                spending_lower[cat] = spending_lower.get(cat, 0.0) + (item.total_price or 0.0) * rate
        else:
            cat = (r.category or "Прочее").strip().lower()
            spending_lower[cat] = spending_lower.get(cat, 0.0) + rec_total

    general_budget = next((b for b in budgets if b.category is None), None)
    total_budget_limit = general_budget.limit if general_budget else None

    status_items = []
    for b in budgets:
        if b.category is None:
            spent = round(total_spent, 2)
        else:
            cat_key = b.category.strip().lower()
            spent = round(spending_lower.get(cat_key, 0.0), 2)

        remaining = round(b.limit - spent, 2)
        percent = round((spent / b.limit) * 100, 1) if b.limit > 0 else 0.0
        is_over = spent > b.limit

        status_items.append(
            schemas.BudgetStatusItem(
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

