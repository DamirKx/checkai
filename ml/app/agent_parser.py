import copy
import difflib
import json
import os
import re
from typing import Any, Dict, List, Optional

import ollama

from .merchant import canonical_merchant
from .textfix import fix_item_names
from .fiscal import extract_fiscal_info


MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
# Два фиксированных уровня окна контекста (задача 5.3): Ollama не перезагружает модель на каждом чеке
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
NUM_CTX_LONG = int(os.getenv("OLLAMA_NUM_CTX_LONG", "16384"))
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))
OLLAMA_LONG_TIMEOUT = int(os.getenv("OLLAMA_LONG_TIMEOUT", "180"))


class LLMTruncatedError(RuntimeError):
    """Ответ модели обрезан по лимиту токенов (done_reason == 'length')."""


RECEIPT_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "store": {"type": "string"},
        "date": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "price_per_unit": {"type": "number"},
                    "total_price": {"type": "number"},
                    "category": {"type": ["string", "null"]},
                },
                "required": ["name", "quantity", "price_per_unit", "total_price"],
            },
        },
        "total": {"type": ["number", "null"]},
        "currency": {"type": ["string", "null"]},
    },
    "required": ["store", "date", "items", "total"],
}

CHUNK_ITEMS_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
            },
        },
    },
    "required": ["items"],
}

CHUNK_SYSTEM_PROMPT = """Ты — модуль извлечения позиций из фрагмента длинного кассового чека.
Верни ТОЛЬКО JSON вида: {"items": [["название товара", quantity, price_per_unit, total_price], ...]}.
- Только реальные товары и услуги с ценой.
- НЕ включай ИТОГ, НДС, оплату, сдачу, скидки без товара, реквизиты ОФД, адреса, кассира.
- Не придумывай товары, которых нет в тексте."""


SYSTEM_PROMPT = """
Ты — модуль разбора кассовых чеков. На вход — OCR-текст чека, на выход — ТОЛЬКО JSON:

{
  "store": "название магазина",
  "date": "дата и время",
  "items": [{"name": "название товара", "quantity": 1, "price_per_unit": 100.0, "total_price": 100.0, "category": null}],
  "total": 100.0,
  "currency": null
}

currency — KZT, RUB, USD, EUR, KGS, UZS, CNY, TRY или null, если валюта неизвестна.

STORE — организация, а не адрес. Ищи по всему чеку, в том числе в конце. Не бери адрес, город, улицу,
индекс, ИНН/БИН, ФН/ФД/ФП, номер чека, терминала или мерчанта.
Если есть юрлицо (ООО, ТОО, ИП, АО, Управляющая компания, SDN BHD, LLP, LTD) — store это оно, целиком.
Название торгового центра, филиала или «Магазин 199» — не store, если на чеке есть юрлицо.
«ООО "КРИСТИАН ДИОР"» → store = «ООО "КРИСТИАН ДИОР"». «00 ДНС Ритейл» → «ДНС Ритейл».

DATE — дата и время покупки: строки «13.05.19» и «21:17» → "13.05.19 21:17". Нет даты → "".

ITEMS — только реальные товары и услуги. Товар — строка с названием, за которой рядом идёт строка с ценой:
  РУЧКА-РЕМЕШОК ДЛЯ СУМКИ          → name «РУЧКА-РЕМЕШОК ДЛЯ СУМКИ», quantity 1, price_per_unit 65900, total_price 65900
  1 X 65900.00
  шт. Lenor кондиционер д/белья    → name «Lenor кондиционер д/белья 1.8л», quantity 1, price_per_unit 2850, total_price 2850
  1.8л
  2850.00 *1 =2850.00
Название может занимать две строки — объединяй их. Каждая пара «название + цена» — отдельный товар,
даже если названия похожи. Не создавай товар, которого нет в тексте, ради совпадения суммы.

Количество и цена: «4 X 163.00» → quantity 4, price_per_unit 163, total_price 652;
«163.00 *4 =652.00» → то же; «800 X 10.22» → quantity 800, total_price 8176.

НЕ товары (никогда не клади в items): ИТОГ/ИТОГО/ВСЕГО/К ОПЛАТЕ/ОПЛАТА, НДС и СУММА НДС, ПОЛУЧЕНО,
НАЛИЧНЫМИ/БЕЗНАЛИЧНЫМИ/ПЛАТ.КАРТОЙ, СДАЧА, СМЕНА, ЧЕК, КАССА, КАССИР, ПРИХОД, МЕСТО РАСЧЕТОВ,
реквизиты (ИНН, БИН, ФН, ФД, ФП, РН ККТ, ЗНМ, ОФД, сайт), карта, терминал, адрес.
OCR путает буквы: «HAC 20%» = НДС, «НТОГ» = ИТОГ — это тоже не товары. «TOBAP» — служебная метка, не название.

TOTAL — итог всего чека (ИТОГО, ИТОГ, ВСЕГО, К ОПЛАТЕ). Не сумма НДС и не цена одного товара.

Верни только JSON.
"""


