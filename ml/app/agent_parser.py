import copy
import json
import re
from typing import Any, Dict, List, Optional

import ollama


MODEL = "qwen2.5:3b"


SYSTEM_PROMPT = """
Ты — AI-модуль для анализа кассовых чеков.

На вход поступает OCR-текст кассового чека.
Извлеки только реальные данные покупки.

Верни ТОЛЬКО JSON:

{
  "store": "название магазина",
  "date": "дата и время",
  "items": [
    {
      "name": "название товара",
      "quantity": 1,
      "price_per_unit": 100.0,
      "total_price": 100.0,
      "category": null
    }
  ],
  "total": 100.0,
  "currency": null
}



Определи currency: ISO-код KZT, RUB, USD, EUR, KGS, UZS, CNY, TRY или null, если валюта неизвестна.

========================
STORE
========================

Название магазина может находиться в любой части чека,
в том числе в конце.

Просматривай ВЕСЬ OCR-текст.

Не используй вместо магазина:
- адрес;
- город;
- область;
- улицу;
- проспект;
- дом;
- корпус;
- индекс;
- место расчетов;
- номер документа;
- номер чека;
- ИНН;
- ФН;
- ФД;
- ФП;
- номер терминала;
- номер мерчанта.

Например:

00 ДНС Ритейл

может означать:

ДНС Ритейл

Если рядом находится адрес Ростов-на-Дону,
это не означает, что адрес является названием магазина.

Если встречается:

ООО "КРИСТИАН ДИОР КУТЮР СТОЛЕШНИКОВ"

это название организации и его следует использовать как store.


========================
DATE
========================

Найди дату и время покупки.

Пример:

13.05.19
21:17

может быть:

"date": "13.05.19 21:17"

Если дата отсутствует:

"date": ""


========================
ITEMS
========================

Добавляй только реальные товары или услуги,
за которые покупатель платит.

ОСОБЕННО ВАЖНО:

Если строка содержит название товара,
а следующая или ближайшая строка имеет формат:

1 X 65900.00

то строка перед ней является сильным кандидатом
на реальный товар.

Например:

С854ОСВТЕ, РУЧКА-РЕМЕШОК ДЛЯ СУМКИ
1 X 65900.00

означает:

{
  "name": "С854ОСВТЕ, РУЧКА-РЕМЕШОК ДЛЯ СУМКИ",
  "quantity": 1,
  "price_per_unit": 65900.00,
  "total_price": 65900.00,
  "category": null
}

Другой пример:

MO447CTZ0, CYMKA (M928, TU ( U. ))
1 X 160000.00

означает второй отдельный товар:

{
  "name": "MO447CTZ0, CYMKA (M928, TU ( U. ))",
  "quantity": 1,
  "price_per_unit": 160000.00,
  "total_price": 160000.00,
  "category": null
}

Если в чеке есть две конструкции:

название товара
1 X 65900.00

и

название другого товара
1 X 160000.00

то это ДВА товара.

Не заменяй второй товар строкой НДС, ИТОГ,
ПОЛУЧЕНО или другой служебной строкой.


========================
НЕ ТОВАРЫ
========================

Никогда не добавляй в items:

- ИТОГ
- ИТОГО
- НТОГ
- НТОГО
- ВСЕГО
- К ОПЛАТЕ
- ОПЛАТА
- ПОЛУЧЕНО
- ПЛАТ.КАРТОЙ
- НДС
- НДС 20%
- НДС 10%
- HAC 20%
- HAC 10%
- СУММА НДС
- СУMMA HAC
- МЕСТО РАСЧЕТОВ
- МЕСТО РАСЧЁТОВ
- СМЕНА
- ЧЕК
- КАССОВЫЙ ЧЕК
- ПРИХОД
- КАССИР
- КАССА
- ПАВИЛЬОН
- БУТИК
- КАССОВЫЙ ЗАЛ
- ОТДЕЛ
- ДОКУМЕНТ
- НОМЕР ДОКУМЕНТА
- ТЕРМИНАЛ
- МЕРЧАНТ
- КАРТА
- MASTERCARD
- VISA
- КОД АВТОРИЗАЦИИ
- НОМЕР ССЫЛКИ
- ИНН
- КПП
- ФН
- ФД
- ФП
- РН ККТ
- САЙТ ФНС
- САЙТ ОФД
- ОФД
- URL
- АДРЕС
- АДРЕС МАГАЗИНА
- АДРЕС ДЛЯ ПРЕТЕНЗИЙ
- БЕЗНАЛИЧНЫМИ
- НАЛИЧНЫМИ


========================
OCR-ОШИБКИ
========================

OCR может путать русские и английские буквы.

Например:

HAC

может означать:

НДС

Поэтому:

HAC 20%

НЕ является товаром.


НТОГ

может означать:

ИТОГ

Поэтому НТОГ НЕ является товаром.


CMартфOH

может означать:

Смартфон


TOBAP

может означать:

ТОВАР

Это служебная метка типа позиции,
а не название товара.


========================
КОЛИЧЕСТВО И ЦЕНА
========================

Если:

1 X 4199.00

то:

quantity = 1
price_per_unit = 4199.00
total_price = 4199.00


Если:

4 X 163.00

то:

quantity = 4
price_per_unit = 163.00
total_price = 652.00


Если:

800 X 10.22

то:

quantity = 800
price_per_unit = 10.22
total_price = 8176.00


========================
TOTAL
========================

Найди итоговую сумму всего чека.

Приоритет:

1. ИТОГО
2. ИТОГ
3. НТОГ
4. ВСЕГО
5. К ОПЛАТЕ
6. ОПЛАТА

Не используй сумму НДС как total.

Не используй цену отдельного товара как total.

Не используй ПОЛУЧЕНО как название товара.


========================
ВАЖНЫЕ ПРИМЕРЫ
========================

Так делать НЕЛЬЗЯ:

{
  "name": "СМЕНА:149 ЧЕК:6",
  "price_per_unit": 125009
}

СМЕНА и ЧЕК — служебные данные.


Так делать НЕЛЬЗЯ:

{
  "name": "HAC 20%",
  "price_per_unit": 160000
}

HAC 20% — OCR-вариант НДС.


Так делать НЕЛЬЗЯ:

{
  "name": "НТОГ",
  "price_per_unit": 225900
}

НТОГ — итог чека.


Так делать НЕЛЬЗЯ:

{
  "name": "ПОЛУЧЕНО:",
  "price_per_unit": 225900
}

ПОЛУЧЕНО — платежная информация.


========================
ФИНАЛЬНАЯ ПРОВЕРКА
========================

Перед ответом обязательно проверь:

1. store — организация, а не адрес.
2. date — дата и время покупки.
3. items содержат только реальные товары.
4. Найдены все строки вида "название + количество X цена".
5. НДС/HAC отсутствуют в items.
6. ИТОГ/НТОГ отсутствуют в items.
7. СМЕНА/ЧЕК отсутствуют в items.
8. ПОЛУЧЕНО отсутствует в items.
9. ПЛАТ.КАРТОЙ отсутствует в items.
10. МЕСТО РАСЧЕТОВ отсутствует в items.
11. Документы и реквизиты отсутствуют в items.
12. total — итог всего чека.
13. Не создавай фиктивный товар для совпадения суммы.

Верни только JSON.
"""


