from typing import List, Literal, Optional, Dict, Any, get_args
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator, model_validator

from app.dates import normalize_date_time
from app.currencies import normalize_currency, DEFAULT_CURRENCY
from app.categories import (
    DEFAULT_ICON, DESCRIPTION_MAX_LENGTH, ICON_MAX_LENGTH, NAME_MAX_LENGTH, UNCATEGORIZED_NAME,
)
from app import rates

import math
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


# Кто выбрал категорию позиции (PROFILE_TASKS.md, п. 2.1). None — категории нет
CategorySource = Literal["ai", "keywords", "rule", "user"]
# photo — распознан с фото, manual — введён руками (NEXT_WEEK_TASKS.md, A.2.1)
ReceiptSource = Literal["photo", "manual"]

# Пределы чисел в чеке (A.2.2). Те же константы — MAX_AMOUNT / MAX_QUANTITY во frontend/static/js/app.js
MAX_AMOUNT = 1e9
MAX_QUANTITY = 100000


def _check_finite(v: Optional[float]) -> Optional[float]:
    # NaN и Infinity JSON-парсер Python пропускает, а аналитику они ломают
    if v is not None and not math.isfinite(v):
        raise ValueError("Укажите обычное число")
    return v


def _check_amount(v: Optional[float]) -> Optional[float]:
    v = _check_finite(v)
    if v is None:
        return None
    if v < 0:
        raise ValueError("Сумма не может быть отрицательной")
    if v > MAX_AMOUNT:
        raise ValueError("Слишком большая сумма: не больше 1 000 000 000")
    return v


def items_total(items: List["ItemIn"]) -> float:
    """Сумма позиций — итог чека, если его не прислали."""
    return round(sum(i.total_price or 0.0 for i in items), 2)


class ItemIn(BaseModel):
    # Без extra="forbid": новые поля фронта не должны давать 422 (A.2, п. 1)
    name: str = Field(max_length=500)
    quantity: Optional[float] = 1.0
    price_per_unit: Optional[float] = 0.0
    total_price: Optional[float] = 0.0
    category_id: Optional[int] = None
    # Название — если category_id нет (гость распознал чек, вошёл и сохраняет)
    category: Optional[str] = Field(default=None, max_length=100)
    category_source: Optional[CategorySource] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Укажите название товара")
        return v

    @field_validator("quantity")
    @classmethod
    def _validate_quantity(cls, v: Optional[float]) -> float:
        v = _check_finite(v)
        # Поле не прислали или null — одна штука; 0 — ошибка, а не «молча 1»
        if v is None:
            return 1.0
        if v <= 0:
            raise ValueError("Количество должно быть больше нуля")
        if v > MAX_QUANTITY:
            raise ValueError("Слишком большое количество: не больше 100 000")
        return v

    @field_validator("price_per_unit", "total_price")
    @classmethod
    def _validate_amounts(cls, v: Optional[float]) -> Optional[float]:
        return _check_amount(v)


class _ReceiptIn(BaseModel):
    """Общее у ReceiptCreate и ReceiptUpdate: дата и время, пределы итога, итог по позициям."""

    @field_validator("total", check_fields=False)
    @classmethod
    def _validate_total(cls, v: Optional[float]) -> Optional[float]:
        return _check_amount(v)

    @model_validator(mode="after")
    def _normalize_date_time(self):
        self.date, self.time = normalize_date_time(self.date, self.time)
        return self

    @model_validator(mode="after")
    def _total_from_items(self):
        # Итог не пришёл или 0, а позиции есть — итог = сумма позиций (A.2.2)
        if not self.total and self.items:
            self.total = items_total(self.items)
        return self


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    quantity: Optional[float] = 1.0
    price_per_unit: Optional[float] = 0.0
    total_price: Optional[float] = 0.0
    category_id: Optional[int] = None
    category: Optional[str] = None
    category_source: Optional[str] = None


