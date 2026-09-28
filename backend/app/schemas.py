from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel

class ItemSchema(BaseModel):
    name: str
    quantity: Optional[float] = 1.0
    price_per_unit: Optional[float] = 0.0
    total_price: Optional[float] = 0.0
    class Config:
        from_attributes = True

class ReceiptCreate(BaseModel):
    store: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    category: Optional[str] = "Продукты"
    total: Optional[float] = 0.0
    items: List[ItemSchema] = []

class ReceiptOut(BaseModel):
    id: int
    store: Optional[str]
    date: Optional[str]
    time: Optional[str]
    category: Optional[str]
    total: Optional[float]
    image_path: Optional[str]
    created_at: datetime
    items: List[ItemSchema] = []
    class Config:
        from_attributes = True

class AnalyticsOut(BaseModel):
    total_spent: float
    receipts_count: int
    avg_receipt: float
    top_store: Optional[str]
    by_category: Dict[str, float]
    by_store: Dict[str, float]
    by_month: Dict[str, float]
    recent_items: List[Dict[str, Any]]
