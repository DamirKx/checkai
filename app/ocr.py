import os
import sys
import json
import tempfile
from typing import List, Dict, Any

# КРИТИЧНО: Отключаем багнутые фичи PaddlePaddle 3.x ДО импорта PaddleOCR
os.environ['FLAGS_enable_pir_api'] = '0'
os.environ['FLAGS_use_onednn'] = '0'
os.environ['FLAGS_enable_pir_in_executor'] = '0'
os.environ['FLAGS_pir_apply_inplace_pass'] = '0'
os.environ['FLAGS_use_mkldnn'] = '0'

import paddle
paddle.set_flags({
    'FLAGS_enable_pir_api': False,
    'FLAGS_enable_pir_in_executor': False,
    'FLAGS_pir_apply_inplace_pass': False,
})
# Принудительно отключаем OneDNN (MKL-DNN)
try:
    paddle.set_flags({'FLAGS_use_onednn': False})
except:
    pass
try:
    paddle.set_flags({'FLAGS_use_mkldnn': False})
except:
    pass

from paddleocr import PaddleOCR
from app.parser import parse_receipt


def init_ocr() -> PaddleOCR:
    """Инициализирует PaddleOCR (русский язык)."""
    return PaddleOCR(lang='ru', enable_mkldnn=False)


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

    ocr_result = result[0]

    # PaddleOCR 3.x возвращает dict с ключами rec_texts/rec_scores
    # PaddleOCR 2.x возвращает список [ [box, (text, score)], ... ]
    lines = []
    if isinstance(ocr_result, dict) and 'rec_texts' in ocr_result:
        # Формат 3.x
        texts = ocr_result['rec_texts']
        scores = ocr_result['rec_scores']
        for text, score in zip(texts, scores):
            t = str(text).strip()
            if t and float(score) >= 0.2:
                lines.append(t)
    elif isinstance(ocr_result, list):
        # Формат 2.x (fallback)
        for element in ocr_result:
            if len(element) >= 2:
                text_info = element[1]
                if isinstance(text_info, (tuple, list)) and len(text_info) >= 2:
                    text, score = text_info[0], text_info[1]
                    t = str(text).strip()
                    if t and float(score) >= 0.2:
                        lines.append(t)

    return lines


def analyze_receipt(image_input, ocr: PaddleOCR = None) -> Dict[str, Any]:
    """Полный цикл: изображение → структурированный словарь."""
    if ocr is None:
        ocr = init_ocr()

    lines = ocr_image(ocr, image_input)
    
    # Склеиваем строки в один текст
    raw_text = "\n".join(lines)
    
    # Используем крутой парсер Qwen, который написал разработчик!
    from ml.app.agent_parser import parse_receipt as ai_parse_receipt
    parsed = ai_parse_receipt(raw_text)
    
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