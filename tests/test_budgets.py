import os
import unittest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Устанавливаем тестовый SECRET_KEY до импорта приложения, если его нет
os.environ.setdefault("SECRET_KEY", "test_secret_key_1234567890_antigravity_safe_key_32chars")

from server import app
from app import database, models, auth
from app.categories import create_default_categories


class TestBudgetsAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        database.init_db()

    def setUp(self):
        self.db: Session = database.SessionLocal()
        # Очищаем тестовых пользователей
        test_emails = ["test_user_budget_a@example.com", "test_user_budget_b@example.com"]
        for email in test_emails:
            u = self.db.query(models.User).filter(models.User.email == email).first()
            if u:
                self.db.delete(u)
        self.db.commit()

        # Создаём пользователя А
        hashed_pw = auth.hash_password("password123")
        self.user_a = models.User(email="test_user_budget_a@example.com", hashed_password=hashed_pw, full_name="User A")
        self.user_b = models.User(email="test_user_budget_b@example.com", hashed_password=hashed_pw, full_name="User B")
        create_default_categories(self.db, self.user_a)
        create_default_categories(self.db, self.user_b)
        self.db.add_all([self.user_a, self.user_b])
        self.db.commit()
        self.db.refresh(self.user_a)
        self.db.refresh(self.user_b)

        token_a, _ = auth.issue_tokens(self.user_a)
        token_b, _ = auth.issue_tokens(self.user_b)
        self.headers_a = {"Authorization": f"Bearer {token_a}"}
        self.headers_b = {"Authorization": f"Bearer {token_b}"}

    def cat(self, name, user=None):
        """id стандартной категории пользователя (по умолчанию A)."""
        user = user or self.user_a
        return next(c.id for c in user.categories if c.name == name)

    def tearDown(self):
        test_emails = ["test_user_budget_a@example.com", "test_user_budget_b@example.com"]
        for email in test_emails:
            u = self.db.query(models.User).filter(models.User.email == email).first()
            if u:
                self.db.delete(u)
        self.db.commit()
        self.db.close()

    def test_unauthorized_access(self):
        """Эндпоинты требуют токен (без него 401)."""
        r = self.client.get("/api/budgets")
        self.assertEqual(r.status_code, 401)

        r = self.client.put("/api/budgets", json={"budgets": []})
        self.assertEqual(r.status_code, 401)

        r = self.client.get("/api/budgets/status")
        self.assertEqual(r.status_code, 401)

    def test_empty_budgets_returns_empty_list_not_404(self):
        """Если лимитов нет, ответ [], а не 404."""
        r = self.client.get("/api/budgets", headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_put_and_get_budgets(self):
        """Установка и получение лимитов пользователя."""
        payload = {
            "budgets": [
                {"category_id": self.cat("Кафе"), "limit": 15000.0},
                {"category_id": None, "limit": 150000.0},
                {"category_id": self.cat("Продукты"), "limit": 30000.0},
            ]
        }
        r = self.client.put("/api/budgets", json=payload, headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        res = r.json()
        # Проверяем сортировку: общий первым, затем в порядке категорий
        self.assertEqual(len(res), 3)
        self.assertIsNone(res[0]["category_id"])
        self.assertIsNone(res[0]["category"])
        self.assertEqual(res[0]["limit"], 150000.0)
        self.assertEqual(res[1]["category"], "Продукты")
        self.assertEqual(res[1]["category_id"], self.cat("Продукты"))
        self.assertEqual(res[2]["category"], "Кафе")

        # GET должен возвращать ровно то же
        r_get = self.client.get("/api/budgets", headers=self.headers_a)
        self.assertEqual(r_get.status_code, 200)
        self.assertEqual(r_get.json(), res)

    def test_old_format_with_category_name_rejected(self):
        """Старый формат {"category": "Кафе"} → 422, а не молча общий лимит."""
        r = self.client.put(
            "/api/budgets", json={"budgets": [{"category": "Кафе", "limit": 50000.0}]}, headers=self.headers_a
        )
        self.assertEqual(r.status_code, 422)
        self.assertTrue(any("category_id" in str(e) for e in r.json().get("detail", [])))
        self.assertEqual(self.client.get("/api/budgets", headers=self.headers_a).json(), [])

    def test_foreign_category_rejected(self):
        """Лимит на категорию пользователя B → 422, лимиты A не изменились."""
        self.client.put("/api/budgets", json={"budgets": [{"category_id": None, "limit": 1000.0}]}, headers=self.headers_a)
        r = self.client.put(
            "/api/budgets",
            json={"budgets": [{"category_id": self.cat("Кафе", self.user_b), "limit": 500.0}]},
            headers=self.headers_a,
        )
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"], "Категория не найдена")
        self.assertEqual(len(self.client.get("/api/budgets", headers=self.headers_a).json()), 1)

    def test_validation_duplicate_categories_error(self):
        """Дубли категорий → 422 с текстом на русском."""
        # 1. Повторный null
        payload = {
            "budgets": [
                {"category_id": None, "limit": 10000.0},
                {"limit": 20000.0},
            ]
        }
        r = self.client.put("/api/budgets", json=payload, headers=self.headers_a)
        self.assertEqual(r.status_code, 422)
        self.assertTrue(any("Общий лимит" in str(e) for e in r.json().get("detail", [])))

        # 2. Повторная категория
        payload2 = {
            "budgets": [
                {"category_id": self.cat("Кафе"), "limit": 10000.0},
                {"category_id": self.cat("Кафе"), "limit": 20000.0},
            ]
        }
        r2 = self.client.put("/api/budgets", json=payload2, headers=self.headers_a)
        self.assertEqual(r2.status_code, 422)
        self.assertTrue(any("указан несколько раз" in str(e) for e in r2.json().get("detail", [])))

    def test_validation_limit_boundaries(self):
        """limit > 0 и не больше 1e12."""
        # limit <= 0
        r = self.client.put(
            "/api/budgets",
            json={"budgets": [{"category_id": None, "limit": 0.0}]},
            headers=self.headers_a,
        )
        self.assertEqual(r.status_code, 422)
        self.assertTrue(any("больше нуля" in str(e) for e in r.json().get("detail", [])))

        r_neg = self.client.put(
            "/api/budgets",
            json={"budgets": [{"category_id": None, "limit": -100.0}]},
            headers=self.headers_a,
        )
        self.assertEqual(r_neg.status_code, 422)

        # limit > 1e12
        r_big = self.client.put(
            "/api/budgets",
            json={"budgets": [{"category_id": None, "limit": 1.1e12}]},
            headers=self.headers_a,
        )
        self.assertEqual(r_big.status_code, 422)
        self.assertTrue(any("не может превышать" in str(e) for e in r_big.json().get("detail", [])))

    def test_validation_max_50_items(self):
        """Больше 50 записей → 422 с текстом на русском."""
        items = [{"category_id": i + 1, "limit": 100.0} for i in range(51)]
        r = self.client.put("/api/budgets", json={"budgets": items}, headers=self.headers_a)
        self.assertEqual(r.status_code, 422)
        self.assertTrue(any("не может превышать 50" in str(e) for e in r.json().get("detail", [])))

    def test_user_isolation(self):
        """Пользователь видит только свои лимиты."""
        self.client.put(
            "/api/budgets",
            json={"budgets": [{"category_id": self.cat("Транспорт"), "limit": 5000.0}]},
            headers=self.headers_a,
        )

        r_b = self.client.get("/api/budgets", headers=self.headers_b)
        self.assertEqual(r_b.status_code, 200)
        self.assertEqual(r_b.json(), [])

    def test_budget_status_calculation(self):
        """P2. GET /api/budgets/status?month=YYYY-MM — расчёт трат на сервере."""
        # Задаем бюджеты: общий 10000, Кафе 3000
        self.client.put(
            "/api/budgets",
            json={
                "budgets": [
                    {"category_id": None, "limit": 10000.0},
                    {"category_id": self.cat("Кафе"), "limit": 3000.0},
                ]
            },
            headers=self.headers_a,
        )

        # Создаём чек за 2026-10:
        # 1 чек: Кафе 2500 (2 позиции: 1500 кофе, 1000 круассан)
        receipt1 = models.Receipt(
            user_id=self.user_a.id,
            store="Кофейня",
            date="2026-10-15",
            time="10:00",
            category_id=self.cat("Кафе"),
            total=2500.0,
        )
        receipt1.items = [
            models.ReceiptItem(name="Кофе", quantity=1.0, price_per_unit=1500.0, total_price=1500.0, category_id=self.cat("Кафе")),
            models.ReceiptItem(name="Круассан", quantity=1.0, price_per_unit=1000.0, total_price=1000.0, category_id=self.cat("Кафе")),
        ]

        # 2 чек: Продукты 3500 (без позиций)
        receipt2 = models.Receipt(
            user_id=self.user_a.id,
            store="Супермаркет",
            date="2026-10-18",
            time="18:30",
            category_id=self.cat("Продукты"),
            total=3500.0,
        )

        # 3 чек за другой месяц (2026-09) — не должен влиять на октябрь
        receipt3 = models.Receipt(
            user_id=self.user_a.id,
            store="Супермаркет",
            date="2026-09-10",
            time="12:00",
            category_id=self.cat("Кафе"),
            total=5000.0,
        )

        self.db.add_all([receipt1, receipt2, receipt3])
        self.db.commit()

        # Запрашиваем статус за 2026-10
        r = self.client.get("/api/budgets/status?month=2026-10", headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        data = r.json()

        self.assertEqual(data["month"], "2026-10")
        self.assertEqual(data["total_budget"], 10000.0)
        self.assertEqual(data["total_spent"], 6000.0)  # 2500 + 3500

        budgets = data["budgets"]
        self.assertEqual(len(budgets), 2)

        # Общий бюджет: limit 10000, spent 6000, remaining 4000, percent 60%
        gen = budgets[0]
        self.assertIsNone(gen["category"])
        self.assertEqual(gen["limit"], 10000.0)
        self.assertEqual(gen["spent"], 6000.0)
        self.assertEqual(gen["remaining"], 4000.0)
        self.assertEqual(gen["percent"], 60.0)
        self.assertFalse(gen["is_over_budget"])

        # Кафе: limit 3000, spent 2500, remaining 500, percent 83.3%
        cafe = budgets[1]
        self.assertEqual(cafe["category"], "Кафе")
        self.assertEqual(cafe["category_id"], self.cat("Кафе"))
        self.assertEqual(cafe["limit"], 3000.0)
        self.assertEqual(cafe["spent"], 2500.0)
        self.assertEqual(cafe["remaining"], 500.0)
        self.assertEqual(cafe["percent"], 83.3)
        self.assertFalse(cafe["is_over_budget"])

        # Проверка неверного формата месяца
        r_bad = self.client.get("/api/budgets/status?month=invalid", headers=self.headers_a)
        self.assertEqual(r_bad.status_code, 422)

    def test_cascade_delete_with_user(self):
        """Лимиты удаляются вместе с пользователем (cascade)."""
        self.client.put(
            "/api/budgets",
            json={"budgets": [{"category_id": self.cat("Развлечения"), "limit": 5000.0}]},
            headers=self.headers_a,
        )
        self.assertEqual(self.db.query(models.Budget).filter(models.Budget.user_id == self.user_a.id).count(), 1)

        # Удаляем пользователя через delete_me
        r_del = self.client.request(
            "DELETE",
            "/api/auth/me",
            json={"password": "password123"},
            headers=self.headers_a,
        )
        self.assertEqual(r_del.status_code, 200)

        # Проверяем, что бюджет удалился
        self.assertEqual(self.db.query(models.Budget).filter(models.Budget.user_id == self.user_a.id).count(), 0)


if __name__ == "__main__":
    unittest.main()
