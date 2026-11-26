"""Язык ответов API: ru, kk, en.

Язык запроса — заголовок Accept-Language, без него — ru. preferences.language пользователя для ответов
не используется: фронт всегда присылает заголовок.

Тексты для человека — в MESSAGES по стабильному коду. Русские тексты совпадают с прежними буква в букву:
без заголовка ответы такие же, как до перевода (правило 0.1). Логи и комментарии остаются на русском.
"""
import re
from typing import Any, Dict, NamedTuple, Optional

from fastapi import Depends, HTTPException, Request
from pydantic_core import PydanticCustomError

LANGS = ("ru", "kk", "en")
DEFAULT_LANG = "ru"
# Синоним kz принимается на входе, хранится и отдаётся только kk
_ALIASES = {"kz": "kk"}


def normalize_lang(value: Any) -> Optional[str]:
    """'KK-kz' → 'kk', 'kz' → 'kk', 'en_US' → 'en'. Неизвестный язык — None."""
    base = re.split(r"[-_]", str(value or "").strip().lower(), maxsplit=1)[0]
    base = _ALIASES.get(base, base)
    return base if base in LANGS else None


def parse_accept_language(header: Optional[str]) -> str:
    """Первый поддерживаемый язык по убыванию q: 'fr, kk;q=0.5' → 'kk'. Ничего не подошло — ru."""
    candidates = []
    for order, part in enumerate((header or "").split(",")):
        tag, _, params = part.strip().partition(";")
        q = 1.0
        match = re.search(r"q\s*=\s*([0-9.]+)", params)
        if match:
            try:
                q = float(match.group(1))
            except ValueError:
                q = 0.0
        lang = normalize_lang(tag)
        if lang and q > 0:
            candidates.append((-q, order, lang))
    return min(candidates)[2] if candidates else DEFAULT_LANG


def get_lang(request: Request) -> str:
    """Зависимость FastAPI: язык запроса."""
    return parse_accept_language(request.headers.get("accept-language"))


Lang = Depends(get_lang)


# --- Тексты ---
# Код → {язык: шаблон str.format}. У всех трёх языков одинаковый набор {параметров} (tests/test_i18n.py).
# Казахские тексты — на проверку носителю языка, как и frontend/static/js/locales/kk.js.

