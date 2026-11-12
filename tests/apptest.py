"""Общее для тестов NEXT_WEEK_TASKS.md: два пользователя со стандартными категориями, без сети за курсами.

Импортируется после testenv. Тестов здесь нет — только базовый класс.
"""
import io
import os
import time
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

import testenv  # noqa: F401 — временная база и папки, до импорта server

import server
from server import app
from app import auth, config, database, models, rates
from app.categories import create_default_categories
from app.rate_limit import limiter
from app.tasks import insights_task_manager

PASSWORD = "password123"


def image_bytes(color=(200, 30, 30), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def put_upload(content: bytes, ext: str = ".jpg") -> str:
    """Файл в UPLOADS_DIR, как после /api/analyze. Возвращает image_path для сохранения чека."""
    os.makedirs(config.UPLOADS_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(config.UPLOADS_DIR, filename), "wb") as f:
        f.write(content)
    return f"{server.RECEIPTS_URL_PREFIX}/{filename}"


class AppTestCase(unittest.TestCase):
    EMAILS = ("user_a@example.com", "user_b@example.com")

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        database.init_db()

    def setUp(self):
        fetch = patch("app.rates._fetch_day", return_value={})
        fetch.start()
        self.addCleanup(fetch.stop)
        self._bg = rates.RUN_IN_BACKGROUND
        rates.RUN_IN_BACKGROUND = False
        rates.reset_failures()
        limiter.clear()

        self.db: Session = database.SessionLocal()
        self._delete_users()
        hashed = auth.hash_password(PASSWORD)
        self.user_a = models.User(email=self.EMAILS[0], hashed_password=hashed, full_name="Алия Тестова")
        self.user_b = models.User(email=self.EMAILS[1], hashed_password=hashed)
        for user in (self.user_a, self.user_b):
            create_default_categories(self.db, user)
        self.db.add_all([self.user_a, self.user_b])
        self.db.commit()
        self.headers_a = self.headers(self.user_a)
        self.headers_b = self.headers(self.user_b)

    def tearDown(self):
        rates.RUN_IN_BACKGROUND = self._bg
        self._delete_users()
        self.db.close()

    def _delete_users(self):
        self.db.expire_all()
        for email in self.EMAILS:
            user = self.db.query(models.User).filter(models.User.email == email).first()
            if user:
                self.db.delete(user)
        self.db.commit()

    def headers(self, user):
        self.db.refresh(user)
        return {"Authorization": f"Bearer {auth.issue_tokens(user)[0]}"}

    def category(self, name, user=None):
        user = user or self.user_a
        return self.db.query(models.Category).filter_by(user_id=user.id, name=name).one()

    def save(self, headers=None, status=200, **fields):
        r = self.client.post("/api/receipts/save", json=fields, headers=headers or self.headers_a)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def wait_task(self, task_id, timeout=10.0):
        """Ждёт, пока фоновая задача (анализ расходов) завершится, и возвращает её статус."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.client.get(f"/api/tasks/{task_id}")
            self.assertEqual(r.status_code, 200, r.text)
            if r.json()["status"] in ("completed", "failed"):
                return r.json()
            time.sleep(0.05)
        self.fail(f"Задача {task_id} не завершилась за {timeout} с")


__all__ = ["AppTestCase", "PASSWORD", "image_bytes", "put_upload", "insights_task_manager"]
