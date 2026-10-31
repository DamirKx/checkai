import os
import re
import json
import logging
import requests
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

# Пытаемся импортировать единую функцию нормализации из бэкенда (п. 0.6),
# а при автономном запуске из папки ml/ используем точную копию контракта.
try:
    from app.categories import normalize_item_name  # type: ignore
except ImportError:
    def normalize_item_name(name: str) -> str:
        """'Крем NIVEA 50мл' → 'крем nivea'. Пустая строка — правило не создаётся."""
        s = str(name or "").lower().replace("ё", "е")
        # числа с единицами; \b — чтобы «2 груши» не превратилось в «уши»
        s = re.sub(r"\d+(?:[.,]\d+)?\s*(?:(?:кг|гр|г|мл|л|шт|уп)\b|%)?", " ", s)
        s = re.sub(r"[^\w\s]", " ", s)                                     # знаки препинания
        s = re.sub(r"\s+", " ", s).strip()
        return s[:200]


# Встроенный словарь для стандартных названий категорий (перенесён из frontend/static/js/app.js и дополнен)
KEYWORDS: Dict[str, List[str]] = {
    "продукты": [
        "картоф", "молок", "хлеб", "яйц", "сыр", "мяс", "рыб", "куриц", "овощ",
        "фрукт", "масло", "рис", "греч", "сахар", "мук", "сок", "вода", "чай",
        "кофе", "макарон", "йогурт", "колбас", "кефир", "печен", "шоколад",
        "помидор", "огур", "яблок", "банан", "лук", "закусоч", "сметан", "творог",
        "конфет", "круп", "батон", "булк", "липтон", "напиток", "груш",
    ],
    "одежда": [
        "кофт", "футбол", "брюк", "джинс", "куртк", "пальто", "плать", "юбк",
        "обув", "ботин", "кроссов", "носк", "шапк", "одеж", "рубаш", "бель",
        "сумк", "cymka", "ремеш", "аксессуар", "перчат", "шарф", "handkerchief", "bag", "crocs",
    ],
    "дом и быт": [
        "моющ", "порош", "шампун", "мыло", "бумаг", "пакет", "посуда", "лампоч",
        "бытов", "губк", "салфет", "кирпич", "цемент", "щебен", "шебень", "гвозд",
        "гв03ди", "жидкие", "домофон", "донофон", "ремонт", "строит", "хозтов", "чистящ",
        "зубн", "паста", "lamp", "board", "sprayer", "tape", "kleenso", "plastic",
    ],
    "транспорт": [
        "бензин", "дизел", "такси", "проезд", "метро", "автобус", "парков",
        "топлив", "азс", "шиномонтаж", "мойк", "auto", "wash", "windshiled",
        "windshield", "engine", "wd40", "hd40", "grease", "plug", "starter",
    ],
    "кафе": [
        "ресторан", "кафе", "обед", "ужин", "ланч", "доставка еды", "американо",
        "капучино", "эспрессо", "бургер", "пицца", "ролл", "суши", "шаурм",
        "кофейн", "латте", "чай термос", "чай чер",
    ],
    "здоровье": [
        "аптек", "лекар", "витамин", "таблет", "медицин", "клиник", "бинт",
        "мазь", "сироп", "капл", "пластыр", "анализ", "врач", "стоматол",
    ],
    "развлечения": [
        "кино", "театр", "музей", "игра", "подписк", "концерт", "билет",
        "книг", "игруш", "развлеч", "боулинг", "парк", "modelling clay", "kiddy",
    ],
}


def _extract_description_keys(description: Optional[str]) -> set:
    """
    Извлекает ключи из описания категории по п. 2 Шаг 4:
    разбить по запятым, точкам с запятой и пробелам; в нижний регистр, ё -> е;
    слова короче 3 букв выбросить; ключ — первые 5 букв слова (или всё слово, если короче).
    """
    if not description:
        return set()
    text = str(description).lower().replace("ё", "е")
    # Разбиваем по запятым, точкам с запятой, пробелам и прочим разделителям
    tokens = re.split(r"[,;\s]+", text)
    keys = set()
    for token in tokens:
        word = re.sub(r"[^\w]", "", token)
        if len(word) >= 3:
            keys.add(word[:5])
    return keys