MESSAGES: Dict[str, Dict[str, str]] = {
    # Общие
    "too_many_requests": {
        "ru": "Слишком много запросов. Пожалуйста, попробуйте позже.",
        "kk": "Сұраулар тым көп. Кейінірек қайталап көріңіз.",
        "en": "Too many requests. Please try again later.",
    },
    "auth_required": {
        "ru": "Необходима авторизация",
        "kk": "Авторизация қажет",
        "en": "Authorization required",
    },

    # Вход, регистрация, профиль
    "email_taken": {
        "ru": "Пользователь с таким email уже зарегистрирован",
        "kk": "Бұл email-мен пайдаланушы тіркелген",
        "en": "A user with this email is already registered",
    },
    "wrong_credentials": {
        "ru": "Неверный email или пароль",
        "kk": "Email немесе құпиясөз қате",
        "en": "Wrong email or password",
    },
    "session_expired": {
        "ru": "Сессия истекла, войдите снова",
        "kk": "Сессия мерзімі өтті, қайта кіріңіз",
        "en": "Your session has expired, please sign in again",
    },
    "current_password_required": {
        "ru": "Введите текущий пароль для смены email или пароля",
        "kk": "Email немесе құпиясөзді ауыстыру үшін ағымдағы құпиясөзді енгізіңіз",
        "en": "Enter your current password to change your email or password",
    },
    "wrong_current_password": {
        "ru": "Неверный текущий пароль",
        "kk": "Ағымдағы құпиясөз қате",
        "en": "Wrong current password",
    },
    "wrong_password": {
        "ru": "Неверный пароль",
        "kk": "Құпиясөз қате",
        "en": "Wrong password",
    },
    "wrong_recovery_code": {
        "ru": "Неверный email или код восстановления",
        "kk": "Email немесе қалпына келтіру коды қате",
        "en": "Wrong email or recovery code",
    },
    "invalid_tz": {
        "ru": "Параметр tz: укажите часовой пояс, например Asia/Almaty",
        "kk": "tz параметрі: уақыт белдеуін көрсетіңіз, мысалы Asia/Almaty",
        "en": "Parameter tz: specify a time zone, e.g. Asia/Almaty",
    },

    # Файлы и фото
    "file_too_big": {
        "ru": "Файл слишком большой. Максимум — {mb} МБ",
        "kk": "Файл тым үлкен. Ең көбі — {mb} МБ",
        "en": "File is too large. Maximum is {mb} MB",
    },
    "file_empty": {
        "ru": "Файл пустой",
        "kk": "Файл бос",
        "en": "The file is empty",
    },
    "avatar_too_big": {
        "ru": "Фото слишком большое. Максимум — {mb} МБ",
        "kk": "Фото тым үлкен. Ең көбі — {mb} МБ",
        "en": "Photo is too large. Maximum is {mb} MB",
    },
    "avatar_unsupported": {
        "ru": "Загрузите фото в формате JPG, PNG или WEBP",
        "kk": "JPG, PNG немесе WEBP пішіміндегі фотоны жүктеңіз",
        "en": "Upload a JPG, PNG or WEBP photo",
    },
    "avatar_resolution": {
        "ru": "Фото слишком большого разрешения",
        "kk": "Фото ажыратымдылығы тым жоғары",
        "en": "Photo resolution is too high",
    },
    "image_required": {
        "ru": "Загрузите изображение чека",
        "kk": "Чек суретін жүктеңіз",
        "en": "Upload a receipt image",
    },
    "unsupported_image_type": {
        "ru": "Поддерживаются изображения JPG, PNG, WEBP, BMP или TIFF",
        "kk": "JPG, PNG, WEBP, BMP немесе TIFF суреттері қолданылады",
        "en": "Supported images: JPG, PNG, WEBP, BMP or TIFF",
    },
    "invalid_image_path": {
        "ru": "Некорректный путь к изображению чека",
        "kk": "Чек суретінің жолы дұрыс емес",
        "en": "Invalid receipt image path",
    },

    # Распознавание
    "no_receipt_text": {
        "ru": "На изображении не удалось найти текст чека",
        "kk": "Суреттен чек мәтінін табу мүмкін болмады",
        "en": "Couldn't find receipt text in the image",
    },
    "ocr_failed": {
        "ru": "Не удалось распознать чек. Попробуйте ещё раз позже.",
        "kk": "Чекті тану мүмкін болмады. Кейінірек қайталап көріңіз.",
        "en": "Couldn't recognise the receipt. Please try again later.",
    },
    "demo_not_found": {
        "ru": "Демо-файл images.jpeg не найден",
        "kk": "images.jpeg демо-файлы табылмады",
        "en": "Demo file images.jpeg not found",
    },
    "task_not_found": {
        "ru": "Задача не найдена или устарела",
        "kk": "Тапсырма табылмады немесе ескірген",
        "en": "Task not found or expired",
    },

    # Чеки
    "receipt_not_found": {
        "ru": "Чек не найден",
        "kk": "Чек табылмады",
        "en": "Receipt not found",
    },
    "duplicate_receipt": {
        "ru": "Похоже, этот чек уже сохранён",
        "kk": "Бұл чек сақталған сияқты",
        "en": "Looks like this receipt is already saved",
    },
    "invalid_query_date": {
        "ru": "Параметр {name}: укажите дату в формате ГГГГ-ММ-ДД",
        "kk": "{name} параметрі: күнді ЖЖЖЖ-АА-КК пішімінде көрсетіңіз",
        "en": "Parameter {name}: use the YYYY-MM-DD date format",
    },
    "invalid_month": {
        "ru": "Параметр month: укажите месяц в формате ГГГГ-ММ",
        "kk": "month параметрі: айды ЖЖЖЖ-АА пішімінде көрсетіңіз",
        "en": "Parameter month: use the YYYY-MM format",
    },

    # Категории и правила
    "category_not_found": {
        "ru": "Категория не найдена",
        "kk": "Санат табылмады",
        "en": "Category not found",
    },
    "category_exists": {
        "ru": "Категория «{name}» уже есть",
        "kk": "«{name}» санаты бар",
        "en": "Category “{name}” already exists",
    },
    "categories_limit": {
        "ru": "Можно создать не больше {max} категорий",
        "kk": "Ең көбі {max} санат құруға болады",
        "en": "You can create at most {max} categories",
    },
    "move_to_deleted_category": {
        "ru": "Нельзя перенести позиции в удаляемую категорию",
        "kk": "Позицияларды жойылатын санатқа ауыстыруға болмайды",
        "en": "Items can't be moved to the category being deleted",
    },
    "categories_order_invalid": {
        "ru": "Передайте все свои категории, каждую один раз",
        "kk": "Барлық санаттарыңызды бір-бір реттен жіберіңіз",
        "en": "Send all your categories, each once",
    },
    "item_not_found": {
        "ru": "Позиция не найдена",
        "kk": "Позиция табылмады",
        "en": "Item not found",
    },
    "rule_not_found": {
        "ru": "Правило не найдено",
        "kk": "Ереже табылмады",
        "en": "Rule not found",
    },

    # Рекомендации
    "insights_consent_required": {
        "ru": "Нужно согласие на анализ",
        "kk": "Талдауға келісім керек",
        "en": "Consent to the analysis is required",
    },
    "insights_retry_hours": {
        "ru": "Обновить советы можно через {hours} ч",
        "kk": "Кеңестерді {hours} сағаттан кейін жаңартуға болады",
        "en": "You can refresh the tips in {hours} h",
    },
    "insights_retry_minutes": {
        "ru": "Обновить советы можно через {minutes} мин",
        "kk": "Кеңестерді {minutes} минуттан кейін жаңартуға болады",
        "en": "You can refresh the tips in {minutes} min",
    },
    "insights_failed": {
        "ru": "Не удалось подготовить советы. Попробуйте позже",
        "kk": "Кеңестерді дайындау мүмкін болмады. Кейінірек қайталап көріңіз",
        "en": "Couldn't prepare the tips. Please try again later",
    },

    # Проверки полей (422, app/schemas.py) — код становится полем code у ошибки
    "not_finite": {
        "ru": "Укажите обычное число",
        "kk": "Кәдімгі сан көрсетіңіз",
        "en": "Enter a regular number",
    },
    "amount_negative": {
        "ru": "Сумма не может быть отрицательной",
        "kk": "Сома теріс болмауы керек",
        "en": "Amount can't be negative",
    },
    "amount_too_big": {
        "ru": "Слишком большая сумма: не больше 1 000 000 000",
        "kk": "Сома тым үлкен: ең көбі 1 000 000 000",
        "en": "Amount too large: at most 1,000,000,000",
    },
    "item_name_required": {
        "ru": "Укажите название товара",
        "kk": "Тауар атауын көрсетіңіз",
        "en": "Enter the item name",
    },
    "quantity_not_positive": {
        "ru": "Количество должно быть больше нуля",
        "kk": "Саны нөлден үлкен болуы керек",
        "en": "Quantity must be greater than zero",
    },
    "quantity_too_big": {
        "ru": "Слишком большое количество: не больше 100 000",
        "kk": "Саны тым көп: ең көбі 100 000",
        "en": "Quantity too large: at most 100,000",
    },
    "invalid_email": {
        "ru": "Введите корректный email (например, user@example.com)",
        "kk": "Дұрыс email енгізіңіз (мысалы, user@example.com)",
        "en": "Enter a valid email (e.g. user@example.com)",
    },
    "password_too_short": {
        "ru": "Пароль должен содержать минимум {min} символов",
        "kk": "Құпиясөз кемінде {min} таңбадан тұруы керек",
        "en": "Password must be at least {min} characters",
    },
    "password_too_long": {
        "ru": "Пароль слишком длинный",
        "kk": "Құпиясөз тым ұзын",
        "en": "Password is too long",
    },
    "unknown_theme": {
        "ru": "Неизвестная тема. Доступны: {allowed}",
        "kk": "Белгісіз тақырып. Қолжетімді: {allowed}",
        "en": "Unknown theme. Available: {allowed}",
    },
    "unknown_accent": {
        "ru": "Неизвестный акцентный цвет. Доступны: {allowed}",
        "kk": "Белгісіз акцент түсі. Қолжетімді: {allowed}",
        "en": "Unknown accent colour. Available: {allowed}",
    },
    "unknown_language": {
        "ru": "Неизвестный язык. Доступны: {allowed}",
        "kk": "Белгісіз тіл. Қолжетімді: {allowed}",
        "en": "Unknown language. Available: {allowed}",
    },
    "currency_unsupported": {
        "ru": "Валюта «{value}» не поддерживается",
        "kk": "«{value}» валютасы қолданылмайды",
        "en": "Currency “{value}” is not supported",
    },
    "budget_by_name": {
        "ru": "Лимит задаётся по category_id, а не по названию категории",
        "kk": "Лимит санат атауы бойынша емес, category_id бойынша қойылады",
        "en": "The limit is set by category_id, not by category name",
    },
    "budget_limit_not_positive": {
        "ru": "Лимит бюджета должен быть больше нуля",
        "kk": "Бюджет лимиті нөлден үлкен болуы керек",
        "en": "Budget limit must be greater than zero",
    },
    "budget_limit_too_big": {
        "ru": "Лимит бюджета не может превышать 1 000 000 000 000",
        "kk": "Бюджет лимиті ең көбі 1 000 000 000 000 болуы мүмкін",
        "en": "Budget limit can't exceed 1,000,000,000,000",
    },
    "budgets_too_many": {
        "ru": "Количество лимитов не может превышать 50",
        "kk": "Лимиттер саны ең көбі 50 болуы мүмкін",
        "en": "There can't be more than 50 limits",
    },
    "budget_total_duplicate": {
        "ru": "Общий лимит (без категории) указан несколько раз",
        "kk": "Жалпы лимит (санатсыз) бірнеше рет көрсетілген",
        "en": "The overall limit (no category) is given more than once",
    },
    "budget_category_duplicate": {
        "ru": "Лимит для одной категории указан несколько раз",
        "kk": "Бір санаттың лимиті бірнеше рет көрсетілген",
        "en": "The limit for one category is given more than once",
    },
    "category_name_length": {
        "ru": "Название категории: от 1 до {max} символов",
        "kk": "Санат атауы: 1-ден {max} таңбаға дейін",
        "en": "Category name: 1 to {max} characters",
    },
    "category_name_reserved": {
        "ru": "Это название зарезервировано",
        "kk": "Бұл атау резервте тұр",
        "en": "This name is reserved",
    },
    "category_description_length": {
        "ru": "Описание категории: не больше {max} символов",
        "kk": "Санат сипаттамасы: ең көбі {max} таңба",
        "en": "Category description: at most {max} characters",
    },
    "category_icon_length": {
        "ru": "Иконка: не больше {max} символов",
        "kk": "Белгіше: ең көбі {max} таңба",
        "en": "Icon: at most {max} characters",
    },

    # Шаблонные советы (app/insights.py, rule_insights). Числа и суммы подставляет бэк из сводки
    "insight.over_budget_total.title": {
        "ru": "Общий лимит превышен на {over}",
        "kk": "Жалпы лимит {over} асып кетті",
        "en": "Overall limit exceeded by {over}",
    },
    "insight.over_budget_total.text": {
        "ru": "В этом месяце потрачено {spent} при лимите {limit}.",
        "kk": "Осы айда {limit} лимитпен {spent} жұмсалды.",
        "en": "This month you spent {spent} against a limit of {limit}.",
    },
    "insight.over_budget.title": {
        "ru": "{name}: лимит превышен на {over}",
        "kk": "{name}: лимит {over} асып кетті",
        "en": "{name}: limit exceeded by {over}",
    },
    "insight.over_budget.text": {
        "ru": "Потрачено {spent} при лимите {limit}.",
        "kk": "{limit} лимитпен {spent} жұмсалды.",
        "en": "Spent {spent} against a limit of {limit}.",
    },
    "insight.budget_forecast.title": {
        "ru": "{name}: лимит может закончиться {date}",
        "kk": "{name}: лимит {date} таусылуы мүмкін",
        "en": "{name}: limit may run out on {date}",
    },
    "insight.budget_forecast.text": {
        "ru": "Уже потрачено {spent} из {limit}. При таком темпе лимита не хватит до конца месяца.",
        "kk": "{limit} ішінен {spent} жұмсалды. Осы қарқынмен лимит ай соңына дейін жетпейді.",
        "en": "Already spent {spent} of {limit}. At this pace the limit won't last until the end of the month.",
    },
    "insight.category_growth.title": {
        "ru": "{name}: на {growth}% больше обычного",
        "kk": "{name}: әдеттегіден {growth}% көп",
        "en": "{name}: {growth}% above usual",
    },
    "insight.category_growth.text": {
        "ru": "В этом месяце — {spent}, обычно около {median} в месяц.",
        "kk": "Осы айда — {spent}, әдетте айына шамамен {median}.",
        "en": "This month: {spent}, usually about {median} a month.",
    },
    "insight.budget_suggestion.title": {
        "ru": "Поставить лимит на «{name}»",
        "kk": "«{name}» санатына лимит қою",
        "en": "Set a limit for “{name}”",
    },
    "insight.budget_suggestion.text": {
        "ru": "Последние {months} месяца вы тратите от {min} до {max}. Лимит {amount} поможет держать это под контролем.",
        "kk": "Соңғы {months} айда {min} мен {max} аралығында жұмсайсыз. {amount} лимиті шығынды бақылауда ұстауға көмектеседі.",
        "en": "Over the last {months} months you've spent between {min} and {max}. A limit of {amount} will help keep it under control.",
    },
    "insight.frequent_small.title": {
        "ru": "{count} мелких покупок",
        "kk": "{count} ұсақ сатып алу",
        "en": "{count} small purchases",
    },
    "insight.frequent_small.title_store": {
        "ru": "{count} мелких покупок в «{store}»",
        "kk": "«{store}»: {count} ұсақ сатып алу",
        "en": "{count} small purchases at “{store}”",
    },
    "insight.frequent_small.text": {
        "ru": "За этот месяц они сложились в {total}.",
        "kk": "Осы айда олардың жиыны {total} болды.",
        "en": "This month they added up to {total}.",
    },
    "insight.uncategorized.title": {
        "ru": "{share}% трат без категории",
        "kk": "Шығындардың {share}% санатсыз",
        "en": "{share}% of spending is uncategorized",
    },
    "insight.uncategorized.text": {
        "ru": "Разложите их по категориям — советы и лимиты станут точнее.",
        "kk": "Оларды санаттарға бөліңіз — кеңестер мен лимиттер дәлірек болады.",
        "en": "Sort it into categories — tips and limits will be more accurate.",
    },
    "insight.unconverted.title": {
        "ru": "Не учтено чеков: {count} ({currencies})",
        "kk": "Есепке алынбаған чектер: {count} ({currencies})",
        "en": "Receipts not counted: {count} ({currencies})",
    },
    "insight.unconverted.text": {
        "ru": "Для них пока нет курса к {currency}, поэтому суммы в советах без них.",
        "kk": "Олар үшін {currency} бағамы әлі жоқ, сондықтан кеңестердегі сомалар оларсыз есептелді.",
        "en": "There's no {currency} exchange rate for them yet, so the amounts in these tips leave them out.",
    },
    "insight.summary": {
        "ru": "В {month} потрачено {amount}.",
        "kk": "{month} {amount} жұмсалды.",
        "en": "You spent {amount} in {month}.",
    },
}

