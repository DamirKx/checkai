"""Приведение даты и времени чека к единому формату.

В базе дата хранится как строка YYYY-MM-DD, время — как HH:MM.
Такой формат правильно сортируется и фильтруется прямо в SQL.
"""
import re
from datetime import date
from typing import Optional, Tuple

_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
# ДД.ММ.ГГГГ, ДД.ММ.ГГ, а также с разделителями / и -
_DMY_DATE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})(?!\d)")
_TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?::\d{2})?(?!\d)")


def _parse_time(text: str) -> Optional[str]:
    match = _TIME.search(text)
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return f"{hours:02d}:{minutes:02d}"


def split_date_time(value) -> Tuple[Optional[str], Optional[str]]:
    """Разбирает строку вроде «29.07.2025 16:36» или «2025-07-29».

    Возвращает (YYYY-MM-DD или None, HH:MM или None).
    """
    if not value:
        return None, None
    text = str(value).strip()

    iso_date = None
    match = _ISO_DATE.search(text)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    else:
        match = _DMY_DATE.search(text)
        if match:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if year < 100:
                year += 2000

    if match:
        try:
            iso_date = date(year, month, day).isoformat()
        except ValueError:
            iso_date = None
        # Время ищем в оставшейся части строки, чтобы не спутать его с датой
        text = text[:match.start()] + " " + text[match.end():]

    return iso_date, _parse_time(text)


def normalize_date_time(raw_date, raw_time=None) -> Tuple[Optional[str], Optional[str]]:
    """Нормализует пару (дата, время) из распознавания или запроса клиента."""
    iso_date, time_from_date = split_date_time(raw_date)
    time_value = _parse_time(str(raw_time)) if raw_time else None
    return iso_date, time_value or time_from_date