def _compute_llm_params(prompt: str, is_long: bool = False) -> Dict[str, Any]:
    """Выбирает num_predict, num_ctx и timeout от длины текста (задача 5.3)."""
    line_count = max(1, len(prompt.splitlines()))
    if is_long or line_count > 100:
        num_ctx = NUM_CTX_LONG
        num_predict = max(2048, min(6144, line_count * 40))
        timeout = OLLAMA_LONG_TIMEOUT
    else:
        num_ctx = NUM_CTX
        num_predict = 2048
        timeout = OLLAMA_TIMEOUT
    return {"num_ctx": num_ctx, "num_predict": num_predict, "timeout": timeout}


def _normalize_compact_items(items: Any) -> List[Dict[str, Any]]:
    """Преобразует компактные массивы [name, qty, price, total] в стандартные словари (задача 5.5)."""
    if not isinstance(items, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for it in items:
        if isinstance(it, dict):
            normalized.append(it)
        elif isinstance(it, (list, tuple)) and len(it) >= 4:
            normalized.append({
                "name": str(it[0] or "").strip(),
                "quantity": it[1],
                "price_per_unit": it[2],
                "total_price": it[3],
                "category": None,
            })
    return normalized


_LLM_NOISE_PATTERNS = (
    re.compile(r"https?://|www\.|\.(?:ru|kz|com)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:инн|бин|кпп|рн\s*ккт|знм|зн\s*ккт|рнм|фн|фд|фп|фпд|фискальн\w*\s+признак)\s*[:№#\-]?\s*[\w\-]+", re.IGNORECASE),
    re.compile(r"^\s*(?:сайт\s+(?:фнс|офд)|офд\s*[:\-]|оператор\s+фискальных\s+данных)", re.IGNORECASE),
    re.compile(r"^\s*(?:смена\s*[:№]|чек\s*[:№]|кассир\s*[:\-]|касса\s*[:№]|терминал\s*[:№]|мерчант\s*[:№])", re.IGNORECASE),
    re.compile(r"^\s*(?:код\s+авторизации|номер\s+ссылки|спасибо\s+за\s+покупку|добро\s+пожаловать|ждем\s+вас\s+снова)", re.IGNORECASE),
    re.compile(r"^\s*(?:бонус\w*\s+(?:начислено|списано|баланс)|карта\s+лояльности|возврат\s+товара\s+в\s+течение)", re.IGNORECASE),
)


def filter_service_lines_for_llm(ocr_text: str) -> str:
    """Убирает явный служебный мусор (ОФД, URL, реклама, реквизиты) до отправки в LLM (задача 5.5).

    Не удаляет строки с юрлицами/магазинами, датами, товарами и ИТОГ/К ОПЛАТЕ.
    """
    if os.getenv("OCR_FILTER_SERVICE_LINES", "1").strip().lower() in ("0", "false", "no", "off"):
        return ocr_text
    kept: List[str] = []
    for line in ocr_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pat.search(stripped) for pat in _LLM_NOISE_PATTERNS):
            continue
        kept.append(stripped)
    return "\n".join(kept) if kept else ocr_text