# Месяц для insight.summary: «В ноябре» / «Қарашада» / «in November»
MONTHS_IN: Dict[str, tuple] = {
    "ru": ("январе", "феврале", "марте", "апреле", "мае", "июне",
           "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"),
    "kk": ("Қаңтарда", "Ақпанда", "Наурызда", "Сәуірде", "Мамырда", "Маусымда",
           "Шілдеде", "Тамызда", "Қыркүйекте", "Қазанда", "Қарашада", "Желтоқсанда"),
    "en": ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"),
}
MONTHS_SHORT_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Встроенные ошибки Pydantic (422). Для ru остаётся прежний текст Pydantic — так ответ не меняется
# (правило 0.1); для kk и en — перевод. Параметры — из ctx ошибки; если их нет, остаётся текст Pydantic
VALIDATION_MESSAGES: Dict[str, Dict[str, str]] = {
    "missing": {"kk": "Міндетті өріс", "en": "Required field"},
    "json_invalid": {"kk": "JSON дұрыс емес", "en": "Invalid JSON"},
    "model_type": {"kk": "Объект күтілген", "en": "An object is expected"},
    "model_attributes_type": {"kk": "Объект күтілген", "en": "An object is expected"},
    "dict_type": {"kk": "Объект күтілген", "en": "An object is expected"},
    "list_type": {"kk": "Тізім күтілген", "en": "A list is expected"},
    "string_type": {"kk": "Мәтін күтілген", "en": "A string is expected"},
    "int_type": {"kk": "Бүтін сан күтілген", "en": "An integer is expected"},
    "int_parsing": {"kk": "Бүтін сан күтілген", "en": "An integer is expected"},
    "int_from_float": {"kk": "Бүтін сан күтілген", "en": "An integer is expected"},
    "float_type": {"kk": "Сан күтілген", "en": "A number is expected"},
    "float_parsing": {"kk": "Сан күтілген", "en": "A number is expected"},
    "bool_type": {"kk": "true немесе false күтілген", "en": "true or false is expected"},
    "bool_parsing": {"kk": "true немесе false күтілген", "en": "true or false is expected"},
    "string_too_long": {"kk": "Ең көбі {max_length} таңба", "en": "At most {max_length} characters"},
    "string_too_short": {"kk": "Кемінде {min_length} таңба", "en": "At least {min_length} characters"},
    "too_long": {"kk": "Ең көбі {max_length} элемент", "en": "At most {max_length} items"},
    "too_short": {"kk": "Кемінде {min_length} элемент", "en": "At least {min_length} items"},
    "greater_than_equal": {"kk": "Мән кемінде {ge} болуы керек", "en": "Must be at least {ge}"},
    "greater_than": {"kk": "Мән {gt}-ден үлкен болуы керек", "en": "Must be greater than {gt}"},
    "less_than_equal": {"kk": "Мән ең көбі {le} болуы керек", "en": "Must be at most {le}"},
    "less_than": {"kk": "Мән {lt}-ден кіші болуы керек", "en": "Must be less than {lt}"},
    "literal_error": {"kk": "Рұқсат етілген мәндер: {expected}", "en": "Allowed values: {expected}"},
}

