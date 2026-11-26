"""Единая реализация OCR и конвейера анализа чека.

Используется и сервером (через тонкий реэкспорт в app/ocr.py), и ML-бенчмарками.
Поддерживает:
- настройку флагов PaddlePaddle в одном месте (3.0, 3.1);
- склейку фрагментов OCR в строки по координатам с компенсацией наклона (4.1, OCR_MERGE_LINES);
- определение размера чека receipt_size (5.1) и нарезку длинных фото на полосы с перекрытием (5.2);
- склейку нескольких фото одного чека merge_pages (5.7);
- fallback на regex-парсер, уверенность по позициям (4.2), коды warnings и прогресс on_progress (5.8).
"""
import difflib
import logging
import math
import os
import statistics
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# КРИТИЧНО: Отключаем нестабильные PIR-фичи PaddlePaddle 3.x ДО импорта PaddleOCR
os.environ.setdefault("FLAGS_enable_pir_api", "0")
os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
os.environ.setdefault("FLAGS_pir_apply_inplace_pass", "0")

_ENABLE_MKLDNN = os.getenv("OCR_ENABLE_MKLDNN", "0").strip().lower() in ("1", "true", "yes", "on")
if not _ENABLE_MKLDNN:
    os.environ.setdefault("FLAGS_use_onednn", "0")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")

try:
    import paddle

    paddle.set_flags({
        "FLAGS_enable_pir_api": False,
        "FLAGS_enable_pir_in_executor": False,
        "FLAGS_pir_apply_inplace_pass": False,
    })
    if not _ENABLE_MKLDNN:
        for _flag in ("FLAGS_use_onednn", "FLAGS_use_mkldnn"):
            try:
                paddle.set_flags({_flag: False})
            except Exception:
                pass
except Exception:
    pass

from paddleocr import PaddleOCR

from .agent_parser import parse_receipt_with_correction
from .categorize import categorize_items
from .confidence import attach_item_confidences, compute_confidence
from .currency import SUPPORTED, detect_currency
from .fiscal import extract_fiscal_info
from .merchant import canonical_merchant
from .parser import parse_receipt as parse_receipt_regex
from .preprocess import load_prepared_image, prepare_for_ocr, slice_long_image
from .textfix import fix_item_names

logger = logging.getLogger("checkai.ocr")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def resolve_currency(lines: List[str], parsed: Dict[str, Any]) -> Tuple[str, str]:
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


def receipt_size(
    lines: Sequence[str],
    image_shape: Optional[Sequence[int]] = None,
) -> str:
    """Определяет размер чека: 'short' или 'long' (задача 5.1).

    Длинный чек — если OCR-строк больше OCR_LONG_LINES_THRESHOLD (по умолчанию 80)
    или соотношение сторон фото h/w > OCR_LONG_ASPECT_RATIO (по умолчанию 3.0).
    """
    long_lines = _int_env("OCR_LONG_LINES_THRESHOLD", 80)
    long_ratio = _float_env("OCR_LONG_ASPECT_RATIO", 3.0)

    if len(lines) > long_lines:
        return "long"

    if image_shape and len(image_shape) >= 2:
        h, w = float(image_shape[0]), float(image_shape[1])
        if w > 0 and (h / w) > long_ratio:
            return "long"

    return "short"


def init_ocr() -> PaddleOCR:
    """Инициализирует PaddleOCR (PP-OCRv5_mobile_det + eslav_PP-OCRv5_mobile_rec)."""
    enable_mkldnn = os.getenv("OCR_ENABLE_MKLDNN", "0").strip().lower() in ("1", "true", "yes", "on")
    kwargs: Dict[str, Any] = {
        "enable_mkldnn": enable_mkldnn,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "text_detection_model_name": "PP-OCRv5_mobile_det",
        "text_recognition_model_name": "eslav_PP-OCRv5_mobile_rec",
    }
    cpu_threads = _int_env("OCR_CPU_THREADS", 0)
    if cpu_threads > 0:
        kwargs["cpu_threads"] = cpu_threads
    return PaddleOCR(**kwargs)


