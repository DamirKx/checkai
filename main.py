import os
import sys
import json
import tempfile
from typing import List, Dict, Any

from paddleocr import PaddleOCR
from parser import parse_receipt


def init_ocr() -> PaddleOCR:
    """Инициализирует PaddleOCR (русский язык)."""
    return PaddleOCR(lang='ru')


def ocr_image(ocr: PaddleOCR, image_input) -> List[str]:
    """
    Принимает путь к файлу (str) или байты (bytes).
    Возвращает список распознанных строк текста.
    """
    # Если на вход пришли байты — сохраняем во временный файл
    temp_path = None
    if isinstance(image_input, (bytes, bytearray)):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
            tmp.write(image_input)
            temp_path = tmp.name
        image_path = temp_path
    else:
        image_path = image_input

    try:
        result = ocr.ocr(image_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

    if not result or not result[0]:
        return []

    ocr_result = result[0]  # OCRResult (dict-like объект paddlex)

    # Ключ rec_texts содержит список распознанных строк
    texts = ocr_result['rec_texts']
    scores = ocr_result['rec_scores']

    lines = []
    for text, score in zip(texts, scores):
        t = str(text).strip()
        if t and float(score) >= 0.2:
            lines.append(t)

    return lines


def analyze_receipt(image_input, ocr: PaddleOCR = None) -> Dict[str, Any]:
    """Полный цикл: изображение → структурированный словарь."""
    if ocr is None:
        ocr = init_ocr()

    lines = ocr_image(ocr, image_input)
    parsed = parse_receipt(lines)
    parsed['raw_lines'] = lines
    return parsed


if __name__ == '__main__':
    img_path = sys.argv[1] if len(sys.argv) > 1 else 'images.jpeg'

    print(f"=== Распознавание: {img_path} ===")
    ocr_instance = init_ocr()
    data = analyze_receipt(img_path, ocr=ocr_instance)

    print("\n" + "=" * 50)
    print("           ИТОГОВЫЕ ДАННЫЕ ЧЕКА")
    print("=" * 50)
    print(f"Магазин:      {data.get('store') or '—'}")
    print(f"Дата/Время:   {data.get('date') or '—'}  {data.get('time') or '—'}")
    print("-" * 50)
    print(f"{'Товар':<28} | {'Кол':<4} | {'Цена':>7} | {'Сумма':>8}")
    print("-" * 50)
    for item in data.get('items', []):
        name = item['name'][:26] + '..' if len(item['name']) > 28 else item['name']
        q = item['quantity']
        p = f"{item['price_per_unit']:.2f}" if item.get('price_per_unit') else '--'
        s = f"{item['total_price']:.2f}" if item.get('total_price') else '--'
        print(f"{name:<28} | {q:<4} | {p:>7} | {s:>8}")
    print("-" * 50)
    total = data.get('total') or 0.0
    print(f"ИТОГО: {total:.2f} ₸")
    print("=" * 50)

    out_path = os.path.splitext(img_path)[0] + '_result.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nJSON сохранён: {out_path}")