from typing import List, Optional, Dict, Any
from collections import defaultdict
from sqlalchemy.orm import Session
from app import models
from app import schemas

def create_receipt(db: Session, data: schemas.ReceiptCreate, image_path: Optional[str] = None) -> models.Receipt:
    receipt = models.Receipt(
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
            receipt_id=receipt.id, name=item_data.name, quantity=item_data.quantity or 1.0,
            price_per_unit=item_data.price_per_unit or 0.0, total_price=item_data.total_price or 0.0
        ))
    db.commit()
    db.refresh(receipt)
    return receipt

def get_all_receipts(db: Session, skip: int = 0, limit: int = 100) -> List[models.Receipt]:
    return db.query(models.Receipt).order_by(models.Receipt.created_at.desc()).offset(skip).limit(limit).all()

def get_receipt_by_id(db: Session, receipt_id: int) -> Optional[models.Receipt]:
    return db.query(models.Receipt).filter(models.Receipt.id == receipt_id).first()

def delete_receipt(db: Session, receipt_id: int) -> bool:
    receipt = get_receipt_by_id(db, receipt_id)
    if not receipt: return False
    db.delete(receipt)
    db.commit()
    return True

def get_analytics(db: Session) -> Dict[str, Any]:
    receipts = db.query(models.Receipt).all()
    if not receipts:
        return {"total_spent": 0.0, "receipts_count": 0, "avg_receipt": 0.0, "top_store": None,
                "by_category": {}, "by_store": {}, "by_month": {}, "recent_items": []}
    
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
    all_items = db.query(models.ReceiptItem).join(models.Receipt).order_by(models.Receipt.created_at.desc()).limit(5).all()
    recent_items = [{"name": i.name, "price": i.total_price, "store": i.receipt.store if i.receipt else None} for i in all_items]
    
    return {
        "total_spent": round(total_spent, 2), "receipts_count": receipts_count,
        "avg_receipt": round(total_spent / receipts_count, 2) if receipts_count > 0 else 0.0,
        "top_store": top_store, "by_category": dict(by_category),
        "by_store": dict(by_store), "by_month": dict(sorted(by_month.items())),
        "recent_items": recent_items,
    }
