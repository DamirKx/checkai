import os
import sys
import json
import tempfile
from typing import List, Dict, Any, Optional, Tuple

from paddleocr import PaddleOCR

# Старый RegEx-парсер оставляем для сравнения
from app.parser import parse_receipt as parse_receipt_regex

# Наш новый локальный AI Agent
from app.agent_parser import parse_receipt_with_correction
from app.currency import detect_currency, SUPPORTED

# Подключаем модуль распределения по категориям
from app.categorize import categorize_items
from app.preprocess import prepare_for_ocr
from app.confidence import compute_confidence



def resolve_currency(lines, parsed):
    """Выбирает валюту с учётом приоритетов."""
    detected, source = detect_currency(lines)

    ai_currency = parsed.get("currency")
    if isinstance(ai_currency, str):
        ai_currency = ai_currency.strip().upper()
    else:
        ai_currency = None

    if detected is not None and source == "symbol":
        return detected, "symbol"

    if ai_currency in SUPPORTED:
        return ai_currency, "ai"

    if detected is not None and source == "country":
        return detected, "country"

    return "KZT", "default"

def init_ocr() -> PaddleOCR:
    """
    Инициализация PaddleOCR.
    Используется русская модель распознавания.
    """
    return PaddleOCR(
        enable_mkldnn=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="eslav_PP-OCRv5_mobile_rec",
    )

def ocr_image_with_scores(
    ocr: PaddleOCR,
    image_input
) -> Tuple[List[str], List[float]]:
    """
    Выполняет OCR изображения.
    Возвращает кортеж: (список строк, список уверенностей строк).
    """
    temp_path = None
    if isinstance(image_input, (bytes, bytearray)):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp.write(image_input)
            temp_path = tmp.name
        image_path = temp_path
    else:
        image_path = image_input

    try:
        result = ocr.ocr(prepare_for_ocr(image_path))
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

    if not result or not result[0]:
        return [], []

    ocr_result = result[0]
    lines = []
    line_scores = []

    if isinstance(ocr_result, dict) and "rec_texts" in ocr_result:
        texts = ocr_result["rec_texts"]
        scores = ocr_result.get("rec_scores", [])
        for text, score in zip(texts, scores):
            text = str(text).strip()
            if text and float(score) >= 0.2:
                lines.append(text)
                line_scores.append(float(score))
    elif isinstance(ocr_result, list):
        for element in ocr_result:
            if len(element) >= 2:
                text_info = element[1]
                if isinstance(text_info, (tuple, list)) and len(text_info) >= 2:
                    text, score = text_info[0], text_info[1]
                    text = str(text).strip()
                    if text and float(score) >= 0.2:
                        lines.append(text)
                        line_scores.append(float(score))

    return lines, line_scores


def ocr_image(
    ocr: PaddleOCR,
    image_input
) -> List[str]:
    """
    Выполняет OCR изображения.
    Возвращает список распознанных строк.
    """
    lines, _ = ocr_image_with_scores(ocr, image_input)
    return lines


def analyze_receipt(
    image_input,
    ocr: PaddleOCR = None,
    categories: Optional[List[dict]] = None
) -> Dict[str, Any]:
    """
    Главная функция ML-сервиса.

    Pipeline:

    image
        ↓
    PaddleOCR
        ↓
    OCR text
        ↓
    Local AI Agent (Qwen2.5-3B)
        ↓
    Self-Correction
        ↓
    Categorization (Categories / Keywords)
        ↓
    JSON

    Принимает:
- путь к изображению;
- bytes изображения;
- список пользовательских категорий (опционально).

    Возвращает:
- dict с результатом анализа.
    """

    if ocr is None:
        ocr = init_ocr()

    # 1. OCR (получаем строки и уверенности от PaddleOCR)
    lines, scores = ocr_image_with_scores(
        ocr,
        image_input
    )

    # 2. Превращаем список строк
    # в единый OCR-текст для LLM.
    ocr_text = "\n".join(lines)

    # 3. Отправляем OCR в AI Agent.
    parsed = parse_receipt_with_correction(
        ocr_text
    )

    # 4. Распределяем товары по категориям
    categorize_items(parsed.get("items", []), categories or [])

    # 5. Определяем валюту и сохраняем исходные OCR-строки.
    currency, currency_source = resolve_currency(lines, parsed)

    parsed["currency"] = currency
    parsed["currency_source"] = currency_source
    parsed["raw_lines"] = lines

    # 6. Уверенность распознавания чека (PROFILE_TASKS.md, п. 2.3)
    parsed["confidence"] = compute_confidence(scores, parsed.get("items") or [], parsed.get("total"))

    # 7. Гарантируем category_source, category_id, category у каждой позиции (PROFILE_TASKS.md, п. 2.1)
    for it in parsed.get("items") or []:
        it.setdefault("category_id", None)
        it.setdefault("category", None)
        it.setdefault("category_source", None)

    return parsed