# Стандартные категории. В базе — русское название, перевод — по default_key
CATEGORY_NAMES: Dict[str, Dict[str, str]] = {
    "groceries": {"ru": "Продукты", "kk": "Азық-түлік", "en": "Groceries"},
    "clothing": {"ru": "Одежда", "kk": "Киім", "en": "Clothing"},
    "home": {"ru": "Дом и быт", "kk": "Үй және тұрмыс", "en": "Home & household"},
    "transport": {"ru": "Транспорт", "kk": "Көлік", "en": "Transport"},
    "cafe": {"ru": "Кафе", "kk": "Кафе", "en": "Cafés & restaurants"},
    "health": {"ru": "Здоровье", "kk": "Денсаулық", "en": "Health"},
    "entertainment": {"ru": "Развлечения", "kk": "Ойын-сауық", "en": "Entertainment"},
    "other": {"ru": "Другое", "kk": "Басқа", "en": "Other"},
    "uncategorized": {"ru": "Без категории", "kk": "Санатсыз", "en": "Uncategorized"},
}


def _lang(lang: Optional[str]) -> str:
    return normalize_lang(lang) or DEFAULT_LANG


class CategoryName(NamedTuple):
    """Параметр текста — название категории: стандартная подставляется на языке текста, своя — как есть."""
    name: str
    default_key: Optional[str] = None