def categorize_via_qwen(items: List[dict], categories: List[dict]) -> Dict[int, Optional[int]]:
    """
    Запрос к локальной модели Qwen через Ollama для распределения товаров по категориям.
    Возвращает словарь маппинга: {item_index: category_list_index} (индекс в списке categories, 0-based).
    """
    if not items or not categories:
        return {}

    use_ai = os.getenv("USE_AI_PARSER", "auto").strip().lower()
    if use_ai in ("0", "false", "no", "off"):
        return {}

    # Формируем пронумерованный список категорий с 1 с описанием
    cats_text = ""
    valid_cat_numbers = {}  # номер с 1 -> индекс в списке categories (0-based)
    for idx, cat in enumerate(categories, start=1):
        valid_cat_numbers[idx] = idx - 1
        desc = cat.get("description") or "нет описания"
        cats_text += f"{idx}. {cat['name']} — {desc}\n"

    # Формируем список товаров с 0
    items_text = ""
    for idx, item in enumerate(items):
        items_text += f"{idx}. {item.get('name', 'Товар')}\n"

    prompt = (
        "Ты — интеллектуальный классификатор товаров из кассовых чеков по категориям.\n"
        "Ниже приведен список доступных категорий с их описаниями:\n"
        f"{cats_text}\n"
        "А также список товаров из чека:\n"
        f"{items_text}\n"
        "Для каждого товара определи наиболее подходящую категорию по её номеру (1, 2, ...).\n"
        "Описание категории важнее её названия. Если ни одна категория не подходит — null.\n"
        "Примеры:\n"
        "- Молоко, чай, хлеб, мясо -> Продукты\n"
        "- Одежда, обувь, сумка, ремешок, аксессуары -> Одежда\n"
        "- Кирпич, цемент, щебень, гвозди, стройматериалы, лампа, посуда, бытовая химия -> Дом и быт\n"
        "- Бургер, пицца, блюда в кафе или ресторане -> Кафе\n"
        "- Автозапчасти, автохимия (WD40, моторное масло, омыватель, свечи) -> Транспорт\n"
        "- Игрушки, пластилин (modelling clay), кино, игры -> Развлечения\n"
        "- Смартфон, электроника, техника, скидочная карта -> null\n"
        "Ответ верни строго в формате JSON без лишнего текста и блоков кода:\n"
        "{\n  \"items\": [\n    {\"index\": 0, \"category\": 1},\n    {\"index\": 1, \"category\": null}\n  ]\n}"
    )

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "temperature": 0.0
        }
    }

    results: Dict[int, Optional[int]] = {}
    seen_indices = set()
    duplicate_indices = set()

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=30)
        if response.status_code == 200:
            data = response.json()
            raw_answer = data.get("response", "").strip()

            # Очищаем возможные маркеры кода markdown
            if raw_answer.startswith("```"):
                raw_answer = re.sub(r"^```(?:json)?\s*", "", raw_answer, flags=re.IGNORECASE)
                raw_answer = re.sub(r"\s*```$", "", raw_answer)

            parsed_json = json.loads(raw_answer)
            items_list = parsed_json.get("items")
            if isinstance(items_list, list):
                for el in items_list:
                    if not isinstance(el, dict):
                        continue
                    i = el.get("index")
                    c_num = el.get("category")
                    if not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= len(items):
                        continue
                    if i in seen_indices:
                        # Повтор index -> для этой позиции ответа нет (уйдёт в ключевые слова)
                        duplicate_indices.add(i)
                        results.pop(i, None)
                        continue
                    seen_indices.add(i)

                    # Проверяем, что номер категории валидный и не является категорией "Другое" без явных ключей
                    if isinstance(c_num, int) and not isinstance(c_num, bool) and c_num in valid_cat_numbers:
                        cat_idx = valid_cat_numbers[c_num]
                        cat_name_lower = str(categories[cat_idx].get("name") or "").strip().lower()
                        # ML не кладёт в «Другое» сам, неподходящее остаётся «Без категории» (None)
                        if cat_name_lower != "другое":
                            results[i] = cat_idx
    except Exception as e:
        logger.warning(f"Ошибка при запросе категоризации к Qwen: {e}")

    return results


