import os
from unittest.mock import patch, MagicMock
from app.categorize import categorize_items, normalize_item_name


def test_normalize_item_name():
    assert normalize_item_name("Крем NIVEA 50мл") == "крем nivea"
    assert normalize_item_name("Крем Nivea 100 мл") == "крем nivea"
    assert normalize_item_name("2 груши") == "груши"
    assert normalize_item_name("Молоко 2,5% 1л") == "молоко"
    assert normalize_item_name("Шоколад Alpen Gold 85г") == "шоколад alpen gold"
    assert normalize_item_name("Ёлка") == "елка"


def test_custom_category_beats_builtin_keywords():
    """Своя категория «Косметика» с описанием -> «Крем Nivea» и «Шампунь» попадают в неё, а не в «Дом и быт»."""
    categories = [
        {"id": 1, "name": "Дом и быт", "description": "Бытовая химия, хозтовары, посуда, мелочи для дома", "position": 0},
        {"id": 2, "name": "Косметика", "description": "Крем, шампунь, тушь, помада", "position": 1},
        {"id": 3, "name": "Продукты", "description": "Еда и напитки для дома: молочные продукты, хлеб, мясо", "position": 2},
    ]

    items = [
        {"name": "Крем Nivea 50мл", "price_per_unit": 100, "total_price": 100},
        {"name": "Шампунь Head & Shoulders 400мл", "price_per_unit": 250, "total_price": 250},
        {"name": "Молоко 3.2% 1л", "price_per_unit": 80, "total_price": 80},
        {"name": "Неизвестный артикул XYZ", "price_per_unit": 50, "total_price": 50},
    ]

    with patch.dict(os.environ, {"USE_AI_PARSER": "0"}):
        categorize_items(items, categories)

    # Крем Nivea -> Косметика (из описания пользователя)
    assert items[0]["category_id"] == 2
    assert items[0]["category"] == "Косметика"
    assert items[0]["category_source"] == "keywords"

    # Шампунь есть и во встроенном словаре «Дом и быт», и в описании «Косметики» -> побеждает описание пользователя
    assert items[1]["category_id"] == 2
    assert items[1]["category"] == "Косметика"
    assert items[1]["category_source"] == "keywords"

    # Молоко -> Продукты
    assert items[2]["category_id"] == 3
    assert items[2]["category"] == "Продукты"
    assert items[2]["category_source"] == "keywords"

    # Неизвестный товар -> None (не «Другое»)
    assert items[3]["category_id"] is None
    assert items[3]["category"] is None
    assert items[3]["category_source"] is None


def test_qwen_out_of_range_falls_back_to_keywords():
    """Qwen вернул номер вне списка или дубль index -> позиция уходит в ключевые слова."""
    categories = [
        {"id": 10, "name": "Продукты", "description": "Еда и напитки, молоко, хлеб"},
        {"id": 20, "name": "Косметика", "description": "Крем, шампунь, тушь"},
    ]

    items = [
        {"name": "Молоко 2.5%", "total_price": 500},
        {"name": "Крем Nivea", "total_price": 1890},
        {"name": "Хлеб бородинский", "total_price": 200},
    ]

    # Для index 0 Qwen вернул валидный номер 1 (Продукты)
    # Для index 1 Qwen вернул номер 99 (вне списка) -> должен сработать fallback на ключевые слова (Косметика)
    # Для index 2 Qwen вернул дублирующий index -> должен сработать fallback на ключевые слова (Продукты)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "response": '{"items": [{"index": 0, "category": 1}, {"index": 1, "category": 99}, {"index": 2, "category": 1}, {"index": 2, "category": 2}]}'
    }

    with patch.dict(os.environ, {"USE_AI_PARSER": "1"}), patch("app.categorize.requests.post", return_value=mock_resp):
        categorize_items(items, categories)

    assert items[0]["category_id"] == 10
    assert items[0]["category"] == "Продукты"
    assert items[0]["category_source"] == "ai"

    assert items[1]["category_id"] == 20
    assert items[1]["category"] == "Косметика"
    assert items[1]["category_source"] == "keywords"

    assert items[2]["category_id"] == 10
    assert items[2]["category"] == "Продукты"
    assert items[2]["category_source"] == "keywords"


def test_guest_categories_have_none_id():
    """У гостя id категорий равен None, но category (название) и category_source заполняются."""
    guest_categories = [
        {"id": None, "name": "Продукты", "description": "Еда и напитки: молоко, хлеб"},
        {"id": None, "name": "Другое", "description": "Всё, что не подошло в другие категории"},
    ]

    items = [
        {"name": "Молоко 1л", "total_price": 450},
        {"name": "Непонятная услуга", "total_price": 1000},
    ]

    with patch.dict(os.environ, {"USE_AI_PARSER": "0"}):
        categorize_items(items, guest_categories)

    assert items[0]["category_id"] is None
    assert items[0]["category"] == "Продукты"
    assert items[0]["category_source"] == "keywords"

    # Неподходящий товар не попадает в «Другое» автоматически
    assert items[1]["category_id"] is None
    assert items[1]["category"] is None
    assert items[1]["category_source"] is None


def test_categorize_empty_inputs():
    items = [{"name": "Товар", "price_per_unit": 10, "total_price": 10, "category": "Старое"}]
    categorize_items(items, [])

    assert items[0]["category_id"] is None
    assert items[0]["category"] is None
    assert items[0]["category_source"] is None


if __name__ == "__main__":
    test_normalize_item_name()
    test_custom_category_beats_builtin_keywords()
    test_qwen_out_of_range_falls_back_to_keywords()
    test_guest_categories_have_none_id()
    test_categorize_empty_inputs()
    print("Все тесты категоризации пройдены!")

