import os
import re
import json
import logging
import requests
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
# Как в agent_parser.py: одинаковый num_ctx, чтобы Ollama не перезагружала модель между запросами
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
CATEGORIZE_TIMEOUT = int(os.getenv("OLLAMA_CATEGORIZE_TIMEOUT", "30"))
CATEGORIZE_BATCH_SIZE = int(os.getenv("CATEGORIZE_BATCH_SIZE", "30"))

# Ограниченный кэш по нормализованному названию (задача 3.3): (cats_sig, norm_name) -> cat_idx
_CATEGORY_CACHE: Dict[tuple, int] = {}
_MAX_CACHE_SIZE = 2000


def clear_category_cache() -> None:
    """Очищает in-memory кэш категоризации."""
    _CATEGORY_CACHE.clear()


def _categories_signature(categories: List[dict]) -> tuple:
    return tuple(
        (c.get("id"), str(c.get("name") or "").strip().lower(), str(c.get("default_key") or "").strip().lower())
        for c in categories
    )

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
DEFAULT_KEY_TO_RU: Dict[str, str] = {
    "groceries": "продукты",
    "clothing": "одежда",
    "home": "дом и быт",
    "transport": "транспорт",
    "cafe": "кафе",
    "health": "здоровье",
    "entertainment": "развлечения",
    "other": "другое",
}

