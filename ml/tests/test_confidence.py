"""Тесты расчёта уверенности распознавания (PROFILE_TASKS.md, п. 2.3)."""
import pytest
from app.confidence import compute_confidence


def test_perfect_ocr_and_math():
    scores = [0.98, 0.99, 0.95]
    items = [
        {"name": "Хлеб", "quantity": 2, "price_per_unit": 100, "total_price": 200},
        {"name": "Молоко", "quantity": 1, "price_per_unit": 350, "total_price": 350},
    ]
    total = 550.0
    conf = compute_confidence(scores, items, total)
    assert 0.95 <= conf <= 1.0


def test_ocr_low_confidence():
    scores = [0.3, 0.4, 0.35]
    items = [
        {"name": "Хлеб", "quantity": 1, "price_per_unit": 100, "total_price": 100},
    ]
    total = 100.0
    conf = compute_confidence(scores, items, total)
    # OCR ~0.35 * 0.6 + Math 1.0 * 0.4 = ~0.61
    assert 0.5 <= conf <= 0.7


def test_math_mismatch_lowers_confidence():
    scores = [0.95, 0.95]
    items = [
        {"name": "Хлеб", "quantity": 1, "price_per_unit": 100, "total_price": 100},
    ]
    total = 999.0  # Большое расхождение
    conf_mismatch = compute_confidence(scores, items, total)
    conf_match = compute_confidence(scores, items, 100.0)
    assert conf_mismatch < conf_match


def test_empty_inputs_handled_gracefully():
    assert 0.0 <= compute_confidence([], [], None) <= 1.0
    assert 0.0 <= compute_confidence(None, None, None) <= 1.0
    assert 0.0 <= compute_confidence([0.8], None, None) <= 1.0
