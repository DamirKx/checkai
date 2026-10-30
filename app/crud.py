import time
from datetime import date, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload
from app import models
from app import schemas
from app import rates
from app.categories import (
    CategoryError, DEFAULT_ICON, MAX_CATEGORIES, UNCATEGORIZED_NAME,
    create_default_categories, find_category_by_name, get_user_category,
    main_category_id, normalize_item_name, resolve_category_id,
)
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
    create_default_categories(db, user)
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
        # created_at хранится в UTC, а курс при сохранении берётся на местное «сегодня»
        return r.created_at.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d")
    return date.today().strftime("%Y-%m-%d")


def _rate_day(receipt_date: Optional[str]) -> str:
    """День курса для сохраняемого чека: дата покупки, без неё — сегодня."""
    return receipt_date[:10] if receipt_date else date.today().strftime("%Y-%m-%d")


def _resolve_rate(db: Session, cur: str, receipt_date: Optional[str]) -> Optional[float]:
    """Курс чека к тенге при сохранении.

    Для KZT курс всегда 1.0, поэтому ленту на дату (нужна для смены основной валюты)
    докачиваем в фоне и не заставляем пользователя ждать Нацбанк.
    """
    if cur == "KZT":
        return 1.0
    return rates.rate_to_kzt(db, cur, _rate_day(receipt_date), fetch=True)


def _ensure_rates_later(cur: str, receipt_date: Optional[str]) -> None:
    if cur == "KZT":
        rates.run_in_background(rates.ensure_rates, _rate_day(receipt_date))


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
    rates_cache: Dict[tuple, Optional[float]] = {}

    def cached_rate(cur: str, day: str) -> Optional[float]:
        if (cur, day) not in rates_cache:
            rates_cache[(cur, day)] = rates.rate_to_kzt(db, cur, day, fetch=False)
        return rates_cache[(cur, day)]

    for r in receipt_list:
        cur = r.currency or DEFAULT_CURRENCY
        day = receipt_day(r)
        # Курс чека не сохранился (например, не было сети), но мог появиться в exchange_rates позже.
        # В объект не записываем: при чтении базу не меняем, это делает backfill_user_rates.
        rate_kzt = r.rate_to_kzt
        if rate_kzt is None:
            rate_kzt = 1.0 if cur == "KZT" else cached_rate(cur, day)

        r.base_currency = base
        if cur == base:
            r.rate_to_base = 1.0
        elif rate_kzt is None:
            r.rate_to_base = None
        elif base == "KZT":
            r.rate_to_base = rate_kzt
        else:
            base_rate = cached_rate(base, day)
            r.rate_to_base = (rate_kzt / base_rate) if base_rate else None

    return receipt_list[0] if is_single else receipt_list


def _build_items(db: Session, user_id: int, items: List[schemas.ItemIn]) -> List[models.ReceiptItem]:
    """Позиции чека с category_id пользователя. Чужой category_id — CategoryError(422)."""
    return [
        models.ReceiptItem(
            name=item_data.name,
            quantity=item_data.quantity or 1.0,
            price_per_unit=item_data.price_per_unit or 0.0,
            total_price=item_data.total_price or 0.0,
            category_id=resolve_category_id(db, user_id, item_data.category_id, item_data.category),
        )
        for item_data in items
    ]


def _receipt_category_id(
    db: Session, user_id: int, items: List[models.ReceiptItem], requested_id: Optional[int]
) -> Optional[int]:
    """Категорию чека вычисляет сервер; присланная клиентом учитывается только у чека без позиций."""
    if items:
        return main_category_id(items)
    return resolve_category_id(db, user_id, requested_id, None)


