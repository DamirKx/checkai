"""Настройки приложения из переменных окружения (и файла .env, если он есть)."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# .env удобен при локальном запуске; в Docker переменные передаёт docker-compose.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:  # python-dotenv не обязателен
    pass


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"Переменная окружения {name} должна быть целым числом, получено: {raw!r}")


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


DATA_DIR = os.path.join(BASE_DIR, "data")
# Интерфейс: шаблон страницы и статика (CSS, JS, PWA)
FRONTEND_TEMPLATES_DIR = os.path.join(BASE_DIR, "frontend", "templates")
FRONTEND_STATIC_DIR = os.path.join(BASE_DIR, "frontend", "static")
# Папка с фото чеков. Если база вынесена в другое место (например, в тестах), фото стоит вынести рядом:
# очистка «осиротевших» фото сверяет файлы именно с этой базой.
UPLOADS_DIR = os.getenv("UPLOADS_DIR", "").strip() or os.path.join(DATA_DIR, "receipts")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or f"sqlite:///{os.path.join(DATA_DIR, 'checkai.db')}"

# Обязательная переменная: без неё сервер не стартует (см. app/auth.py).
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()

# Максимальный размер загружаемого фото чека, в мегабайтах.
MAX_UPLOAD_MB = _int_env("MAX_UPLOAD_MB", 10)

# Доверять заголовку X-Forwarded-For (IP клиента для лимита запросов).
# Включайте только за своим обратным прокси (nginx и т.п.): иначе клиент подставит любой IP и обойдёт лимит.
TRUST_PROXY = _bool_env("TRUST_PROXY", False)

# Как часто удалять фото чеков, которые распознали, но не сохранили (часы). 0 — не удалять.
# Удаляются только файлы старше суток.
ORPHAN_CLEANUP_HOURS = _int_env("ORPHAN_CLEANUP_HOURS", 6)

# Фото профиля. Раздаётся статикой только эта папка, не data/ целиком.
AVATARS_DIR = os.getenv("AVATARS_DIR", "").strip() or os.path.join(DATA_DIR, "avatars")
AVATARS_URL_PREFIX = "/avatars"
MAX_AVATAR_BYTES = 2 * 1024 * 1024

# Загружать модель распознавания при старте сервера (в фоне), а не при первом чеке.
# В Docker включено (docker-compose.yml); локально и в тестах — нет: загрузка занимает память и время
OCR_WARMUP = _bool_env("OCR_WARMUP", False)

# Уровень логов приложения (логгеры checkai.*): DEBUG | INFO | WARNING | ERROR.
# На INFO видно время этапов распознавания: «analyze: ocr=…s llm=…s categorize=…s»
LOG_LEVEL = os.getenv("LOG_LEVEL", "").strip().upper() or "INFO"
if LOG_LEVEL not in ("DEBUG", "INFO", "WARNING", "ERROR"):
    raise RuntimeError(f"LOG_LEVEL должен быть DEBUG, INFO, WARNING или ERROR, получено: {LOG_LEVEL!r}")


# --- ИИ-рекомендации по расходам ---
# gemini | ollama | off. off — только шаблонные советы по фактам, данные никуда не уходят
AI_PROVIDER = (os.getenv("AI_PROVIDER", "").strip().lower() or "gemini")
if AI_PROVIDER not in ("gemini", "ollama", "off"):
    raise RuntimeError(f"AI_PROVIDER должен быть gemini, ollama или off, получено: {AI_PROVIDER!r}")
# Ключ только на сервере: во фронт и в логи не попадает. Читает ML (ml/llm.py)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip()
# Не чаще одного нового анализа на пользователя (на каждом языке) за столько минут; 0 — без ограничения.
# Старая настройка в часах (INSIGHTS_REFRESH_HOURS) учитывается, только если INSIGHTS_REFRESH_MINUTES не задана
def insights_refresh_minutes() -> int:
    if os.getenv("INSIGHTS_REFRESH_MINUTES", "").strip():
        return _int_env("INSIGHTS_REFRESH_MINUTES", 1)
    if os.getenv("INSIGHTS_REFRESH_HOURS", "").strip():
        return _int_env("INSIGHTS_REFRESH_HOURS", 0) * 60
    return 1


INSIGHTS_REFRESH_MINUTES = insights_refresh_minutes()