class ReceiptCreate(_ReceiptIn):
    store: Optional[str] = Field(default=None, max_length=255)
    # Каноническое имя сети от ML. Не пришло — сервер вычислит его из store
    merchant: Optional[str] = Field(default=None, max_length=255)
    # Принимается в любом распознаваемом виде, сохраняется как YYYY-MM-DD
    date: Optional[str] = None
    time: Optional[str] = None
    # Учитывается только у чека без позиций: иначе категорию вычисляет сервер
    category_id: Optional[int] = None
    total: Optional[float] = 0.0
    currency: str = DEFAULT_CURRENCY
    # Путь, который вернул /api/analyze, например /data/receipts/<uuid>.jpg
    image_path: Optional[str] = Field(default=None, max_length=500)
    # Без image_path сервер всегда ставит manual (A.2.1)
    source: ReceiptSource = "photo"
    # «Всё равно сохранить» после окна дубликата (C.2.4). В базу не сохраняется
    allow_duplicate: bool = False
    items: List[ItemIn] = []

    @field_validator("currency", mode="before")
    @classmethod
    def _validate_currency(cls, v: Any) -> str:
        return normalize_currency(v)

    @model_validator(mode="after")
    def _manual_without_photo(self):
        if not self.image_path:
            self.source = "manual"
        return self


class ReceiptUpdate(_ReceiptIn):
    store: Optional[str] = Field(default=None, max_length=255)
    merchant: Optional[str] = Field(default=None, max_length=255)
    date: Optional[str] = None
    time: Optional[str] = None
    category_id: Optional[int] = None
    total: Optional[float] = 0.0
    # None — оставить текущую валюту чека (клиент, который не знает о валютах, её не сбросит)
    currency: Optional[str] = None
    # Фронт шлёт тот же payload, что при создании. Источник задаётся при создании чека и не меняется
    source: Optional[ReceiptSource] = None
    # Правка сделала чек копией другого — «Всё равно сохранить» (C.2.5)
    allow_duplicate: bool = False
    items: List[ItemIn] = []

    @field_validator("currency", mode="before")
    @classmethod
    def _validate_currency(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return normalize_currency(v)


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    store: Optional[str]
    merchant: Optional[str] = None
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
    source: ReceiptSource = "photo"
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


ThemeName = Literal["dark", "light", "system"]
# Палитра каждого цвета — в frontend/static/css/app.css («Акцентные цвета»): новый цвет добавляется вместе с фронтом
AccentName = Literal["violet", "red", "orange", "green", "blue", "pink"]


class Preferences(BaseModel):
    """Настройки оформления (PROFILE_TASKS.md, п. 1.2). Неизвестное значение — 422."""
    theme: ThemeName = "system"
    accent: AccentName = "violet"
    # Согласие на ИИ-анализ расходов (NEXT_WEEK_TASKS.md, B.2.5)
    ai_insights: bool = False

    @classmethod
    def from_stored(cls, value: Any) -> "Preferences":
        """Из колонки users.preferences: битые и устаревшие значения заменяются значениями по умолчанию."""
        if isinstance(value, Preferences):
            return value
        stored = value if isinstance(value, dict) else {}
        clean = {}
        for key in cls.model_fields:
            if key in stored:
                try:
                    cls.model_validate({key: stored[key]})
                    clean[key] = stored[key]
                except ValueError:
                    pass
        return cls(**clean)


class PreferencesUpdate(BaseModel):
    """PATCH /api/auth/me: меняются только пришедшие ключи, null у ключа — значение по умолчанию."""
    theme: Optional[str] = None
    accent: Optional[str] = None
    ai_insights: Optional[bool] = None

    @field_validator("theme")
    @classmethod
    def _check_theme(cls, v: Optional[str]) -> Optional[str]:
        allowed = get_args(ThemeName)
        if v is not None and v not in allowed:
            raise ValueError(f"Неизвестная тема. Доступны: {', '.join(allowed)}")
        return v

    @field_validator("accent")
    @classmethod
    def _check_accent(cls, v: Optional[str]) -> Optional[str]:
        allowed = get_args(AccentName)
        if v is not None and v not in allowed:
            raise ValueError(f"Неизвестный акцентный цвет. Доступны: {', '.join(allowed)}")
        return v


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=255)
    email: Optional[str] = None
    new_password: Optional[str] = None
    current_password: Optional[str] = None
    base_currency: Optional[str] = None
    # {"accent": "red"} не сбрасывает тему; "preferences": null — вернуть всё по умолчанию
    preferences: Optional[PreferencesUpdate] = None
    # То же, что preferences.ai_insights (B.2.5): фронт шлёт оба поля. Текущий пароль не нужен
    ai_insights_consent: Optional[bool] = None

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
    last_login_at: Optional[datetime] = None
    # "/avatars/<uuid>.jpg" или null (PROFILE_TASKS.md, п. 1.1)
    avatar_url: Optional[str] = None
    # false — кода восстановления нет, сбросить забытый пароль не получится: стоит предложить создать
    has_recovery_code: bool = False
    preferences: Preferences = Preferences()
    # Копия preferences.ai_insights (B.2.5)
    ai_insights_consent: bool = False

    @field_validator("preferences", mode="before")
    @classmethod
    def _stored_preferences(cls, v: Any) -> Preferences:
        return Preferences.from_stored(v)

    @computed_field
    @property
    def rates_pending(self) -> bool:
        """True, пока после смены основной валюты докачиваются курсы: суммы временно неполные (п. 1.6)."""
        return rates.user_rates_pending(self.id)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    user: UserOut
    # Только в ответах регистрации и сброса пароля: новый код восстановления. Сервер показывает его один раз
    recovery_code: Optional[str] = None


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


