"""Правка позиций после модели: количества, служебные строки, выдуманные товары, оператор фискальных данных вместо магазина.

PROMPT_EXAMPLE_ITEMS повторяет товары из примеров в промптах agent_parser.py — меняйте вместе.
"""
import difflib
import re
from typing import List

from .receipt_totals import extract_total_from_ocr
from .service_lines import clean_items


def correct_item_quantities(ocr_text, data):
    """Исправление количества при подтверждении OCR."""
    lines = [
        line.strip()
        for line in ocr_text.splitlines()
        if line.strip()
    ]

    price_qty_pattern = re.compile(
        r"(\d+[.,]\d{2})\s*[*xх×]\s*(\d+(?:[.,]\d+)?)",
        re.IGNORECASE
    )

    pairs = [
        (
            float(price.replace(",", ".")),
            float(qty.replace(",", "."))
        )
        for line in lines
        for price, qty in price_qty_pattern.findall(line)
    ]

    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue

        try:
            price = float(item.get("price_per_unit", 0))
        except (TypeError, ValueError):
            continue

        if price <= 0:
            continue

        # 1. Формат «цена * N» / «цена x N» / «цена × N» (сопоставление по цене)
        quantities = {
            qty
            for found_price, qty in pairs
            if abs(found_price - price) < 0.01
        }
        if len(quantities) == 1:
            qty = quantities.pop()
            item["quantity"] = int(qty) if qty == int(qty) else qty
            item["total_price"] = round(price * qty, 2)
            continue

        # 2. Старый формат «2350.001» + следующая строка «2350.00» (сопоставление по названию)
        name = str(item.get("name", "")).strip().lower()
        if not name:
            continue

        for i, line in enumerate(lines):
            if line.lower() != name:
                continue

            for j in range(i + 1, min(i + 4, len(lines) - 1)):
                match = re.fullmatch(
                    r"(\d+[.,]\d{2})([1-9])",
                    lines[j]
                )

                if not match:
                    continue

                found_price = float(
                    match.group(1).replace(",", ".")
                )
                quantity = int(match.group(2))

                next_line = lines[j + 1]

                if not re.fullmatch(
                    r"\d+[.,]\d{2}",
                    next_line
                ):
                    continue

                found_total = float(
                    next_line.replace(",", ".")
                )

                if (
                    abs(found_price - price) < 0.01
                    and abs(found_price * quantity - found_total) < 0.01
                ):
                    item["quantity"] = quantity
                    item["total_price"] = round(found_total, 2)
                    break

    return data


def remove_confirmed_service_rows(ocr_text, data):
    """Убирает подтверждённые строки итогов и обслуживания."""
    import re

    lines = [
        line.strip()
        for line in ocr_text.splitlines()
        if line.strip()
    ]

    service_context = any(
        re.search(r"надбавк|обслуж", line, re.I)
        for line in lines
    )

    subtotal = None
    service = None

    if service_context:
        for i, line in enumerate(lines):
            if re.search(r"надбавк|обслуж", line, re.I):
                for candidate in lines[i + 1:i + 5]:
                    if re.fullmatch(r"\d+[.,]\d{2}", candidate):
                        subtotal = float(candidate.replace(",", "."))
                        break

            match = re.fullmatch(
                r"\+(\d+[.,]\d{2})",
                line
            )

            if match:
                service = float(
                    match.group(1).replace(",", ".")
                )

    if subtotal is None or service is None:
        return data

    confirmed_total = extract_total_from_ocr(ocr_text)

    if confirmed_total is None:
        return data

    if abs(subtotal + service - confirmed_total) > 0.01:
        return data

    cleaned = []

    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue

        name = str(item.get("name", "")).lower()

        if re.search(
            r"надбавк|обслуж|подная суеса|полная сумма",
            name
        ):
            continue

        cleaned.append(item)

    data["items"] = cleaned
    return data


