"""Каноническое имя магазина (PROFILE_TASKS.md, п. 2.2)."""
import pytest

from app.merchant import canonical_merchant
from app.parser import parse_receipt


@pytest.mark.parametrize("store", ["MAGNUM", "Magnum Cash&Carry", 'ТОО "Magnum"', "МАГНУМ", "МAГHУМ", "magnum №12"])
def test_magnum_variants(store):
    assert canonical_merchant(store) == "Magnum"


@pytest.mark.parametrize("store, expected", [
    ("SMALL", "Small"),
    ("ТОО «Galmart»", "Galmart"),
    ("Technodom Plus", "Technodom"),
    ("СУЛПАК", "Sulpak"),
    ("arbuz.kz", "Арбуз"),
    ("Пятерочка 1234", "Пятёрочка"),
])
def test_known_chains(store, expected):
    assert canonical_merchant(store) == expected


@pytest.mark.parametrize("store, expected", [
    # Форма собственности, кавычки, адрес
    ("ТОО «Ромашка Плюс» г. Алматы", "Ромашка Плюс"),
    ("ИП Иванов, ул. Абая 1", "Иванов"),
    ("LLP KAZ FOOD", "Kaz Food"),
    # Один регистр и одна раскладка
    ("АBТOKAФE", "Автокафе"),
    ('000 "Автокафе"', "Автокафе"),
    # Смешанный регистр и апостроф — часть имени
    ("McDonald's", "McDonald's"),
    ("кофейня зерно", "Кофейня зерно"),
])
def test_unknown_stores_heuristics(store, expected):
    assert canonical_merchant(store) == expected


@pytest.mark.parametrize("store", [None, "", "   ", "ТОО", '""'])
def test_empty(store):
    assert canonical_merchant(store) is None


def test_parser_returns_merchant_next_to_store():
    result = parse_receipt(['ТОО "MAGNUM CASH&CARRY"', "Хлеб", "100.00 * 1шт. = 100.00", "ИТОГ", "100.00"])
    assert result["store"] == "MAGNUM CASH&CARRY"
    assert result["merchant"] == "Magnum"
