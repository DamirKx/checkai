from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.dates import normalize_date_time
from app.currencies import normalize_currency, DEFAULT_CURRENCY
from app.categories import (
    DEFAULT_ICON, DESCRIPTION_MAX_LENGTH, ICON_MAX_LENGTH, NAME_MAX_LENGTH, UNCATEGORIZED_NAME,
)

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


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    quantity: Optional[float] = 1.0
    price_per_unit: Optional[float] = 0.0
    total_price: Optional[float] = 0.0
    category_id: Optional[int] = None
    # Название — если category_id нет (гость распознал чек, вошёл и сохраняет)
    category: Optional[str] = Field(default=None, max_length=100)


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    quantity: Optional[float] = 1.0
    price_per_unit: Optional[float] = 0.0
    total_price: Optional[float] = 0.0
    category_id: Optional[int] = None
    category: Optional[str] = None


class ReceiptCreate(BaseModel):
    store: Optional[str] = Field(default=None, max_length=255)
    # Принимается в любом распознаваемом виде, сохраняется как YYYY-MM-DD
    date: Optional[str] = None
    time: Optional[str] = None
    # Учитывается только у чека без позиций: иначе категорию вычисляет сервер
    category_id: Optional[int] = None
    total: Optional[float] = 0.0
    currency: str = DEFAULT_CURRENCY
    # Путь, который вернул /api/analyze, например /data/receipts/<uuid>.jpg
    image_path: Optional[str] = Field(default=None, max_length=500)
    items: List[ItemIn] = []

    @field_validator("currency", mode="before")
    @classmethod
    def _validate_currency(cls, v: Any) -> str:
        return normalize_currency(v)

    @model_validator(mode="after")
    def _normalize_date_time(self):
        self.date, self.time = normalize_date_time(self.date, self.time)
        return self


class ReceiptUpdate(BaseModel):
    store: Optional[str] = Field(default=None, max_length=255)
    date: Optional[str] = None
    time: Optional[str] = None
    category_id: Optional[int] = None
    total: Optional[float] = 0.0
    # None — оставить текущую валюту чека (клиент, который не знает о валютах, её не сбросит)
    currency: Optional[str] = None
    items: List[ItemIn] = []

    @field_validator("currency", mode="before")
    @classmethod
    def _validate_currency(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return normalize_currency(v)

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
    category_id: Optional[int] = None
    category: Optional[str] = None
    total: Optional[float]
    currency: str = DEFAULT_CURRENCY
    rate_to_kzt: Optional[float] = None
    base_currency: str = DEFAULT_CURRENCY
    rate_to_base: Optional[float] = None
    image_path: Optional[str] = None
    created_at: datetime
    items: List[ItemOut] = []


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
    base_currency: Optional[str] = None

    @field_validator("base_currency", mode="before")
    @classmethod
    def _validate_base_currency(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return normalize_currency(v)

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
    base_currency: str = DEFAULT_CURRENCY
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
    # Сколько чеков вошло в суммы (остальные — в unconverted)
    converted_count: int = 0
    avg_receipt: float
    top_store: Optional[str]
    by_category: Dict[str, float]
    by_store: Dict[str, float]
    by_month: Dict[str, float]
    recent_items: List[Dict[str, Any]]
    base_currency: str = DEFAULT_CURRENCY
    unconverted: List[Dict[str, Any]] = []


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
    # None — общий лимит на месяц
    category_id: Optional[int] = None
    limit: float

    @model_validator(mode="before")
    @classmethod
    def _reject_category_name(cls, data: Any) -> Any:
        # Иначе лимит старого формата {"category": "Кафе"} молча стал бы общим
        if isinstance(data, dict) and "category" in data and "category_id" not in data:
            raise ValueError("Лимит задаётся по category_id, а не по названию категории")
        return data

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
            if item.category_id in seen_categories:
                if item.category_id is None:
                    raise ValueError("Общий лимит (без категории) указан несколько раз")
                raise ValueError("Лимит для одной категории указан несколько раз")
            seen_categories.add(item.category_id)
        return items


class BudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category_id: Optional[int] = None
    category: Optional[str] = None
    limit: float


class BudgetStatusItem(BaseModel):
    category_id: Optional[int] = None
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
    base_currency: str = DEFAULT_CURRENCY
    unconverted: List[Dict[str, Any]] = []


class CurrencyOut(BaseModel):
    code: str
    symbol: str
    name: str
    digits: int


# --- Категории (ограничения — CATEGORIES_TASKS.md, п. 0.4) ---

class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    icon: str
    position: int


class CategoryDefaultOut(BaseModel):
    name: str
    description: Optional[str] = None
    icon: str


class CategoryUpdate(BaseModel):
    """Все поля необязательные: меняется только то, что пришло в запросе (model_fields_set)."""
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, v: Any) -> str:
        name = str(v or "").strip()
        if not 1 <= len(name) <= NAME_MAX_LENGTH:
            raise ValueError(f"Название категории: от 1 до {NAME_MAX_LENGTH} символов")
        if name.lower() == UNCATEGORIZED_NAME.lower():
            raise ValueError("Это название зарезервировано")
        return name

    @field_validator("description", mode="before")
    @classmethod
    def _validate_description(cls, v: Any) -> Optional[str]:
        description = str(v).strip() if v is not None else ""
        if len(description) > DESCRIPTION_MAX_LENGTH:
            raise ValueError(f"Описание категории: не больше {DESCRIPTION_MAX_LENGTH} символов")
        return description or None

    @field_validator("icon", mode="before")
    @classmethod
    def _validate_icon(cls, v: Any) -> str:
        # Один эмодзи может занимать несколько символов (флаги, цвет кожи)
        icon = str(v).strip() if v is not None else ""
        if len(icon) > ICON_MAX_LENGTH:
            raise ValueError(f"Иконка: не больше {ICON_MAX_LENGTH} символов")
        return icon or DEFAULT_ICON


class CategoryCreate(CategoryUpdate):
    name: str


class CategoryOrder(BaseModel):
    ids: List[int]


class ItemsMove(BaseModel):
    item_ids: List[int] = Field(min_length=1, max_length=500)
    # None — «Без категории»
    category_id: Optional[int] = None
    remember: bool = False


class ItemsMoveOut(BaseModel):
    moved: int
    rules_created: int


class CategoryDeleteOut(BaseModel):
    success: bool = True
    moved_items: int
    deleted_budget: bool


class CategoryRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pattern: str
    category_id: int
    category: str
    created_at: datetime


