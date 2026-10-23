import json
import re
from datetime import datetime
from pathlib import Path

from app.ocr import analyze_receipt_comparison, init_ocr

# Папки с фотографиями и эталонной разметкой
BASE_DIR = Path(__file__).resolve().parent
IMAGES_DIR = BASE_DIR / "images"
ANNOTATIONS_DIR = BASE_DIR / "annotations"
CACHE_FILE = BASE_DIR / "results_cache.json"


def normalize_text(value):
    """Нормализует названия магазинов."""
    if not value:
        return ""

    return re.sub(r"[^a-zа-яё0-9]", "", str(value).casefold())


def normalize_date(value):
    """Преобразует дату в YYYY-MM-DD."""
    if not value:
        return None

    for fmt in (
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d.%m.%y",
        "%d.%m.%y %H:%M",
        "%d.%m.%Y %H:%M",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %I:%M:%S %p",
    ):
        try:
            return datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except ValueError:
            continue

    return None


def get_number(value):
    """Преобразует цену или сумму в число."""
    if value is None:
        return None

    try:
        if isinstance(value, str):
            value = value.replace(" ", "").replace("\u00a0", "")
            value = value.replace(",", ".")

        return float(value)
    except (TypeError, ValueError):
        return None


def get_item_total(item):
    """Получает полную стоимость товарной позиции."""
    total = get_number(item.get("total_price", item.get("total")))

    if total is not None:
        return total

    price = get_number(item.get("price_per_unit", item.get("price")))
    quantity = get_number(item.get("quantity", 1))

    if price is None or quantity is None:
        return None

    return price * quantity


from app.currency import detect_currency, SUPPORTED


def evaluate_parser(predicted, expected):
    """Сравнивает результат парсера с эталоном."""
    predicted = predicted if isinstance(predicted, dict) else {}

    # Не оцениваем поля, отсутствующие в эталоне
    expected_store = expected.get("store")
    expected_date = normalize_date(expected.get("date"))
    expected_total = get_number(expected.get("total"))

    store_correct = None
    date_correct = None
    total_correct = None

    if expected_store:
        store_correct = (
            normalize_text(predicted.get("store"))
            == normalize_text(expected_store)
        )

    if expected_date is not None:
        date_correct = (
            normalize_date(predicted.get("date")) == expected_date
        )

    if expected_total is not None:
        predicted_total = get_number(predicted.get("total"))
        total_correct = (
            predicted_total is not None
            and abs(predicted_total - expected_total) < 0.01
        )
    else:
        predicted_total = get_number(predicted.get("total"))

    expected_currency = str(expected.get("currency") or "").upper()
    predicted_currency = str(predicted.get("currency") or "").upper()

    currency_correct = None
    if expected_currency in SUPPORTED:
        currency_correct = predicted_currency == expected_currency

    # Новые чеки SROIE пока не имеют разметки товаров
    items_annotated = expected.get("items_annotated", True)

    metrics = {
        "store_correct": store_correct,
        "date_correct": date_correct,
        "total_correct": total_correct,
        "currency_correct": currency_correct,
        "items_annotated": items_annotated,
        "matched_items": 0,
        "expected_items": 0,
        "predicted_items": 0,
        "item_recall": None,
        "item_precision": None,
        "items_sum": None,
        "sum_matches_total": None,
    }

    if not items_annotated:
        return metrics

    expected_items = expected.get("items") or []
    predicted_items = predicted.get("items") or []

    matched = 0
    used = set()

    # Сопоставляем товары по сумме и количеству
    for expected_item in expected_items:
        expected_sum = get_item_total(expected_item)
        expected_qty = get_number(expected_item.get("quantity", 1))

        for index, predicted_item in enumerate(predicted_items):
            if index in used or not isinstance(predicted_item, dict):
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

    # При нуле предсказанных товаров точность равна 0,
    # если в эталоне товары есть
    recall = (
        matched / expected_count
        if expected_count > 0 else None
    )

    precision = (
        matched / predicted_count
        if predicted_count > 0
        else (0.0 if expected_count > 0 else None)
    )

    totals = [
        get_item_total(item)
        for item in predicted_items
        if isinstance(item, dict)
    ]

    items_sum = (
        sum(totals)
        if len(totals) == predicted_count
        and totals
        and all(value is not None for value in totals)
        else None
    )

    # Проверяем сумму с учётом возможных доплат в эталоне
    extra_charges = get_number(expected.get("extra_charges")) or 0

    sum_matches_total = (
        items_sum is not None
        and predicted_total is not None
        and abs(items_sum + extra_charges - predicted_total) < 0.01
    )

    metrics.update({
        "matched_items": matched,
        "expected_items": expected_count,
        "predicted_items": predicted_count,
        "item_recall": recall,
        "item_precision": precision,
        "items_sum": items_sum,
        "sum_matches_total": sum_matches_total,
    })

    return metrics


def format_status(value):
    """Отображает результат проверки."""
    if value is None:
        return "N/A"
    return "OK" if value else "Ошибка"


