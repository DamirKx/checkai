import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone, tzinfo
from types import SimpleNamespace
from typing import List, Optional, Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload
from app import models
from app import schemas
from app import rates
from app.duplicates import field, match_score, total_tolerance
from app.categories import (
    CategoryError, DEFAULT_ICON, MAX_CATEGORIES, UNCATEGORIZED_NAME,
    create_default_categories, find_category_by_name, get_user_category,
    main_category_id, normalize_item_name, resolve_category_id,
)
from app.currencies import DEFAULT_CURRENCY

try:
    from ml.app.merchant import canonical_merchant
except ImportError:  # ML-пакет не установлен: merchant берётся только из запроса
    def canonical_merchant(store: Optional[str]) -> Optional[str]:
        return None

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
    recovery_code_hash: Optional[str] = None,
) -> models.User:
    """Создание нового пользователя."""
    user = models.User(
        email=email.strip().lower(),
        hashed_password=hashed_password,
        full_name=full_name.strip() if full_name else None,
        base_currency=base_currency or DEFAULT_CURRENCY,
        recovery_code_hash=recovery_code_hash,
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
    built = []
    for item_data in items:
        category_id = resolve_category_id(db, user_id, item_data.category_id, item_data.category)
        built.append(models.ReceiptItem(
            name=item_data.name,
            # Количество уже проверено схемой (> 0): 0 — это 422, а не «молча 1» (A.2.2)
            quantity=item_data.quantity,
            price_per_unit=item_data.price_per_unit or 0.0,
            total_price=item_data.total_price or 0.0,
            category_id=category_id,
            # Нет категории — нет и источника (PROFILE_TASKS.md, п. 2.1)
            category_source=item_data.category_source if category_id is not None else None,
        ))
    return built


def _merchant(merchant: Optional[str], store: Optional[str]) -> Optional[str]:
    """Имя сети из запроса, а если его нет — вычисленное из store (п. 2.2)."""
    merchant = (merchant or "").strip()
    return merchant[:255] if merchant else canonical_merchant(store)


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
    image_hash: Optional[str] = None,
    fiscal_id: Optional[str] = None,
) -> models.Receipt:
    # Категории проверяем до запроса курса: на чужой категории незачем ждать Нацбанк
    items = _build_items(db, user_id, data.items)
    category_id = _receipt_category_id(db, user_id, items, data.category_id)

    cur = data.currency or DEFAULT_CURRENCY
    rate = _resolve_rate(db, cur, data.date)

    receipt = models.Receipt(
        user_id=user_id,
        store=data.store,
        merchant=_merchant(data.merchant, data.store),
        date=data.date,  # уже YYYY-MM-DD (см. ReceiptCreate)
        time=data.time,
        category_id=category_id,
        total=data.total or 0.0,
        currency=cur,
        rate_to_kzt=rate,
        image_path=image_path,
        # Без фото чек всегда введён вручную, что бы ни прислал клиент (A.2.1)
        source=data.source if image_path else "manual",
        image_hash=image_hash if image_path else None,
        fiscal_id=fiscal_id,
    )
    receipt.items = items
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    _ensure_rates_later(cur, data.date)
    return receipt