def _call_llm(
    prompt: str,
    system_prompt: str = SYSTEM_PROMPT,
    schema: Optional[Dict[str, Any]] = None,
    is_long: bool = False,
) -> Dict[str, Any]:
    params = _compute_llm_params(prompt, is_long=is_long)
    use_schema = os.getenv("OLLAMA_USE_SCHEMA", "1").strip().lower() not in ("0", "false", "no", "off")
    fmt: Any = (schema if schema is not None else RECEIPT_JSON_SCHEMA) if use_schema else "json"

    client = ollama.Client(timeout=params["timeout"])
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    options = {
        "temperature": 0,
        "num_predict": params["num_predict"],
        "num_ctx": params["num_ctx"],
    }

    try:
        response = client.chat(
            model=MODEL,
            messages=messages,
            format=fmt,
            options=options,
            keep_alive="30m",
        )
    except Exception:
        if fmt != "json":
            response = client.chat(
                model=MODEL,
                messages=messages,
                format="json",
                options=options,
                keep_alive="30m",
            )
        else:
            raise

    done_reason = response.get("done_reason") if isinstance(response, dict) else getattr(response, "done_reason", None)
    msg = response["message"] if isinstance(response, dict) else response.message
    content = (msg["content"] if isinstance(msg, dict) else msg.content).strip()

    if done_reason == "length":
        raise LLMTruncatedError(f"Ollama truncated output at num_predict={params['num_predict']}")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        content = re.sub(r"^```json\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)
        parsed = json.loads(content)

    if isinstance(parsed, dict) and "items" in parsed:
        parsed["items"] = _normalize_compact_items(parsed["items"])
    return parsed


def parse_receipt(
    ocr_text: str,
    is_long: bool = False,
) -> Dict[str, Any]:
    filtered_text = filter_service_lines_for_llm(ocr_text)
    prompt = f"""
Разбери кассовый чек. OCR-ТЕКСТ:

{filtered_text}

Найди store, date, items, total, currency по правилам. Особенно внимательно ищи пары
«название товара + строка с количеством и ценой». Верни только JSON.
"""

    result = _call_llm(prompt, is_long=is_long)

    result.setdefault("store", "")
    result.setdefault("date", "")
    result.setdefault("items", [])
    result.setdefault("total", None)
    result.setdefault("currency", None)

    result["items"] = _normalize_compact_items(result.get("items"))

    result["merchant"] = canonical_merchant(result.get("store"))
    result["fiscal"] = extract_fiscal_info(ocr_text)

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


# Строка, в которой только сумма: «1000 тг», «1 000 ₸», «=20140.00», «107.14 tг». Копейки необязательны
_AMOUNT_LINE = re.compile(
    r"[=≡]?\s*(\d{1,3}(?:[  ]\d{3})+|\d+)([.,]\d{1,2})?\s*"
    r"(?:[тt][гr]\.?|₸|kzt|tenge|руб\.?|р\.?|rub|rm)?",
    re.IGNORECASE,
)


def _amount_line(line: str) -> Optional[float]:
    """Сумма, если строка — это только сумма. Без копеек и без валюты — от трёх цифр:
    «1» после «ИТОГ» — скорее количество позиций, чем итог."""
    text = line.strip()
    match = _AMOUNT_LINE.fullmatch(text)
    if not match:
        return None
    digits = re.sub(r"[ \u00a0]", "", match.group(1))
    has_currency = bool(re.search(r"\d\s*[^\d\s.,=≡]", text))
    if not match.group(2) and not has_currency and len(digits) < 3:
        return None
    return float(digits + (match.group(2) or "").replace(",", "."))


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
        if re.fullmatch(r"(?:ИТОГО|ИТОГ|ТОГ)\s*:?", line, re.I):
            if i + 1 < len(lines):
                # «Итого» / «1000 тг»: на казахстанских чеках копеек часто нет.
                # Без этого бралось следующее число с копейками — сумма НДС
                amount = _amount_line(lines[i + 1])
                if amount is not None:
                    return amount
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
            # Дошли до другой подписи («НДС», «Наличными») — сумма итога не здесь
            if _amount_line(next_line) is None and re.fullmatch(r"[^\d]*[A-Za-zА-Яа-яЁё]{3,}[^\d]*", next_line):
                break

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


def _deduplicate_chunk_items(existing: List[Dict[str, Any]], new_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Убирает дубли позиций на стыках перекрывающихся блоков длинного чека (задача 5.4)."""
    if not existing:
        return list(new_items)
    tail = existing[-5:]
    result: List[Dict[str, Any]] = []
    for item in new_items:
        name = str(item.get("name") or "").casefold()
        try:
            t_price = float(item.get("total_price") or 0.0)
        except (TypeError, ValueError):
            t_price = 0.0
        is_dup = False
        for prev in tail:
            p_name = str(prev.get("name") or "").casefold()
            try:
                p_price = float(prev.get("total_price") or 0.0)
            except (TypeError, ValueError):
                p_price = 0.0
            if (
                abs(t_price - p_price) < 0.02
                and re.findall(r"\d+", name) == re.findall(r"\d+", p_name)
                and difflib.SequenceMatcher(None, name, p_name).ratio() >= 0.92
            ):
                is_dup = True
                break
        if not is_dup:
            result.append(item)
    return result


def parse_long_receipt(
    ocr_text: str,
    on_progress: Optional[Any] = None,
    chunk_lines: int = 35,
    overlap_lines: int = 3,
) -> Dict[str, Any]:
    """Разбор длинного чека кусками (задача 5.4).

    1. Шапка (первые ~20 строк) → магазин и дата.
    2. Итог → extract_total_from_ocr по полному тексту.
    3. Позиции → блоками по 35 строк с перекрытием 3 строки в компактном формате.
    4. Дедупликация стыков и стандартная финализация.
    """
    from .parser import parse_receipt as parse_receipt_regex

    raw_lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    header_data = parse_receipt_regex(raw_lines[:25]) if raw_lines else {}
    store = str(header_data.get("store") or "")
    date_str = str(header_data.get("date") or "")
    currency = header_data.get("currency")

    filtered_lines = [line.strip() for line in filter_service_lines_for_llm(ocr_text).splitlines() if line.strip()]
    if not filtered_lines:
        filtered_lines = raw_lines

    step = max(10, chunk_lines - overlap_lines)
    chunks: List[List[str]] = []
    idx = 0
    while idx < len(filtered_lines):
        chunks.append(filtered_lines[idx:idx + chunk_lines])
        if idx + chunk_lines >= len(filtered_lines):
            break
        idx += step

    all_items: List[Dict[str, Any]] = []
    warnings: List[str] = ["long_receipt"]
    total_chunks = len(chunks)

    for c_idx, chunk in enumerate(chunks, start=1):
        if on_progress:
            on_progress("llm", c_idx - 1, total_chunks)
        chunk_prompt = (
            "Извлеки только товары и услуги с ценами из этого фрагмента чека.\n"
            "ФРАГМЕНТ:\n" + "\n".join(chunk)
        )
        try:
            chunk_res = _call_llm(
                chunk_prompt,
                system_prompt=CHUNK_SYSTEM_PROMPT,
                schema=CHUNK_ITEMS_JSON_SCHEMA,
                is_long=False,
            )
            chunk_items = _normalize_compact_items(chunk_res.get("items") if isinstance(chunk_res, dict) else [])
            all_items.extend(_deduplicate_chunk_items(all_items, chunk_items))
        except LLMTruncatedError:
            if "llm_truncated" not in warnings:
                warnings.append("llm_truncated")
        if on_progress:
            on_progress("llm", c_idx, total_chunks)

    data: Dict[str, Any] = {
        "store": store,
        "date": date_str,
        "items": all_items,
        "total": extract_total_from_ocr(ocr_text),
        "currency": currency,
        "parser_used": "qwen_chunks",
        "warnings": warnings,
    }

    data = finalize_receipt_items(ocr_text, data)
    fix_item_names(data.get("items"))
    data = drop_fiscal_operator_store(ocr_text, data)
    data["merchant"] = canonical_merchant(data.get("store"))
    data["fiscal"] = extract_fiscal_info(ocr_text)
    return data


def parse_receipt_with_correction(
    ocr_text: str,
    is_long: bool = False,
    on_progress: Optional[Any] = None,
) -> Dict[str, Any]:
    long_threshold = int(os.getenv("OCR_LONG_LINES_THRESHOLD", "80"))
    line_count = len([ln for ln in ocr_text.splitlines() if ln.strip()])
    if is_long or line_count > long_threshold:
        return parse_long_receipt(ocr_text, on_progress=on_progress)

    warnings: List[str] = []

    # 1. Первичный анализ Qwen; при обрезке ответа (done_reason == 'length') переходим к разбору кусками (5.3, 5.4)
    try:
        data = parse_receipt(ocr_text)
    except LLMTruncatedError:
        chunked = parse_long_receipt(ocr_text, on_progress=on_progress)
        chunk_warnings = list(chunked.get("warnings") or [])
        if "llm_truncated" not in chunk_warnings:
            chunk_warnings.append("llm_truncated")
        chunked["warnings"] = chunk_warnings
        return chunked

    # 2. Убираем служебные строки
    data = clean_items(data)
    data = correct_item_quantities(ocr_text, data)
    ocr_numbers = extract_ocr_numbers(ocr_text)

    # 3. Сравниваем сумму товаров с итогом из OCR
    if (
        needs_correction(ocr_text, data)
        and not all_items_confirmed(data.get("items", []), ocr_numbers)
    ):
        previous_currency = data.get("currency")
        before_correction = copy.deepcopy(data)

        try:
            corrected = self_correction(ocr_text, data)
            if not corrected.get("currency"):
                corrected["currency"] = previous_currency
            corrected = clean_items(corrected)
            corrected = correct_item_quantities(ocr_text, corrected)

            if _should_accept_correction(
                ocr_text,
                before_correction,
                corrected,
                ocr_numbers,
            ):
                data = corrected
        except LLMTruncatedError:
            warnings.append("llm_truncated")

    # 6. Исправляем количество товаров по OCR
    data = correct_item_quantities(ocr_text, data)

    # 7. Отдельно получаем итог из OCR
    ocr_total = extract_total_from_ocr(ocr_text)
    if data.get("total") is None and ocr_total is not None:
        data["total"] = ocr_total

    data = finalize_receipt_items(ocr_text, data)

    ocr_total = extract_total_from_ocr(ocr_text)
    if ocr_total is not None:
        data["total"] = ocr_total

    # «Lепог» → «Lenor»: OCR смешивает латиницу и кириллицу в одном слове
    fix_item_names(data.get("items"))
    data = drop_fiscal_operator_store(ocr_text, data)

    # store — как напечатано на чеке, merchant — каноническое имя сети
    data["merchant"] = canonical_merchant(data.get("store"))
    # Фискальные реквизиты
    data["fiscal"] = extract_fiscal_info(ocr_text)
    data.setdefault("parser_used", "qwen")
    if warnings:
        data["warnings"] = warnings

    return data