KEYWORDS: Dict[str, List[str]] = {
    "продукты": [
        "картоф", "молок", "хлеб", "яйц", "сыр", "мяс", "рыб", "куриц", "овощ",
        "фрукт", "масло", "рис", "греч", "сахар", "мук", "сок", "вода", "чай",
        "макарон", "йогурт", "колбас", "кефир", "печен", "шоколад", "пряник",
        "помидор", "огур", "яблок", "банан", "лук", "закусоч", "сметан", "творог",
        "конфет", "круп", "батон", "булк", "липтон", "напиток", "груш", "спагетти",
        # Казахский язык
        "нан", "сүт", "ет", "су", "шай", "қант", "балық", "май", "жұмыртқа", "күріш", "алма",
        "шұжық", "айран", "қаймақ", "көкөніс", "жеміс", "картоп", "қызанақ", "қияр", "пияз",
        "ұн", "ірімшік", "сары май", "тауық", "тұз", "шырын", "сусын", "кәмпит", "тоқаш",
        "кеспе", "сүзбе", "қарақұмық", "жүзім", "лимон",
        # Английский язык
        "milk", "bread", "egg ", "eggs", "cheese", "meat", "chicken", "beef", "pork", "fish",
        "rice", "pasta", "spaghetti", "sugar", "flour", "butter", "yogurt", "sour cream",
        "cottage cheese", "apple", "banana", "tomato", "cucumber", "potato", "onion",
        "carrot", "fruit", "vegetable", "juice", "water", "tea ", "chocolate", "cookie",
        "cereal", "oatmeal", "sausage", "bacon", "grocery",
    ],
    "одежда": [
        "кофт", "футбол", "брюк", "джинс", "куртк", "пальто", "плать", "юбк",
        "обув", "ботин", "кроссов", "носк", "шапк", "одеж", "рубаш", "бель",
        "сумк", "cymka", "ремеш", "аксессуар", "перчат", "шарф", "handkerchief", "bag", "crocs",
        "zara", "nike", "adidas", "h&m", "jacket", "shirt", "shoes", "pants", "dress",
        # Казахский язык
        "көйлек", "шалбар", "етік", "аяқ киім", "жейде", "қолғап", "күрте", "белдемше",
        "шұлық", "бас киім", "қалпақ", "мойынорағыш", "белдік", "сөмке", "кроссовка",
        "бәтеңке", "туфли", "киім", "іш киім", "футболка", "свитер", "джинсы",
        # Английский язык
        "t-shirt", "tshirt", "jeans", "coat", "skirt", "socks", "hat ", "hats", "cap ", "caps",
        "baseball cap", "boots", "sneakers", "belt", "scarf", "gloves", "underwear",
        "sweater", "hoodie", "shorts", "suit ", "suits", "blouse", "sandals", "clothing",
        "apparel", "accessories", "backpack",
    ],
    "дом и быт": [
        "моющ", "порош", "шампун", "мыло", "бумаг", "пакет", "посуда", "лампоч",
        "бытов", "губк", "салфет", "кирпич", "цемент", "щебен", "шебень", "гвозд",
        "гв03ди", "жидкие", "домофон", "донофон", "ремонт", "строит", "хозтов", "чистящ",
        "зубн", "паста", "lamp", "board", "sprayer", "tape", "kleenso", "plastic",
        # Гигиена и косметика — в разметке эталонов это «Дом и быт»
        "прокладк", "тампон", "подгузн", "щетк", "щётк", "крем", "гель", "дезодор", "бальзам",
        "кондиционер", "стирал", "отбел", "освежит", "капсул", "универсальн", "средство для",
        "fairy", "lenor", "ariel", "tide", "persil", "domestos", "pantene", "always", "oral",
        "colgate", "nivea", "dove", "olay", "proper", "head shoulders", "gillette",
        # Казахский язык
        "сабын", "сусабын", "кір жуғыш", "ұнтақ", "тазалағыш", "ыдыс", "қағаз", "майлық",
        "дәретхана", "тіс паста", "тіс щетка", "жөндеу", "құрылыс", "шам", "батарейка",
        "шелек", "шүберек", "жөргег", "жаялық", "дезодорант", "үй шаруаш",
        # Английский язык
        "soap", "shampoo", "detergent", "laundry", "bleach", "dishwash", "sponge", "napkin",
        "toilet paper", "paper towel", "trash bag", "toothpaste", "toothbrush", "cleaning",
        "cleaner", "bulb", "lightbulb", "hardware", "paint", "cement", "brick", "kitchenware",
        "towel", "diaper", "deodorant", "conditioner", "shower gel", "lotion", "moisturizer",
        "household",
    ],
    "транспорт": [
        "бензин", "дизел", "такси", "проезд", "метро", "автобус", "парков",
        "топлив", "азс", "шиномонтаж", "мойк", "auto", "wash", "windshiled",
        "windshield", "engine", "wd40", "hd40", "grease", "plug", "starter",
        "яндекс", "yandex", "uber", "indrive", "onay", "онай", "самокат",
        "каршеринг", "поезд", "авиабилет", "проездн",
        # Казахский язык
        "жанармай", "ұшақ", "дизель", "жол ақы", "жолақы", "пойыз", "тұрақ",
        "автотұрақ", "көлік жуу", "дөңгелек", "май ауыстыр", "әуе билет", "отын",
        "қозғалтқыш", "көлік", "жүру ақысы",
        # Английский язык
        "gasoline", "petrol", "diesel", "fuel", "taxi", "cab ", "bus ", "buses", "subway", "metro",
        "train", "flight", "airfare", "airline", "parking", "car wash", "tire", "oil change",
        "motor oil", "car part", "scooter", "rideshare", "transit", "toll ",
    ],
    "кафе": [
        "ресторан", "кафе", "обед", "ужин", "ланч", "доставка еды", "американо",
        "капучино", "эспрессо", "бургер", "пицца", "ролл", "суши", "шаурм",
        "кофейн", "латте", "чай термос", "чай чер", "кофе", "раф", "додо",
        "dodo", "kfc", "mcdonald", "макдон", "burger king", "wolt", "glovo",
        "starbucks", "старбакс", "донер", "фастфуд",
        # Казахский язык
        "мейрамхана", "дәмхана", "асхана", "түскі ас", "кешкі ас", "таңғы ас",
        "дөнер", "тағам жеткізу", "балмұздақ", "бәліш", "самса", "шашлық", "кәуап",
        "палау", "мәнті", "бешбармақ",
        # Английский язык
        "restaurant", "cafe", "coffee", "cappuccino", "latte", "americano", "espresso",
        "lunch", "dinner", "breakfast", "brunch", "burger", "pizza", "sushi", "sushi roll",
        "shawarma", "doner", "sandwich", "bakery", "pastry", "ice cream", "food delivery",
        "takeout", "fast food",
    ],
    "здоровье": [
        "аптек", "лекар", "витамин", "таблет", "медицин", "клиник", "бинт",
        "мазь", "сироп", "капл", "пластыр", "анализ", "врач", "стоматол",
        "парацетамол", "нурофен", "аспирин", "бад", "линз",
        # Казахский язык
        "дәріхана", "дәрі", "дәрігер", "емхана", "аурухана", "дәрумен", "дәрі-дәрмек",
        "дәрмек", "тіс дәрігер", "стоматолог", "талдау", "көзілдірік", "жақпамай",
        "тамшы", "сықпа", "медицина", "емдеу",
        # Английский язык
        "pharmacy", "drugstore", "medicine", "medication", "vitamin", "pill ", "pills", "tablet",
        "capsule", "clinic", "hospital", "doctor", "dentist", "dental", "medical", "health",
        "bandage", "ointment", "syrup", "eye drops", "aspirin", "ibuprofen", "paracetamol",
        "supplement", "lab test", "contact lens",
    ],
    "развлечения": [
        "кино", "театр", "музей", "игра", "подписк", "концерт", "билет",
        "книг", "игруш", "развлеч", "боулинг", "парк", "modelling clay", "kiddy",
        "netflix", "spotify", "спотифай", "steam", "playstation", "квест", "кинопарк", "kinopark",
        # Казахский язык
        "мұражай", "ойын", "жазылым", "кітап", "ойыншық", "саябақ", "ойын-сауық",
        "демалыс", "аттракцион", "караоке", "бильярд", "цирк", "көрме", "кинотеатр",
        # Английский язык
        "cinema", "movie", "theater", "theatre", "museum", "game", "gaming", "subscription",
        "concert", "ticket", "book ", "books", "toy ", "toys", "bowling", "park ", "amusement",
        "entertainment", "arcade", "karaoke", "billiards", "circus", "exhibition", "streaming",
    ],
}


