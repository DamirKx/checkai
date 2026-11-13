"""Тесты ИИ-рекомендаций по расходам (NEXT_WEEK_TASKS.md, B.1)."""
from unittest.mock import patch, MagicMock
import pytest

from app.advisor import (
    detect_facts,
    build_rule_insights,
    generate_insights,
    _has_hallucinated_numbers,
    _validate_ai_response,
)


@pytest.fixture
def sample_snapshot():
    return {
        "base_currency": "KZT",
        "today": "2026-11-12",
        "total_limit": 300000,
        "months": [
            {"month": "2026-11", "total": 320000, "receipts": 35},
        ],
        "categories": [
            {
                "id": 4,
                "name": "Кафе",
                "this_month": 32000,
                "prev_months": [21000, 23500, 22800],
                "limit": 25000,
                "receipts": 14,
            },
            {
                "id": 1,
                "name": "Продукты",
                "this_month": 80000,
                "prev_months": [75000, 78000, 76000],
                "limit": 100000,
                "receipts": 20,
            },
            {
                "id": 3,
                "name": "Транспорт",
                "this_month": 23000,
                "prev_months": [22000, 24000, 23000],
                "limit": None,
                "receipts": 10,
            },
        ],
        "small_purchases": [
            {"category_id": 4, "store": "Coffee Boom", "count": 14, "total": 21000},
        ],
        "recurring": [
            {"category_id": 7, "store": "Яндекс Плюс", "amount": 2990},
        ],
        "uncategorized_share": 0.18,
        "unconverted": [
            {"currency": "USD", "count": 2},
        ],
    }


def test_detect_facts_finds_all_types(sample_snapshot):
    facts = detect_facts(sample_snapshot)
    types = {f["type"] for f in facts}

    assert "over_budget" in types       # Общий лимит и Кафе
    assert "budget_forecast" in types   # Продукты (80 000 за 12 дней -> к 30 дню превысит 100 000)
    assert "category_growth" in types   # Кафе (32 000 vs медиана ~22 800)
    assert "frequent_small" in types    # Coffee Boom (14 раз)
    assert "recurring" in types         # Яндекс Плюс
    assert "budget_suggestion" in types # Транспорт стабилен
    assert "uncategorized" in types     # 18% > 15%
    assert "unconverted" in types       # 2 чека USD


def test_build_rule_insights(sample_snapshot):
    facts = detect_facts(sample_snapshot)
    res = build_rule_insights(sample_snapshot, facts)

    assert res["model"] == "rules"
    items = res["insights"]["items"]
    assert len(items) >= 2
    for item in items:
        assert item["type"] in {
            "over_budget", "budget_forecast", "category_growth",
            "frequent_small", "recurring", "budget_suggestion",
            "uncategorized", "unconverted"
        }
        assert len(item["title"]) <= 60
        assert len(item["text"]) <= 300
        assert item["action"]["kind"] in ("open_history", "set_budget", "open_categories", "none")


def test_hallucinated_numbers_detection():
    allowed = {25000.0, 32000.0, 7000.0, 14.0, 40.0}

    # Все числа допустимы
    assert not _has_hallucinated_numbers("Лимит 25 000 ₸ превышен на 7 000 ₸ (потрачено 32 000 ₸)", allowed)
    # Число 99 999 отсутствует в фактах -> галлюцинация
    assert _has_hallucinated_numbers("Вы потратили 99 999 ₸ на кофе", allowed)


def test_validate_ai_response_drops_hallucinated_items(sample_snapshot):
    facts = detect_facts(sample_snapshot)
    raw_ai = {
        "summary": "Ваши расходы за месяц.",
        "items": [
            {
                "id": "over_budget:4:2026-11",
                "type": "over_budget",
                "priority": 1,
                "title": "Кафе: лимит превышен",
                "text": "Потрачено 32 000 ₸ при лимите 25 000 ₸.",  # Числа есть в фактах
                "category_id": 4,
                "action": {"kind": "open_history", "category_id": 4},
            },
            {
                "id": "fake:99",
                "type": "over_budget",
                "priority": 2,
                "title": "Выдуманный расход",
                "text": "Вы потратили 555 555 ₸ на неизвестные услуги.",  # Выдуманное число!
                "category_id": 4,
                "action": {"kind": "none"},
            },
            {
                "id": "small:4",
                "type": "frequent_small",
                "priority": 3,
                "title": "Coffee Boom: 14 покупок",
                "text": "14 раз на сумму 21 000 ₸.",
                "category_id": 4,
                "action": {"kind": "open_history", "category_id": 4},
            },
        ],
    }

    validated = _validate_ai_response(raw_ai, facts, sample_snapshot)
    assert validated is not None
    # Элемент с 555 555 должен быть отброшен
    titles = [it["title"] for it in validated["items"]]
    assert "Выдуманный расход" not in titles
    assert len(validated["items"]) == 2


def test_generate_insights_fallback_on_error(sample_snapshot):
    with patch("app.advisor.complete_json", side_effect=RuntimeError("API error")):
        res = generate_insights(sample_snapshot, provider="gemini")
        assert res["model"] == "rules"
        assert len(res["insights"]["items"]) >= 2


def test_generate_insights_off_provider(sample_snapshot):
    res = generate_insights(sample_snapshot, provider="off")
    assert res["model"] == "rules"
    assert len(res["insights"]["items"]) >= 2
