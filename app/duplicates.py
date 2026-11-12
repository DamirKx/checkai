"""Правило «тот же чек по содержимому» (NEXT_WEEK_TASKS.md, C.0).

Повторяет findDuplicate / normText / itemsSimilarity из frontend/static/js/app.js. Пороги и нормализацию
менять только вместе с фронтом, иначе пользователь увидит окно дубликата дважды или не увидит вовсе.
Общие примеры для обеих сторон — tests/fixtures/duplicates.json.
"""
import math
import re
from typing import Any, Iterable, List, Optional

from app.currencies import CURRENCIES

# Время покупки отличается не больше чем на столько минут
DUP_TIME_MIN = 2
# Доля совпавших позиций, если позиции есть у обоих чеков
DUP_ITEMS_MIN = 0.6
# Если дата неизвестна хотя бы у одного чека
DUP_NODATE_ITEMS_MIN = 0.8

_NOT_LETTER_OR_DIGIT = re.compile(r"[^a-zа-я0-9әғқңөұүһі]+")
_LEGAL_FORM = re.compile(r" (тоо|ип|ао|too|llp|ltd|llc)(?= )")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")


def field(obj: Any, name: str, default: Any = None) -> Any:
    """Поле чека из pydantic-схемы, модели или словаря распознавания."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def norm_text(value: Optional[str]) -> str:
    """«ТОО «MAGNUM»» → «magnum»: регистр, ё, знаки и правовая форма не важны."""
    s = " " + _NOT_LETTER_OR_DIGIT.sub(" ", str(value or "").lower().replace("ё", "е")) + " "
    return re.sub(r"\s+", " ", _LEGAL_FORM.sub(" ", s)).strip()


def minutes_of(value: Optional[str]) -> Optional[int]:
    m = _TIME.search(str(value or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def same_store(a: str, b: str) -> bool:
    """Нормализованные названия: одно пустое, равны или одно содержит другое."""
    return not a or not b or a == b or a in b or b in a


def _js_round(x: float) -> int:
    # Math.round в JS: половина — вверх. round() в Python округляет к чётному
    return math.floor(x + 0.5)


def _num(value: Any) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.0
    return n if math.isfinite(n) else 0.0


def _item_keys(items: Iterable[Any]) -> List[str]:
    keys = []
    for item in items or []:
        name = str(field(item, "name") or "").strip()
        if name:
            keys.append(f"{norm_text(name)}|{_js_round(_num(field(item, 'total_price')))}")
    return keys


def items_similarity(a: Iterable[Any], b: Iterable[Any]) -> Optional[float]:
    """Доля совпавших позиций (название + сумма строки, с повторами); None — у одного из чеков позиций нет."""
    ka, kb = _item_keys(a), _item_keys(b)
    if not ka or not kb:
        return None
    left = {}
    for k in kb:
        left[k] = left.get(k, 0) + 1
    same = 0
    for k in ka:
        if left.get(k, 0) > 0:
            left[k] -= 1
            same += 1
    return same / max(len(ka), len(kb))


def total_tolerance(currency: Optional[str]) -> float:
    """Итоги равны, если разница меньше 1 для валют без копеек и меньше 0,01 для остальных."""
    digits = CURRENCIES.get(currency or "", {}).get("digits", 2)
    return 0.01 if digits else 1.0


def same_total(a: Any, b: Any, currency: Optional[str]) -> bool:
    return round(abs(_num(a) - _num(b)), 2) < total_tolerance(currency)


def iso_day(value: Optional[str]) -> str:
    return str(value or "")[:10]


def match_score(draft: Any, other: Any) -> Optional[float]:
    """Насколько other похож на draft, или None, если это не дубликат. Больше — похожее.

    Вызывающий уже проверил пользователя, валюту и что это не сам чек.
    """
    total = _num(field(draft, "total"))
    currency = field(draft, "currency")
    if not total > 0 or not same_total(field(other, "total"), total, currency):
        return None
    date, other_date = iso_day(field(draft, "date")), iso_day(field(other, "date"))
    if date and other_date and date != other_date:
        return None
    time, other_time = minutes_of(field(draft, "time")), minutes_of(field(other, "time"))
    if time is not None and other_time is not None and abs(time - other_time) > DUP_TIME_MIN:
        return None
    store, other_store = norm_text(field(draft, "store")), norm_text(field(other, "store"))
    if not same_store(store, other_store):
        return None
    sim = items_similarity(field(draft, "items") or [], field(other, "items") or [])
    # Дата неизвестна у одного из чеков — нужен почти тот же состав
    if not (date and other_date) and not (sim is not None and sim >= DUP_NODATE_ITEMS_MIN):
        return None
    if sim is not None and sim < DUP_ITEMS_MIN:
        return None
    return (
        (sim if sim is not None else 0.5)
        + (1 if time is not None and other_time is not None else 0)
        + (1 if store and other_store else 0)
    )


def fiscal_id_from(fiscal: Any) -> Optional[str]:
    """«РНМ:номер:ФП» из реквизитов от ML (C.3). Нужны хотя бы РНМ/ФН и номер чека, иначе None:
    лучше не найти дубликат, чем склеить два разных чека."""
    if not isinstance(fiscal, dict):
        return None

    def digits(key: str) -> str:
        return re.sub(r"\D", "", str(fiscal.get(key) or ""))

    kkm, number, fp = digits("kkm"), digits("number"), digits("fp")
    if not kkm or not number:
        return None
    return f"{kkm}:{number}:{fp}"[:64]
