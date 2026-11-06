"""Расчёт общей уверенности распознавания чека (0.0 .. 1.0).

PROFILE_TASKS.md, п. 2.3:
- средняя уверенность PaddleOCR по строкам
- сходимость арифметики: сумма позиций vs total, quantity * price vs total_price
"""
from typing import List, Optional, Any, Dict


def compute_confidence(
    line_scores: Optional[List[float]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    total: Optional[float] = None,
) -> float:
    """Возвращает общую уверенность от 0.0 до 1.0, округлённую до 2 знаков."""
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