def _match_by_keywords(item_name: str, categories: List[dict], desc_keys_list: List[set]) -> Optional[int]:
    """
    Подбирает индекс категории в списке categories по ключевым словам (Шаг 4):
    1. Из описаний категорий пользователя — главный источник.
    2. Встроенный словарь KEYWORDS — для стандартных названий, если у пользователя есть категория с таким названием.
    3. При совпадении нескольких категорий:
       - побеждает категория из описания пользователя;
       - среди равных — больше совпавших ключей;
       - при равенстве — меньшая position (или индекс в списке).
    """
    norm_name = normalize_item_name(item_name)
    if not norm_name:
        return None

    best_cat_idx: Optional[int] = None
    best_score = None  # кортеж (from_user_desc: int, matched_count: int, -position: int, -idx: int)

    for idx, cat in enumerate(categories):
        cat_name = str(cat.get("name") or "").strip()
        cat_name_lower = cat_name.lower().replace("ё", "е")
        position = cat.get("position", idx)
        if not isinstance(position, (int, float)):
            position = idx

        # 1. Ключи из описания пользователя (для "Другое" стандартное описание "Всё, что не подошло..." не должно матчить всё подряд)
        user_keys = desc_keys_list[idx]
        if cat_name_lower == "другое" and "не подошло" in str(cat.get("description") or "").lower():
            user_keys = set()

        matched_user_keys = sum(1 for k in user_keys if k in norm_name)

        # 2. Ключи из встроенного словаря KEYWORDS
        builtin_keys = KEYWORDS.get(cat_name_lower, [])
        matched_builtin_keys = sum(1 for k in builtin_keys if k in norm_name)

        if matched_user_keys > 0:
            candidate_score = (1, matched_user_keys, -position, -idx)
        elif matched_builtin_keys > 0:
            candidate_score = (0, matched_builtin_keys, -position, -idx)
        else:
            continue

        if best_score is None or candidate_score > best_score:
            best_score = candidate_score
            best_cat_idx = idx

    return best_cat_idx


def categorize_items(items: List[dict], categories: List[dict]) -> None:
    """Проставляет каждой позиции category_id, category и category_source ('ai' | 'keywords' | None).
    categories: [{"id": int | None, "name": str, "description": str | None}] — категории пользователя
    или стандартные (у гостя id = None). Меняет items на месте."""
    if not items:
        return

    if not categories:
        for item in items:
            item["category_id"] = None
            item["category"] = None
            item["category_source"] = None
        return

    # 1. Пробуем классифицировать через Qwen (AI)
    ai_mapping = categorize_via_qwen(items, categories)

    # Предварительно строим ключи из описаний категорий
    desc_keys_list = [_extract_description_keys(cat.get("description")) for cat in categories]

    for idx, item in enumerate(items):
        matched_cat_idx: Optional[int] = None
        source: Optional[str] = None

        # Проверяем результат от Qwen
        if idx in ai_mapping and ai_mapping[idx] is not None:
            c_idx = ai_mapping[idx]
            if 0 <= c_idx < len(categories):
                matched_cat_idx = c_idx
                source = "ai"

        # 2. Если Qwen не определил, пробуем ключевые слова
        if matched_cat_idx is None:
            kw_idx = _match_by_keywords(item.get("name", ""), categories, desc_keys_list)
            if kw_idx is not None:
                matched_cat_idx = kw_idx
                source = "keywords"

        # 3. Проставляем поля позиции
        if matched_cat_idx is not None:
            cat_obj = categories[matched_cat_idx]
            item["category_id"] = cat_obj.get("id")
            item["category"] = cat_obj.get("name")
            item["category_source"] = source
        else:
            item["category_id"] = None
            item["category"] = None
            item["category_source"] = None