def _call_llm(
    prompt: str,
    system_prompt: str = SYSTEM_PROMPT
) -> Dict[str, Any]:

    response = ollama.Client(timeout=120).chat(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        format="json",
        options={
            "temperature": 0,
            "num_predict": 2048
        }
    )

    content = response["message"]["content"].strip()

    try:
        return json.loads(content)

    except json.JSONDecodeError:

        content = re.sub(
            r"^```json\s*",
            "",
            content,
            flags=re.IGNORECASE
        )

        content = re.sub(
            r"\s*```$",
            "",
            content
        )

        return json.loads(content)


def parse_receipt(
    ocr_text: str
) -> Dict[str, Any]:

    prompt = f"""
Проанализируй OCR-текст кассового чека.

OCR-ТЕКСТ:

{ocr_text}

Извлеки:

- store
- date
- items
- total
- currency

Название магазина ищи по всему чеку.

Для определения товаров особенно внимательно
ищи конструкции:

НАЗВАНИЕ ТОВАРА
количество X цена

Например:

MO447CTZ0, CYMKA
1 X 160000.00

Это реальный товар стоимостью 160000.

Не используй как товары:

- НДС
- HAC 20%
- ИТОГ
- НТОГ
- СМЕНА
- ЧЕК
- ПОЛУЧЕНО
- ПЛАТ.КАРТОЙ
- МЕСТО РАСЧЕТОВ
- служебные реквизиты.

Верни только JSON.
"""

    result = _call_llm(prompt)

    result.setdefault("store", "")
    result.setdefault("date", "")
    result.setdefault("items", [])
    result.setdefault("total", None)
    result.setdefault("currency", None)

    if not isinstance(
        result["items"],
        list
    ):
        result["items"] = []

    return result


