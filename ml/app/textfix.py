"""Исправление смешанных алфавитов в тексте OCR: «Lепог» → «Lenor», «Fаігу» → «Fairy», «шaмпунь» → «шампунь».

OCR путает похожие буквы латиницы и кириллицы внутри одного слова. Слово переводится целиком в тот алфавит,
в который его можно перевести без потерь; если можно в оба — в тот, которого в слове больше.
Слова из одного алфавита не трогаются: «Ргорег» (всё кириллицей) так не исправить — нет признака ошибки.

Без зависимостей: модуль импортируют и ML (app.textfix), и бэкенд (ml.app.textfix).
"""
import re
from typing import Optional

# Кириллица → латиница. Кроме точных двойников — буквы, которые OCR часто читает вместо латинских
# в шрифтах чеков: п↔n, г↔r, и↔u, ь↔b, і (укр.)↔i, ј↔j, ѕ↔s
_TO_LAT = {
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "У": "Y", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t",
    "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "п": "n", "г": "r", "и": "u", "ь": "b",
}
# Латиница → кириллица: только точные двойники (n, r, u в русском слове — скорее настоящие латинские буквы)
_TO_CYR = {
    "A": "А", "B": "В", "E": "Е", "K": "К", "M": "М", "H": "Н", "O": "О", "P": "Р", "C": "С", "T": "Т",
    "X": "Х", "Y": "У",
    "a": "а", "e": "е", "k": "к", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у",
}
_CYR = re.compile(r"[а-яёА-ЯЁіїјѕІЇЈЅ]")
_LAT = re.compile(r"[a-zA-Z]")
# Слово — буквы, цифры и дефис внутри: «Oral-B», «400мл», «д/посудомоеч» делится по «/»
_WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁёІіЇїЈјЅѕ]+(?:-[0-9A-Za-zА-Яа-яЁёІіЇїЈјЅѕ]+)*")


def fix_word(word: str) -> str:
    """Одно слово из букв обеих раскладок → одна раскладка. Остальные слова возвращаются как есть."""
    if not (_CYR.search(word) and _LAT.search(word)):
        return word
    can_lat = all(ch in _TO_LAT for ch in word if _CYR.match(ch))
    can_cyr = all(ch in _TO_CYR for ch in word if _LAT.match(ch))
    if can_lat and can_cyr:
        to_lat = len(_LAT.findall(word)) >= len(_CYR.findall(word))
    elif can_lat or can_cyr:
        to_lat = can_lat
    else:
        return word
    table = _TO_LAT if to_lat else _TO_CYR
    return "".join(table.get(ch, ch) for ch in word)


def fix_mixed_script(text: Optional[str]) -> Optional[str]:
    """Исправляет каждое слово строки; пробелы и знаки препинания не меняются."""
    if not text or not isinstance(text, str):
        return text
    return _WORD.sub(lambda m: fix_word(m.group(0)), text)


def fix_item_names(items) -> None:
    """Названия позиций чека — на месте. Используется после разбора чека любым парсером."""
    for item in items or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            item["name"] = fix_mixed_script(item["name"])