_NAME_TOKEN = re.compile(r"[0-9a-zа-яёіїјѕ]{3,}")
# Двойники латиницы и кириллицы сводятся к одной букве: OCR и модель пишут одно слово по-разному
_TWINS = str.maketrans("авекмнорстухпгі", "abekmhopctyxnri")


def _tokens(text: str) -> List[str]:
    return _NAME_TOKEN.findall(str(text or "").casefold().replace("ё", "е").translate(_TWINS))


# Товары из примеров в промптах: модель иногда переносит их в чек, где их нет (receipt_006 → «MO447CTZ0, CYMKA»)
PROMPT_EXAMPLE_ITEMS = (
    "MO447CTZ0, CYMKA (M928, TU ( U. ))",
    "С854ОСВТЕ, РУЧКА-РЕМЕШОК ДЛЯ СУМКИ",
    "Lenor кондиционер д/белья 1.8л",
    "СМЕНА:149 ЧЕК:6",
)


_FISCAL_OPERATOR = re.compile(r"оператор\s+фиск|(?<![\w])(?:офд|ofd)(?![\w])|oofd", re.IGNORECASE)
_OPERATOR_LABEL = re.compile(r"оператор\s+фиск", re.IGNORECASE)
_LEGAL_WORDS = {"тоо", "too", "ооо", "ао", "ип", "оао", "зао", "пао", "llp", "llc", "ltd", "jsc"}


def drop_fiscal_operator_store(ocr_text, data):
    """store — продавец. «Оператор фискальных данных: АО "Казахтелеком"» — это ОФД, а не магазин.

    Если название встречается в чеке только рядом с «ОФД» / «оператор фискальных данных»
    (в той же строке или сразу после подписи «Оператор фискальных данных»), store очищается: лучше пусто, чем чужая организация.
    """
    store = str(data.get("store") or "")
    words = [w for w in _tokens(store) if w not in _LEGAL_WORDS]
    if not words:
        return data
    lines = [line for line in ocr_text.splitlines() if line.strip()]
    mentions = [i for i, line in enumerate(lines) if any(w in _tokens(line) for w in words)]
    # Предыдущая строка учитывается, только если это подпись «Оператор фискальных данных: АО»,
    # перенесённая на следующую строку. Адрес сайта ОФД над продавцом (receipt_001) — не повод
    if mentions and all(
        _FISCAL_OPERATOR.search(lines[i]) or (i > 0 and _OPERATOR_LABEL.search(lines[i - 1]))
        for i in mentions
    ):
        data["store"] = ""
    return data


def drop_invented_items(ocr_text, data):
    """Убирает позиции, которых нет в тексте чека.

    - Похожа на пример из промпта — остаётся, только если все её слова есть в OCR.
    - Любая другая — остаётся, если в OCR есть хотя бы одно слово названия. Слова сравниваются нестрого:
      опечатки OCR и исправления модели («кемп» → «крем») проходят.
    """
    ocr_words = set(_tokens(ocr_text))
    if not ocr_words:
        return data

    def seen(word):
        return word in ocr_words or any(
            abs(len(w) - len(word)) <= 2 and difflib.SequenceMatcher(None, word, w).ratio() >= 0.75
            for w in ocr_words
        )

    examples = [set(_tokens(name)) for name in PROMPT_EXAMPLE_ITEMS]
    kept = []
    for item in data.get("items") or []:
        words = _tokens(item.get("name")) if isinstance(item, dict) else []
        if not words:
            kept.append(item)
            continue
        found = sum(1 for w in words if seen(w))
        like_example = any(len(set(words) & ex) * 2 >= len(ex) for ex in examples)
        if (found == len(words)) if like_example else (found > 0):
            kept.append(item)
    data["items"] = kept
    return data


def finalize_receipt_items(ocr_text, data):
    data = clean_items(data)
    data = drop_invented_items(ocr_text, data)
    data = remove_confirmed_service_rows(ocr_text, data)
    data = correct_item_quantities(ocr_text, data)
    return data