class UnconvertedCount(BaseModel):
    currency: str
    count: int


class ActivityDay(BaseModel):
    date: str
    count: int
    total: float


class TopStore(BaseModel):
    name: str
    count: int


class TopCategory(BaseModel):
    id: int
    total: float


class BiggestReceipt(BaseModel):
    id: int
    total: float
    store: Optional[str] = None


class ProfileStatsOut(BaseModel):
    """GET /api/profile/stats (PROFILE_TASKS.md, п. 1.3). Все суммы — в основной валюте."""
    base_currency: str
    receipts_count: int
    items_count: int
    total_spent: float
    average_receipt: float
    unconverted: List[UnconvertedCount] = []
    first_receipt_at: Optional[str] = None
    last_receipt_at: Optional[str] = None
    activity: List[ActivityDay] = []
    longest_streak: int = 0
    top_weekday: Optional[int] = None
    top_store: Optional[TopStore] = None
    top_category: Optional[TopCategory] = None
    biggest_receipt: Optional[BiggestReceipt] = None
    month_total: float = 0.0
    prev_month_total: float = 0.0


class ResetPasswordRequest(BaseModel):
    """Сброс забытого пароля по коду восстановления."""
    email: str
    recovery_code: str = Field(max_length=64)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Пароль должен содержать минимум {MIN_PASSWORD_LENGTH} символов")
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError("Пароль слишком длинный")
        return value


class RecoveryCodeRequest(BaseModel):
    """Новый код восстановления — только с текущим паролем."""
    password: str


class RecoveryCodeOut(BaseModel):
    recovery_code: str


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




# --- Автокатегория для названий без фото (NEXT_WEEK_TASKS.md, A.2.3) ---

CATEGORIZE_MAX_ITEMS = 50


class CategorizeName(BaseModel):
    name: str = Field(max_length=500)


class CategorizeIn(BaseModel):
    items: List[CategorizeName] = Field(min_length=1, max_length=CATEGORIZE_MAX_ITEMS)


class CategorizedItem(BaseModel):
    name: str
    category_id: Optional[int] = None
    category: Optional[str] = None
    category_source: Optional[str] = None


class CategorizeOut(BaseModel):
    items: List[CategorizedItem]