def print_receipt_metrics(name, metrics):
    """Печатает результаты одного чека."""
    print(f"\n{name}:")
    print(f"  Магазин: {format_status(metrics['store_correct'])}")
    print(f"  Дата: {format_status(metrics['date_correct'])}")
    print(f"  Итог: {format_status(metrics['total_correct'])}")
    print(f"  Валюта: {format_status(metrics['currency_correct'])}")

    if not metrics["items_annotated"]:
        print("  Товары: нет эталонной разметки")
        return

    print(
        f"  Найдено товаров: "
        f"{metrics['matched_items']}/{metrics['expected_items']}"
    )

    recall = metrics["item_recall"]

    print(
        f"  Recall: {recall:.1%}"
        if recall is not None else "  Recall: N/A"
    )

    print(f"  Сумма товаров: {metrics['items_sum']}")
    print(
        f"  Сумма совпадает с итогом: "
        f"{format_status(metrics['sum_matches_total'])}"
    )


def calculate_accuracy(results, key):
    """Считает точность только по размеченным полям."""
    values = [
        result[key]
        for result in results
        if result[key] is not None
    ]

    if not values:
        return None, 0

    return sum(values) / len(values), len(values)


def format_percent(value):
    return f"{value:.1%}" if value is not None else "N/A"


def print_summary(name, results):
    """Общая статистика одного парсера."""
    if not results:
        print(f"\n{name}: нет результатов")
        return

    store_acc, store_count = calculate_accuracy(results, "store_correct")
    date_acc, date_count = calculate_accuracy(results, "date_correct")
    total_acc, total_count = calculate_accuracy(results, "total_correct")
    currency_acc, currency_count = calculate_accuracy(
        results, "currency_correct"
    )

    item_results = [
        result for result in results
        if result["items_annotated"]
    ]

    matched = sum(r["matched_items"] for r in item_results)
    expected = sum(r["expected_items"] for r in item_results)
    predicted = sum(r["predicted_items"] for r in item_results)

    recall = matched / expected if expected else None
    precision = matched / predicted if predicted else (
        0.0 if expected else None
    )

    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None
        and recall is not None
        and precision + recall > 0
        else 0.0 if expected else None
    )

    sum_acc, sum_count = calculate_accuracy(
        item_results, "sum_matches_total"
    )

    print("\n" + "=" * 45)
    print(f"ОБЩАЯ СТАТИСТИКА: {name}")
    print("=" * 45)

    print(f"Обработано чеков: {len(results)}")
    print(f"Точность магазина: {format_percent(store_acc)} ({store_count} чеков)")
    print(f"Точность даты: {format_percent(date_acc)} ({date_count} чеков)")
    print(f"Точность итога: {format_percent(total_acc)} ({total_count} чеков)")
    print(
        f"Точность валюты: {format_percent(currency_acc)} "
        f"({currency_count} чеков)"
    )

    print(f"\nЧеков с разметкой товаров: {len(item_results)}")
    print(f"Правильно найдено товаров: {matched}/{expected}")
    print(f"Precision: {format_percent(precision)}")
    print(f"Recall: {format_percent(recall)}")
    print(f"F1-score: {format_percent(f1)}")
    print(
        f"Совпадение суммы товаров с итогом: "
        f"{format_percent(sum_acc)} ({sum_count} чеков)"
    )


def main():
    """Запускает оценку Regex и Qwen."""
    annotations = sorted(ANNOTATIONS_DIR.glob("*.json"))

    if not annotations:
        print("Эталонные JSON не найдены.")
        return

    stats = {"Regex": [], "Qwen": []}

    if CACHE_FILE.exists():
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    else:
        cache = {}

    print(f"Сохранённых результатов: {len(cache)}")

    # Инициализируем PaddleOCR только один раз
    ocr = init_ocr()

    for annotation_path in annotations:
        with open(annotation_path, encoding="utf-8") as f:
            expected = json.load(f)

        receipt_id = expected["receipt_id"]

        # Временно пропускаем чек, на котором зависает Qwen
        if receipt_id == "receipt_015":
            print("Пропускаем receipt_015: проблема с Ollama")
            continue

        image_path = None

        for extension in (".jpeg", ".jpg", ".png"):
            candidate = IMAGES_DIR / f"{receipt_id}{extension}"

            if candidate.exists():
                image_path = candidate
                break

        if image_path is None:
            print(f"Фото не найдено: {receipt_id}")
            continue

        if receipt_id in cache:
            print(f"Пропускаем {receipt_id}: результат уже сохранён")
            result = cache[receipt_id]
        else:
            print(f"\\nПроверяем: {receipt_id}", flush=True)

            try:
                result = analyze_receipt_comparison(
                    str(image_path), ocr=ocr
                )
            except Exception as error:
                print(f"Ошибка распознавания: {error}")
                continue

            cache[receipt_id] = result

            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)

            print(f"Сохранён результат: {receipt_id}")

        detected_currency, detected_source = detect_currency(
            result.get("ocr_lines") or []
        )

        for parser_key in ("regex_v01", "ai_agent_v02"):
            original = result.get(parser_key)
            if isinstance(original, dict):
                result[parser_key] = dict(original)
                result[parser_key]["currency"] = detected_currency

        for name, key in (
            ("Regex", "regex_v01"),
            ("Qwen", "ai_agent_v02"),
        ):
            metrics = evaluate_parser(result.get(key), expected)
            stats[name].append(metrics)
            print_receipt_metrics(name, metrics)

    print("\n" + "=" * 45)
    print("ИТОГОВОЕ СРАВНЕНИЕ ПАРСЕРОВ")
    print("=" * 45)

    for name, results in stats.items():
        print_summary(name, results)


if __name__ == "__main__":
    main()
