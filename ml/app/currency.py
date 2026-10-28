"""Определение валюты чека по распознанным строкам OCR."""
import re
from typing import List, Tuple, Optional

# Символы и маркеры валют
_SYMBOL_PATTERNS = [
    ("KZT", re.compile(r"(?:₸|\bтг\b|\bтнг\b|\bтенге\b|\bKZT\b)", re.IGNORECASE)),
    ("RUB", re.compile(r"(?:₽|\bруб(?:\.|\b)|\bRUB\b|\bRUR\b|(?<=\d)\s*р\.(?!\w))", re.IGNORECASE)),
    ("USD", re.compile(r"(?:\$|\bUSD\b)", re.IGNORECASE)),
    ("EUR", re.compile(r"(?:€|\bEUR\b)", re.IGNORECASE)),
    ("KGS", re.compile(r"(?:\bсом\b|\bKGS\b)", re.IGNORECASE)),
    ("UZS", re.compile(r"(?:\bсум\b|\bсўм\b|\bUZS\b)", re.IGNORECASE)),
    ("CNY", re.compile(r"(?:¥|元|\bCNY\b|\bRMB\b)", re.IGNORECASE)),
    ("TRY", re.compile(r"(?:₺|\bTL\b|\bTRY\b)", re.IGNORECASE)),
]

# Маркеры страны чека
_KZ_COUNTRY_RE = re.compile(
    r"(?:\bБИН\b|\bИИН\b|\bРНМ\b|\bЗНМ\b|НДС\s*12\s*%|ҚҚС|[әғқңөұүһі])",
    re.IGNORECASE,
)
_RU_COUNTRY_RE = re.compile(
    r"(?:\bИНН\s*\d{10,12}\b|\bФН\b|\bФД\b|\bФП\b|НДС\s*20\s*%|\bОФД\b|Кассовый\s+чек)",
    re.IGNORECASE,
)


def detect_currency(lines: List[str]) -> Tuple[Optional[str], str]:
    """Валюта чека по OCR-строкам.
    
    Возвращает (код ISO или None, источник: 'symbol' | 'country' | 'default').
    """
    text = "\n".join(lines)
    if not text.strip():
        return None, "default"

    # 1. Поиск по символам и словам валют (самая частая)
    scores: dict = {}
    for code, pattern in _SYMBOL_PATTERNS:
        matches = len(pattern.findall(text))
        if matches > 0:
            scores[code] = matches

    if scores:
        top_currency = max(scores, key=scores.get)
        return top_currency, "symbol"

    # 2. Поиск по маркерам страны
    if _KZ_COUNTRY_RE.search(text):
        return "KZT", "country"
    if _RU_COUNTRY_RE.search(text):
        return "RUB", "country"

    # 3. Ничего не найдено
    return None, "default"
