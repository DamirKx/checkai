"""Числа в тексте OCR: итог чека, сумма позиций, подтверждение цен позиций текстом чека."""
import re
from typing import Any, Dict, List, Optional

from .service_lines import normalize_service_text


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
