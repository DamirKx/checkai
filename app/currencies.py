"""Поддерживаемые валюты. Единственный источник списка для API, фронта и проверок."""
from typing import Dict, Any, List, Optional

CURRENCIES: Dict[str, Dict[str, Any]] = {
    "KZT": {"symbol": "₸", "name": "Казахстанский тенге", "digits": 0},
    "RUB": {"symbol": "₽", "name": "Российский рубль", "digits": 0},
    "USD": {"symbol": "$", "name": "Доллар США", "digits": 2},
    "EUR": {"symbol": "€", "name": "Евро", "digits": 2},
    "KGS": {"symbol": "сом", "name": "Киргизский сом", "digits": 2},
    "UZS": {"symbol": "сум", "name": "Узбекский сум", "digits": 0},
    "CNY": {"symbol": "¥", "name": "Китайский юань", "digits": 2},
    "TRY": {"symbol": "₺", "name": "Турецкая лира", "digits": 2},
}

# Названия на kk и en (I18N_TASKS.md, п. 3.6). Русское — поле name выше
CURRENCY_NAMES: Dict[str, Dict[str, str]] = {
    "KZT": {"kk": "Қазақстан теңгесі", "en": "Kazakhstani tenge"},
    "RUB": {"kk": "Ресей рублі", "en": "Russian ruble"},
    "USD": {"kk": "АҚШ доллары", "en": "US dollar"},
    "EUR": {"kk": "Еуро", "en": "Euro"},
    "KGS": {"kk": "Қырғыз сомы", "en": "Kyrgyzstani som"},
    "UZS": {"kk": "Өзбек сумы", "en": "Uzbekistani som"},
    "CNY": {"kk": "Қытай юані", "en": "Chinese yuan"},
    "TRY": {"kk": "Түрік лирасы", "en": "Turkish lira"},
}

DEFAULT_CURRENCY = "KZT"


def normalize_currency(value: Any) -> str:
    """'usd ' -> 'USD'. Неизвестная валюта — ошибка с кодом currency_unsupported (станет 422)."""
    if value is None:
        return DEFAULT_CURRENCY
    code = str(value).strip().upper()
    if not code:
        return DEFAULT_CURRENCY
    if code not in CURRENCIES:
        # Импорт здесь: app.i18n тянет fastapi, а этот модуль импортируют и скрипты без него
        from app.i18n import validation_error
        raise validation_error("currency_unsupported", value=str(value))
    return code


def currency_name(code: str, lang: Optional[str] = "ru") -> str:
    """Название валюты на языке lang; перевода нет — русское."""
    return CURRENCY_NAMES.get(code, {}).get(lang or "ru") or CURRENCIES[code]["name"]


def get_currencies_list(lang: Optional[str] = "ru") -> List[Dict[str, Any]]:
    """Формат для эндпоинта GET /api/currencies: name — на языке запроса, формат прежний."""
    return [{"code": code, **info, "name": currency_name(code, lang)} for code, info in CURRENCIES.items()]
