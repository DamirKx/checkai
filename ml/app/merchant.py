"""Каноническое имя магазина (merchant) по тому, что напечатано на чеке (store).

«MAGNUM», «Magnum Cash&Carry», «ТОО "Magnum"», «МАГНУМ» → «Magnum».

Без зависимостей: модуль импортируют и ML (ml/app), и бэкенд (ml.app.merchant).
"""
import re
from typing import Optional

from .textfix import fix_mixed_script

# Известные сети: каноническое имя → варианты написания (в нижнем регистре, латиницей и кириллицей).
# Вариант ищется как отдельное слово в любом месте строки, поэтому «Magnum Cash&Carry» → Magnum.
KNOWN_CHAINS = {
    "Magnum": ("magnum", "магнум"),
    "Small": ("small", "смолл", "смол"),
    "Galmart": ("galmart", "галмарт"),
    "Technodom": ("technodom", "технодом"),
    "Sulpak": ("sulpak", "сулпак"),
    "Арбуз": ("arbuz", "арбуз"),
    "Mechta": ("mechta", "мечта"),
    "Alser": ("alser", "алсер"),
    "Anvar": ("anvar", "анвар"),
    "Metro": ("metro cash", "метро кэш", "metro"),
    "Spar": ("spar", "спар"),
    "Fix Price": ("fix price", "fixprice", "фикс прайс"),
    "Interfood": ("interfood", "интерфуд"),
    "Ramstore": ("ramstore", "рамстор"),
    "Toimart": ("toimart", "тоймарт"),
    "Europharma": ("europharma", "еврофарма"),
    "Sadyhan": ("sadyhan", "садыхан"),
    "Пятёрочка": ("пятерочка", "пятёрочка", "pyaterochka"),
    "Магнит": ("магнит", "magnit"),
    "Перекрёсток": ("перекресток", "перекрёсток", "perekrestok"),
}

# Организационно-правовые формы: убираются целым словом
_LEGAL_FORMS = (
    "тоо", "ип", "ао", "оао", "зао", "пао", "ооо", "чп", "кх",
    "too", "llp", "llc", "ltd", "inc", "jsc", "ip",
    "филиал", "branch",
)
_LEGAL_RE = re.compile(r"(?<![\w])(?:" + "|".join(_LEGAL_FORMS) + r")\.?(?![\w])", re.IGNORECASE)
# Апостроф внутри слова (McDonald's) — часть имени, его не трогаем
_QUOTES_RE = re.compile(r"[\"«»“”„‟]|(?<!\w)['’‘`]|['’‘`](?!\w)")
# Номер филиала: «№ 12», «#3», «магазин 15» в конце
_BRANCH_RE = re.compile(r"(?:№|#|n°)\s*\d+|\b(?:магазин|маг\.|store|shop)\s*\d+\b|\s\d{1,4}$", re.IGNORECASE)
# Начало адреса: всё после запятой или после «г.», «ул.», «пр.» и т.п.
_ADDRESS_RE = re.compile(r",.*$|\s(?:г|ул|пр|пр-т|мкр|д)\.\s?.*$|\s(?:город|улица|проспект)\s.*$", re.IGNORECASE)

# OCR путает похожие буквы латиницы и кириллицы: «МAГНУМ», «АBТOKAФE»
_CYR_TWINS = "АВЕКМНОРСТУХавекмнорстух"
_LAT_TWINS = "ABEKMHOPCTYXabekmhopctyx"
# Только для сравнения с вариантами написания сетей; исправление слов для показа — app.textfix
_CYR_TO_LAT = str.maketrans(_CYR_TWINS, _LAT_TWINS)
# Кавычки с названием внутри: ТОО «Ромашка», ООО "Автокафе"
_QUOTED_RE = re.compile(r"[\"«“„](.{3,}?)[\"»”“]")


def _skeleton(text: str) -> str:
    """Строка, в которой латинские и кириллические двойники совпадают: для сравнения, не для показа."""
    return text.lower().replace("ё", "е").translate(_CYR_TO_LAT)


_CHAIN_PATTERNS = [
    (name, re.compile(r"(?<![\w])(?:" + "|".join(re.escape(_skeleton(a)) for a in aliases) + r")(?![\w])"))
    for name, aliases in KNOWN_CHAINS.items()
]


def _clean(store: str) -> str:
    quoted = _QUOTED_RE.search(store)
    text = quoted.group(1) if quoted else store
    text = _QUOTES_RE.sub(" ", text)
    text = _LEGAL_RE.sub(" ", text)
    text = _ADDRESS_RE.sub("", text)
    text = _BRANCH_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-–—:;")
    return text


def _one_case(text: str) -> str:
    """«АВТОКАФЕ» → «Автокафе»; смешанный регистр («McDonald's», «iPhone Store») не трогаем."""
    letters = [ch for ch in text if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters):
        return " ".join(w[:1].upper() + w[1:].lower() for w in text.split(" "))
    if letters and all(ch.islower() for ch in letters):
        return text[:1].upper() + text[1:]
    return text


def canonical_merchant(store: Optional[str]) -> Optional[str]:
    """Короткое имя сети без формы собственности, кавычек, адреса и номера филиала. None — если имени нет."""
    if not store or not str(store).strip():
        return None
    raw = str(store).strip()

    skeleton = _skeleton(raw)
    for name, pattern in _CHAIN_PATTERNS:
        if pattern.search(skeleton):
            return name

    cleaned = _clean(raw)
    if not cleaned or not re.search(r"\w", cleaned):
        return None
    cleaned = fix_mixed_script(cleaned)
    return _one_case(cleaned)[:255]
