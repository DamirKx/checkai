import re
from collections import Counter
from typing import List, Optional, Tuple


SUPPORTED = {
    "KZT", "RUB", "USD", "EUR",
    "KGS", "UZS", "CNY", "TRY"
}

PATTERNS = {
    "KZT": [
        r"₸", r"\bтг\b", r"\bтнг\b",
        r"\bтенге\b", r"\bKZT\b"
    ],
    "RUB": [
        r"₽", r"\bруб(?:\.|ля|лей)?\b",
        r"\bRUB\b", r"\bRUR\b",
        r"\d[\s]*р\.(?!\s*/)"
    ],
    "USD": [r"\$", r"\bUSD\b"],
    "EUR": [r"€", r"\bEUR\b"],
    "KGS": [r"\bсом\b", r"\bKGS\b"],
    "UZS": [r"\bсум\b", r"\bсўм\b", r"\bUZS\b"],
    "CNY": [r"¥", r"元", r"\bCNY\b", r"\bRMB\b"],
    "TRY": [r"₺", r"\bTL\b", r"\bTRY\b"],
}


def detect_currency(lines: List[str]) -> Tuple[Optional[str], str]:
    """
    Определяет валюту по OCR-строкам.

    Возвращает:
        (currency, source)

    source:
        symbol  - символ или название валюты
        country - признаки страны
        default - определить не удалось
    """
    text = "\n".join(str(line) for line in lines)
    counts = Counter()

    # 1. Поиск символов и обозначений валют
    for currency, patterns in PATTERNS.items():
        for pattern in patterns:
            matches = re.findall(
                pattern,
                text,
                flags=re.IGNORECASE
            )
            counts[currency] += len(matches)

    if any(count > 0 for count in counts.values()):
        currency = counts.most_common(1)[0][0]
        if currency in SUPPORTED:
            return currency, "symbol"

    # 2. Определение по стране

    # Казахстан
    kz_patterns = [
        r"\bБИН\b",
        r"\bИИН\b",
        r"\bРНМ\b",
        r"\bЗНМ\b",
        r"НДС\s*12\s*%",
        r"ҚҚС",
        r"[әғқңөұүһі]"
    ]

    if any(
        re.search(p, text, re.IGNORECASE)
        for p in kz_patterns
    ):
        return "KZT", "country"

    # Россия
    ru_patterns = [
        r"\bИНН\s*:?\s*\d{10}(?:\d{2})?\b",
        r"\bФН\b",
        r"\bФД\b",
        r"\bФП\b",
        r"НДС\s*20\s*%",
        r"\bОФД\b",
        r"Кассовый чек"
    ]

    if any(
        re.search(p, text, re.IGNORECASE)
        for p in ru_patterns
    ):
        return "RUB", "country"

    # 3. Ничего не найдено
    return None, "default"