def normalize_service_text(
    text: str
) -> str:

    text = str(text).upper().strip()

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    # OCR-варианты НДС
    text = text.replace(
        "HAC",
        "НДС"
    )

    text = text.replace(
        "НAC",
        "НДС"
    )

    text = text.replace(
        "HAС",
        "НДС"
    )

    # OCR-вариант ИТОГ
    if text.startswith("НТОГ"):
        text = "ИТОГ" + text[4:]

    return text


def is_service_item(
    name: str
) -> bool:

    if not name:
        return True

    text = normalize_service_text(
        name
    )

    service_keywords = [
        "ИТОГ",
        "ИТОГО",
        "ВСЕГО",
        "К ОПЛАТЕ",
        "ОПЛАТА",
        "ПОЛУЧЕНО",
        "ПЛАТ.КАРТОЙ",
        "НДС",
        "СУММА НДС",
        "МЕСТО РАСЧЕТОВ",
        "МЕСТО РАСЧЁТОВ",
        "СМЕНА",
        "ЧЕК:",
        "ЧЕК №",
        "КАССОВЫЙ ЧЕК",
        "КАССИР",
        "КАССА",
        "ПАВИЛЬОН",
        "БУТИК",
        "ДОКУМЕНТ",
        "ТЕРМИНАЛ",
        "МЕРЧАНТ",
        "КАРТА:",
        "MASTERCARD",
        "VISA",
        "КОД АВТОРИЗАЦИИ",
        "НОМЕР ССЫЛКИ",
        "ИНН",
        "КПП",
        "РН ККТ",
        "САЙТ ФНС",
        "САЙТ ОФД",
        "ОФД",
        "БЕЗНАЛИЧНЫМИ",
        "НАЛИЧНЫМИ",
        "ПРИХОД",
        "АДРЕС ДЛЯ",
        "АДРЕС МАГАЗИНА"
    ]

    for keyword in service_keywords:

        if keyword in text:
            return True

    # ФН, ФД, ФП и похожие реквизиты
    if re.fullmatch(
        r"(ФН|ФД|ФП)\s*[:№]?\s*\w*",
        text
    ):
        return True

    # СМЕНА:149 ЧЕК:6
    if (
        "СМЕНА" in text
        and "ЧЕК" in text
    ):
        return True

    # Строка только из служебных чисел
    if re.fullmatch(
        r"[\d\s\-:/]+",
        text
    ):
        return True

    # URL
    if (
        "HTTP://" in text
        or "HTTPS://" in text
        or "WWW." in text
        or ".RU" in text
        or ".COM" in text
    ):
        return True

    return False


def clean_items(
    data: Dict[str, Any]
) -> Dict[str, Any]:

    items = data.get(
        "items",
        []
    )

    if not isinstance(
        items,
        list
    ):
        data["items"] = []
        return data

    clean: List[Dict[str, Any]] = []

    for item in items:

        if not isinstance(
            item,
            dict
        ):
            continue

        name = str(
            item.get(
                "name",
                ""
            )
        ).strip()

        if is_service_item(name):
            continue

        try:
            quantity = float(
                item.get(
                    "quantity",
                    1
                )
            )

        except (
            TypeError,
            ValueError
        ):
            quantity = 1.0

        try:
            price = float(
                item.get(
                    "price_per_unit",
                    0
                )
            )

        except (
            TypeError,
            ValueError
        ):
            price = 0.0

        try:
            total_price = float(
                item.get(
                    "total_price",
                    quantity * price
                )
            )

        except (
            TypeError,
            ValueError
        ):
            total_price = (
                quantity * price
            )

        if price <= 0:
            continue

        if quantity <= 0:
            continue

        if total_price <= 0:
            total_price = (
                quantity * price
            )

        if quantity.is_integer():
            quantity = int(quantity)

        item["name"] = name
        item["quantity"] = quantity
        item["price_per_unit"] = round(
            price,
            2
        )
        item["total_price"] = round(
            total_price,
            2
        )

        item.setdefault(
            "category",
            None
        )

        clean.append(item)

    data["items"] = clean

    return data


