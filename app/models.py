from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class Receipt(Base):
    __tablename__ = "receipts"
    id = Column(Integer, primary_key=True, index=True)
    store = Column(String(255), nullable=True)
    date = Column(String(20), nullable=True)
    time = Column(String(10), nullable=True)
    category = Column(String(100), nullable=True, default="Продукты")
    total = Column(Float, nullable=True, default=0.0)
    image_path = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    items = relationship("ReceiptItem", back_populates="receipt", cascade="all, delete-orphan")

class ReceiptItem(Base):
    __tablename__ = "receipt_items"
    id = Column(Integer, primary_key=True, index=True)
    receipt_id = Column(Integer, ForeignKey("receipts.id"), nullable=False)
    name = Column(String(500), nullable=False)
    quantity = Column(Float, nullable=True, default=1.0)
    price_per_unit = Column(Float, nullable=True, default=0.0)
    total_price = Column(Float, nullable=True, default=0.0)
    receipt = relationship("Receipt", back_populates="items")
