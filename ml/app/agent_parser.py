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
  "total": 100.0
}


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

    response = ollama.chat(
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
            "temperature": 0
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

    # 3. Сравниваем сумму товаров
    # с итогом из OCR
    if needs_correction(
        ocr_text,
        data
    ):

        # 4. Qwen повторно анализирует чек
        data = self_correction(
            ocr_text,
            data
        )

        # 5. Снова удаляем служебный мусор
        data = clean_items(
            data
        )

    # 6. Отдельно получаем итог из OCR
    ocr_total = extract_total_from_ocr(
        ocr_text
    )

    if (
        data.get("total") is None
        and ocr_total is not None
    ):
        data["total"] = ocr_total

    return data