def _parse_number(
    value: str
) -> Optional[float]:

    if not value:
        return None

    value = str(value).strip()

    value = value.replace(
        " ",
        ""
    )

    value = value.replace(
        ",",
        "."
    )

    value = re.sub(
        r"^[^\d]+",
        "",
        value
    )

    try:
        return float(value)

    except ValueError:
        return None


def extract_total_from_ocr(
    ocr_text: str
) -> Optional[float]:

    lines = [
        line.strip()
        for line in ocr_text.splitlines()
        if line.strip()
    ]

    # OCR иногда распознаёт "ИТОГО" как "ТОГ".
    # Ищем сумму непосредственно после такого заголовка.
    for i, line in enumerate(lines):
        if re.fullmatch(r"(?:ИТОГО|ИТОГ|ТОГ)", line, re.I):
            if i + 1 < len(lines):
                match = re.search(
                    r"\d+[.,]\d{2}",
                    lines[i + 1]
                )
                if match:
                    return float(
                        match.group().replace(",", ".")
                    )

    # Для строки "К ОПЛАТЕ" пропускаем обменный курс.
    # Число без разделителя восстанавливаем только
    # если оно согласуется с суммой позиций и надбавкой.
    for i, line in enumerate(lines):
        if not re.search(r"К\s*ОПЛАТЕ", line, re.I):
            continue

        for candidate in lines[i + 1:i + 5]:
            candidate = candidate.strip()

            if "=" in candidate:
                continue

            if re.fullmatch(r"\d{5,8}", candidate):
                possible_total = int(candidate) / 100

                # Ищем промежуточную сумму и обслуживание.
                subtotal = None
                service = None

                for j, source_line in enumerate(lines):
                    normalized = source_line.lower()

                    if (
                        "надбавка" in normalized
                        or "обслуж" in normalized
                    ):
                        for nearby in lines[j + 1:j + 5]:
                            nearby = nearby.strip()

                            if re.fullmatch(
                                r"\d+[.,]\d{2}", nearby
                            ):
                                subtotal = float(
                                    nearby.replace(",", ".")
                                )
                                break

                    service_match = re.fullmatch(
                        r"\+(\d+[.,]\d{2})",
                        source_line.strip()
                    )

                    if service_match:
                        service = float(
                            service_match.group(1).replace(",", ".")
                        )

                # Принимаем сумму только при совпадении.
                if subtotal is not None and service is not None:
                    calculated = round(subtotal + service, 2)

                    if abs(calculated - possible_total) < 0.01:
                        return possible_total

                continue

            if re.fullmatch(r"[^\d]*\d+[.,]\d{2}", candidate):
                match = re.search(
                    r"\d+[.,]\d{2}",
                    candidate
                )
                if match:
                    return float(
                        match.group().replace(",", ".")
                    )

    patterns = [
        (
            r"(?:ИТОГО|ИТОГ|НТОГ|ВСЕГО)"
            r"\s*[:\-]?\s*[^\d]{0,15}"
            r"(\d[\d\s]*[.,]\d{1,2})"
        ),
        (
            r"(?:К\s*ОПЛАТЕ|ОПЛАТА)"
            r"\s*[:\-]?\s*[^\d]{0,15}"
            r"(\d[\d\s]*[.,]\d{1,2})"
        )
    ]

    candidates = []

    for line in lines:

        for pattern in patterns:

            matches = re.findall(
                pattern,
                line,
                flags=re.IGNORECASE
            )

            for value in matches:

                number = _parse_number(
                    value
                )

                if number is not None:
                    candidates.append(
                        number
                    )

    if candidates:
        return candidates[-1]

    keywords = [
        "ИТОГО",
        "ИТОГ",
        "НТОГ",
        "ВСЕГО",
        "К ОПЛАТЕ",
        "ОПЛАТА"
    ]

    for i, line in enumerate(
        lines
    ):

        normalized = normalize_service_text(
            line
        )

        if not any(
            keyword in normalized
            for keyword in keywords
        ):
            continue

        next_lines = lines[
            i + 1:i + 4
        ]

        for next_line in next_lines:

            matches = re.findall(
                r"(\d[\d\s]*[.,]\d{1,2})",
                next_line
            )

            for value in matches:

                number = _parse_number(
                    value
                )

                if number is not None:
                    return number

    return None