# Служебные слова описаний: «Еда и напитки для дома», «мелочи для дома» не должны давать ключи «для» и «дома»
DESCRIPTION_STOP_WORDS = {
    "для", "дома", "вне", "все", "всё", "что", "или", "как", "при", "без", "под", "над", "это",
    "үшін", "және", "үйге", "басқа",
    "for", "and", "the", "with", "home", "etc",
}

# «Кондиционер д/белья», «крем для лица»: слово после «д/» и «для» — назначение, а не сам товар.
# Без этого «д/белья» попадает в «Одежду», а «д/лица» — никуда
_PURPOSE_RE = re.compile(r"(?:(?<![\w])д\s*/\s*|(?<![\w])для\s+)[\w.]+", re.IGNORECASE)


def _keyword_text(item_name: str) -> str:
    """Текст для поиска ключевых слов: без «для X», нормализованный, с пробелами по краям для границ слова."""
    return " " + normalize_item_name(_PURPOSE_RE.sub(" ", str(item_name or ""))) + " "


def _has_key(key: str, text: str) -> bool:
    """Ключ — начало слова (а для коротких 1–2 буквенных слов вроде «су», «ет» — слово целиком)."""
    if len(key) <= 2:
        return (" " + key + " ") in text
    return (" " + key) in text


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
        if len(word) >= 3 and word not in DESCRIPTION_STOP_WORDS:
            keys.add(word[:5])
    return keys