def create_receipt(
    db: Session,
    data: schemas.ReceiptCreate,
    user_id: int,
    image_path: Optional[str] = None,
) -> models.Receipt:
    # Категории проверяем до запроса курса: на чужой категории незачем ждать Нацбанк
    items = _build_items(db, user_id, data.items)
    category_id = _receipt_category_id(db, user_id, items, data.category_id)

    cur = data.currency or DEFAULT_CURRENCY
    rate = _resolve_rate(db, cur, data.date)

    receipt = models.Receipt(
        user_id=user_id,
        store=data.store,
        date=data.date,  # уже YYYY-MM-DD (см. ReceiptCreate)
        time=data.time,
        category_id=category_id,
        total=data.total or 0.0,
        currency=cur,
        rate_to_kzt=rate,
        image_path=image_path,
    )
    receipt.items = items
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    _ensure_rates_later(cur, data.date)
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
    category_id: Optional[int] = None,
    q: Optional[str] = None,
) -> List[models.Receipt]:
    query = (
        db.query(models.Receipt)
        .options(
            joinedload(models.Receipt.category_rel),
            selectinload(models.Receipt.items).joinedload(models.ReceiptItem.category_rel),
        )
        .filter(models.Receipt.user_id == user_id)
    )

    if from_date:
        query = query.filter(models.Receipt.date >= from_date)
    if to_date:
        query = query.filter(models.Receipt.date <= to_date)
    if category_id is not None:
        # Категория любой позиции чека, а у чека без позиций — его собственная (как в аналитике).
        # 0 — «Без категории»
        target = None if category_id == 0 else category_id
        has_item = models.Receipt.items.any(
            models.ReceiptItem.category_id.is_(None) if target is None
            else models.ReceiptItem.category_id == target
        )
        own = models.Receipt.category_id.is_(None) if target is None else models.Receipt.category_id == target
        query = query.filter(has_item | (~models.Receipt.items.any() & own))
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
    items = _build_items(db, receipt.user_id, data.items)
    category_id = _receipt_category_id(db, receipt.user_id, items, data.category_id)

    # Без currency в запросе валюта чека не меняется
    cur = data.currency or receipt.currency or DEFAULT_CURRENCY
    # Курс берётся заново, если изменились валюта или дата или его не удалось получить раньше
    rate_changed = cur != receipt.currency or data.date != receipt.date
    if rate_changed or receipt.rate_to_kzt is None:
        receipt.rate_to_kzt = _resolve_rate(db, cur, data.date)

    receipt.currency = cur
    receipt.store = data.store
    receipt.date = data.date
    receipt.time = data.time
    receipt.category_id = category_id
    receipt.total = data.total or 0.0
    # Позиции пересоздаются и получают новые id — фронт после редактирования перечитывает чеки
    receipt.items = items
    db.commit()
    db.refresh(receipt)
    if rate_changed:
        _ensure_rates_later(cur, data.date)
    return receipt


def backfill_user_rates(db: Session, user_id: int) -> None:
    """Докачивает курсы на дни чеков пользователя и проставляет rate_to_kzt чекам, где его нет.

    Запускается в фоне при смене основной валюты: запросы к Нацбанку идут по одному с паузой.
    """
    receipts = db.query(models.Receipt).filter(models.Receipt.user_id == user_id).all()
    for day in sorted({receipt_day(r) for r in receipts}):
        if rates.has_rates(db, day):
            continue
        rates.ensure_rates(db, day)
        time.sleep(0.3)

    for r in receipts:
        if r.rate_to_kzt is None:
            cur = r.currency or DEFAULT_CURRENCY
            r.rate_to_kzt = 1.0 if cur == "KZT" else rates.rate_to_kzt(db, cur, receipt_day(r), fetch=False)
    db.commit()

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
        .options(
            joinedload(models.Receipt.category_rel),
            selectinload(models.Receipt.items).joinedload(models.ReceiptItem.category_rel),
        )
        .filter(models.Receipt.user_id == user_id)
        .order_by(*_receipts_order())
        .all()
    )

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
                cat = item.category or UNCATEGORIZED_NAME
                by_category[cat] = by_category.get(cat, 0.0) + (item.total_price or 0.0) * rate
        else:
            cat = r.category or UNCATEGORIZED_NAME
            by_category[cat] = by_category.get(cat, 0.0) + rec_total

    top_store = max(by_store, key=by_store.get) if by_store else None

    recent_items: List[Dict[str, Any]] = []
    for r in receipts:
        # Позиции чеков без курса не показываем: их цены нельзя выразить в основной валюте
        if r.rate_to_base is None:
            continue
        rate = r.rate_to_base
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


# --- Операции с бюджетами ---

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
            raise CategoryError(422, "Категория не найдена")

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



# --- Операции с категориями ---

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
        raise CategoryError(409, f"Категория «{existing.name}» уже есть")


def create_category(db: Session, user_id: int, data: schemas.CategoryCreate) -> models.Category:
    count = db.query(func.count(models.Category.id)).filter(models.Category.user_id == user_id).scalar() or 0
    if count >= MAX_CATEGORIES:
        raise CategoryError(422, f"Можно создать не больше {MAX_CATEGORIES} категорий")
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
    """Категория чека после переноса позиций (п. 0.5). У чека без позиций она не меняется."""
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
            raise CategoryError(422, "Нельзя перенести позиции в удаляемую категорию")
        if get_user_category(db, user_id, move_to) is None:
            raise CategoryError(422, "Категория не найдена")

    try:
        items = (
            db.query(models.ReceiptItem)
            .join(models.Receipt)
            .filter(models.Receipt.user_id == user_id, models.ReceiptItem.category_id == category.id)
            .all()
        )
        for item in items:
            item.category_id = move_to
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
        raise CategoryError(422, "Передайте все свои категории, каждую один раз")
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
        raise CategoryError(422, "Категория не найдена")
    items = (
        db.query(models.ReceiptItem)
        .join(models.Receipt)
        .filter(models.Receipt.user_id == user_id, models.ReceiptItem.id.in_(ids))
        .all()
    )
    if len(items) != len(ids):
        raise CategoryError(404, "Позиция не найдена")

    try:
        for item in items:
            item.category_id = category_id

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