def calculate_items_total(
    data: Dict[str, Any]
) -> float:

    total = 0.0

    items = data.get(
        "items",
        []
    )

    if not isinstance(
        items,
        list
    ):
        return total

    for item in items:

        try:

            quantity = float(
                item.get(
                    "quantity",
                    1
                )
            )

            price = float(
                item.get(
                    "price_per_unit",
                    0
                )
            )

            total += (
                quantity * price
            )

        except (
            TypeError,
            ValueError
        ):
            continue

    return round(
        total,
        2
    )


def needs_correction(
    ocr_text: str,
    data: Dict[str, Any]
) -> bool:

    ocr_total = extract_total_from_ocr(
        ocr_text
    )

    if ocr_total is None:
        return False

    calculated_total = calculate_items_total(
        data
    )

    return abs(
        calculated_total - ocr_total
    ) > 0.01


def self_correction(
    ocr_text: str,
    data: Dict[str, Any]
) -> Dict[str, Any]:

    ocr_total = extract_total_from_ocr(
        ocr_text
    )

    calculated_total = calculate_items_total(
        data
    )

    prompt = f"""
Повторно проверь результат анализа кассового чека.

OCR-ТЕКСТ:

{ocr_text}


ПЕРВИЧНЫЙ РЕЗУЛЬТАТ:

{json.dumps(
    data,
    ensure_ascii=False,
    indent=2
)}


TOTAL ИЗ OCR:

{ocr_total}


СУММА НАЙДЕННЫХ ТОВАРОВ:

{calculated_total}


Сумма товаров не совпадает с итогом.

Нужно повторно найти РЕАЛЬНЫЕ товары,
а не создавать служебные позиции.


Ищи конструкции:

НАЗВАНИЕ
количество X цена


Например:

С854ОСВТЕ, РУЧКА-РЕМЕШОК ДЛЯ СУМКИ
1 X 65900.00

это товар стоимостью 65900.


MO447CTZ0, CYMKA (M928, TU ( U. ))
1 X 160000.00

это ВТОРОЙ товар стоимостью 160000.


65900 + 160000 = 225900.


НЕ используй как товары:

СМЕНА
ЧЕК
HAC 20%
НДС
НТОГ
ИТОГ
ПОЛУЧЕНО
ПЛАТ.КАРТОЙ
МЕСТО РАСЧЕТОВ
БЕЗНАЛИЧНЫМИ
СУММА НДС
служебные номера.


Очень важно:

Не создавай фиктивный товар только для того,
чтобы сумма товаров совпала с total.

Каждый item должен соответствовать
реальной товарной строке OCR.


Проверь:

- store
- date
- items
- quantity
- price_per_unit
- total_price
- total


Верни только JSON:

{{
  "store": "...",
  "date": "...",
  "items": [
    {{
      "name": "...",
      "quantity": 1,
      "price_per_unit": 100.0,
      "total_price": 100.0,
      "category": null
    }}
  ],
  "total": 100.0
}}
"""

    result = _call_llm(
        prompt
    )

    result.setdefault(
        "store",
        data.get(
            "store",
            ""
        )
    )

    result.setdefault(
        "date",
        data.get(
            "date",
            ""
        )
    )

    result.setdefault(
        "items",
        data.get(
            "items",
            []
        )
    )

    result.setdefault(
        "total",
        data.get(
            "total"
        )
    )

    if not isinstance(
        result["items"],
        list
    ):
        result["items"] = data.get(
            "items",
            []
        )

    return result



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


def extract_ocr_numbers(
    ocr_text: str
) -> set:
    """Собирает множество чисел с копейками из OCR-текста (форматы 2350.00, 2350,00, =2350.00, 2 350.00)."""
    numbers = set()
    for match in re.findall(
        r"=?\s*(\d+(?:[\s\u00a0]\d{3})*[.,]\d{2})",
        ocr_text or ""
    ):
        cleaned = (
            match.replace(" ", "")
            .replace("\u00a0", "")
            .replace(",", ".")
        )
        try:
            numbers.add(round(float(cleaned), 2))
        except ValueError:
            continue
    return numbers


def _number_in_ocr(
    value: Any,
    ocr_numbers: set
) -> bool:
    try:
        num = round(float(value), 2)
    except (TypeError, ValueError):
        return False
    return any(
        abs(num - candidate) < 0.01
        for candidate in ocr_numbers
    )


