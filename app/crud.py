from typing import List, Optional, Dict, Any
from collections import defaultdict
from sqlalchemy.orm import Session
from app import models
from app import schemas

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
    image_path: Optional[str] = None,
    user_id: Optional[int] = None
) -> models.Receipt:
    receipt = models.Receipt(
        user_id=user_id,
        store=data.store,
        date=data.date,
        time=data.time,
        category=data.category or "Продукты",
        total=data.total or 0.0,
        image_path=image_path,
    )
    db.add(receipt)
    db.flush()
    for item_data in data.items:
        db.add(models.ReceiptItem(
            receipt_id=receipt.id,
            name=item_data.name,
            quantity=item_data.quantity or 1.0,
            price_per_unit=item_data.price_per_unit or 0.0,
            total_price=item_data.total_price or 0.0
        ))
    db.commit()
    db.refresh(receipt)
    return receipt

def get_all_receipts(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[int] = None
) -> List[models.Receipt]:
    query = db.query(models.Receipt)
    if user_id is not None:
        query = query.filter(models.Receipt.user_id == user_id)
    return query.order_by(models.Receipt.created_at.desc()).offset(skip).limit(limit).all()

def get_receipt_by_id(
    db: Session,
    receipt_id: int,
    user_id: Optional[int] = None
) -> Optional[models.Receipt]:
    query = db.query(models.Receipt).filter(models.Receipt.id == receipt_id)
    if user_id is not None:
        query = query.filter(models.Receipt.user_id == user_id)
    return query.first()

def delete_receipt(
    db: Session,
    receipt_id: int,
    user_id: Optional[int] = None
) -> bool:
    receipt = get_receipt_by_id(db, receipt_id, user_id=user_id)
    if not receipt:
        return False
    db.delete(receipt)
    db.commit()
    return True

def get_analytics(
    db: Session,
    user_id: Optional[int] = None
) -> Dict[str, Any]:
    query = db.query(models.Receipt)
    if user_id is not None:
        query = query.filter(models.Receipt.user_id == user_id)
    receipts = query.all()
    
    if not receipts:
        return {
            "total_spent": 0.0, "receipts_count": 0, "avg_receipt": 0.0, "top_store": None,
            "by_category": {}, "by_store": {}, "by_month": {}, "recent_items": []
        }
    
    total_spent = sum(r.total or 0.0 for r in receipts)
    receipts_count = len(receipts)
    
    by_category: Dict[str, float] = defaultdict(float)
    by_store: Dict[str, float] = defaultdict(float)
    by_month: Dict[str, float] = defaultdict(float)
    
    for r in receipts:
        by_category[r.category or "Прочее"] += r.total or 0.0
        by_store[r.store or "Неизвестно"] += r.total or 0.0
        if r.date and len(r.date.split(".")) == 3:
            parts = r.date.split(".")
            by_month[f"{parts[2]}-{parts[1]}"] += r.total or 0.0
            
    top_store = max(by_store, key=by_store.get) if by_store else None
    
    items_query = db.query(models.ReceiptItem).join(models.Receipt)
    if user_id is not None:
        items_query = items_query.filter(models.Receipt.user_id == user_id)
    all_items = items_query.order_by(models.Receipt.created_at.desc()).limit(5).all()
    recent_items = [{"name": i.name, "price": i.total_price, "store": i.receipt.store if i.receipt else None} for i in all_items]
    
    return {
        "total_spent": round(total_spent, 2),
        "receipts_count": receipts_count,
        "avg_receipt": round(total_spent / receipts_count, 2) if receipts_count > 0 else 0.0,
        "top_store": top_store,
        "by_category": dict(by_category),
        "by_store": dict(by_store),
        "by_month": dict(sorted(by_month.items())),
        "recent_items": recent_items,
    }