def analyze_receipt_comparison(
    image_input,
    ocr: PaddleOCR = None,
    categories: Optional[List[dict]] = None
) -> Dict[str, Any]:
    """
    Сравнение двух подходов:

    RegEx Parser v0.1
    VS
    Local AI Agent v0.2

    Используется для тестирования
    и оценки качества модели.
    """

    if ocr is None:
        ocr = init_ocr()

    # Один раз выполняем OCR.
    lines = ocr_image(
        ocr,
        image_input
    )

    # -------------------------
    # RegEx Parser v0.1
    # -------------------------

    regex_result = parse_receipt_regex(
        lines
    )

    # -------------------------
    # AI Agent v0.2
    # -------------------------

    ocr_text = "\n".join(lines)

    ai_result = parse_receipt_with_correction(
        ocr_text
    )

    # Распределяем по категориям для AI-результата
    categorize_items(ai_result.get("items", []), categories or [])

    currency, currency_source = resolve_currency(lines, ai_result)
    ai_result["currency"] = currency
    ai_result["currency_source"] = currency_source

    return {
        "ocr_lines": lines,
        "regex_v01": regex_result,
        "ai_agent_v02": ai_result
    }


if __name__ == "__main__":

    # Позволяет запускать:
    #
    # python -m app.ocr image.jpeg

    if len(sys.argv) > 1:
        img_path = sys.argv[1]
    else:
        img_path = "images.jpeg"

    print(
        f"=== Распознавание: {img_path} ==="
    )

    # Инициализируем OCR один раз.
    ocr_instance = init_ocr()

    # Анализируем чек.
    data = analyze_receipt(
        img_path,
        ocr=ocr_instance
    )

    print()
    print("=" * 60)
    print("              AI AGENT v0.2")
    print("=" * 60)

    print(
        f"Магазин:      "
        f"{data.get('store') or '—'}"
    )

    print(
        f"Дата/Время:   "
        f"{data.get('date') or '—'}"
    )

    print("-" * 60)

    print(
        f"{'Товар':<24} | "
        f"{'Категория':<12} | "
        f"{'Кол':<3} | "
        f"{'Цена':>8} | "
        f"{'Сумма':>8}"
    )

    print("-" * 60)

    for item in data.get(
        "items",
        []
    ):

        name = str(
            item.get(
                "name",
                "—"
            )
        )

        if len(name) > 24:
            name = name[:22] + ".."

        category = str(item.get("category") or "Без кат.")
        if len(category) > 12:
            category = category[:10] + ".."

        quantity = item.get(
            "quantity",
            1
        )

        price = item.get(
            "price_per_unit"
        )

        total_price = item.get(
            "total_price"
        )

        if price is not None:
            try:
                price_text = (
                    f"{float(price):.2f}"
                )
            except (TypeError, ValueError):
                price_text = str(price)
        else:
            price_text = "--"

        if total_price is not None:
            try:
                total_text = (
                    f"{float(total_price):.2f}"
                )
            except (TypeError, ValueError):
                total_text = str(
                    total_price
                )
        else:
            total_text = "--"

        print(
            f"{name:<24} | "
            f"{category:<12} | "
            f"{str(quantity):<3} | "
            f"{price_text:>8} | "
            f"{total_text:>8}"
        )

    print("-" * 60)

    total = data.get("total")

    if total is not None:

        try:
            print(
                f"ИТОГО: "
                f"{float(total):.2f}"
            )

        except (TypeError, ValueError):

            print(
                f"ИТОГО: {total}"
            )

    else:

        print("ИТОГО: —")

    print("=" * 60)

    # Сохраняем JSON рядом
    # с исходным изображением.
    out_path = (
        os.path.splitext(img_path)[0]
        + "_ai_result.json"
    )

    with open(
        out_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"\nAI JSON сохранён: "
        f"{out_path}"
    )