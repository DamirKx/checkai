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
