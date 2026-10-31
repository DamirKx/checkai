from unittest.mock import patch

from app.agent_parser import (
    correct_item_quantities,
    parse_receipt_with_correction,
)


def test_price_star_quantity_multiline_name():
    """Формат 2350.00 *1 / =2350.00, название в две строки, Qwen дал quantity=10 -> после correct_item_quantities количество 1."""
    ocr_text = (
        "шт. Always прокладки Ultra\n"
        "ночные, 10шт\n"
        "2350.00 *1\n"
        "=2350.00\n"
        "ИТОГО\n"
        "=2350.00"
    )
    data = {
        "store": "Magnum",
        "date": "30.10.2026",
        "items": [
            {
                "name": "шт. Always прокладки Ultra ночные, 10шт",
                "quantity": 10,
                "price_per_unit": 2350.0,
                "total_price": 23500.0,
                "category": None,
            }
        ],
        "total": 2350.0,
    }

    result = correct_item_quantities(ocr_text, data)

    assert result["items"][0]["quantity"] == 1
    assert result["items"][0]["total_price"] == 2350.0


def test_legacy_merged_price_quantity_format():
    """Формат 150.002 / 300.00 по-прежнему исправляет количество."""
    ocr_text = (
        "Чай Липтон\n"
        "150.002\n"
        "300.00\n"
        "Итого\n"
        "300.00"
    )
    data = {
        "store": "ООО Ромашка",
        "date": "",
        "items": [
            {
                "name": "Чай Липтон",
                "quantity": 1,
                "price_per_unit": 150.0,
                "total_price": 150.0,
                "category": None,
            }
        ],
        "total": 300.0,
    }

    result = correct_item_quantities(ocr_text, data)

    assert result["items"][0]["quantity"] == 2
    assert result["items"][0]["total_price"] == 300.0


def test_ambiguous_same_price_different_quantities_not_modified():
    """Одна цена с двумя разными количествами в OCR -> позиция не меняется."""
    ocr_text = (
        "Товар А\n"
        "500.00 *1\n"
        "=500.00\n"
        "Товар Б\n"
        "500.00 *3\n"
        "=1500.00"
    )
    data = {
        "store": "Магазин",
        "date": "",
        "items": [
            {
                "name": "Товар А",
                "quantity": 2,
                "price_per_unit": 500.0,
                "total_price": 1000.0,
                "category": None,
            }
        ],
        "total": 2000.0,
    }

    result = correct_item_quantities(ocr_text, data)

    assert result["items"][0]["quantity"] == 2
    assert result["items"][0]["total_price"] == 1000.0


def test_skip_self_correction_when_all_prices_confirmed_in_ocr():
    """Все цены первого ответа есть в OCR, сумма не сходится с итогом -> _call_llm вызван один раз (самокоррекции нет)."""
    ocr_text = (
        "Магазин у дома\n"
        "шт. Always прокладки Ultra\n"
        "ночные, 10шт\n"
        "2350.00 *1\n"
        "=2350.00\n"
        "Шампунь Pantene\n"
        "2850.00 *1\n"
        "=2850.00\n"
        "ИТОГО\n"
        "3200.00"
    )
    first_response = {
        "store": "Магазин у дома",
        "date": "30.10.2026",
        "items": [
            {
                "name": "Always прокладки Ultra ночные, 10шт",
                "quantity": 10,
                "price_per_unit": 2350.0,
                "total_price": 23500.0,
                "category": None,
            },
            {
                "name": "Шампунь Pantene",
                "quantity": 1,
                "price_per_unit": 2850.0,
                "total_price": 2850.0,
                "category": None,
            },
        ],
        "total": 3200.0,
        "currency": "KZT",
    }

    with patch("app.agent_parser._call_llm", return_value=first_response) as mock_llm:
        result = parse_receipt_with_correction(ocr_text)

    assert mock_llm.call_count == 1
    assert len(result["items"]) == 2
    assert result["items"][0]["quantity"] == 1
    assert result["items"][0]["total_price"] == 2350.0
    assert result["items"][1]["price_per_unit"] == 2850.0
    assert result["total"] == 3200.0


