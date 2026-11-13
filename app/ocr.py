import os
import sys
import json
import time
import tempfile
import logging
from typing import List, Dict, Any, Optional, Tuple

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
    return PaddleOCR(
        enable_mkldnn=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="eslav_PP-OCRv5_mobile_rec",
    )


def ocr_image_with_scores(ocr: PaddleOCR, image_input) -> Tuple[List[str], List[float]]:
    """
    Принимает путь к файлу (str) или байты (bytes).
    Возвращает кортеж: (список строк текста, список уверенностей строк).
    """
    temp_path = None
    if isinstance(image_input, (bytes, bytearray)):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
            tmp.write(image_input)
            temp_path = tmp.name
        image_path = temp_path
    else:
        image_path = image_input

    try:
        try:
            from ml.app.preprocess import prepare_for_ocr
            ocr_input = prepare_for_ocr(image_path)
        except ImportError:
            ocr_input = image_path
        # Масштаб и контраст перед OCR (ml/app/preprocess.py, OCR_PREPROCESS=0 — выключить)
        result = ocr.ocr(ocr_input)
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
    if isinstance(ocr_result, dict) and 'rec_texts' in ocr_result:
        texts = ocr_result['rec_texts']
        scores = ocr_result.get('rec_scores', [])
        for text, score in zip(texts, scores):
            t = str(text).strip()
            if t and float(score) >= 0.2:
                lines.append(t)
                line_scores.append(float(score))
    elif isinstance(ocr_result, list):
        for element in ocr_result:
            if len(element) >= 2:
                text_info = element[1]
                if isinstance(text_info, (tuple, list)) and len(text_info) >= 2:
                    text, score = text_info[0], text_info[1]
                    t = str(text).strip()
                    if t and float(score) >= 0.2:
                        lines.append(t)
                        line_scores.append(float(score))

    return lines, line_scores


def ocr_image(ocr: PaddleOCR, image_input) -> List[str]:
    """
    Принимает путь к файлу (str) или байты (bytes).
    Возвращает список распознанных строк текста.
    """
    lines, _ = ocr_image_with_scores(ocr, image_input)
    return lines


logger = logging.getLogger("checkai.ocr")


def analyze_receipt(
    image_input, ocr: PaddleOCR = None, categories: Optional[List[dict]] = None
) -> Dict[str, Any]:
    """Полный цикл: изображение → структурированный словарь.

    Если Ollama/Qwen доступна — используется нейросетевой анализ.
    Если Ollama отключена или не отвечает — срабатывает мгновенный алгоритмический парсер app.parser.

    categories — категории пользователя (или стандартные у гостя), по которым раскладываются товары.
    Раскладку (categorize_items) подключает ML в своей ветке; до этого аргумент не используется.
    """
    if ocr is None:
        ocr = init_ocr()

    t_ocr_start = time.perf_counter()
    lines, line_scores = ocr_image_with_scores(ocr, image_input)
    ocr_elapsed = time.perf_counter() - t_ocr_start
    raw_text = "\n".join(lines)

    use_ai = os.getenv("USE_AI_PARSER", "auto").strip().lower()
    parsed = None
    parser_used = "regex"

    t_llm_start = time.perf_counter()
    if use_ai not in ("0", "false", "no", "off"):
        try:
            try:
                from ml.app.agent_parser import parse_receipt_with_correction as ai_parse_receipt
            except ImportError:
                from ml.app.agent_parser import parse_receipt as ai_parse_receipt
            parsed = ai_parse_receipt(raw_text)
            if parsed:
                parser_used = "qwen"
        except Exception as e:
            logger.warning(
                "Qwen не разобрал чек (%s: %s). Используется встроенный парсер app.parser",
                type(e).__name__,
                e,
            )
    llm_elapsed = time.perf_counter() - t_llm_start

    if not parsed:
        parsed = parse_receipt(lines)

    # Каноническое имя сети и исправление «Lепог» → «Lenor» — для любого парсера,
    # запасной app.parser сам этого не делает
    try:
        from ml.app.merchant import canonical_merchant
        from ml.app.textfix import fix_item_names
        fix_item_names(parsed.get("items"))
        parsed["merchant"] = canonical_merchant(parsed.get("store"))
    except Exception as e:
        logger.warning("Ошибка в canonical_merchant: %s", e)
        parsed.setdefault("merchant", None)

    # Определение валюты чека
    currency, source = None, "default"
    try:
        from ml.app.currency import detect_currency
        currency, source = detect_currency(lines)
    except Exception as e:
        logger.warning("Ошибка в detect_currency: %s", e)

    parsed["currency"] = currency or "KZT"
    parsed["currency_source"] = source

    t_cat_start = time.perf_counter()
    try:
        from ml.app.categorize import categorize_items
        categorize_items(parsed.get("items") or [], categories or [])
    except Exception as e:
        logger.warning("Ошибка в categorize_items: %s", e)
    cat_elapsed = time.perf_counter() - t_cat_start

    # Уверенность распознавания чека (PROFILE_TASKS.md, п. 2.3)
    try:
        from ml.app.confidence import compute_confidence
        parsed["confidence"] = compute_confidence(line_scores, parsed.get("items") or [], parsed.get("total"))
    except Exception as e:
        logger.warning("Ошибка в compute_confidence: %s", e)
        parsed.setdefault("confidence", None)

    # Фискальные реквизиты (NEXT_WEEK_TASKS.md, C.3)
    try:
        from ml.app.fiscal import extract_fiscal_info
        parsed.setdefault("fiscal", extract_fiscal_info(lines))
    except Exception as e:
        logger.warning("Ошибка в extract_fiscal_info: %s", e)
        parsed.setdefault("fiscal", None)

    # Гарантия наличия category_id, category, category_source (PROFILE_TASKS.md, п. 2.1)
    for it in parsed.get("items") or []:
        it.setdefault("category_id", None)
        it.setdefault("category", None)
        it.setdefault("category_source", None)

    items_list = parsed.get("items") or []
    logger.info(
        "analyze: ocr=%.1fs llm=%.1fs categorize=%.1fs parser=%s items=%d",
        ocr_elapsed,
        llm_elapsed,
        cat_elapsed,
        parser_used,
        len(items_list),
    )

    parsed["raw_lines"] = lines
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