def find_duplicate(
    db: Session,
    user_id: int,
    data: Any,
    image_hash: Optional[str] = None,
    fiscal_id: Optional[str] = None,
    exclude_id: Optional[int] = None,
    by_content: bool = True,
) -> Optional[models.Receipt]:
    """Уже сохранённый чек пользователя, который совпадает с data, или None (NEXT_WEEK_TASKS.md, C.2.3).

    По убыванию надёжности: тот же файл фото, тот же фискальный номер, то же содержимое
    (правило C.0, app/duplicates.py). data — ReceiptCreate / ReceiptUpdate или словарь распознавания.
    by_content=False — только фото и фискальный номер (так проверяется ответ распознавания).
    """
    base = db.query(models.Receipt).filter(models.Receipt.user_id == user_id)
    if exclude_id is not None:
        base = base.filter(models.Receipt.id != exclude_id)

    for column, value in ((models.Receipt.image_hash, image_hash), (models.Receipt.fiscal_id, fiscal_id)):
        if value:
            found = base.filter(column == value).order_by(models.Receipt.created_at.desc()).first()
            if found is not None:
                return found
    if not by_content:
        return None

    total = field(data, "total")
    if isinstance(total, bool) or not isinstance(total, (int, float)) or total <= 0:
        return None
    currency = field(data, "currency")
    if currency is None and exclude_id is not None:
        # Правка без валюты оставляет валюту чека
        own = db.get(models.Receipt, exclude_id)
        currency = own.currency if own is not None else None
    currency = currency or DEFAULT_CURRENCY
    draft = {
        "total": total, "currency": currency,
        "date": field(data, "date"), "time": field(data, "time"), "store": field(data, "store"),
        "items": field(data, "items") or [],
    }
    # Кандидаты — SQL-запросом, остальное (время, магазин, позиции) — в Python
    tolerance = total_tolerance(currency)
    query = base.options(selectinload(models.Receipt.items)).filter(
        models.Receipt.currency == currency,
        models.Receipt.total > total - tolerance,
        models.Receipt.total < total + tolerance,
    )
    if draft["date"]:
        query = query.filter((models.Receipt.date == str(draft["date"])[:10]) | models.Receipt.date.is_(None))

    best, best_score = None, -1.0
    for candidate in query.order_by(*_receipts_order()):
        score = match_score(draft, candidate)
        if score is not None and score > best_score:
            best, best_score = candidate, score
    return best


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
            models.Receipt.merchant.ilike(term, escape="\\") |
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
    # source не меняется: он определён при создании чека (A.2.1)
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
    receipt.merchant = _merchant(data.merchant, data.store)
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


# --- Сводка профиля (PROFILE_TASKS.md, п. 1.3) ---

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

# --- Сводка для ИИ-рекомендаций (NEXT_WEEK_TASKS.md, B.2.2) ---

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
    db: Session, user: models.User, months: int = SNAPSHOT_MONTHS, today: Optional[date] = None
) -> Dict[str, Any]:
    """Обезличенная сводка расходов для ИИ: только агрегаты в основной валюте, без email, имени и id пользователя.

    Пересчёт валют — attach_base_rates, как в get_analytics; чеки без курса только перечисляются в unconverted.
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
        # Категории без трат за полгода и без лимита ИИ не нужны
        if row["limit"] is not None or any(v for (cid, _), v in spent.items() if cid == c.id):
            categories.append(row)

    categorized_total = sum(spent.values())
    uncategorized = sum(v for (cid, _), v in spent.items() if cid is None)

    return {
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


def snapshot_hash(snapshot: Dict[str, Any]) -> str:
    """Хеш сводки без точного дня: в пределах месяца те же данные — тот же анализ (B.2.4)."""
    stable = {**snapshot, "today": str(snapshot.get("today", ""))[:7]}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# --- Сохранённые анализы (B.2.3) ---

INSIGHTS_KEEP = 3


def get_latest_insight(db: Session, user_id: int) -> Optional[models.Insight]:
    return (
        db.query(models.Insight)
        .filter(models.Insight.user_id == user_id)
        .order_by(models.Insight.created_at.desc(), models.Insight.id.desc())
        .first()
    )


def save_insight(db: Session, user_id: int, model: str, data: Dict[str, Any], digest: str) -> models.Insight:
    """Новый анализ; старше последних INSIGHTS_KEEP удаляются."""
    insight = models.Insight(user_id=user_id, model=model[:100], data=data, snapshot_hash=digest)
    db.add(insight)
    db.flush()
    old = (
        db.query(models.Insight.id)
        .filter(models.Insight.user_id == user_id)
        .order_by(models.Insight.created_at.desc(), models.Insight.id.desc())
        .offset(INSIGHTS_KEEP)
        .all()
    )
    if old:
        db.query(models.Insight).filter(models.Insight.id.in_([row.id for row in old])).delete(synchronize_session=False)
    db.commit()
    db.refresh(insight)
    return insight