def _categorize_batch_via_qwen(items: List[dict], categories: List[dict]) -> Dict[int, Optional[int]]:
    """Один запрос к Ollama для батча товаров."""
    cats_text = ""
    valid_cat_numbers = {}
    index_by_name = {}
    for idx, cat in enumerate(categories, start=1):
        valid_cat_numbers[idx] = idx - 1
        cat_name = str(cat.get("name") or "").strip()
        index_by_name[cat_name.lower().replace("ё", "е")] = idx - 1
        if cat.get("display_name"):
            disp_name = str(cat["display_name"]).strip()
            index_by_name[disp_name.lower().replace("ё", "е")] = idx - 1
        else:
            disp_name = cat_name
        desc = cat.get("description") or "нет описания"
        cats_text += f"- {disp_name} — {desc}\n"

    items_text = ""
    for idx, item in enumerate(items):
        items_text += f"{idx}. {item.get('name', 'Товар')}\n"

    prompt = (
        "Ты — интеллектуальный классификатор товаров из кассовых чеков по категориям.\n"
        "Названия товаров и категорий могут быть на русском, казахском или английском языке.\n"
        "Ниже приведен список доступных категорий с их описаниями:\n"
        f"{cats_text}\n"
        "А также список товаров из чека:\n"
        f"{items_text}\n"
        "Для каждого товара выбери наиболее подходящую категорию и верни её название точно как в списке.\n"
        "Описание категории важнее её названия. Если ни одна категория не подходит — null.\n"
        "Примеры:\n"
        "- Молоко, чай, хлеб, мясо -> Продукты\n"
        "- Одежда, обувь, сумка, ремешок, аксессуары -> Одежда\n"
        "- Кирпич, цемент, щебень, гвозди, стройматериалы, лампа, посуда, бытовая химия -> Дом и быт\n"
        "- Шампунь, крем, зубная щётка, прокладки, подгузники, кондиционер для белья, капсулы для посудомойки -> Дом и быт\n"
        "- Слова после «для» и «д/» — назначение товара: «крем д/лица» — это крем, «кондиционер д/белья» — бытовая химия\n"
        "- Бургер, пицца, блюда в кафе или ресторане -> Кафе\n"
        "- Автозапчасти, автохимия (WD40, моторное масло, омыватель, свечи) -> Транспорт\n"
        "- Игрушки, пластилин (modelling clay), кино, игры -> Развлечения\n"
        "- Смартфон, электроника, техника, скидочная карта -> null\n"
        "Ответ верни строго в формате JSON без лишнего текста и блоков кода:\n"
        "{\n  \"items\": [\n    {\"index\": 0, \"category\": \"<название из списка>\"},\n    {\"index\": 1, \"category\": null}\n  ]\n}\n"
        "В ответе должны быть все товары, по одному разу каждый."
    )

    timeout = max(CATEGORIZE_TIMEOUT, min(90, 20 + len(items) * 2))
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "temperature": 0.0,
            "num_ctx": NUM_CTX,
        },
    }

    results: Dict[int, Optional[int]] = {}
    seen_indices = set()

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            raw_answer = data.get("response", "").strip()

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
                        results.pop(i, None)
                        continue
                    seen_indices.add(i)

                    cat_idx = None
                    if isinstance(c_num, str):
                        cat_idx = index_by_name.get(c_num.strip().lower().replace("ё", "е"))
                    elif isinstance(c_num, int) and not isinstance(c_num, bool) and c_num in valid_cat_numbers:
                        cat_idx = valid_cat_numbers[c_num]
                    if cat_idx is not None:
                        cat_obj = categories[cat_idx]
                        cat_name_lower = str(cat_obj.get("name") or "").strip().lower()
                        cat_default_key = str(cat_obj.get("default_key") or "").strip().lower()
                        if cat_default_key != "other" and cat_name_lower != "другое":
                            results[i] = cat_idx
    except Exception as e:
        logger.warning(f"Ошибка при запросе категоризации к Qwen: {e}")

    return results


def categorize_via_qwen(items: List[dict], categories: List[dict]) -> Dict[int, Optional[int]]:
    """Запрос к локальной модели Qwen через Ollama (с батчингом для длинных чеков, задачи 3.3, 5.3)."""
    if not items or not categories:
        return {}

    use_ai = os.getenv("USE_AI_PARSER", "auto").strip().lower()
    if use_ai in ("0", "false", "no", "off"):
        return {}

    batch_size = max(5, CATEGORIZE_BATCH_SIZE)
    if len(items) <= batch_size:
        return _categorize_batch_via_qwen(items, categories)

    combined: Dict[int, Optional[int]] = {}
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        batch_res = _categorize_batch_via_qwen(batch, categories)
        for local_idx, cat_idx in batch_res.items():
            combined[start + local_idx] = cat_idx
    return combined


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
    norm_name = _keyword_text(item_name)
    if not norm_name.strip():
        return None

    best_cat_idx: Optional[int] = None
    best_score = None  # кортеж (from_user_desc: int, matched_count: int, -position: int, -idx: int)

    for idx, cat in enumerate(categories):
        cat_name = str(cat.get("name") or "").strip()
        cat_name_lower = cat_name.lower().replace("ё", "е")
        default_key = str(cat.get("default_key") or "").strip().lower()
        position = cat.get("position", idx)
        if not isinstance(position, (int, float)):
            position = idx

        # 1. Ключи из описания пользователя (для "Другое" стандартное описание не должно матчить всё подряд)
        user_keys = desc_keys_list[idx]
        desc_lower = str(cat.get("description") or "").lower()
        if (default_key == "other" or cat_name_lower == "другое") and any(
            marker in desc_lower for marker in ("не подошло", "didn't fit", "сәйкес келм")
        ):
            user_keys = set()

        matched_user_keys = sum(1 for k in user_keys if _has_key(k, norm_name))

        # 2. Ключи из встроенного словаря KEYWORDS: по default_key с fallback на русское name
        kw_key = DEFAULT_KEY_TO_RU.get(default_key, cat_name_lower)
        builtin_keys = KEYWORDS.get(kw_key, [])
        matched_builtin_keys = sum(1 for k in builtin_keys if _has_key(k, norm_name))

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


