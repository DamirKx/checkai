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


DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOADS_DIR = os.path.join(DATA_DIR, "receipts")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or f"sqlite:///{os.path.join(DATA_DIR, 'checkai.db')}"

# Обязательная переменная: без неё сервер не стартует (см. app/auth.py).
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()

# Максимальный размер загружаемого фото чека, в мегабайтах.
MAX_UPLOAD_MB = _int_env("MAX_UPLOAD_MB", 10)
