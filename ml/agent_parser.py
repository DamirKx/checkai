"""Разбор чека языковой моделью (Qwen через Ollama): промпты, вызов модели, самокоррекция суммы, длинные чеки.

Правила, которые проверяют и чистят ответ модели по тексту OCR, — в service_lines.py, receipt_totals.py
и receipt_items.py. Их имена, нужные снаружи, по-прежнему импортируются отсюда.
"""
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
from .receipt_items import (
    correct_item_quantities,
    drop_fiscal_operator_store,
    drop_invented_items,  # noqa: F401 — здесь не нужна, но её импортируют из agent_parser (тесты)
    finalize_receipt_items,
)
from .receipt_totals import (
    all_items_confirmed,
    calculate_items_total,
    extract_ocr_numbers,
    extract_total_from_ocr,
    is_item_confirmed,
)
from .service_lines import clean_items


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
