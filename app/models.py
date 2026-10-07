from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


def utcnow() -> datetime:
    """Текущее время в UTC без tzinfo: так оно хранится в SQLite, фронт добавляет «Z» сам."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    receipts = relationship("Receipt", back_populates="user", cascade="all, delete-orphan")

class Receipt(Base):
    __tablename__ = "receipts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    store = Column(String(255), nullable=True)
    # Дата покупки в формате YYYY-MM-DD (см. app/dates.py)
    date = Column(String(20), nullable=True, index=True)
    # Время покупки в формате HH:MM
    time = Column(String(10), nullable=True)
    category = Column(String(100), nullable=True, default="Продукты")
    total = Column(Float, nullable=True, default=0.0)
    image_path = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    
    user = relationship("User", back_populates="receipts")
    items = relationship("ReceiptItem", back_populates="receipt", cascade="all, delete-orphan")

class ReceiptItem(Base):
    __tablename__ = "receipt_items"
    id = Column(Integer, primary_key=True, index=True)
    receipt_id = Column(Integer, ForeignKey("receipts.id"), nullable=False)
    name = Column(String(500), nullable=False)
    quantity = Column(Float, nullable=True, default=1.0)
    price_per_unit = Column(Float, nullable=True, default=0.0)
    total_price = Column(Float, nullable=True, default=0.0)
    category = Column(String(100), nullable=True)
    receipt = relationship("Receipt", back_populates="items")
