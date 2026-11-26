"""Тесты длинных чеков и ускорения распознавания:
- единая реализация OCR (app.ocr -> ml.ocr)
- масштабирование по ширине OCR_TARGET_WIDTH и нарезка длинных фото полосами
- быстрая категоризация (rules -> cache -> keywords -> Qwen + батчинг)
- склейка фрагментов OCR в строки по координатам с компенсацией наклона
- уверенность по каждой позиции (items[].confidence)
- признак длинного чека receipt_size
- параметры LLM, обработка done_reason=='length', разбор длинного чека кусками, фильтр служебных строк
- склейка нескольких фото одного чека merge_pages
- новые метрики бенчмарка (p50/p95, short/long, regex fallback)
"""
import os
from unittest.mock import MagicMock, patch

import numpy as np

from ml.agent_parser import (
    LLMTruncatedError,
    _compute_llm_params,
    _normalize_compact_items,
    filter_service_lines_for_llm,
    parse_long_receipt,
    parse_receipt_with_correction,
)
from ml.categorize import categorize_items, clear_category_cache
from ml.confidence import attach_item_confidences, compute_item_confidence
from ml.ocr import (
    _deduplicate_overlap_fragments,
    merge_ocr_fragments,
    merge_pages,
    receipt_size,
)
from ml.preprocess import prepare, slice_long_image


def test_receipt_size_short_and_long():
    assert receipt_size(["строка"] * 40, (1200, 800)) == "short"
    assert receipt_size(["строка"] * 85, (1200, 800)) == "long"
    assert receipt_size(["строка"] * 20, (4000, 1000)) == "long"  # h/w = 4.0 > 3.0


def test_preprocess_target_width_preserves_long_receipt_height():
    long_img = np.zeros((6000, 1500, 3), np.uint8)
    with patch.dict(os.environ, {"OCR_TARGET_WIDTH": "1200", "OCR_CLAHE": "0"}, clear=False):
        os.environ.pop("OCR_MAX_SIDE", None)
        out = prepare(long_img)
    assert out.shape[:2] == (4800, 1200)


def test_slice_long_image_and_overlap():
    short_img = np.zeros((1000, 1000, 3), np.uint8)
    assert len(slice_long_image(short_img, slice_height=1600, overlap=140)) == 1

    long_img = np.zeros((4200, 1000, 3), np.uint8)
    slices = slice_long_image(long_img, slice_height=1600, overlap=150)
    assert len(slices) == 3
    assert slices[0][1] == 0
    assert slices[1][1] == 1450
    assert slices[-1][0].shape[0] + slices[-1][1] == 4200


def test_merge_ocr_fragments_by_coordinates():
    # Два фрагмента одной строки ("Молоко 3.2%" слева и "2 x 450 = 900" справа) + вторая строка ниже
    fragments = [
        {"text": "2 x 450 = 900", "score": 0.95, "x_min": 400.0, "x_max": 600.0, "y_center": 102.0, "height": 28.0, "angle": 0.0},
        {"text": "Молоко 3.2%", "score": 0.90, "x_min": 40.0, "x_max": 280.0, "y_center": 100.0, "height": 28.0, "angle": 0.0},
        {"text": "Хлеб", "score": 0.88, "x_min": 40.0, "x_max": 120.0, "y_center": 160.0, "height": 26.0, "angle": 0.0},
        {"text": "250.00", "score": 0.92, "x_min": 450.0, "x_max": 560.0, "y_center": 162.0, "height": 26.0, "angle": 0.0},
    ]
    lines, scores = merge_ocr_fragments(fragments)
    assert lines == ["Молоко 3.2% 2 x 450 = 900", "Хлеб 250.00"]
    assert len(scores) == 2
    assert 0.90 <= scores[0] <= 0.95


def test_deduplicate_overlap_fragments():
    frags = [
        {"text": "Молоко 3.2%", "score": 0.82, "x_min": 40.0, "x_max": 260.0, "y_center": 1500.0, "height": 28.0, "angle": 0.0},
        {"text": "Молоко 3.2%", "score": 0.96, "x_min": 42.0, "x_max": 262.0, "y_center": 1503.0, "height": 28.0, "angle": 0.0},
        {"text": "Кефир 1л", "score": 0.91, "x_min": 40.0, "x_max": 200.0, "y_center": 1560.0, "height": 28.0, "angle": 0.0},
    ]
    dedup = _deduplicate_overlap_fragments(frags)
    assert len(dedup) == 2
    assert dedup[0]["score"] == 0.96


def test_merge_pages_with_and_without_overlap():
    p1 = ["ТОО Магнум", "Молоко 450", "Хлеб 200", "Сыр 1500"]
    p2 = ["Хлеб 200", "Сыр 1500", "Яблоки 800", "ИТОГО 2950"]
    merged, warnings = merge_pages([p1, p2])
    assert merged == ["ТОО Магнум", "Молоко 450", "Хлеб 200", "Сыр 1500", "Яблоки 800", "ИТОГО 2950"]
    assert warnings == []

    p3 = ["Совсем другая страница", "Чай 600"]
    merged2, warnings2 = merge_pages([p1, p3])
    assert "pages_not_overlapping" in warnings2
    assert len(merged2) == len(p1) + len(p3)


