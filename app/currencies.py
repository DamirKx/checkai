"""Поддерживаемые валюты. Единственный источник списка для API, фронта и проверок."""
from typing import Dict, Any, List

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

DEFAULT_CURRENCY = "KZT"


def normalize_currency(value: Any) -> str:
    """'usd ' -> 'USD'. Неизвестная валюта — ValueError с понятным русским текстом (станет 422)."""
    if value is None:
        return DEFAULT_CURRENCY
    code = str(value).strip().upper()
    if not code:
        return DEFAULT_CURRENCY
    if code not in CURRENCIES:
        raise ValueError(f"Валюта «{value}» не поддерживается")
    return code


def get_currencies_list() -> List[Dict[str, Any]]:
    """Формат для эндпоинта GET /api/currencies."""
    return [{"code": code, **info} for code, info in CURRENCIES.items()]