def categorize_items(
    items: List[dict],
    categories: List[dict],
    rules: Optional[Dict[str, int]] = None,
) -> None:
    """Проставляет каждой позиции category_id, category и category_source ('rule' | 'cache' | 'ai' | 'keywords' | None).

    При CATEGORIZE_FAST_FIRST=1 (или на длинных чеках > 20 позиций, задача 3.3) порядок:
    1. Правила пользователя (rules: norm_name -> category_id) -> 'rule'
    2. Кэш по нормализованному названию -> 'cache'
    3. Уверенные ключевые слова -> 'keywords'
    4. Qwen — только для оставшихся незнакомых позиций -> 'ai' (и сохраняется в кэш).
    """
    if not items:
        return

    if not categories:
        for item in items:
            item["category_id"] = None
            item["category"] = None
            item["category_source"] = None
        return

    desc_keys_list = [_extract_description_keys(cat.get("description")) for cat in categories]
    cat_idx_by_id = {cat.get("id"): idx for idx, cat in enumerate(categories) if cat.get("id") is not None}
    cats_sig = _categories_signature(categories)

    fast_env = os.getenv("CATEGORIZE_FAST_FIRST", "").strip().lower()
    fast_first = (
        fast_env in ("1", "true", "yes", "on")
        or (fast_env == "" and len(items) > 20)
    )

    resolved: Dict[int, tuple] = {}  # item_idx -> (cat_idx, source)

    # 1. Правила пользователя (если переданы) — всегда имеют высший приоритет
    if rules:
        for idx, item in enumerate(items):
            norm = normalize_item_name(item.get("name", ""))
            if norm and norm in rules:
                c_idx = cat_idx_by_id.get(rules[norm])
                if c_idx is not None:
                    resolved[idx] = (c_idx, "rule")

    if fast_first:
        # 2. Кэш по нормализованному названию
        for idx, item in enumerate(items):
            if idx in resolved:
                continue
            norm = normalize_item_name(item.get("name", ""))
            if norm and (cats_sig, norm) in _CATEGORY_CACHE:
                c_idx = _CATEGORY_CACHE[(cats_sig, norm)]
                if 0 <= c_idx < len(categories):
                    resolved[idx] = (c_idx, "cache")

        # 3. Ключевые слова до Qwen
        unknown_indices: List[int] = []
        for idx, item in enumerate(items):
            if idx in resolved:
                continue
            kw_idx = _match_by_keywords(item.get("name", ""), categories, desc_keys_list)
            if kw_idx is not None:
                resolved[idx] = (kw_idx, "keywords")
            else:
                unknown_indices.append(idx)

        # 4. Qwen только для незнакомых позиций
        if unknown_indices:
            unknown_items = [items[i] for i in unknown_indices]
            ai_sub = categorize_via_qwen(unknown_items, categories)
            for local_i, c_idx in ai_sub.items():
                if c_idx is not None and 0 <= c_idx < len(categories):
                    orig_idx = unknown_indices[local_i]
                    resolved[orig_idx] = (c_idx, "ai")
                    norm = normalize_item_name(items[orig_idx].get("name", ""))
                    if norm:
                        if len(_CATEGORY_CACHE) >= _MAX_CACHE_SIZE:
                            _CATEGORY_CACHE.clear()
                        _CATEGORY_CACHE[(cats_sig, norm)] = c_idx
    else:
        # Классический порядок (AI -> keywords), но с поддержкой rules и батчинга
        remaining_indices = [i for i in range(len(items)) if i not in resolved]
        if remaining_indices:
            rem_items = [items[i] for i in remaining_indices]
            ai_mapping = categorize_via_qwen(rem_items, categories)
            for local_i, orig_idx in enumerate(remaining_indices):
                c_idx = ai_mapping.get(local_i)
                if c_idx is not None and 0 <= c_idx < len(categories):
                    resolved[orig_idx] = (c_idx, "ai")
                else:
                    kw_idx = _match_by_keywords(items[orig_idx].get("name", ""), categories, desc_keys_list)
                    if kw_idx is not None:
                        resolved[orig_idx] = (kw_idx, "keywords")

    # Проставляем поля позиции
    for idx, item in enumerate(items):
        if idx in resolved:
            c_idx, source = resolved[idx]
            cat_obj = categories[c_idx]
            item["category_id"] = cat_obj.get("id")
            item["category"] = cat_obj.get("name")
            item["category_source"] = source
        else:
            item["category_id"] = None
            item["category"] = None
            item["category_source"] = None