def test_filter_service_lines_and_compact_items():
    raw = "\n".join([
        "ТОО Магнум",
        "БИН: 140840021970",
        "Сайт ОФД: https://consumer.oofd.kz",
        "СМЕНА: 14",
        "Молоко 3.2% 1 x 450 = 450.00",
        "Спасибо за покупку!",
        "ИТОГО 450.00",
    ])
    filtered = filter_service_lines_for_llm(raw)
    assert "ТОО Магнум" in filtered
    assert "Молоко 3.2%" in filtered
    assert "ИТОГО 450.00" in filtered
    assert "consumer.oofd.kz" not in filtered
    assert "БИН:" not in filtered
    assert "Спасибо за покупку" not in filtered

    compact = _normalize_compact_items([["Молоко", 2, 450, 900]])
    assert compact == [{"name": "Молоко", "quantity": 2, "price_per_unit": 450, "total_price": 900, "category": None}]


def test_llm_params_and_truncated_fallback_to_chunks():
    short_p = _compute_llm_params("a\n" * 20, is_long=False)
    long_p = _compute_llm_params("a\n" * 120, is_long=True)
    assert long_p["num_ctx"] >= short_p["num_ctx"]
    assert long_p["num_predict"] > short_p["num_predict"]

    sample_ocr = "ТОО Магнум\nМолоко 3.2% 1 x 450.00\nХлеб 1 x 200.00\nИТОГО 650.00"
    calls = {"n": 0}

    def fake_call_llm(prompt, system_prompt="", schema=None, is_long=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMTruncatedError("truncated")
        return {"items": [["Молоко 3.2%", 1, 450.0, 450.0], ["Хлеб", 1, 200.0, 200.0]]}

    with patch("ml.agent_parser._call_llm", side_effect=fake_call_llm):
        res = parse_receipt_with_correction(sample_ocr)

    assert res["parser_used"] == "qwen_chunks"
    assert "llm_truncated" in (res.get("warnings") or [])
    assert len(res["items"]) == 2
    assert res["total"] == 650.0


def test_parse_long_receipt_deduplicates_chunk_boundary():
    lines = ["ТОО Супермаркет", "15.11.2026 12:00"]
    for i in range(1, 51):
        lines.append(f"Товар_{i} 1 x 100.00")
    lines.append("ИТОГО 5000.00")
    ocr_text = "\n".join(lines)

    # Два чанка с общим элементом на границе
    chunk_responses = [
        {"items": [["Товар_1", 1, 100.0, 100.0], ["Товар_2", 1, 100.0, 100.0]]},
        {"items": [["Товар_2", 1, 100.0, 100.0], ["Товар_3", 1, 100.0, 100.0]]},
    ]

    with patch("ml.agent_parser._call_llm", side_effect=chunk_responses):
        res = parse_long_receipt(ocr_text, chunk_lines=35, overlap_lines=3)

    names = [it["name"] for it in res["items"]]
    assert names == ["Товар_1", "Товар_2", "Товар_3"]
    assert res["parser_used"] == "qwen_chunks"
    assert res["total"] == 5000.0


def test_item_confidence_scores():
    lines = ["Молоко 3.2% 2 x 450 = 900.00", "Странный товар 999.00"]
    scores = [0.97, 0.40]
    items = [
        {"name": "Молоко 3.2%", "quantity": 2, "price_per_unit": 450.0, "total_price": 900.0},
        {"name": "Выдуманный", "quantity": 2, "price_per_unit": 100.0, "total_price": 500.0},
    ]
    attach_item_confidences(items, lines, scores)
    assert items[0]["confidence"] >= 0.90
    assert items[1]["confidence"] < items[0]["confidence"]
    assert 0.0 <= compute_item_confidence(items[1], lines, scores) <= 1.0


def test_fast_categorize_rules_cache_keywords_then_qwen():
    clear_category_cache()
    categories = [
        {"id": 1, "name": "Продукты", "default_key": "groceries", "position": 0},
        {"id": 2, "name": "Техника", "description": "гаджеты, кабели", "position": 1},
    ]
    rules = {"мой любимый товар": 2}
    items = [
        {"name": "Мой любимый товар 1шт"},
        {"name": "Молоко 3.2% 1л"},
        {"name": "Артикул ZX-9000"},
    ]
    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"response": '{"items": [{"index": 0, "category": "Техника"}]}'}

    with patch.dict(os.environ, {"USE_AI_PARSER": "1", "CATEGORIZE_FAST_FIRST": "1"}), \
            patch("ml.categorize.requests.post", return_value=fake_resp) as post_mock:
        categorize_items(items, categories, rules=rules)
        # Qwen вызван ровно 1 раз и только для 1 неизвестного товара (Артикул ZX-9000)
        assert post_mock.call_count == 1
        assert items[0]["category_source"] == "rule"
        assert items[0]["category"] == "Техника"
        assert items[1]["category_source"] == "keywords"
        assert items[1]["category"] == "Продукты"
        assert items[2]["category_source"] == "ai"
        assert items[2]["category"] == "Техника"

        # Повторный вызов для того же «Артикул ZX-9000» должен взяться из кэша без вызова Qwen!
        items2 = [{"name": "Артикул ZX-9000"}]
        categorize_items(items2, categories)
        assert post_mock.call_count == 1
        assert items2[0]["category_source"] == "cache"
        assert items2[0]["category"] == "Техника"
