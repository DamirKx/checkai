"""Чеки: курсы к основной валюте, создание, поиск дубликата, список с фильтрами, правка, удаление."""
import time
from datetime import date, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload
from app import models
from app import schemas
from app import rates
from app.duplicates import field, match_score, total_tolerance
from app.categories import main_category_id, resolve_category_id
from app.currencies import DEFAULT_CURRENCY
try:
    from ml.merchant import canonical_merchant
except ImportError:  # ML-пакет не установлен: merchant берётся только из запроса
    def canonical_merchant(store: Optional[str]) -> Optional[str]:
        return None


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
            # Количество уже проверено схемой (> 0): 0 — это 422, а не «молча 1»
            quantity=item_data.quantity,
            price_per_unit=item_data.price_per_unit or 0.0,
            total_price=item_data.total_price or 0.0,
            category_id=category_id,
            # Нет категории — нет и источника
            category_source=item_data.category_source if category_id is not None else None,
        ))
    return built


def _merchant(merchant: Optional[str], store: Optional[str]) -> Optional[str]:
    """Имя сети из запроса, а если его нет — вычисленное из store."""
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
        # Без фото чек всегда введён вручную, что бы ни прислал клиент
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
    """Уже сохранённый чек пользователя, который совпадает с data, или None.

    По убыванию надёжности: тот же файл фото, тот же фискальный номер, то же содержимое
    (правило — app/duplicates.py). data — ReceiptCreate / ReceiptUpdate или словарь распознавания.
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
    # source не меняется: он определён при создании чека
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