def test_reject_self_correction_when_confirmed_prices_corrupted():
    """Самокоррекция вернула цены, которых нет в OCR (2850 -> 285) -> результат отклонён, остались исходные цены."""
    ocr_text = (
        "Магазин\n"
        "Товар 1\n"
        "2850.00 *1\n"
        "=2850.00\n"
        "Товар 2\n"
        "1000.00 *1\n"
        "=1000.00\n"
        "ИТОГО\n"
        "1285.00"
    )
    # В первом ответе у второго товара цена 999.00 (её нет в OCR), поэтому запускается самокоррекция
    first_response = {
        "store": "Магазин",
        "date": "",
        "items": [
            {
                "name": "Товар 1",
                "quantity": 1,
                "price_per_unit": 2850.0,
                "total_price": 2850.0,
                "category": None,
            },
            {
                "name": "Товар 2",
                "quantity": 1,
                "price_per_unit": 999.0,
                "total_price": 999.0,
                "category": None,
            },
        ],
        "total": 1285.0,
    }
    # Самокоррекция портит подтверждённую цену 2850.00 -> 285.00 ради совпадения с итогом 1285.00
    corrupted_second_response = {
        "store": "Магазин",
        "date": "",
        "items": [
            {
                "name": "Товар 1",
                "quantity": 1,
                "price_per_unit": 285.0,
                "total_price": 285.0,
                "category": None,
            },
            {
                "name": "Товар 2",
                "quantity": 1,
                "price_per_unit": 1000.0,
                "total_price": 1000.0,
                "category": None,
            },
        ],
        "total": 1285.0,
    }

    with patch(
        "app.agent_parser._call_llm",
        side_effect=[first_response, corrupted_second_response],
    ) as mock_llm:
        result = parse_receipt_with_correction(ocr_text)

    assert mock_llm.call_count == 2
    assert result["items"][0]["price_per_unit"] == 2850.0
    assert result["items"][0]["total_price"] == 2850.0


def test_accept_self_correction_when_missing_item_added():
    """Самокоррекция добавила пропущенную позицию, и сумма сошлась -> результат принят."""
    ocr_text = (
        "Магазин\n"
        "Молоко\n"
        "500.00 *1\n"
        "=500.00\n"
        "Хлеб\n"
        "200.00 *1\n"
        "=200.00\n"
        "ИТОГО\n"
        "700.00"
    )
    # В первом ответе цена 490.00 (не из OCR) и пропущен второй товар
    first_response = {
        "store": "Магазин",
        "date": "",
        "items": [
            {
                "name": "Молоко",
                "quantity": 1,
                "price_per_unit": 490.0,
                "total_price": 490.0,
                "category": None,
            }
        ],
        "total": 700.0,
    }
    second_response = {
        "store": "Магазин",
        "date": "",
        "items": [
            {
                "name": "Молоко",
                "quantity": 1,
                "price_per_unit": 500.0,
                "total_price": 500.0,
                "category": None,
            },
            {
                "name": "Хлеб",
                "quantity": 1,
                "price_per_unit": 200.0,
                "total_price": 200.0,
                "category": None,
            },
        ],
        "total": 700.0,
    }

    with patch(
        "app.agent_parser._call_llm",
        side_effect=[first_response, second_response],
    ) as mock_llm:
        result = parse_receipt_with_correction(ocr_text)

    assert mock_llm.call_count == 2
    assert len(result["items"]) == 2
    assert result["items"][0]["total_price"] == 500.0
    assert result["items"][1]["total_price"] == 200.0
    assert result["total"] == 700.0


if __name__ == "__main__":
    test_price_star_quantity_multiline_name()
    test_legacy_merged_price_quantity_format()
    test_ambiguous_same_price_different_quantities_not_modified()
    test_skip_self_correction_when_all_prices_confirmed_in_ocr()
    test_reject_self_correction_when_confirmed_prices_corrupted()
    test_accept_self_correction_when_missing_item_added()
    print("Все 6 тестов количества и самокоррекции пройдены!")