def tr(code: str, lang: Optional[str] = DEFAULT_LANG, **params: Any) -> str:
    """Текст по коду на языке lang; перевода нет — русский, кода нет — сам код (ошибку видно сразу)."""
    texts = MESSAGES.get(code)
    if texts is None:
        return code
    template = texts.get(_lang(lang)) or texts[DEFAULT_LANG]
    if not params:
        return template
    params = {
        k: category_display_name(v.default_key, v.name, lang) if isinstance(v, CategoryName) else v
        for k, v in params.items()
    }
    return template.format(**params)


def tr_validation(error_type: str, lang: Optional[str], ctx: Optional[Dict[str, Any]]) -> Optional[str]:
    """Перевод встроенной ошибки Pydantic или None — тогда остаётся её текст."""
    texts = VALIDATION_MESSAGES.get(error_type)
    template = texts.get(_lang(lang)) if texts else None
    if not template:
        return None
    try:
        return template.format(**(ctx or {}))
    except (KeyError, IndexError, ValueError):
        return None


def category_display_name(default_key: Optional[str], name: str, lang: Optional[str]) -> str:
    """Название категории для человека: стандартная — перевод по default_key, своя — как в базе."""
    names = CATEGORY_NAMES.get(default_key or "")
    return names.get(_lang(lang), name) if names else name


