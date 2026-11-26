"""Служебные строки чека (итого, НДС, оплата, реквизиты): распознать и убрать из позиций."""
import re
from typing import Any, Dict, List


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