def is_item_confirmed(
    item: Dict[str, Any],
    ocr_numbers: set
) -> bool:
    """Позиция подтверждена, если её price_per_unit и total_price есть во множестве чисел OCR."""
    if not isinstance(item, dict):
        return False
    return (
        _number_in_ocr(item.get("price_per_unit"), ocr_numbers)
        and _number_in_ocr(item.get("total_price"), ocr_numbers)
    )


def all_items_confirmed(
    items: List[Dict[str, Any]],
    ocr_numbers: set
) -> bool:
    if not items:
        return False
    return all(
        is_item_confirmed(item, ocr_numbers)
        for item in items
    )


def _should_accept_correction(
    ocr_text: str,
    before_data: Dict[str, Any],
    after_data: Dict[str, Any],
    ocr_numbers: set
) -> bool:
    """Принимает результат self_correction, только если:
    (а) |сумма позиций - итог| уменьшилась И
    (б) ни у одной позиции, подтверждённой до коррекции, цена не стала неподтверждённой.
    """
    ocr_total = extract_total_from_ocr(ocr_text)
    if ocr_total is None:
        return False

    before_diff = abs(calculate_items_total(before_data) - ocr_total)
    after_diff = abs(calculate_items_total(after_data) - ocr_total)
    if after_diff >= before_diff - 1e-6:
        return False

    before_items = before_data.get("items") or []
    after_items = after_data.get("items") or []

    if len(before_items) == len(after_items):
        for before_item, after_item in zip(before_items, after_items):
            if is_item_confirmed(before_item, ocr_numbers):
                if not is_item_confirmed(after_item, ocr_numbers):
                    return False
    else:
        after_prices = []
        after_totals = []
        for item in after_items:
            if not isinstance(item, dict):
                continue
            try:
                after_prices.append(round(float(item.get("price_per_unit")), 2))
            except (TypeError, ValueError):
                pass
            try:
                after_totals.append(round(float(item.get("total_price")), 2))
            except (TypeError, ValueError):
                pass

        for before_item in before_items:
            if not is_item_confirmed(before_item, ocr_numbers):
                continue
            b_price = round(float(before_item["price_per_unit"]), 2)
            b_total = round(float(before_item["total_price"]), 2)
            if not any(abs(b_price - p) < 0.01 for p in after_prices):
                return False
            if not any(abs(b_total - t) < 0.01 for t in after_totals):
                return False

    return True


def finalize_receipt_items(ocr_text, data):
    data = clean_items(data)
    data = remove_confirmed_service_rows(ocr_text, data)
    data = correct_item_quantities(ocr_text, data)
    return data


def parse_receipt_with_correction(
    ocr_text: str
) -> Dict[str, Any]:

    # 1. Первичный анализ Qwen
    data = parse_receipt(
        ocr_text
    )

    # 2. Убираем служебные строки
    data = clean_items(
        data
    )

    data = correct_item_quantities(
        ocr_text,
        data
    )

    ocr_numbers = extract_ocr_numbers(
        ocr_text
    )

    # 3. Сравниваем сумму товаров
    # с итогом из OCR
    if (
        needs_correction(ocr_text, data)
        and not all_items_confirmed(data.get("items", []), ocr_numbers)
    ):

        # 4. Qwen повторно анализирует чек
        previous_currency = data.get("currency")
        before_correction = copy.deepcopy(data)

        corrected = self_correction(
            ocr_text,
            data
        )

        if not corrected.get("currency"):
            corrected["currency"] = previous_currency

        # 5. Снова удаляем служебный мусор
        corrected = clean_items(
            corrected
        )

        corrected = correct_item_quantities(
            ocr_text,
            corrected
        )

        if _should_accept_correction(
            ocr_text,
            before_correction,
            corrected,
            ocr_numbers
        ):
            data = corrected

    # 6. Исправляем количество товаров по OCR
    data = correct_item_quantities(
        ocr_text,
        data
    )

    # 7. Отдельно получаем итог из OCR
    ocr_total = extract_total_from_ocr(
        ocr_text
    )

    if (
        data.get("total") is None
        and ocr_total is not None
    ):
        data["total"] = ocr_total

    data = finalize_receipt_items(ocr_text, data)

    ocr_total = extract_total_from_ocr(ocr_text)
    if ocr_total is not None:
        data["total"] = ocr_total

    return data
