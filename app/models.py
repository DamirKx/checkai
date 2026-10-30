from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint
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
    base_currency = Column(String(3), nullable=False, default="KZT", server_default="KZT")
    created_at = Column(DateTime, default=utcnow)
    receipts = relationship("Receipt", back_populates="user", cascade="all, delete-orphan")
    budgets = relationship("Budget", back_populates="user", cascade="all, delete-orphan")
    categories = relationship("Category", cascade="all, delete-orphan", order_by="Category.position")


class Category(Base):
    """Категория расходов пользователя. «Без категории» — это category_id = NULL, а не запись."""
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(50), nullable=False)
    # По описанию ML раскладывает товары
    description = Column(String(300), nullable=True)
    icon = Column(String(8), nullable=False, default="◌")
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow)


class CategoryRule(Base):
    """«Товар с таким названием → такая категория». pattern — normalize_item_name(название)."""
    __tablename__ = "category_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "pattern", name="uq_category_rules_user_pattern"),
    )
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    pattern = Column(String(200), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    category_rel = relationship("Category")


class _CategoryName:
    """Название категории для схем ответа (from_attributes). В базе хранится только category_id."""

    @property
    def category(self):
        return self.category_rel.name if self.category_rel else None


class Receipt(_CategoryName, Base):
    __tablename__ = "receipts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    store = Column(String(255), nullable=True)
    # Дата покупки в формате YYYY-MM-DD (см. app/dates.py)
    date = Column(String(20), nullable=True, index=True)
    # Время покупки в формате HH:MM
    time = Column(String(10), nullable=True)
    # Вычисляет сервер: категория самой дорогой позиции (app/categories.py, main_category_id)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True)
    total = Column(Float, nullable=True, default=0.0)
    currency = Column(String(3), nullable=False, default="KZT", server_default="KZT")
    rate_to_kzt = Column(Float, nullable=True)
    image_path = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    
    user = relationship("User", back_populates="receipts")
    items = relationship("ReceiptItem", back_populates="receipt", cascade="all, delete-orphan")
    category_rel = relationship("Category")

class ReceiptItem(_CategoryName, Base):
    __tablename__ = "receipt_items"
    id = Column(Integer, primary_key=True, index=True)
    receipt_id = Column(Integer, ForeignKey("receipts.id"), nullable=False)
    name = Column(String(500), nullable=False)
    quantity = Column(Float, nullable=True, default=1.0)
    price_per_unit = Column(Float, nullable=True, default=0.0)
    total_price = Column(Float, nullable=True, default=0.0)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True)
    receipt = relationship("Receipt", back_populates="items")
    category_rel = relationship("Category")


class Budget(_CategoryName, Base):
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # NULL — общий лимит. Лимит удалённой категории удаляется вместе с ней
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=True, index=True)
    limit = Column(Float, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="budgets")
    category_rel = relationship("Category")


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    __table_args__ = (
        UniqueConstraint("date", "currency", name="uq_exchange_rates_date_currency"),
    )

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    currency = Column(String(3), nullable=False)
    rate_to_kzt = Column(Float, nullable=False)
    fetched_at = Column(DateTime, default=utcnow, nullable=False)

