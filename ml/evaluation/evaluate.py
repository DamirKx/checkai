import json
import re
from datetime import datetime
from pathlib import Path

from app.ocr import analyze_receipt_comparison, init_ocr

# Папки с фотографиями и правильными JSON-разметками
BASE_DIR = Path(__file__).resolve().parent
IMAGES_DIR = BASE_DIR / "images"
ANNOTATIONS_DIR = BASE_DIR / "annotations"


def normalize_text(value):
    """Приводит названия магазинов к единому формату."""
    if not value:
        return ""

    return re.sub(r"[^a-zа-яё0-9]", "", str(value).casefold())


def normalize_date(value):
    """Приводит даты к формату YYYY-MM-DD."""
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
            continue

    return None


def get_number(value):
    """Преобразует значение в число."""
    try:
        if value is None:
            return None
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def get_item_total(item):
    """Получает общую стоимость товарной позиции."""
    total = get_number(item.get("total_price", item.get("total")))

    if total is not None:
        return total

    price = get_number(item.get("price_per_unit", item.get("price")))
    quantity = get_number(item.get("quantity", 1))

    if price is None or quantity is None:
        return None

    return price * quantity


def evaluate_parser(predicted, expected):
    """Сравнивает результат парсера с эталонным JSON."""
    predicted = predicted or {}

    # Проверка магазина
    store_correct = (
        bool(expected.get("store"))
        and normalize_text(predicted.get("store"))
        == normalize_text(expected.get("store"))
    )

    # Проверка даты
    expected_date = normalize_date(expected.get("date"))
    predicted_date = normalize_date(predicted.get("date"))

    date_correct = (
        expected_date is not None
        and expected_date == predicted_date
    )

    # Проверка итоговой суммы
    predicted_total = get_number(predicted.get("total"))
    expected_total = get_number(expected.get("total"))

    total_correct = (
        predicted_total is not None
        and expected_total is not None
        and abs(predicted_total - expected_total) < 0.01
    )

    # Сопоставление товарных позиций по сумме и количеству
    expected_items = expected.get("items") or []
    predicted_items = predicted.get("items") or []

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

    expected_count = len(expected_items)
    predicted_count = len(predicted_items)

    item_recall = (
        matched / expected_count
        if expected_count > 0
        else None
    )

    item_precision = (
        matched / predicted_count
        if predicted_count > 0
        else None
    )

    # Проверка суммы распознанных товаров
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
        "store_correct": bool(store_correct),
        "date_correct": date_correct,
        "total_correct": total_correct,
        "matched_items": matched,
        "expected_items": expected_count,
        "predicted_items": predicted_count,
        "item_recall": item_recall,
        "item_precision": item_precision,
        "items_sum": items_sum,
        "sum_matches_total": sum_matches_total,
    }


def print_receipt_metrics(name, metrics):
    """Выводит результаты одного парсера."""
    print(f"\n{name}:")
    print(f"  Магазин: {metrics['store_correct']}")
    print(f"  Дата: {metrics['date_correct']}")
    print(f"  Итог: {metrics['total_correct']}")
    print(
        f"  Найдено товаров: "
        f"{metrics['matched_items']}/{metrics['expected_items']}"
    )

    if metrics["item_recall"] is not None:
        print(f"  Полнота товаров: {metrics['item_recall']:.1%}")
    else:
        print("  Полнота товаров: N/A")

    print(f"  Сумма товаров: {metrics['items_sum']}")
    print(
        f"  Сумма совпадает с итогом: "
        f"{metrics['sum_matches_total']}"
    )


def print_summary(name, results):
    """Считает общую статистику по всем проверенным чекам."""
    if not results:
        print(f"\n{name}: нет результатов")
        return

    count = len(results)

    store_accuracy = sum(
        result["store_correct"] for result in results
    ) / count

    date_accuracy = sum(
        result["date_correct"] for result in results
    ) / count

    total_accuracy = sum(
        result["total_correct"] for result in results
    ) / count

    sum_accuracy = sum(
        result["sum_matches_total"] for result in results
    ) / count

    matched_items = sum(
        result["matched_items"] for result in results
    )

    expected_items = sum(
        result["expected_items"] for result in results
    )

    predicted_items = sum(
        result["predicted_items"] for result in results
    )

    # Общая полнота по всем товарным позициям
    recall = (
        matched_items / expected_items
        if expected_items > 0
        else None
    )

    # Общая точность найденных товарных позиций
    precision = (
        matched_items / predicted_items
        if predicted_items > 0
        else None
    )

    # F1 объединяет precision и recall
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None
        and recall is not None
        and precision + recall > 0
        else None
    )

    print(f"\n{'=' * 45}")
    print(f"ОБЩАЯ СТАТИСТИКА: {name}")
    print(f"{'=' * 45}")

    print(f"Проверено чеков: {count}")
    print(f"Точность магазина: {store_accuracy:.1%}")
    print(f"Точность даты: {date_accuracy:.1%}")
    print(f"Точность итога: {total_accuracy:.1%}")
    print(f"Совпадение суммы товаров с итогом: {sum_accuracy:.1%}")

    print(f"Правильно найдено товаров: {matched_items}/{expected_items}")
    print(
        f"Полнота товаров (Recall): "
        f"{recall:.1%}" if recall is not None
        else "Полнота товаров (Recall): N/A"
    )
    print(
        f"Точность товаров (Precision): "
        f"{precision:.1%}" if precision is not None
        else "Точность товаров (Precision): N/A"
    )
    print(
        f"F1-score товаров: {f1:.1%}"
        if f1 is not None
        else "F1-score товаров: N/A"
    )


def main():
    """Запускает оценку обоих парсеров на эталонных чеках."""
    annotations = sorted(ANNOTATIONS_DIR.glob("*.json"))

    if not annotations:
        print("Эталонные JSON не найдены.")
        return

    # Храним результаты каждого парсера отдельно
    stats = {
        "Regex": [],
        "Qwen": [],
    }

    # Загружаем PaddleOCR один раз
    ocr = init_ocr()

    for annotation_path in annotations:
        with open(annotation_path, encoding="utf-8") as file:
            expected = json.load(file)

        receipt_id = expected["receipt_id"]

        # Поддерживаем несколько форматов фотографий
        image_path = None

        for extension in (".jpeg", ".jpg", ".png"):
            candidate = IMAGES_DIR / f"{receipt_id}{extension}"

            if candidate.exists():
                image_path = candidate
                break

        if image_path is None:
            print(f"Фотография не найдена: {receipt_id}")
            continue

        print(f"\nПроверяем: {receipt_id}", flush=True)

        try:
            result = analyze_receipt_comparison(
                str(image_path),
                ocr=ocr,
            )
        except Exception as error:
            print(f"Ошибка распознавания {receipt_id}: {error}")
            continue

        for name, key in (
            ("Regex", "regex_v01"),
            ("Qwen", "ai_agent_v02"),
        ):
            metrics = evaluate_parser(
                result.get(key),
                expected,
            )

            stats[name].append(metrics)
            print_receipt_metrics(name, metrics)

    # Итоговые показатели по всем обработанным чекам
    print("\n" + "=" * 45)
    print("ИТОГОВОЕ СРАВНЕНИЕ ПАРСЕРОВ")
    print("=" * 45)

    for name, results in stats.items():
        print_summary(name, results)


if __name__ == "__main__":
    main()
