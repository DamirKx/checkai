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


class ReceiptUpdate(BaseModel):
    store: Optional[str] = Field(default=None, max_length=255)
    date: Optional[str] = None
    time: Optional[str] = None
    category: Optional[str] = Field(default="Продукты", max_length=100)
    total: Optional[float] = 0.0
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
    email: str
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


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=255)
    email: Optional[str] = None
    new_password: Optional[str] = None
    current_password: Optional[str] = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        if not _EMAIL_REGEX.match(v):
            raise ValueError("Введите корректный email (например, user@example.com)")
        return v

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Пароль должен содержать минимум {MIN_PASSWORD_LENGTH} символов")
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError("Пароль слишком длинный")
        return value


class UserDelete(BaseModel):
    password: str


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
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    user: UserOut


class RefreshTokenInput(BaseModel):
    refresh_token: str


class AnalyticsOut(BaseModel):
    total_spent: float
    receipts_count: int
    avg_receipt: float
    top_store: Optional[str]
    by_category: Dict[str, float]
    by_store: Dict[str, float]
    by_month: Dict[str, float]
    recent_items: List[Dict[str, Any]]


class ForgotPasswordRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_REGEX.match(v):
            raise ValueError("Введите корректный email (например, user@example.com)")
        return v


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Пароль должен содержать минимум {MIN_PASSWORD_LENGTH} символов")
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError("Пароль слишком длинный")
        return value


class HealthCheckOut(BaseModel):
    status: str
    database: str
    ocr: str
    ollama: str


class TaskStatusOut(BaseModel):
    task_id: str
    status: str
    step: Optional[str] = None
    progress: int = 0
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class BudgetItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: Optional[str] = Field(default=None, max_length=100)
    limit: float

    @field_validator("category", mode="before")
    @classmethod
    def _clean_category(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            cleaned = v.strip()
            return cleaned if cleaned else None
        return v

    @field_validator("limit")
    @classmethod
    def _validate_limit(cls, v: float) -> float:
        if v is None or v <= 0:
            raise ValueError("Лимит бюджета должен быть больше нуля")
        if v > 1e12:
            raise ValueError("Лимит бюджета не может превышать 1 000 000 000 000")
        return round(float(v), 2)


class BudgetListUpdate(BaseModel):
    budgets: List[BudgetItem]

    @field_validator("budgets")
    @classmethod
    def _validate_budgets_list(cls, items: List[BudgetItem]) -> List[BudgetItem]:
        if len(items) > 50:
            raise ValueError("Количество лимитов не может превышать 50")

        seen_categories = set()
        for item in items:
            cat_key = item.category.lower() if item.category is not None else None
            if cat_key in seen_categories:
                if cat_key is None:
                    raise ValueError("Общий лимит (без категории) указан несколько раз")
                else:
                    raise ValueError(f"Категория «{item.category}» указана несколько раз")
            seen_categories.add(cat_key)
        return items


class BudgetStatusItem(BaseModel):
    category: Optional[str] = None
    limit: float
    spent: float
    remaining: float
    percent: float
    percentage: float
    is_over_budget: bool


class BudgetStatusOut(BaseModel):
    month: str
    total_budget: Optional[float] = None
    total_spent: float
    budgets: List[BudgetStatusItem]