def _box_geometry(box: Any) -> Optional[Tuple[float, float, float, float, float]]:
    """Извлекает (x_min, x_max, y_center, height, angle_rad) из полигона или bbox."""
    if box is None:
        return None
    try:
        # bbox [x1, y1, x2, y2]
        if len(box) == 4 and not hasattr(box[0], "__len__"):
            x1, y1, x2, y2 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
            return min(x1, x2), max(x1, x2), (y1 + y2) / 2.0, max(1.0, abs(y2 - y1)), 0.0
        # 4-point polygon [[x0,y0], [x1,y1], [x2,y2], [x3,y3]]
        pts = [(float(p[0]), float(p[1])) for p in box]
        if len(pts) < 4:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        y_center = sum(ys) / len(ys)
        height = max(1.0, y_max - y_min)
        dx = pts[1][0] - pts[0][0]
        dy = pts[1][1] - pts[0][1]
        angle = math.atan2(dy, dx) if abs(dx) > 1e-3 else 0.0
        return x_min, x_max, y_center, height, angle
    except (TypeError, ValueError, IndexError):
        return None


def _extract_fragments(ocr_result: Any, y_offset: int = 0) -> List[Dict[str, Any]]:
    """Извлекает фрагменты {text, score, x_min, x_max, y_center, height, angle} из ответа PaddleOCR."""
    fragments: List[Dict[str, Any]] = []
    if not ocr_result:
        return fragments

    if isinstance(ocr_result, dict) and "rec_texts" in ocr_result:
        texts = ocr_result["rec_texts"]
        scores = ocr_result.get("rec_scores", [])
        polys = ocr_result.get("rec_polys")
        if polys is None or len(polys) == 0:
            polys = ocr_result.get("dt_polys")
        if polys is None or len(polys) == 0:
            polys = ocr_result.get("rec_boxes")

        for idx, (text, score) in enumerate(zip(texts, scores)):
            t = str(text).strip()
            s = float(score)
            if not t or s < 0.2:
                continue
            box = polys[idx] if polys is not None and idx < len(polys) else None
            geom = _box_geometry(box)
            if geom is not None:
                x_min, x_max, y_c, h, angle = geom
                fragments.append({
                    "text": t,
                    "score": s,
                    "x_min": x_min,
                    "x_max": x_max,
                    "y_center": y_c + y_offset,
                    "height": h,
                    "angle": angle,
                })
            else:
                fragments.append({"text": t, "score": s, "x_min": None})
    elif isinstance(ocr_result, list):
        for element in ocr_result:
            if len(element) < 2:
                continue
            box = element[0]
            text_info = element[1]
            if not isinstance(text_info, (tuple, list)) or len(text_info) < 2:
                continue
            t = str(text_info[0]).strip()
            s = float(text_info[1])
            if not t or s < 0.2:
                continue
            geom = _box_geometry(box)
            if geom is not None:
                x_min, x_max, y_c, h, angle = geom
                fragments.append({
                    "text": t,
                    "score": s,
                    "x_min": x_min,
                    "x_max": x_max,
                    "y_center": y_c + y_offset,
                    "height": h,
                    "angle": angle,
                })
            else:
                fragments.append({"text": t, "score": s, "x_min": None})

    return fragments


