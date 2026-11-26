"""Категоризация на эталонных позициях (evaluation/categorize_cases.json) без Ollama и разбор ответа Qwen.

Точность вместе с Qwen меряет evaluation/categorize_eval.py (нужна Ollama).
"""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import ml.categorize as cz
from ml.categorize import categorize_items

CASES = json.loads((Path(__file__).resolve().parents[1] / "evaluation" / "categorize_cases.json").read_text("utf-8"))["cases"]
CATEGORIES = [
    {"id": 1, "name": "Продукты", "default_key": "groceries", "description": "Еда и напитки для дома"},
    {"id": 2, "name": "Одежда", "default_key": "clothing", "description": "Одежда, обувь, аксессуары"},
    {"id": 3, "name": "Дом и быт", "default_key": "home", "description": "Бытовая химия, хозтовары, посуда"},
    {"id": 4, "name": "Транспорт", "default_key": "transport", "description": "Топливо, такси, проезд, парковка"},
    {"id": 5, "name": "Кафе", "default_key": "cafe", "description": "Готовая еда и напитки вне дома"},
    {"id": 6, "name": "Здоровье", "default_key": "health", "description": "Аптека, лекарства, витамины"},
    {"id": 7, "name": "Развлечения", "default_key": "entertainment", "description": "Кино, театр, концерты, игры"},
    {"id": 8, "name": "Другое", "default_key": "other", "description": "Всё, что не подошло в другие категории"},
]
KEY_BY_NAME = {c["name"]: c["default_key"] for c in CATEGORIES}


def keywords_only(names, store=None):
    items = [{"name": n} for n in names]
    with patch.dict(os.environ, {"USE_AI_PARSER": "0", "CATEGORIZE_FAST_FIRST": "1"}):
        categorize_items(items, CATEGORIES, store=store)
    return items


def test_main_cases_by_keywords():
    """Основной набор целиком раскладывается ключевыми словами (отложенный — только с Qwen, см. categorize_eval.py)."""
    cases = [c for c in CASES if not c.get("holdout")]
    items = keywords_only([c["name"] for c in cases])
    wrong = [(c["name"], c["expected"], KEY_BY_NAME.get(i.get("category"))) for c, i in zip(cases, items)
             if KEY_BY_NAME.get(i.get("category")) != c["expected"]]
    assert not wrong, wrong


def test_fixed_keyword_bugs():
    """Ошибки, найденные на реальных названиях: короткие казахские слова, двусмысленные слова кафе, точность ключа."""
    got = {i["name"]: KEY_BY_NAME.get(i["category"]) for i in keywords_only([
        "Майка женская",            # «май» (каз. масло) — только целым словом
        "Шампиньоны 400г",          # «шам» (каз. лампа) — только целым словом
        "Конфеты Рафаэлло 150г",    # «раф» — только целым словом
        "Кофе Jacobs Monarch 95г",  # упакованный кофе — продукты
        "кофе",                     # просто «кофе» — кафе (ручной ввод)
        "Роллтон лапша 60г",
        "додо пицца",
        "Масло моторное Shell 4л",  # «моторн» точнее, чем «масло»
        "Паста Barilla спагетти 450г",
    ])}
    assert got == {
        "Майка женская": "clothing", "Шампиньоны 400г": "groceries", "Конфеты Рафаэлло 150г": "groceries",
        "Кофе Jacobs Monarch 95г": "groceries", "кофе": "cafe", "Роллтон лапша 60г": "groceries",
        "додо пицца": "cafe", "Масло моторное Shell 4л": "transport", "Паста Barilla спагетти 450г": "groceries",
    }


def test_cafe_store_moves_food_to_cafe():
    """Чек «АВТОКАФЕ»: еда оттуда — «Кафе»; в супермаркете та же еда — «Продукты»; чужие категории не трогаем."""
    names = ["картофель печёный", "Закусочный", "кофе американо 0.2", "Пакет майка"]
    cafe = [KEY_BY_NAME.get(i["category"]) for i in keywords_only(names, store='ООО "АВТОКАФЕ"')]
    shop = [KEY_BY_NAME.get(i["category"]) for i in keywords_only(names, store="Magnum Cash&Carry")]
    assert cafe == ["cafe", "cafe", "cafe", "home"]
    assert shop == ["groceries", "groceries", "cafe", "home"]
    assert cz._is_cafe_store("Coffee BOOM") and cz._is_cafe_store("KFC Абая") and not cz._is_cafe_store("Small")


def _ollama_response(answer: dict, status=200):
    resp = MagicMock(status_code=status)
    resp.json.return_value = {"response": json.dumps(answer, ensure_ascii=False)}
    return resp


def test_qwen_answer_per_item_object():
    """Ответ {"0": ..., "1": ...}: схема требует ключ на каждый товар, «Другое» → без категории."""
    items = [{"name": "ФОЛЬГА АЛЮМИН."}, {"name": "ИБУПРОФЕН"}, {"name": "НЕЧТО"}]
    with patch.dict(os.environ, {"USE_AI_PARSER": "1"}), \
            patch("ml.categorize.requests.post",
                  return_value=_ollama_response({"0": "Дом и быт", "1": "Здоровье", "2": "Другое"})) as post:
        result = cz.categorize_via_qwen(items, CATEGORIES, store="Magnum")
    assert result == {0: 2, 1: 5}
    payload = post.call_args.kwargs["json"]
    assert payload["format"]["required"] == ["0", "1", "2"]
    assert "Magnum" in payload["prompt"]


def test_missing_categorize_model_falls_back_to_main():
    """Модели категорий нет в Ollama (404) — повтор основной моделью, дальше сразу ею."""
    items = [{"name": "ФОЛЬГА"}]
    missing = MagicMock(status_code=404)
    with patch.object(cz, "CATEGORIZE_MODEL", "no-such-model"), patch.object(cz, "_categorize_model_missing", False), \
            patch.dict(os.environ, {"USE_AI_PARSER": "1"}), \
            patch("ml.categorize.requests.post", side_effect=[missing, _ollama_response({"0": "Дом и быт"})]) as post:
        assert cz.categorize_via_qwen(items, CATEGORIES) == {0: 2}
        assert [c.kwargs["json"]["model"] for c in post.call_args_list] == ["no-such-model", cz.MODEL_NAME]
        assert cz._categorize_model_missing is True
