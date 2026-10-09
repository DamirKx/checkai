import json
import re
from datetime import datetime
from pathlib import Path

from app.ocr import analyze_receipt_comparison, init_ocr

BASE_DIR = Path(__file__).resolve().parent
IMAGES_DIR = BASE_DIR / "images"
ANNOTATIONS_DIR = BASE_DIR / "annotations"


def normalize_text(value):
    if not value:
        return ""
    return re.sub(r"[^a-zа-яё0-9]", "", str(value).lower())


def normalize_date(value):
    if not value:
        return None

    for fmt in (
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d.%m.%y",
        "%d.%m.%y %H:%M",
        "%d.%m.%Y %H:%M",
    ):
        try:
            return datetime.strptime(str(value), fmt).date().isoformat()
        except ValueError:
            pass

    return None


def get_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_item_total(item):
    total = get_number(item.get("total_price", item.get("total")))

    if total is not None:
        return total

    price = get_number(item.get("price_per_unit", item.get("price")))
    quantity = get_number(item.get("quantity", 1))

    if price is None or quantity is None:
        return None

    return price * quantity


def evaluate_parser(predicted, expected):
    predicted = predicted or {}

    # Проверяем магазин, дату и итоговую сумму
    store_correct = (
        bool(expected.get("store"))
        and normalize_text(predicted.get("store"))
        == normalize_text(expected.get("store"))
    )

    date_correct = (
        normalize_date(expected.get("date")) is not None
        and normalize_date(predicted.get("date"))
        == normalize_date(expected.get("date"))
    )

    predicted_total = get_number(predicted.get("total"))
    expected_total = get_number(expected.get("total"))

    total_correct = (
        predicted_total is not None
        and expected_total is not None
        and abs(predicted_total - expected_total) < 0.01
    )

    # Сопоставляем товары по сумме и количеству
    expected_items = expected.get("items", [])
    predicted_items = predicted.get("items", [])

    matched = 0
    used = set()

    for expected_item in expected_items:
        expected_sum = get_item_total(expected_item)
        expected_qty = get_number(expected_item.get("quantity", 1))

        for index, predicted_item in enumerate(predicted_items):
            if index in used:
                continue

            predicted_sum = get_item_total(predicted_item)
            predicted_qty = get_number(predicted_item.get("quantity", 1))

            if (
                expected_sum is not None
                and predicted_sum is not None
                and abs(expected_sum - predicted_sum) < 0.01
                and expected_qty == predicted_qty
            ):
                matched += 1
                used.add(index)
                break

    recall = matched / len(expected_items) if expected_items else None

    totals = [get_item_total(item) for item in predicted_items]

    items_sum = (
        sum(totals)
        if totals and all(value is not None for value in totals)
        else None
    )

    sum_matches_total = (
        items_sum is not None
        and predicted_total is not None
        and abs(items_sum - predicted_total) < 0.01
    )

    return {
        "store_correct": store_correct,
        "date_correct": date_correct,
        "total_correct": total_correct,
        "matched_items": matched,
        "expected_items": len(expected_items),
        "item_recall": recall,
        "items_sum": items_sum,
        "sum_matches_total": sum_matches_total,
    }


def main():
    annotations = sorted(ANNOTATIONS_DIR.glob("*.json"))

    if not annotations:
        print("Эталонные JSON не найдены.")
        return

    # Инициализируем PaddleOCR только один раз
    ocr = init_ocr()

    for annotation_path in annotations:
        with open(annotation_path, encoding="utf-8") as file:
            expected = json.load(file)

        receipt_id = expected["receipt_id"]
        image_path = IMAGES_DIR / f"{receipt_id}.jpeg"

        if not image_path.exists():
            print(f"Фото не найдено: {image_path}")
            continue

        print(f"\nПроверяем: {receipt_id}", flush=True)

        result = analyze_receipt_comparison(str(image_path), ocr=ocr)

        for name, key in [
            ("Regex", "regex_v01"),
            ("Qwen", "ai_agent_v02"),
        ]:
            metrics = evaluate_parser(result.get(key), expected)

            print(f"\n{name}:")
            print(f"  Магазин: {metrics['store_correct']}")
            print(f"  Дата: {metrics['date_correct']}")
            print(f"  Итог: {metrics['total_correct']}")
            print(
                f"  Найдено товаров: "
                f"{metrics['matched_items']}/{metrics['expected_items']}"
            )
            print(f"  Полнота товаров: {metrics['item_recall']}")
            print(f"  Сумма товаров: {metrics['items_sum']}")
            print(
                f"  Сумма совпадает с итогом: "
                f"{metrics['sum_matches_total']}"
            )


if __name__ == "__main__":
    main()
