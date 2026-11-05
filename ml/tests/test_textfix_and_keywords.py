"""Исправление смешанных алфавитов, поиск ключевых слов, предобработка фото."""
import os
from unittest.mock import patch

import numpy as np
import pytest

from app.categorize import categorize_items
from app.preprocess import prepare, prepare_for_ocr
from app.textfix import fix_item_names, fix_mixed_script

STANDARD = [
    {"id": 1, "name": "Продукты", "description": "Еда и напитки для дома: молочные продукты, хлеб, мясо, овощи, фрукты, крупы, сладости"},
    {"id": 2, "name": "Одежда", "description": "Одежда, обувь, аксессуары"},
    {"id": 3, "name": "Дом и быт", "description": "Бытовая химия, хозтовары, посуда, мелочи для дома"},
    {"id": 6, "name": "Здоровье", "description": "Аптека, лекарства, витамины, медицинские услуги"},
]


@pytest.mark.parametrize("raw, fixed", [
    ("Lепог кондиционер д/белья", "Lenor кондиционер д/белья"),
    ("Fаігу капсулы", "Fairy капсулы"),
    ("Рantene шaмпунь, 400MЛ", "Pantene шампунь, 400МЛ"),
    ("Orаl-В щетка", "Oral-B щетка"),
    ("Alwaуs прокладки", "Always прокладки"),
    ("МAГHУМ", "МАГНУМ"),
])
def test_mixed_words_fixed(raw, fixed):
    assert fix_mixed_script(raw) == fixed


@pytest.mark.parametrize("text", [
    "Молоко 2,5% 1л", "iPhone 15 Pro", "MO447CTZ0, CYMKA (M928)", "KILAT'AUTO FCO WASH", "Хлеб «Бородинский»", "",
])
def test_single_script_untouched(text):
    assert fix_mixed_script(text) == text


def test_fix_item_names_in_place():
    items = [{"name": "Lепог"}, {"name": None}, "мусор"]
    fix_item_names(items)
    assert items[0]["name"] == "Lenor" and items[1]["name"] is None


def keywords(name):
    items = [{"name": name}]
    with patch.dict(os.environ, {"USE_AI_PARSER": "0"}):
        categorize_items(items, STANDARD)
    return items[0]["category"]


@pytest.mark.parametrize("name, category", [
    # «д/белья» — назначение, а не бельё
    ("Lenor кондиционер д/белья, 1.8л", "Дом и быт"),
    ("Олай крем д/лица", "Дом и быт"),
    ("Always прокладки Ultra", "Дом и быт"),
    ("Женское бельё", "Одежда"),
    ("Ручка-ремешок для сумки", "Одежда"),
    ("Молоко 2,5% 1л", "Продукты"),
])
def test_keywords(name, category):
    assert keywords(name) == category


def test_key_matches_word_start_only():
    # «бель» не должно находиться внутри слова, «сок» — в «высокий»
    assert keywords("Кобель игрушка") is None
    assert keywords("Высокий стул") is None


def test_description_stop_words_ignored():
    # Раньше ключ «для» из «Еда и напитки для дома» отправлял в «Продукты» всё, где есть «для»
    assert keywords("Подставка для телефона") is None


def test_preprocess_scales():
    big = np.zeros((5000, 2000, 3), np.uint8)
    with patch.dict(os.environ, {"OCR_MAX_SIDE": "2000", "OCR_MIN_WIDTH": "900", "OCR_CLAHE": "0"}):
        assert prepare(big).shape[:2] == (2000, 800)
        assert prepare(np.zeros((900, 450, 3), np.uint8)).shape[:2] == (1800, 900)
        assert prepare(np.zeros((1200, 1000, 3), np.uint8)).shape[:2] == (1200, 1000)


def test_preprocess_disabled_or_broken_returns_path(tmp_path):
    bad = tmp_path / "не фото.jpg"
    bad.write_bytes(b"not an image")
    assert prepare_for_ocr(str(bad)) == str(bad)
    with patch.dict(os.environ, {"OCR_PREPROCESS": "0"}):
        assert prepare_for_ocr("x.jpg") == "x.jpg"


def test_drop_invented_items():
    from app.agent_parser import drop_invented_items
    ocr = "DNS 2350\nCMарTOH BQ 5565L_FEST 2/16GB\nСУMА НOC 20%\nИТОГ 4199.00\nOlay Regenerist кемп д/лица"
    data = {"items": [
        {"name": "MO447CTZ0, CYMKA"},                 # пример из промпта, в чеке его нет
        {"name": "Смартфон BQ 5565L FEST 2/16GB"},     # модель исправила OCR — остаётся
        {"name": "Olay Regenerist крем д/лица"},       # «кемп» → «крем» — остаётся
        {"name": "Совсем другой товар"},               # ни одного слова из чека
    ]}
    names = [i["name"] for i in drop_invented_items(ocr, data)["items"]]
    assert names == ["Смартфон BQ 5565L FEST 2/16GB", "Olay Regenerist крем д/лица"]
    # Если пример действительно есть на чеке — не трогаем
    real = {"items": [{"name": "MO447CTZ0, CYMKA (M928, TU ( U. ))"}]}
    assert drop_invented_items("MO447CTZ0, CYMKA (M928, TU ( U. ))\n1 X 160000.00", real)["items"]


KAZAKHTELECOM_RECEIPT = "\n".join([
    "БИН: 140840021970", "1. Услуги перевозки", "1000 тг", "пассажиров", "1 000 тг × 1",
    "Итого", "1000 тг", "НДС", "107.14 tг", "Наличными", "1000 Tг",
    "Оператор фискальных данных: АО", "\"Казахтелеком\".", "ОФД - АО \"КАЗАХТЕЛЕКОМ\"",
])


def test_total_without_kopecks_not_taken_from_vat():
    from app.agent_parser import extract_total_from_ocr
    assert extract_total_from_ocr(KAZAKHTELECOM_RECEIPT) == 1000.0
    assert extract_total_from_ocr("Итого\n1 000 тг\nНДС\n107.14 тг") == 1000.0
    assert extract_total_from_ocr("ИТОГО\n=20140.00") == 20140.0
    assert extract_total_from_ocr("Итого\n50 ₸") == 50.0


def test_fiscal_operator_is_not_store():
    from app.agent_parser import drop_fiscal_operator_store
    assert drop_fiscal_operator_store(KAZAKHTELECOM_RECEIPT, {"store": "ООО \"Казахтелеком\""})["store"] == ""
    seller = "ТОО \"Бухта.kz\"\n" + KAZAKHTELECOM_RECEIPT
    assert drop_fiscal_operator_store(seller, {"store": "ТОО \"Бухта.kz\""})["store"] == "ТОО \"Бухта.kz\""
    assert drop_fiscal_operator_store("ООО \"Super Market\"\nОФД: ТОО \"Kassa24\"", {"store": "Super Market"})["store"] == "Super Market"
