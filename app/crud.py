from typing import List, Optional, Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session
from app import models
from app import schemas

# Все операции с чеками требуют user_id: анонимы с историей не работают
# (эндпоинты защищены через auth.get_current_user).

# --- Операции с пользователями ---

def get_user_by_email(db: Session, email: str) -> Optional[models.User]:
    """Поиск пользователя по email."""
    return db.query(models.User).filter(models.User.email == email.strip().lower()).first()

def get_user_by_id(db: Session, user_id: int) -> Optional[models.User]:
    """Поиск пользователя по id."""
    return db.query(models.User).filter(models.User.id == user_id).first()

def create_user(db: Session, email: str, hashed_password: str, full_name: Optional[str] = None) -> models.User:
    """Создание нового пользователя."""
    user = models.User(
        email=email.strip().lower(),
        hashed_password=hashed_password,
        full_name=full_name.strip() if full_name else None
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

# --- Операции с чеками ---

def create_receipt(
    db: Session,
    data: schemas.ReceiptCreate,
    user_id: int,
    image_path: Optional[str] = None,
) -> models.Receipt:
    receipt = models.Receipt(
        user_id=user_id,
        store=data.store,
        date=data.date,  # уже YYYY-MM-DD (см. ReceiptCreate)
        time=data.time,
        category=data.category or "Продукты",
        total=data.total or 0.0,
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


def get_all_receipts(
    db: Session,
    user_id: int,
    skip: int = 0,
    limit: int = 100,
) -> List[models.Receipt]:
    return (
        db.query(models.Receipt)
        .filter(models.Receipt.user_id == user_id)
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

def delete_receipt(db: Session, receipt: models.Receipt) -> None:
    db.delete(receipt)
    db.commit()

def count_receipts_with_image(db: Session, image_path: str) -> int:
    return db.query(func.count(models.Receipt.id)).filter(models.Receipt.image_path == image_path).scalar() or 0


def get_analytics(db: Session, user_id: int) -> Dict[str, Any]:
    R, I = models.Receipt, models.ReceiptItem
    receipt_total = func.coalesce(R.total, 0.0)

    total_spent, receipts_count = (
        db.query(func.coalesce(func.sum(receipt_total), 0.0), func.count(R.id))
        .filter(R.user_id == user_id)
        .one()
    )
    if not receipts_count:
        return {
            "total_spent": 0.0, "receipts_count": 0, "avg_receipt": 0.0, "top_store": None,
            "by_category": {}, "by_store": {}, "by_month": {}, "recent_items": []
        }

    # По магазинам
    store = func.coalesce(R.store, "Неизвестно")
    by_store = {
        name: round(value, 2)
        for name, value in db.query(store, func.sum(receipt_total))
        .filter(R.user_id == user_id)
        .group_by(store)
        .all()
    }
    top_store = max(by_store, key=by_store.get) if by_store else None

    # По месяцам: date хранится как YYYY-MM-DD, месяц — первые 7 символов
    month = func.substr(R.date, 1, 7)
    by_month = {
        key: round(value, 2)
        for key, value in db.query(month, func.sum(receipt_total))
        .filter(R.user_id == user_id, R.date.isnot(None))
        .group_by(month)
        .order_by(month)
        .all()
    }

    # По категориям: категория товара, иначе категория чека
    category = func.coalesce(I.category, R.category, "Прочее")
    by_category: Dict[str, float] = {
        name: value
        for name, value in db.query(category, func.sum(func.coalesce(I.total_price, 0.0)))
        .join(R, I.receipt_id == R.id)
        .filter(R.user_id == user_id)
        .group_by(category)
        .all()
    }
    # Чеки без позиций учитываем целиком по категории чека
    receipt_category = func.coalesce(R.category, "Прочее")
    for name, value in (
        db.query(receipt_category, func.sum(receipt_total))
        .filter(R.user_id == user_id, ~R.items.any())
        .group_by(receipt_category)
        .all()
    ):
        by_category[name] = by_category.get(name, 0.0) + value
    by_category = {name: round(value, 2) for name, value in by_category.items()}

    recent_rows = (
        db.query(I.name, I.total_price, R.store)
        .join(R, I.receipt_id == R.id)
        .filter(R.user_id == user_id)
        .order_by(*_receipts_order(), I.id)
        .limit(5)
        .all()
    )
    recent_items = [{"name": name, "price": price, "store": store_name} for name, price, store_name in recent_rows]

    return {
        "total_spent": round(total_spent, 2),
        "receipts_count": receipts_count,
        "avg_receipt": round(total_spent / receipts_count, 2),
        "top_store": top_store,
        "by_category": by_category,
        "by_store": by_store,
        "by_month": by_month,
        "recent_items": recent_items,
    }
