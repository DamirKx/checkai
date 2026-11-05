"""Окружение тестов: временная база и папки вместо data/.

Импортируется первой строкой каждого тестового файла — до `import server`: настройки читаются при импорте.
Работает и с pytest, и с `python -m unittest discover -s tests`.

TEST_DATABASE_URL — прогнать тесты на другой базе, например PostgreSQL:
    TEST_DATABASE_URL=postgresql://checkai:checkai_secret@localhost:5432/checkai_test python -m pytest tests
"""
import atexit
import os
import shutil
import tempfile

TMP_DIR = tempfile.mkdtemp(prefix="checkai-tests-")

# Не setdefault: DATABASE_URL из .env или окружения разработчика не должен попасть в тесты
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{os.path.join(TMP_DIR, 'test.db')}"
os.environ["UPLOADS_DIR"] = os.path.join(TMP_DIR, "receipts")
os.environ["AVATARS_DIR"] = os.path.join(TMP_DIR, "avatars")
os.environ["ORPHAN_CLEANUP_HOURS"] = "0"
os.environ.setdefault("SECRET_KEY", "test_secret_key_1234567890_antigravity_safe_key_32chars")


@atexit.register
def _cleanup() -> None:
    # Windows не даст удалить файл базы, пока открыты соединения
    try:
        from app import database
        database.engine.dispose()
    except Exception:
        pass
    shutil.rmtree(TMP_DIR, ignore_errors=True)
