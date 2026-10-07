from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.dates import normalize_date_time

import re

try:
    import email_validator  # noqa: F401
    from pydantic import EmailStr
except ImportError:
    EmailStr = None

_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

MIN_PASSWORD_LENGTH = 8
# bcrypt учитывает только первые 72 байта пароля
MAX_PASSWORD_BYTES = 72


class ItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(min_length=1, max_length=500)
    quantity: Optional[float] = 1.0
    price_per_unit: Optional[float] = 0.0
    total_price: Optional[float] = 0.0
    category: Optional[str] = Field(default=None, max_length=100)


class ReceiptCreate(BaseModel):
    store: Optional[str] = Field(default=None, max_length=255)
    # Принимается в любом распознаваемом виде, сохраняется как YYYY-MM-DD
    date: Optional[str] = None
    time: Optional[str] = None
    category: Optional[str] = Field(default="Продукты", max_length=100)
    total: Optional[float] = 0.0
    # Путь, который вернул /api/analyze, например /data/receipts/<uuid>.jpg
    image_path: Optional[str] = Field(default=None, max_length=500)
    items: List[ItemSchema] = []

    @model_validator(mode="after")
    def _normalize_date_time(self):
        self.date, self.time = normalize_date_time(self.date, self.time)
        return self


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    store: Optional[str]
    date: Optional[str]
    time: Optional[str]
    category: Optional[str]
    total: Optional[float]
    image_path: Optional[str] = None
    created_at: datetime
    items: List[ItemSchema] = []


class UserCreate(BaseModel):
    email: EmailStr if EmailStr is not None else str
    password: str
    full_name: Optional[str] = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_REGEX.match(v):
            raise ValueError("Введите корректный email (например, user@example.com)")
        return v

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Пароль должен содержать минимум {MIN_PASSWORD_LENGTH} символов")
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError("Пароль слишком длинный")
        return value


class UserLogin(BaseModel):
    # Без строгой проверки формата: вход не должен ломаться для уже существующих аккаунтов
    email: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: Optional[str] = None
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AnalyticsOut(BaseModel):
    total_spent: float
    receipts_count: int
    avg_receipt: float
    top_store: Optional[str]
    by_category: Dict[str, float]
    by_store: Dict[str, float]
    by_month: Dict[str, float]
    recent_items: List[Dict[str, Any]]
