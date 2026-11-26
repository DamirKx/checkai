"""Расчёт общей уверенности распознавания чека и уверенности по каждой позиции (0.0 .. 1.0).

Что считается:
- общая уверенность чека: средняя уверенность PaddleOCR по строкам + сходимость арифметики
- уверенность по каждой позиции (item["confidence"]): OCR-уверенность строк позиции +
  сходимость quantity * price == total_price + подтверждение чисел позиции в тексте OCR.
"""
import re
from typing import Any, Dict, List, Optional, Set


_WORD_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁәғқңөұүһіӘҒҚҢӨҰҮҺІ]{3,}")
_NUM_RE = re.compile(r"\d+(?:[.,]\d{1,2})?")


def _extract_numbers(text: str) -> Set[float]:
    nums: Set[float] = set()
    cleaned = re.sub(r"(?<=\d)[\s\xa0](?=\d)", "", text or "")
    for m in _NUM_RE.findall(cleaned):
        try:
            nums.add(round(float(m.replace(",", ".")), 2))
        except ValueError:
            pass
    return nums


def compute_confidence(
    line_scores: Optional[List[float]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    total: Optional[float] = None,
) -> float:
    """Возвращает общую уверенность чека от 0.0 до 1.0, округлённую до 2 знаков."""
    if line_scores:
        valid_scores = [float(s) for s in line_scores if s is not None and 0.0 <= float(s) <= 1.0]
        avg_ocr = sum(valid_scores) / len(valid_scores) if valid_scores else (0.85 if items else 0.5)
    else:
        avg_ocr = 0.85 if items else 0.5

    math_checks: List[float] = []
    for it in items or []:
        q = it.get("quantity")
        p = it.get("price_per_unit")
        t = it.get("total_price")
        if q is not None and p is not None and t is not None:
            try:
                expected = round(float(q) * float(p), 2)
                actual = round(float(t), 2)
                if expected > 0:
                    diff = abs(expected - actual) / max(expected, actual)
                    math_checks.append(1.0 if diff < 0.05 else max(0.0, 1.0 - diff))
            except (ValueError, TypeError, ZeroDivisionError):
                pass

    if items and total is not None:
        try:
            total_float = float(total)
            if total_float > 0:
                items_sum = sum(float(it.get("total_price") or 0.0) for it in items)
                if items_sum > 0:
                    diff = abs(items_sum - total_float) / max(items_sum, total_float)
                    math_checks.append(1.0 if diff < 0.05 else max(0.0, 1.0 - diff))
        except (ValueError, TypeError, ZeroDivisionError):
            pass

    if math_checks:
        avg_math = sum(math_checks) / len(math_checks)
        conf = 0.6 * avg_ocr + 0.4 * avg_math
    else:
        conf = avg_ocr

    return round(max(0.0, min(1.0, conf)), 2)


def compute_item_confidence(
    item: Dict[str, Any],
    lines: Optional[List[str]] = None,
    line_scores: Optional[List[float]] = None,
    ocr_numbers: Optional[Set[float]] = None,
) -> float:
    """Считает уверенность по одной позиции (0.0 .. 1.0), задача 4.2."""
    valid_all = [
        float(s) for s in (line_scores or [])
        if s is not None and 0.0 <= float(s) <= 1.0
    ]
    fallback_ocr = sum(valid_all) / len(valid_all) if valid_all else 0.8

    # 1. OCR-уверенность строк, из которых взята позиция
    matched_scores: List[float] = []
    name_words = {w.lower() for w in _WORD_RE.findall(str(item.get("name") or ""))}
    if lines and line_scores and name_words:
        for line, score in zip(lines, line_scores):
            if score is None:
                continue
            line_words = {w.lower() for w in _WORD_RE.findall(str(line or ""))}
            if name_words & line_words:
                s_val = float(score)
                if 0.0 <= s_val <= 1.0:
                    matched_scores.append(s_val)

    ocr_part = (sum(matched_scores) / len(matched_scores)) if matched_scores else fallback_ocr

    # 2. Арифметика: quantity * price_per_unit == total_price
    math_part = 0.85
    q = item.get("quantity")
    p = item.get("price_per_unit")
    t = item.get("total_price")
    if q is not None and p is not None and t is not None:
        try:
            expected = round(float(q) * float(p), 2)
            actual = round(float(t), 2)
            if expected > 0 and actual > 0:
                diff = abs(expected - actual) / max(expected, actual)
                math_part = 1.0 if diff < 0.05 else max(0.0, 1.0 - diff)
        except (ValueError, TypeError, ZeroDivisionError):
            pass

    # 3. Подтверждение чисел позиции в OCR-тексте
    if ocr_numbers is None and lines:
        ocr_numbers = _extract_numbers("\n".join(lines))

    if ocr_numbers:
        confirmed_hits = 0
        checked = 0
        for key in ("total_price", "price_per_unit"):
            val = item.get(key)
            if val is None:
                continue
            try:
                fval = round(float(val), 2)
            except (ValueError, TypeError):
                continue
            if fval <= 0:
                continue
            checked += 1
            if any(abs(fval - n) < 0.02 for n in ocr_numbers):
                confirmed_hits += 1
        if checked > 0:
            confirmed_part = 1.0 if confirmed_hits == checked else (0.7 if confirmed_hits > 0 else 0.35)
        else:
            confirmed_part = 0.5
    else:
        confirmed_part = 0.75

    conf = 0.5 * ocr_part + 0.3 * math_part + 0.2 * confirmed_part
    return round(max(0.0, min(1.0, conf)), 2)


def attach_item_confidences(
    items: Optional[List[Dict[str, Any]]],
    lines: Optional[List[str]] = None,
    line_scores: Optional[List[float]] = None,
) -> List[Dict[str, Any]]:
    """Проставляет поле `confidence` (0..1) каждой позиции на месте."""
    if not items:
        return []
    ocr_numbers = _extract_numbers("\n".join(lines)) if lines else set()
    for it in items:
        if isinstance(it, dict):
            it["confidence"] = compute_item_confidence(it, lines, line_scores, ocr_numbers=ocr_numbers)
    return items
