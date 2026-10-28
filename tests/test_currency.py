import os
import unittest
from unittest.mock import patch
from datetime import date, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

os.environ.setdefault("SECRET_KEY", "test_secret_key_1234567890_antigravity_safe_key_32chars")

from server import app
from app import database, models, auth, rates, currencies


class TestCurrencyAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        database.init_db()

    def setUp(self):
        self.db: Session = database.SessionLocal()
        # Очищаем тестовых пользователей и курсы
        test_emails = ["test_currency_a@example.com", "test_currency_b@example.com"]
        for email in test_emails:
            u = self.db.query(models.User).filter(models.User.email == email).first()
            if u:
                self.db.delete(u)
        self.db.query(models.ExchangeRate).delete()
        self.db.commit()

        # Создаём тестовых пользователей
        hashed_pw = auth.hash_password("password123")
        self.user_a = models.User(
            email="test_currency_a@example.com",
            hashed_password=hashed_pw,
            full_name="User Currency A",
            base_currency="KZT",
        )
        self.user_b = models.User(
            email="test_currency_b@example.com",
            hashed_password=hashed_pw,
            full_name="User Currency B",
            base_currency="KZT",
        )
        self.db.add_all([self.user_a, self.user_b])
        self.db.commit()
        self.db.refresh(self.user_a)
        self.db.refresh(self.user_b)

        token_a, _ = auth.issue_tokens(self.user_a)
        token_b, _ = auth.issue_tokens(self.user_b)
        self.headers_a = {"Authorization": f"Bearer {token_a}"}
        self.headers_b = {"Authorization": f"Bearer {token_b}"}

    def tearDown(self):
        test_emails = ["test_currency_a@example.com", "test_currency_b@example.com"]
        for email in test_emails:
            u = self.db.query(models.User).filter(models.User.email == email).first()
            if u:
                self.db.delete(u)
        self.db.commit()
        self.db.close()

    def test_get_currencies_without_token(self):
        """GET /api/currencies — доступен без токена, возвращает список валют."""
        r = self.client.get("/api/currencies")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 8)
        codes = [item["code"] for item in data]
        self.assertIn("KZT", codes)
        self.assertIn("USD", codes)
        self.assertIn("RUB", codes)

    def test_save_receipt_without_currency(self):
        """Сохранить чек без currency -> KZT, rate_to_kzt = 1.0, rate_to_base = 1.0."""
        payload = {
            "store": "Magnum",
            "date": "2026-10-20",
            "total": 5000.0,
            "items": [{"name": "Молоко", "quantity": 1, "price_per_unit": 5000.0, "total_price": 5000.0, "category": "Продукты"}],
        }
        r = self.client.post("/api/receipts/save", json=payload, headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["currency"], "KZT")
        self.assertEqual(data["rate_to_kzt"], 1.0)
        self.assertEqual(data["base_currency"], "KZT")
        self.assertEqual(data["rate_to_base"], 1.0)

    @patch("app.rates._fetch_day")
    def test_save_receipt_usd_with_rates(self, mock_fetch):
        """Сохранить чек в USD -> берет курс из ленты, rate_to_base = курс."""
        mock_fetch.return_value = {"USD": 500.0, "RUB": 5.0}

        payload = {
            "store": "Amazon",
            "date": "2026-10-21",
            "currency": "USD",
            "total": 20.0,
            "items": [{"name": "Book", "quantity": 1, "price_per_unit": 20.0, "total_price": 20.0, "category": "Развлечения"}],
        }
        r = self.client.post("/api/receipts/save", json=payload, headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["currency"], "USD")
        self.assertEqual(data["rate_to_kzt"], 500.0)
        self.assertEqual(data["rate_to_base"], 500.0)
        self.assertEqual(mock_fetch.call_count, 1)

    def test_currency_normalization_and_validation(self):
        """currency: 'usd ' -> сохраняется как 'USD'; currency: 'XXX' -> 422."""
        with patch("app.rates._fetch_day", return_value={"USD": 500.0}):
            r = self.client.post("/api/receipts/save", json={"store": "Shop", "currency": "usd ", "total": 10.0}, headers=self.headers_a)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["currency"], "USD")

        r_bad = self.client.post("/api/receipts/save", json={"store": "Shop", "currency": "XXX", "total": 10.0}, headers=self.headers_a)
        self.assertEqual(r_bad.status_code, 422)

    @patch("app.rates._fetch_day")
    def test_network_error_saves_receipt_with_null_rate(self, mock_fetch):
        """_fetch_day бросает ошибку -> чек всё равно сохранён, rate_to_kzt = null, rate_to_base = null."""
        mock_fetch.side_effect = Exception("Connection timeout")

        payload = {"store": "Cafe", "date": "2026-10-18", "currency": "USD", "total": 15.0}
        r = self.client.post("/api/receipts/save", json=payload, headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["currency"], "USD")
        self.assertIsNone(data["rate_to_kzt"])
        self.assertIsNone(data["rate_to_base"])

    @patch("app.rates._fetch_day")
    def test_empty_feed_fallback_to_previous_day(self, mock_fetch):
        """Лента за дату пустая (выходной) -> взят курс предыдущего дня."""
        # Первый вызов (воскресенье) пустой, второй (суббота) пустой, третий (пятница) с курсом
        mock_fetch.side_effect = [{}, {}, {"USD": 495.0, "EUR": 530.0}]

        payload = {"store": "Store", "date": "2026-10-18", "currency": "USD", "total": 10.0}
        r = self.client.post("/api/receipts/save", json=payload, headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["currency"], "USD")
        self.assertEqual(data["rate_to_kzt"], 495.0)

    @patch("app.rates._fetch_day")
    def test_repeat_save_on_same_date_does_not_call_fetch_again(self, mock_fetch):
        """Повторное сохранение за ту же дату не вызывает _fetch_day второй раз."""
        mock_fetch.return_value = {"USD": 480.0}

        r1 = self.client.post("/api/receipts/save", json={"date": "2026-10-15", "currency": "USD", "total": 10.0}, headers=self.headers_a)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(mock_fetch.call_count, 1)

        r2 = self.client.post("/api/receipts/save", json={"date": "2026-10-15", "currency": "USD", "total": 20.0}, headers=self.headers_a)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(mock_fetch.call_count, 1)  # Не вызывался повторно

    @patch("app.rates._fetch_day")
    def test_put_receipt_currency_update(self, mock_fetch):
        """PUT со сменой валюты -> новый курс; без смены валюты и даты -> _fetch_day не вызывается."""
        mock_fetch.return_value = {"USD": 480.0, "EUR": 520.0}

        r_create = self.client.post("/api/receipts/save", json={"date": "2026-10-10", "currency": "USD", "total": 10.0}, headers=self.headers_a)
        self.assertEqual(r_create.status_code, 200)
        rec_id = r_create.json()["id"]
        call_count_before = mock_fetch.call_count

        # Обновление только магазина и суммы — без смены даты и валюты
        r_update1 = self.client.put(f"/api/receipts/{rec_id}", json={"date": "2026-10-10", "currency": "USD", "total": 15.0, "store": "NewStore"}, headers=self.headers_a)
        self.assertEqual(r_update1.status_code, 200)
        self.assertEqual(mock_fetch.call_count, call_count_before)

        # Обновление валюты на EUR
        r_update2 = self.client.put(f"/api/receipts/{rec_id}", json={"date": "2026-10-10", "currency": "EUR", "total": 15.0}, headers=self.headers_a)
        self.assertEqual(r_update2.status_code, 200)
        self.assertEqual(r_update2.json()["currency"], "EUR")
        self.assertEqual(r_update2.json()["rate_to_kzt"], 520.0)

    @patch("app.rates._fetch_day")
    def test_patch_me_base_currency(self, mock_fetch):
        """PATCH /api/auth/me с base_currency: 'USD' -> чеки в тенге получают rate_to_base = 1 / курс USD."""
        mock_fetch.return_value = {"USD": 500.0}

        # Создаём чек в KZT
        r_create = self.client.post("/api/receipts/save", json={"date": "2026-10-12", "currency": "KZT", "total": 10000.0}, headers=self.headers_a)
        self.assertEqual(r_create.status_code, 200)

        # Меняем базовую валюту пользователя на USD
        r_patch = self.client.patch("/api/auth/me", json={"base_currency": "USD"}, headers=self.headers_a)
        self.assertEqual(r_patch.status_code, 200)
        self.assertEqual(r_patch.json()["user"]["base_currency"], "USD")

        # Читаем чеки: rate_to_base должен быть 1.0 / 500.0 = 0.002
        r_list = self.client.get("/api/receipts", headers=self.headers_a)
        self.assertEqual(r_list.status_code, 200)
        receipts = r_list.json()
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["base_currency"], "USD")
        self.assertAlmostEqual(receipts[0]["rate_to_base"], 0.002, places=4)

    @patch("app.rates._fetch_day")
    def test_analytics_multi_currency_and_unconverted(self, mock_fetch):
        """Аналитика: 10 000 ₸ + 20 $ (курс 500) -> 20 000 ₸; чек без курса -> в unconverted, не в сумме."""
        mock_fetch.return_value = {"USD": 500.0}

        # Чек 1: 10 000 KZT
        self.client.post("/api/receipts/save", json={"date": "2026-10-01", "currency": "KZT", "total": 10000.0, "items": [{"name": "Товар 1", "total_price": 10000.0, "category": "Продукты"}]}, headers=self.headers_a)

        # Чек 2: 20 USD (курс 500 -> 10 000 KZT)
        self.client.post("/api/receipts/save", json={"date": "2026-10-02", "currency": "USD", "total": 20.0, "items": [{"name": "Товар 2", "total_price": 20.0, "category": "Кафе"}]}, headers=self.headers_a)

        # Чек 3: без курса (подменим ошибку при сохранении)
        with patch("app.rates._fetch_day", side_effect=Exception("No network")):
            self.client.post("/api/receipts/save", json={"date": "2026-09-01", "currency": "USD", "total": 50.0}, headers=self.headers_a)

        r_analytics = self.client.get("/api/analytics", headers=self.headers_a)
        self.assertEqual(r_analytics.status_code, 200)
        data = r_analytics.json()

        # Сумма должна быть 10 000 + 20 * 500 = 20 000 KZT
        self.assertEqual(data["total_spent"], 20000.0)
        self.assertEqual(data["receipts_count"], 2)
        self.assertEqual(data["base_currency"], "KZT")

        # Чек без курса должен быть в unconverted
        self.assertEqual(len(data["unconverted"]), 1)
        self.assertEqual(data["unconverted"][0]["currency"], "USD")
        self.assertEqual(data["unconverted"][0]["receipts"], 1)

    @patch("app.rates._fetch_day")
    def test_budgets_multi_currency(self, mock_fetch):
        """Бюджеты: лимит в KZT, чек в USD пересчитан в траты."""
        mock_fetch.return_value = {"USD": 500.0}

        # Устанавливаем бюджет на Кафе: 15 000 KZT
        self.client.put("/api/budgets", json={"budgets": [{"category": "Кафе", "limit": 15000.0}]}, headers=self.headers_a)

        # Сохраняем чек в Кафе на $20 (курс 500 -> 10 000 KZT)
        self.client.post("/api/receipts/save", json={"date": "2026-10-05", "currency": "USD", "total": 20.0, "items": [{"name": "Lunch", "total_price": 20.0, "category": "Кафе"}]}, headers=self.headers_a)

        r_status = self.client.get("/api/budgets/status?month=2026-10", headers=self.headers_a)
        self.assertEqual(r_status.status_code, 200)
        data = r_status.json()

        self.assertEqual(data["total_spent"], 10000.0)
        self.assertEqual(len(data["budgets"]), 1)
        self.assertEqual(data["budgets"][0]["spent"], 10000.0)
        self.assertEqual(data["budgets"][0]["remaining"], 5000.0)

    def test_user_isolation(self):
        """Пользователь B не имеет доступа к чекам пользователя A (404)."""
        r_create = self.client.post("/api/receipts/save", json={"currency": "KZT", "total": 100.0}, headers=self.headers_a)
        self.assertEqual(r_create.status_code, 200)
        rec_id = r_create.json()["id"]

        r_b = self.client.get(f"/api/receipts/{rec_id}", headers=self.headers_b)
        self.assertEqual(r_b.status_code, 404)
