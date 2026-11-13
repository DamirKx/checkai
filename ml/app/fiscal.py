"""Извлечение реквизитов фискального чека (NEXT_WEEK_TASKS.md, C.3).

Возвращает словарь:
{
    "fp": "1234567890",      # Фискальный признак (ФП / ФПД)
    "number": "000123",      # Номер чека (Чек № / ФД)
    "kkm": "010101234567",   # Код ККМ КГД / РНМ / ЗНМ / ФН
    "qr": "https://..."      # Ссылка из QR-кода ОФД (если есть) или None
}
Или None, если фискальные реквизиты не найдены.
"""
import re
from typing import Any, Dict, List, Optional, Union


# Замена похожих букв на цифры для исправления ошибок OCR
_OCR_DIGIT_MAP = str.maketrans({
    "O": "0", "o": "0", "О": "0", "о": "0", "Q": "0",
    "I": "1", "l": "1", "i": "1", "|": "1", "!": "1",
    "З": "3",  # кириллическая З в чисто цифровом контексте
    "S": "5", "s": "5",
    "B": "8",
})


def clean_digits(text: str) -> str:
    """Нормализует строку с цифрами, исправляя типичные опечатки OCR."""
    if not text:
        return ""
    # Сначала заменяем OCR-двойники букв на цифры
    converted = text.translate(_OCR_DIGIT_MAP)
    # Оставляем только цифры
    return re.sub(r"\D", "", converted)


# Регулярки для меток и значений
_RE_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)

# QR-ссылки ОФД
_RE_OFD_URL = re.compile(r"https?://(?:[a-zA-Z0-9-]+\.)*(?:oofd\.kz|ofd1\.kz|ofd\.ru|1-ofd\.ru|taxcom\.ru|kofd\.kz|gov\.kz)[^\s]*", re.IGNORECASE)

# ККМ: РНМ, ЗНМ, Код ККМ КГД, РН ККТ, ФН
_RE_KKM_LABEL = re.compile(r"\b(?:рнм|знм|код\s*ккм|ккм\s*кгд|рн\s*ккт|фн|id\s*ккм|ккм)\b\.?", re.IGNORECASE)
_RE_KKM_LINE = re.compile(r"(?:рнм|знм|код\s*ккм(?:\s*кгд)?|рн\s*ккт|фн|id\s*ккм|ккм)\s*[:№#.\s]*([A-Za-zА-Яа-я0-9!|#\s-]{4,30})", re.IGNORECASE)

# Номер чека: Чек №, Чек, Порядковый номер чека, ФД
_RE_NUMBER_LABEL = re.compile(r"\b(?:чек\s*№|чек|пор(?:ядковый)?\s*номер\s*чека|фд|номер\s*чека)\b\.?", re.IGNORECASE)
_RE_NUMBER_LINE = re.compile(r"(?:чек\s*№|чек|пор(?:ядковый)?\s*номер\s*чека|фд|номер\s*чека)\s*[:№#.\s]*([A-Za-zА-Яа-я0-9!|#\s-]{1,20})", re.IGNORECASE)

# ФП: Фискальный признак, ФП, ФПД
_RE_FP_LABEL = re.compile(r"\b(?:фиск(?:альный)?\s*признак|фпд|фп)\b\.?", re.IGNORECASE)
_RE_FP_LINE = re.compile(r"(?:фиск(?:альный)?\s*признак|фпд|фп)\s*[:№#.\s]*([A-Za-zА-Яа-я0-9!|#\s-]{4,24})", re.IGNORECASE)


def extract_fiscal_info(lines_or_text: Union[List[str], str, None]) -> Optional[Dict[str, Optional[str]]]:
    """Извлекает реквизиты фискального чека из списка строк или сплошного текста OCR."""
    if not lines_or_text:
        return None

    if isinstance(lines_or_text, str):
        lines = [line.strip() for line in lines_or_text.splitlines() if line.strip()]
    else:
        lines = [str(line).strip() for line in lines_or_text if str(line).strip()]

    if not lines:
        return None

    kkm: Optional[str] = None
    number: Optional[str] = None
    fp: Optional[str] = None
    qr: Optional[str] = None

    # 1. Поиск QR ссылки ОФД
    for line in lines:
        url_match = _RE_OFD_URL.search(line) or _RE_URL.search(line)
        if url_match:
            cand = url_match.group(0).rstrip(".,;")
            if "ofd" in cand.lower() or "check" in cand.lower() or "ticket" in cand.lower() or "kkt" in cand.lower():
                qr = cand
                break

    # 2. Построчный разбор с поддержкой переноса значения на следующую строку
    n = len(lines)
    for i, line in enumerate(lines):
        # Проверяем ККМ (РНМ / ФН)
        if not kkm:
            m = _RE_KKM_LINE.search(line)
            if m:
                digits = clean_digits(m.group(1))
                if 6 <= len(digits) <= 24:
                    kkm = digits
            if not kkm and _RE_KKM_LABEL.search(line) and i + 1 < n:
                next_line = lines[i + 1]
                digits = clean_digits(next_line)
                if 6 <= len(digits) <= 24:
                    kkm = digits

        # Проверяем номер чека (Чек № / ФД)
        if not number:
            m = _RE_NUMBER_LINE.search(line)
            if m:
                digits = clean_digits(m.group(1))
                if 1 <= len(digits) <= 12:
                    number = digits
            if not number and _RE_NUMBER_LABEL.search(line) and i + 1 < n:
                next_line = lines[i + 1]
                digits = clean_digits(next_line)
                if 1 <= len(digits) <= 12:
                    number = digits

        # Проверяем фискальный признак (ФП / ФПД)
        if not fp:
            m = _RE_FP_LINE.search(line)
            if m:
                digits = clean_digits(m.group(1))
                if 4 <= len(digits) <= 16:
                    fp = digits
            if not fp and _RE_FP_LABEL.search(line) and i + 1 < n:
                next_line = lines[i + 1]
                digits = clean_digits(next_line)
                if 4 <= len(digits) <= 16:
                    fp = digits

    # Если ничего полезного не найдено
    if not kkm and not number and not fp and not qr:
        return None

    return {
        "fp": fp,
        "number": number,
        "kkm": kkm,
        "qr": qr,
    }