def _deduplicate_overlap_fragments(fragments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Убирает дубли фрагментов на стыках полос длинного чека (задача 5.2)."""
    if len(fragments) <= 1:
        return fragments

    kept: List[Dict[str, Any]] = []
    for frag in fragments:
        if frag.get("x_min") is None:
            kept.append(frag)
            continue
        is_dup = False
        for idx, prev in enumerate(kept):
            if prev.get("x_min") is None:
                continue
            max_h = max(frag["height"], prev["height"], 10.0)
            if abs(frag["y_center"] - prev["y_center"]) > 0.6 * max_h:
                continue
            max_w = max(frag["x_max"] - frag["x_min"], prev["x_max"] - prev["x_min"], 20.0)
            close_x = abs(frag["x_min"] - prev["x_min"]) <= 0.5 * max_w
            sim = difflib.SequenceMatcher(None, frag["text"].casefold(), prev["text"].casefold()).ratio()
            if (close_x and sim >= 0.7) or sim >= 0.9:
                # Оставляем фрагмент с более высокой уверенностью
                if frag["score"] > prev["score"]:
                    kept[idx] = frag
                is_dup = True
                break
        if not is_dup:
            kept.append(frag)
    return kept


def merge_ocr_fragments(
    fragments: List[Dict[str, Any]],
    separator: Optional[str] = None,
) -> Tuple[List[str], List[float]]:
    """Склеивает OCR-фрагменты одной визуальной строки по координатам (задача 4.1).

    Компенсирует небольшой наклон фото (до ±15°) по медиане углов широких рамок.
    Если координаты отсутствуют, возвращает фрагменты в исходном порядке.
    """
    if not fragments:
        return [], []

    if any(f.get("x_min") is None for f in fragments):
        return [f["text"] for f in fragments], [f["score"] for f in fragments]

    sep = separator if separator is not None else os.getenv("OCR_MERGE_SEP", " ")

    # Оцениваем средний наклон строки по достаточно широким фрагментам
    angles = [
        f["angle"]
        for f in fragments
        if (f["x_max"] - f["x_min"]) > 2.0 * f["height"] and abs(f["angle"]) <= math.radians(15.0)
    ]
    median_angle = statistics.median(angles) if len(angles) >= 3 else 0.0
    if abs(median_angle) < math.radians(0.5):
        median_angle = 0.0

    cos_a = math.cos(-median_angle)
    sin_a = math.sin(-median_angle)

    projected = []
    for f in fragments:
        x_c = (f["x_min"] + f["x_max"]) / 2.0
        y_c = f["y_center"]
        if median_angle != 0.0:
            x_rot = x_c * cos_a - y_c * sin_a
            y_rot = x_c * sin_a + y_c * cos_a
            x_left = f["x_min"] * cos_a - y_c * sin_a
        else:
            x_rot = x_c
            y_rot = y_c
            x_left = f["x_min"]
        projected.append((y_rot, x_left, x_rot, f))

    projected.sort(key=lambda item: item[0])

    rows: List[List[Tuple[float, float, Dict[str, Any]]]] = []
    row_meta: List[Tuple[float, float]] = []  # (avg_y, avg_h)

    for y_rot, x_left, _x_rot, f in projected:
        placed = False
        if rows:
            avg_y, avg_h = row_meta[-1]
            threshold = 0.5 * max(avg_h, f["height"])
            if abs(y_rot - avg_y) <= threshold:
                rows[-1].append((x_left, y_rot, f))
                n = len(rows[-1])
                new_avg_y = sum(item[1] for item in rows[-1]) / n
                new_avg_h = sum(item[2]["height"] for item in rows[-1]) / n
                row_meta[-1] = (new_avg_y, new_avg_h)
                placed = True
        if not placed:
            rows.append([(x_left, y_rot, f)])
            row_meta.append((y_rot, f["height"]))

    lines: List[str] = []
    scores: List[float] = []
    for row in rows:
        row.sort(key=lambda item: item[0])
        text = sep.join(item[2]["text"] for item in row).strip()
        if text:
            lines.append(text)
            scores.append(sum(item[2]["score"] for item in row) / len(row))

    return lines, scores


def merge_pages(pages: List[List[str]]) -> Tuple[List[str], List[str]]:
    """Склеивает распознанные строки нескольких фото одного чека (задача 5.7).

    На стыке соседних страниц ищет совпадающие строки по похожести (difflib >= 0.75)
    и убирает повторы. Если перекрытие не найдено, соединяет по порядку и добавляет
    предупреждение 'pages_not_overlapping'.
    """
    if not pages:
        return [], []
    cleaned_pages = [[str(line).strip() for line in p if str(line).strip()] for p in pages]
    cleaned_pages = [p for p in cleaned_pages if p]
    if not cleaned_pages:
        return [], []

    merged = list(cleaned_pages[0])
    warnings: List[str] = []

    for next_page in cleaned_pages[1:]:
        max_k = min(15, len(merged), len(next_page))
        best_k = 0
        for k in range(max_k, 0, -1):
            suffix = merged[-k:]
            prefix = next_page[:k]
            ratios = [
                difflib.SequenceMatcher(None, a.casefold(), b.casefold()).ratio()
                for a, b in zip(suffix, prefix)
            ]
            if all(r >= 0.75 for r in ratios):
                best_k = k
                break
        if best_k > 0:
            merged.extend(next_page[best_k:])
        else:
            if "pages_not_overlapping" not in warnings:
                warnings.append("pages_not_overlapping")
            merged.extend(next_page)

    return merged, warnings


def _ocr_single_image_with_meta(
    ocr: PaddleOCR,
    image_input: Any,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> Tuple[List[str], List[float], Optional[Tuple[int, int]]]:
    """Выполняет OCR одного изображения (с нарезкой длинных фото и склейкой строк)."""
    temp_path = None
    if isinstance(image_input, (bytes, bytearray)):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp.write(image_input)
            temp_path = tmp.name
        image_path = temp_path
    else:
        image_path = image_input

    merge_lines_on = os.getenv("OCR_MERGE_LINES", "1").strip().lower() not in ("0", "false", "no", "off")
    image_shape: Optional[Tuple[int, int]] = None
    all_fragments: List[Dict[str, Any]] = []

    try:
        prepared = load_prepared_image(image_path) if isinstance(image_path, str) else None
        if prepared is not None:
            image_shape = (int(prepared.shape[0]), int(prepared.shape[1]))
            slices = slice_long_image(prepared)
            total_slices = len(slices)
            for idx, (crop, y_offset) in enumerate(slices, start=1):
                if on_progress:
                    on_progress("ocr", idx - 1, total_slices)
                result = ocr.ocr(crop)
                if result and result[0]:
                    all_fragments.extend(_extract_fragments(result[0], y_offset=y_offset))
                if on_progress:
                    on_progress("ocr", idx, total_slices)
            if total_slices > 1:
                all_fragments = _deduplicate_overlap_fragments(all_fragments)
        else:
            ocr_input = prepare_for_ocr(image_path) if isinstance(image_path, str) else image_path
            result = ocr.ocr(ocr_input)
            if result and result[0]:
                all_fragments.extend(_extract_fragments(result[0], y_offset=0))
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

    if not all_fragments:
        return [], [], image_shape

    if merge_lines_on:
        lines, line_scores = merge_ocr_fragments(all_fragments)
    else:
        lines = [f["text"] for f in all_fragments]
        line_scores = [f["score"] for f in all_fragments]

    return lines, line_scores, image_shape


def ocr_image_with_scores(
    ocr: PaddleOCR,
    image_input: Any,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> Tuple[List[str], List[float]]:
    """Принимает путь к файлу (str) или байты (bytes). Возвращает (строки, уверенности)."""
    lines, scores, _ = _ocr_single_image_with_meta(ocr, image_input, on_progress=on_progress)
    return lines, scores


def ocr_image(ocr: PaddleOCR, image_input: Any) -> List[str]:
    """Принимает путь к файлу (str) или байты (bytes). Возвращает список строк текста."""
    lines, _ = ocr_image_with_scores(ocr, image_input)
    return lines


def analyze_receipt(
    image_input: Any,
    ocr: PaddleOCR = None,
    categories: Optional[List[dict]] = None,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> Dict[str, Any]:
    """Полный цикл: изображение (или список изображений) → структурированный словарь.

    Если Ollama/Qwen доступна — используется нейросетевой анализ (или разбор кусками для длинных чеков).
    Если Ollama отключена или не отвечает — срабатывает мгновенный алгоритмический парсер.
    """
    if ocr is None:
        ocr = init_ocr()

    warnings: List[str] = []
    t_ocr_start = time.perf_counter()

    if isinstance(image_input, (list, tuple)):
        pages_lines: List[List[str]] = []
        line_scores: List[float] = []
        image_shape: Optional[Tuple[int, int]] = None
        for idx, page_input in enumerate(image_input, start=1):
            if on_progress:
                on_progress("ocr", idx - 1, len(image_input))
            p_lines, p_scores, p_shape = _ocr_single_image_with_meta(ocr, page_input)
            pages_lines.append(p_lines)
            line_scores.extend(p_scores)
            if p_shape and image_shape is None:
                image_shape = p_shape
            if on_progress:
                on_progress("ocr", idx, len(image_input))
        lines, page_warnings = merge_pages(pages_lines)
        warnings.extend(page_warnings)
    else:
        lines, line_scores, image_shape = _ocr_single_image_with_meta(ocr, image_input, on_progress=on_progress)

    ocr_elapsed = time.perf_counter() - t_ocr_start
    raw_text = "\n".join(lines)

    size_kind = receipt_size(lines, image_shape)
    if size_kind == "long" and "long_receipt" not in warnings:
        warnings.append("long_receipt")

    use_ai = os.getenv("USE_AI_PARSER", "auto").strip().lower()
    parsed: Optional[Dict[str, Any]] = None
    parser_used = "regex"

    t_llm_start = time.perf_counter()
    if use_ai not in ("0", "false", "no", "off"):
        try:
            parsed = parse_receipt_with_correction(
                raw_text,
                is_long=(size_kind == "long"),
                on_progress=on_progress,
            )
            if parsed:
                parser_used = parsed.get("parser_used") or "qwen"
                for w in parsed.get("warnings") or []:
                    if w not in warnings:
                        warnings.append(w)
        except Exception as e:
            logger.warning(
                "Qwen не разобрал чек (%s: %s). Используется встроенный парсер",
                type(e).__name__,
                e,
            )
    llm_elapsed = time.perf_counter() - t_llm_start

    if not parsed:
        parsed = parse_receipt_regex(lines)
        parser_used = "regex"

    # Каноническое имя сети и исправление «Lепог» → «Lenor» — для любого парсера
    try:
        fix_item_names(parsed.get("items"))
        parsed["merchant"] = canonical_merchant(parsed.get("store"))
    except Exception as e:
        logger.warning("Ошибка в canonical_merchant: %s", e)
        parsed.setdefault("merchant", None)

    # Определение валюты чека
    try:
        currency, source = resolve_currency(lines, parsed)
    except Exception as e:
        logger.warning("Ошибка в resolve_currency: %s", e)
        currency, source = "KZT", "default"

    parsed["currency"] = currency or "KZT"
    parsed["currency_source"] = source

    t_cat_start = time.perf_counter()
    try:
        # Название магазина — подсказка для двусмысленных товаров («Кофе» в кофейне и в супермаркете)
        categorize_items(parsed.get("items") or [], categories or [],
                         store=" ".join(s for s in (parsed.get("merchant"), parsed.get("store")) if s) or None)
    except Exception as e:
        logger.warning("Ошибка в categorize_items: %s", e)
    cat_elapsed = time.perf_counter() - t_cat_start

    # Уверенность распознавания чека и по каждой позиции (4.2)
    items_list = parsed.get("items") or []
    try:
        attach_item_confidences(items_list, lines, line_scores)
        parsed["confidence"] = compute_confidence(line_scores, items_list, parsed.get("total"))
    except Exception as e:
        logger.warning("Ошибка в compute_confidence: %s", e)
        parsed.setdefault("confidence", None)

    # Проверка расхождения суммы позиций с итогом -> код предупреждения sum_mismatch
    total_val = parsed.get("total")
    if items_list and total_val is not None:
        try:
            items_sum = sum(float(it.get("total_price") or 0.0) for it in items_list)
            if float(total_val) > 0 and abs(items_sum - float(total_val)) > max(1.0, float(total_val) * 0.02):
                if "sum_mismatch" not in warnings:
                    warnings.append("sum_mismatch")
        except (TypeError, ValueError):
            pass

    # Фискальные реквизиты (по полному исходному OCR-тексту)
    try:
        parsed["fiscal"] = extract_fiscal_info(lines)
    except Exception as e:
        logger.warning("Ошибка в extract_fiscal_info: %s", e)
        parsed.setdefault("fiscal", None)

    # Гарантия наличия category_id, category, category_source у каждой позиции
    for it in items_list:
        it.setdefault("category_id", None)
        it.setdefault("category", None)
        it.setdefault("category_source", None)

    parsed["parser_used"] = parser_used
    parsed["warnings"] = warnings
    parsed["raw_lines"] = lines

    logger.info(
        "analyze: ocr=%.1fs llm=%.1fs categorize=%.1fs parser=%s size=%s items=%d",
        ocr_elapsed,
        llm_elapsed,
        cat_elapsed,
        parser_used,
        size_kind,
        len(items_list),
    )

    return parsed


def analyze_receipt_comparison(
    image_input: Any,
    ocr: PaddleOCR = None,
    categories: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """Сравнение RegEx Parser vs Local AI Agent."""
    if ocr is None:
        ocr = init_ocr()

    lines = ocr_image(ocr, image_input)
    regex_result = parse_receipt_regex(lines)
    ai_result = parse_receipt_with_correction("\n".join(lines))
    categorize_items(ai_result.get("items", []), categories or [], store=ai_result.get("store"))
    currency, currency_source = resolve_currency(lines, ai_result)
    ai_result["currency"] = currency
    ai_result["currency_source"] = currency_source
    return {
        "regex": regex_result,
        "ai": ai_result,
        "raw_lines": lines,
    }


if __name__ == "__main__":
    img_path = sys.argv[1] if len(sys.argv) > 1 else "images.jpeg"
    print(f"=== Распознавание: {img_path} ===")
    ocr_instance = init_ocr()
    data = analyze_receipt(img_path, ocr=ocr_instance)
    print(f"Магазин: {data.get('store') or '—'} | Итого: {data.get('total')} {data.get('currency')}")
    print(f"Парсер: {data.get('parser_used')} | Позиций: {len(data.get('items') or [])}")
