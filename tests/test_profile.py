"""Профиль: фото, оформление, сводка, последний вход, пересчёт курсов, источник категории, merchant.

PROFILE_TASKS.md, разделы 1 и 2.
"""
import io
import os
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

import testenv  # noqa: F401,E402 — временная база и папки, до импорта server
os.environ.setdefault("SECRET_KEY", "test_secret_key_1234567890_antigravity_safe_key_32chars")

import server
from server import app
from app import auth, config, database, models, rates
from app.categories import create_default_categories
from app.rate_limit import limiter

EMAILS = ["test_profile_a@example.com", "test_profile_b@example.com"]
PASSWORD = "password123"


def image_bytes(fmt="JPEG", size=(256, 256), color=(200, 30, 30), mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, fmt)
    return buf.getvalue()


class ProfileTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        database.init_db()

    def setUp(self):
        # В сеть за курсами не ходим, фоновые задачи выполняются сразу
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
        self.user_a = models.User(email=EMAILS[0], hashed_password=hashed)
        self.user_b = models.User(email=EMAILS[1], hashed_password=hashed)
        create_default_categories(self.db, self.user_a)
        create_default_categories(self.db, self.user_b)
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
        for email in EMAILS:
            user = self.db.query(models.User).filter(models.User.email == email).first()
            if user:
                server._delete_avatar_file(user.avatar_path)
                self.db.delete(user)
        self.db.commit()

    def headers(self, user):
        self.db.refresh(user)
        return {"Authorization": f"Bearer {auth.issue_tokens(user)[0]}"}

    def me(self, headers=None):
        r = self.client.get("/api/auth/me", headers=headers or self.headers_a)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def upload(self, content, headers=None, filename="me.jpg", content_type="image/jpeg"):
        return self.client.post(
            "/api/auth/me/avatar",
            files={"file": (filename, content, content_type)},
            headers=headers or self.headers_a,
        )

    def avatar_file(self, url):
        return os.path.join(config.AVATARS_DIR, url.rsplit("/", 1)[1])

    def cat(self, name, user=None):
        user = user or self.user_a
        self.db.expire_all()
        return next(c.id for c in user.categories if c.name == name)

    def save(self, headers=None, **fields):
        r = self.client.post("/api/receipts/save", json=fields, headers=headers or self.headers_a)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class TestAvatar(ProfileTestCase):
    def test_upload_shows_in_me_and_is_served(self):
        self.assertIsNone(self.me()["avatar_url"])
        r = self.upload(image_bytes())
        self.assertEqual(r.status_code, 200, r.text)
        url = r.json()["avatar_url"]
        self.assertRegex(url, r"^/avatars/[0-9a-f]{32}\.jpg$")
        self.assertEqual(self.me()["avatar_url"], url)
        served = self.client.get(url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(served.content)).format, "JPEG")

    def test_reupload_removes_old_file(self):
        first = self.upload(image_bytes()).json()["avatar_url"]
        second = self.upload(image_bytes(color=(0, 0, 255))).json()["avatar_url"]
        self.assertNotEqual(first, second)
        self.assertFalse(os.path.exists(self.avatar_file(first)))
        self.assertTrue(os.path.exists(self.avatar_file(second)))

    def test_png_with_alpha_and_webp_become_jpeg(self):
        for fmt, mode, color in (("PNG", "RGBA", (0, 0, 0, 0)), ("WEBP", "RGB", (1, 2, 3))):
            r = self.upload(image_bytes(fmt, mode=mode, color=color), filename=f"x.{fmt.lower()}",
                            content_type=f"image/{fmt.lower()}")
            self.assertEqual(r.status_code, 200, r.text)
            with Image.open(self.avatar_file(r.json()["avatar_url"])) as img:
                self.assertEqual((img.format, img.mode), ("JPEG", "RGB"))

    def test_large_image_is_downscaled(self):
        r = self.upload(image_bytes(size=(2000, 1000)))
        self.assertEqual(r.status_code, 200, r.text)
        with Image.open(self.avatar_file(r.json()["avatar_url"])) as img:
            self.assertLessEqual(max(img.size), server.AVATAR_MAX_SIDE)

    def test_too_big_file(self):
        r = self.upload(b"\xff" * (config.MAX_AVATAR_BYTES + 10))
        self.assertIn(r.status_code, (400, 413))
        self.assertIn("detail", r.json())
        self.assertIsNone(self.me()["avatar_url"])

    def test_text_with_jpg_extension_rejected(self):
        r = self.upload(b"definitely not an image", filename="photo.jpg", content_type="image/jpeg")
        self.assertEqual(r.status_code, 400)
        self.assertIsInstance(r.json()["detail"], str)
        # GIF — настоящая картинка, но не из разрешённых форматов
        r = self.upload(image_bytes("GIF", mode="P", color=1), filename="a.gif", content_type="image/gif")
        self.assertEqual(r.status_code, 400)

    def test_delete(self):
        url = self.upload(image_bytes()).json()["avatar_url"]
        r = self.client.delete("/api/auth/me/avatar", headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["avatar_url"])
        self.assertIsNone(self.me()["avatar_url"])
        self.assertFalse(os.path.exists(self.avatar_file(url)))

    def test_account_deletion_removes_file(self):
        url = self.upload(image_bytes()).json()["avatar_url"]
        r = self.client.request("DELETE", "/api/auth/me", json={"password": PASSWORD}, headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(os.path.exists(self.avatar_file(url)))

    def test_other_user_cannot_touch_avatar(self):
        url = self.upload(image_bytes()).json()["avatar_url"]
        self.assertEqual(self.client.delete("/api/auth/me/avatar", headers=self.headers_b).status_code, 200)
        self.assertEqual(self.me()["avatar_url"], url)
        self.assertTrue(os.path.exists(self.avatar_file(url)))
        self.assertEqual(self.client.delete("/api/auth/me/avatar").status_code, 401)


class TestPreferences(ProfileTestCase):
    def patch_me(self, body, headers=None):
        return self.client.patch("/api/auth/me", json=body, headers=headers or self.headers_a)

    def test_defaults(self):
        self.assertEqual(self.me()["preferences"], {"theme": "system", "accent": "violet", "ai_insights": False})

    def test_partial_update_without_password(self):
        r = self.patch_me({"preferences": {"theme": "light", "accent": "red"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["user"]["preferences"], {"theme": "light", "accent": "red", "ai_insights": False})
        # Пришёл один ключ — второй не сбрасывается
        self.patch_me({"preferences": {"accent": "green"}})
        self.assertEqual(self.me()["preferences"], {"theme": "light", "accent": "green", "ai_insights": False})
        # null у ключа — значение по умолчанию, null целиком — всё по умолчанию
        self.patch_me({"preferences": {"theme": None}})
        self.assertEqual(self.me()["preferences"], {"theme": "system", "accent": "green", "ai_insights": False})
        self.patch_me({"preferences": None})
        self.assertEqual(self.me()["preferences"], {"theme": "system", "accent": "violet", "ai_insights": False})

    def test_unknown_values_rejected(self):
        self.assertEqual(self.patch_me({"preferences": {"accent": "teal"}}).status_code, 422)
        self.assertEqual(self.patch_me({"preferences": {"theme": "sepia"}}).status_code, 422)
        self.assertEqual(self.me()["preferences"], {"theme": "system", "accent": "violet", "ai_insights": False})

    def test_other_fields_untouched_and_broken_stored_value(self):
        self.patch_me({"full_name": "Дамир"})
        self.patch_me({"preferences": {"accent": "pink"}})
        self.assertEqual(self.me()["full_name"], "Дамир")
        # В базе мусор (старая версия, ручная правка) — отдаём значения по умолчанию, а не 500
        self.user_a.preferences = {"theme": "neon", "accent": "blue", "junk": 1}
        self.db.commit()
        self.assertEqual(self.me()["preferences"], {"theme": "system", "accent": "blue", "ai_insights": False})


class TestLastLogin(ProfileTestCase):
    def test_login_sets_last_login_at(self):
        self.assertIsNone(self.me()["last_login_at"])
        r = self.client.post("/api/auth/login", json={"email": EMAILS[0], "password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.json()["user"]["last_login_at"])


class TestRecoveryCode(ProfileTestCase):
    REG_EMAIL = "test_profile_reg@example.com"

    def tearDown(self):
        user = self.db.query(models.User).filter(models.User.email == self.REG_EMAIL).first()
        if user:
            self.db.delete(user)
            self.db.commit()
        super().tearDown()

    def reset(self, code, email=None, new_password="new_password_1"):
        return self.client.post("/api/auth/reset-password", json={
            "email": email or self.REG_EMAIL, "recovery_code": code, "new_password": new_password})

    def register(self):
        r = self.client.post("/api/auth/register", json={"email": self.REG_EMAIL, "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_register_returns_code_once(self):
        data = self.register()
        self.assertRegex(data["recovery_code"], r"^([A-HJ-NP-Z2-9]{4}-){3}[A-HJ-NP-Z2-9]{4}$")
        self.assertTrue(data["user"]["has_recovery_code"])
        user = self.db.query(models.User).filter(models.User.email == self.REG_EMAIL).first()
        self.assertNotIn(data["recovery_code"], user.recovery_code_hash)  # в базе только хеш
        # Вход и /me код не показывают
        login = self.client.post("/api/auth/login", json={"email": self.REG_EMAIL, "password": PASSWORD})
        self.assertIsNone(login.json()["recovery_code"])

    def test_reset_with_code(self):
        data = self.register()
        old_token = {"Authorization": f"Bearer {data['access_token']}"}
        # Регистр, пробелы и дефисы не важны
        sloppy = " " + data["recovery_code"].lower().replace("-", " ") + " "
        r = self.reset(sloppy, email=self.REG_EMAIL.upper())
        self.assertEqual(r.status_code, 200, r.text)
        new = r.json()
        self.assertTrue(new["access_token"])
        self.assertNotEqual(new["recovery_code"], data["recovery_code"])
        self.assertIsNotNone(new["user"]["last_login_at"])

        # Новый пароль работает, старый — нет, старые токены отозваны
        ok = self.client.post("/api/auth/login", json={"email": self.REG_EMAIL, "password": "new_password_1"})
        self.assertEqual(ok.status_code, 200)
        bad = self.client.post("/api/auth/login", json={"email": self.REG_EMAIL, "password": PASSWORD})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get("/api/auth/me", headers=old_token).status_code, 401)
        # Код одноразовый, новый — действует
        limiter.clear()
        self.assertEqual(self.reset(data["recovery_code"]).status_code, 400)
        self.assertEqual(self.reset(new["recovery_code"], new_password="third_password").status_code, 200)

    def test_wrong_code_and_unknown_email_look_the_same(self):
        self.register()
        wrong = self.reset("AAAA-AAAA-AAAA-AAAA")
        unknown = self.reset("AAAA-AAAA-AAAA-AAAA", email="nobody_profile@example.com")
        self.assertEqual((wrong.status_code, unknown.status_code), (400, 400))
        self.assertEqual(wrong.json(), unknown.json())
        # Пользователь без кода (создан до миграции) — тоже 400, не 500
        limiter.clear()
        self.assertFalse(self.me()["has_recovery_code"])
        self.assertEqual(self.reset("AAAA-AAAA-AAAA-AAAA", email=EMAILS[0]).status_code, 400)

    def test_short_new_password_rejected(self):
        data = self.register()
        self.assertEqual(self.reset(data["recovery_code"], new_password="short").status_code, 422)

    def test_regenerate_needs_password(self):
        self.assertEqual(self.client.post("/api/auth/recovery-code", json={"password": PASSWORD}).status_code, 401)
        bad = self.client.post("/api/auth/recovery-code", json={"password": "wrong"}, headers=self.headers_a)
        self.assertEqual(bad.status_code, 400)
        r = self.client.post("/api/auth/recovery-code", json={"password": PASSWORD}, headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.me()["has_recovery_code"])
        first = r.json()["recovery_code"]
        second = self.client.post("/api/auth/recovery-code", json={"password": PASSWORD}, headers=self.headers_a)
        # Старый код больше не действует
        self.assertEqual(self.reset(first, email=EMAILS[0]).status_code, 400)
        self.assertEqual(self.reset(second.json()["recovery_code"], email=EMAILS[0]).status_code, 200)

    def test_forgot_password_removed(self):
        r = self.client.post("/api/auth/forgot-password", json={"email": EMAILS[0]})
        self.assertIn(r.status_code, (404, 405))


class TestRatesPending(ProfileTestCase):
    def test_flag_while_backfill_runs(self):
        rates.RUN_IN_BACKGROUND = True
        started, release = threading.Event(), threading.Event()

        def slow_backfill(db, user_id):
            started.set()
            release.wait(5)

        with patch("app.crud.backfill_user_rates", slow_backfill):
            r = self.client.patch("/api/auth/me", json={"base_currency": "USD"}, headers=self.headers_a)
            self.assertEqual(r.status_code, 200)
            self.assertTrue(started.wait(5))
            self.assertTrue(r.json()["user"]["rates_pending"])
            self.assertTrue(self.me()["rates_pending"])
            self.assertFalse(self.me(self.headers_b)["rates_pending"])
            release.set()
            for _ in range(100):
                if not rates.user_rates_pending(self.user_a.id):
                    break
                threading.Event().wait(0.02)
        self.assertFalse(self.me()["rates_pending"])

    def test_no_flag_without_currency_change(self):
        r = self.client.patch("/api/auth/me", json={"full_name": "Имя"}, headers=self.headers_a)
        self.assertFalse(r.json()["user"]["rates_pending"])


class TestProfileStats(ProfileTestCase):
    def stats(self, tz=None, headers=None):
        params = {"tz": tz} if tz else {}
        r = self.client.get("/api/profile/stats", params=params, headers=headers or self.headers_a)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_empty(self):
        s = self.stats()
        self.assertEqual(s["receipts_count"], 0)
        self.assertEqual(s["total_spent"], 0.0)
        self.assertEqual(s["activity"], [])
        self.assertIsNone(s["top_store"])
        self.assertIsNone(s["top_weekday"])

    def test_numbers_activity_and_habits(self):
        tz = ZoneInfo("Asia/Almaty")
        today = datetime.now(tz).date()
        d = lambda n: (today - timedelta(days=n)).isoformat()
        food, cafe = self.cat("Продукты"), self.cat("Кафе")
        self.save(store="MAGNUM", date=d(0), total=1000,
                  items=[{"name": "Хлеб", "total_price": 600, "category_id": food},
                         {"name": "Кофе", "total_price": 400, "category_id": cafe}])
        self.save(store="Magnum Cash&Carry", date=d(1), total=3000,
                  items=[{"name": "Мясо", "total_price": 3000, "category_id": food}])
        self.save(store=' ТОО "Magnum" ', date=d(2), total=500, category_id=cafe)
        biggest = self.save(store="Technodom", date=d(5), total=90000)
        # Без курса: не входит в суммы, но считается в чеках и в unconverted
        self.save(store="Amazon", date=d(5), total=10, currency="USD")
        # Старый чек за пределами 26 недель
        self.save(store="Small", date=d(400), total=200)

        s = self.stats("Asia/Almaty")
        self.assertEqual(s["base_currency"], "KZT")
        self.assertEqual(s["receipts_count"], 6)
        self.assertEqual(s["items_count"], 3)
        self.assertEqual(s["total_spent"], 94700.0)
        self.assertEqual(s["average_receipt"], round(94700 / 5, 2))  # делим на пересчитанные
        self.assertEqual(s["unconverted"], [{"currency": "USD", "count": 1}])
        self.assertEqual((s["first_receipt_at"], s["last_receipt_at"]), (d(400), d(0)))
        self.assertEqual(
            [(a["date"], a["count"], a["total"]) for a in s["activity"]],
            [(d(5), 2, 90000.0), (d(2), 1, 500.0), (d(1), 1, 3000.0), (d(0), 1, 1000.0)],
        )
        self.assertEqual(s["longest_streak"], 3)
        # Чаще всего — день d(5): там два чека
        self.assertEqual(s["top_weekday"], ((today - timedelta(days=5)).weekday() + 1) % 7)
        # Три варианта написания — один магазин
        self.assertEqual(s["top_store"], {"name": "Magnum", "count": 3})
        # Продукты: 600 + 3000, Кафе: 400 + 500 (чек без позиций — по своей категории); Technodom без категории
        self.assertEqual(s["top_category"], {"id": food, "total": 3600.0})
        self.assertEqual(s["biggest_receipt"], {"id": biggest["id"], "total": 90000.0, "store": "Technodom"})

    def test_old_receipts_without_merchant_grouped(self):
        """Чеки до миграции 0007 (merchant = NULL) склеиваются по сети на лету."""
        for store in ("MAGNUM", "Magnum Cash&Carry", "Small"):
            saved = self.save(store=store, total=1)
            self.db.get(models.Receipt, saved["id"]).merchant = None
        self.db.commit()
        self.assertEqual(self.stats()["top_store"], {"name": "Magnum", "count": 2})

    def test_month_totals_and_created_at_in_tz(self):
        tz = ZoneInfo("Asia/Almaty")
        today = datetime.now(tz).date()
        this_month = today.replace(day=1)
        prev_month = (this_month - timedelta(days=1)).replace(day=1)
        self.save(store="A", date=this_month.isoformat(), total=100)
        self.save(store="B", date=prev_month.isoformat(), total=40)
        # Без даты: день берётся из created_at в часовом поясе tz
        no_date = self.save(store="C", total=7)
        receipt = self.db.get(models.Receipt, no_date["id"])
        # 20:30 UTC — это уже следующий день в Алматы (UTC+5)
        receipt.created_at = datetime(2026, 1, 10, 20, 30)
        self.db.commit()

        s = self.stats("Asia/Almaty")
        self.assertEqual(s["month_total"], 100.0)
        self.assertEqual(s["prev_month_total"], 40.0)
        self.assertEqual(s["first_receipt_at"], "2026-01-11")
        self.assertEqual(self.stats()["first_receipt_at"], "2026-01-10")  # без tz — UTC

    def test_converts_to_base_currency(self):
        with patch("app.rates._fetch_day", return_value={"USD": 500.0}):
            self.save(store="Amazon", date="2026-10-21", total=20, currency="USD")
        self.save(store="Magnum", date="2026-10-21", total=1000)
        self.assertEqual(self.stats()["total_spent"], 11000.0)
        self.client.patch("/api/auth/me", json={"base_currency": "USD"}, headers=self.headers_a)
        s = self.stats()
        self.assertEqual(s["base_currency"], "USD")
        self.assertEqual(s["total_spent"], 22.0)

    def test_bad_tz_and_isolation(self):
        r = self.client.get("/api/profile/stats", params={"tz": "Mars/Base"}, headers=self.headers_a)
        self.assertEqual(r.status_code, 422)
        self.save(store="Magnum", total=10)
        self.assertEqual(self.stats(headers=self.headers_b)["receipts_count"], 0)
        self.assertEqual(self.client.get("/api/profile/stats").status_code, 401)


class TestCategorySourceAndMerchant(ProfileTestCase):
    def test_category_source_saved_and_returned(self):
        food = self.cat("Продукты")
        saved = self.save(store="Magnum", total=300, items=[
            {"name": "Хлеб", "total_price": 100, "category_id": food, "category_source": "ai"},
            {"name": "Молоко", "total_price": 100, "category_id": food, "category_source": "rule"},
            # Источник без категории не сохраняется
            {"name": "Что-то", "total_price": 100, "category_source": "keywords"},
        ])
        self.assertEqual([i["category_source"] for i in saved["items"]], ["ai", "rule", None])
        r = self.client.get(f"/api/receipts/{saved['id']}", headers=self.headers_a)
        self.assertEqual([i["category_source"] for i in r.json()["items"]], ["ai", "rule", None])

        bad = self.client.post("/api/receipts/save", headers=self.headers_a, json={
            "store": "X", "items": [{"name": "Хлеб", "category_source": "magic"}]})
        self.assertEqual(bad.status_code, 422)

    def test_move_sets_user_source(self):
        food, cafe = self.cat("Продукты"), self.cat("Кафе")
        saved = self.save(store="Magnum", total=100, items=[
            {"name": "Кофе", "total_price": 100, "category_id": food, "category_source": "ai"}])
        item_id = saved["items"][0]["id"]
        self.client.post("/api/items/move", json={"item_ids": [item_id], "category_id": cafe}, headers=self.headers_a)
        item = self.client.get(f"/api/receipts/{saved['id']}", headers=self.headers_a).json()["items"][0]
        self.assertEqual((item["category_id"], item["category_source"]), (cafe, "user"))
        self.client.post("/api/items/move", json={"item_ids": [item_id], "category_id": None}, headers=self.headers_a)
        item = self.client.get(f"/api/receipts/{saved['id']}", headers=self.headers_a).json()["items"][0]
        self.assertEqual((item["category_id"], item["category_source"]), (None, None))

    def test_merchant_computed_sent_and_searchable(self):
        auto = self.save(store='ТОО "MAGNUM CASH&CARRY"', total=1)
        self.assertEqual(auto["merchant"], "Magnum")
        sent = self.save(store="ИП Иванов", merchant="Кофейня у Ивана", total=1)
        self.assertEqual(sent["merchant"], "Кофейня у Ивана")
        none = self.save(total=1)
        self.assertIsNone(none["merchant"])

        # Поиск находит чек по merchant, даже если в store этого слова нет
        weird = self.save(store="МAГHУМ №5", total=1)
        self.assertEqual(weird["merchant"], "Magnum")
        found = self.client.get("/api/receipts", params={"q": "magnum"}, headers=self.headers_a).json()
        self.assertEqual({r["id"] for r in found}, {auto["id"], weird["id"]})

        # Редактирование без merchant: пересчитывается из нового store
        r = self.client.put(f"/api/receipts/{auto['id']}", headers=self.headers_a, json={"store": "Small №3", "total": 1})
        self.assertEqual(r.json()["merchant"], "Small")

    def test_analyze_returns_merchant_and_source_for_every_item(self):
        def fake_analyze(image_path, ocr=None, categories=None):
            return {"raw_lines": ["MAGNUM"], "store": "MAGNUM", "merchant": "Magnum", "total": 1,
                    "items": [{"name": "Хлеб", "total_price": 1}]}

        with patch.object(server, "analyze_receipt", fake_analyze), patch.object(server, "get_ocr", lambda: None):
            r = self.client.post("/api/analyze", files={"file": ("r.jpg", image_bytes(), "image/jpeg")})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        server._delete_image_file(data["image_path"])
        self.assertEqual(data["merchant"], "Magnum")
        self.assertIn("category_source", data["items"][0])


if __name__ == "__main__":
    unittest.main()