def validation_error(code: str, **params: Any) -> PydanticCustomError:
    """Ошибка своего валидатора: type = code, msg — русский текст. Перевод — в обработчике 422 (server.py)."""
    return PydanticCustomError(code, MESSAGES[code][DEFAULT_LANG], params or None)


class AppError(HTTPException):
    """Ошибка API по коду: ответ {"detail": текст на языке запроса, "code": code}.

    detail исключения — текст на языке lang (по умолчанию русский): его видят фоновые задачи
    (app/tasks.py), где запроса уже нет. Обработчик в server.py переводит по Accept-Language.
    """

    def __init__(self, status_code: int, code: str, *, headers: Optional[Dict[str, str]] = None,
                 lang: Optional[str] = DEFAULT_LANG, **params: Any):
        super().__init__(status_code=status_code, detail=tr(code, lang, **params), headers=headers)
        self.code = code
        self.params = params

    def in_lang(self, lang: Optional[str]) -> "AppError":
        """То же исключение с текстом на другом языке (для фоновых задач)."""
        return AppError(self.status_code, self.code, headers=self.headers, lang=lang, **self.params)

    def body(self, lang: Optional[str]) -> Dict[str, str]:
        return {"detail": tr(self.code, lang, **self.params), "code": self.code}


# --- Проверка языка ответа ИИ (порог согласован с ML) ---

KAZAKH_LETTERS = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")
EN_MIN_LATIN_SHARE = 0.8
KK_MIN_CYRILLIC_SHARE = 0.5
# В казахском тексте от стольких букв должна встретиться хотя бы одна казахская буква
KK_LETTERS_FOR_CHECK = 50


def _is_cyrillic(ch: str) -> bool:
    return "Ѐ" <= ch <= "ӿ"


def looks_like_language(text: str, lang: Optional[str]) -> bool:
    """Похож ли текст на язык lang. ru не проверяется. Числа, знаки и пустой текст проверку проходят."""
    lang = _lang(lang)
    letters = [ch for ch in str(text or "") if ch.isalpha()]
    if lang == "ru" or not letters:
        return True
    if lang == "en":
        latin = sum(1 for ch in letters if ch.isascii())
        return latin / len(letters) >= EN_MIN_LATIN_SHARE
    cyrillic = sum(1 for ch in letters if _is_cyrillic(ch))
    if cyrillic / len(letters) < KK_MIN_CYRILLIC_SHARE:
        return False
    return len(letters) < KK_LETTERS_FOR_CHECK or any(ch in KAZAKH_LETTERS for ch in letters)
