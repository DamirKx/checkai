"""Тесты извлечения реквизитов фискального чека (NEXT_WEEK_TASKS.md, C.3)."""
import pytest
from app.fiscal import extract_fiscal_info, clean_digits


def test_clean_digits_ocr_confusion():
    assert clean_digits("O1O1O1234567") == "010101234567"
    assert clean_digits("l23456") == "123456"
    assert clean_digits("I23Чек") == "123"
    assert clean_digits("№ 000-123") == "000123"


def test_fiscal_kazakhstan_receipt_single_line():
    lines = [
        "ТОО MAGNUM CASH&CARRY",
        "ФИСКАЛЬНЫЙ ЧЕК",
        "Код ККМ КГД: 010101234567",
        "Чек № 000142",
        "ФП: 9876543210",
        "https://consumer.oofd.kz/ticket/12345",
    ]
    fiscal = extract_fiscal_info(lines)
    assert fiscal is not None
    assert fiscal["kkm"] == "010101234567"
    assert fiscal["number"] == "000142"
    assert fiscal["fp"] == "9876543210"
    assert fiscal["qr"] == "https://consumer.oofd.kz/ticket/12345"


def test_fiscal_multiline_labels():
    lines = [
        "РНМ ККМ",
        "020202998877",
        "Чек",
        "77",
        "ФП",
        "12345678",
    ]
    fiscal = extract_fiscal_info(lines)
    assert fiscal is not None
    assert fiscal["kkm"] == "020202998877"
    assert fiscal["number"] == "77"
    assert fiscal["fp"] == "12345678"


def test_fiscal_russian_receipt_labels():
    lines = [
        "ООО АВТОКАФЕ",
        "РН ККТ 0000502615061209",
        "ФН",
        "7280440500094315",
        "ФД",
        "89523",
        "ФП",
        "608422087",
    ]
    fiscal = extract_fiscal_info(lines)
    assert fiscal is not None
    assert fiscal["kkm"] in ("0000502615061209", "7280440500094315")
    assert fiscal["number"] == "89523"
    assert fiscal["fp"] == "608422087"


def test_fiscal_with_ocr_typos():
    lines = [
        "КОД ККМ: O1O1O1234567",  # буквы O вместо 0
        "Чек №: l45",              # буква l вместо 1
        "ФП: 98765432lO",          # l вместо 1, O вместо 0
    ]
    fiscal = extract_fiscal_info(lines)
    assert fiscal is not None
    assert fiscal["kkm"] == "010101234567"
    assert fiscal["number"] == "145"
    assert fiscal["fp"] == "9876543210"


def test_fiscal_znm_and_ofd_qr():
    lines = [
        "ЗНМ: WNM12345678",
        "Чек № 5",
        "Фискальный признак: 55443322",
        "https://ofd1.kz/check?id=999",
    ]
    fiscal = extract_fiscal_info(lines)
    assert fiscal is not None
    assert fiscal["kkm"] == "12345678"
    assert fiscal["number"] == "5"
    assert fiscal["fp"] == "55443322"
    assert fiscal["qr"] == "https://ofd1.kz/check?id=999"


def test_fiscal_empty_when_no_fiscal_data():
    lines = [
        "Привет мир",
        "Хлеб 100",
        "ИТОГО: 100",
    ]
    assert extract_fiscal_info(lines) is None
    assert extract_fiscal_info([]) is None
    assert extract_fiscal_info(None) is None


def test_fiscal_from_string_text():
    text = "РНМ: 010101234567\nЧек: 42\nФП: 11223344"
    fiscal = extract_fiscal_info(text)
    assert fiscal is not None
    assert fiscal["kkm"] == "010101234567"
    assert fiscal["number"] == "42"
    assert fiscal["fp"] == "11223344"


def test_fiscal_kofd_kz_and_id_kkm():
    lines = [
        "Small Supermarket",
        "ID ККМ: 8877665544",
        "Номер чека: 901",
        "ФПД: 33445566",
        "https://kofd.kz/verify?ticket=abc123xyz",
    ]
    fiscal = extract_fiscal_info(lines)
    assert fiscal is not None
    assert fiscal["kkm"] == "8877665544"
    assert fiscal["number"] == "901"
    assert fiscal["fp"] == "33445566"
    assert fiscal["qr"] == "https://kofd.kz/verify?ticket=abc123xyz"


def test_fiscal_poryadkovy_nomer_and_fpd():
    lines = [
        "Galmart Dostyk",
        "Код ККМ КГД 9900112233",
        "Порядковый номер чека 15",
        "Фискальный признак 77889900",
    ]
    fiscal = extract_fiscal_info(lines)
    assert fiscal is not None
    assert fiscal["kkm"] == "9900112233"
    assert fiscal["number"] == "15"
    assert fiscal["fp"] == "77889900"


if __name__ == "__main__":
    test_clean_digits_ocr_confusion()
    test_fiscal_kazakhstan_receipt_single_line()
    test_fiscal_multiline_labels()
    test_fiscal_russian_receipt_labels()
    test_fiscal_with_ocr_typos()
    test_fiscal_znm_and_ofd_qr()
    test_fiscal_empty_when_no_fiscal_data()
    test_fiscal_from_string_text()
    print("Все тесты fiscal пройдены!")
