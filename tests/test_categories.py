import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

os.environ.setdefault("SECRET_KEY", "test_secret_key_1234567890_antigravity_safe_key_32chars")

import server
from server import app
from app import database, models, auth
from app.rate_limit import limiter
from app.categories import DEFAULT_CATEGORIES, create_default_categories, normalize_item_name

EMAILS = ["test_categories_a@example.com", "test_categories_b@example.com", "test_categories_reg@example.com"]


class TestNormalizeItemName(unittest.TestCase):
    def test_examples(self):
        """Проверочные примеры из CATEGORIES_TASKS.md, п. 0.6."""
        cases = {
            "Крем NIVEA 50мл": "крем nivea",
            "Крем Nivea 100 мл": "крем nivea",
            "2 груши": "груши",
            "Молоко 2,5% 1л": "молоко",
            "Шоколад Alpen Gold 85г": "шоколад alpen gold",
            "Ёлка": "елка",
            "": "",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_item_name(raw), expected, raw)


class TestCategoriesAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        database.init_db()

    def setUp(self):
        # Чеки в KZT докачивают ленту курсов в фоне — в тестах в сеть не ходим
        bg = patch("app.rates.run_in_background")
        bg.start()
        self.addCleanup(bg.stop)
        limiter.clear()

        self.db: Session = database.SessionLocal()
        self._delete_users()
        hashed_pw = auth.hash_password("password123")
        self.user_a = models.User(email=EMAILS[0], hashed_password=hashed_pw)
        self.user_b = models.User(email=EMAILS[1], hashed_password=hashed_pw)
        create_default_categories(self.db, self.user_a)
        create_default_categories(self.db, self.user_b)
        self.db.add_all([self.user_a, self.user_b])
        self.db.commit()
        self.headers_a = {"Authorization": f"Bearer {auth.issue_tokens(self.user_a)[0]}"}
        self.headers_b = {"Authorization": f"Bearer {auth.issue_tokens(self.user_b)[0]}"}

    def tearDown(self):
        self._delete_users()
        self.db.close()

    def _delete_users(self):
        # Категории могли удалить через API — в сессии теста не должно остаться их копий
        self.db.expire_all()
        for email in EMAILS:
            u = self.db.query(models.User).filter(models.User.email == email).first()
            if u:
                self.db.delete(u)
        self.db.commit()

    # --- помощники ---

    def cat(self, name, user=None):
        user = user or self.user_a
        self.db.expire_all()
        return next(c.id for c in user.categories if c.name == name)

    def save(self, items, headers=None, **fields):
        payload = {"store": "Magnum", "date": "2026-10-20", "total": sum(i["total_price"] for i in items),
                   "items": items, **fields}
        r = self.client.post("/api/receipts/save", json=payload, headers=headers or self.headers_a)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def get_receipt(self, receipt_id):
        return self.client.get(f"/api/receipts/{receipt_id}", headers=self.headers_a).json()

    def create(self, name, **fields):
        return self.client.post("/api/categories", json={"name": name, **fields}, headers=self.headers_a)

    # --- справочник ---

    def test_register_creates_default_categories(self):
        """Регистрация → 8 стандартных категорий в нужном порядке."""
        r = self.client.post("/api/auth/register", json={"email": EMAILS[2], "password": "password123"})
        self.assertEqual(r.status_code, 200)
        token = r.json()["access_token"]
        cats = self.client.get("/api/categories", headers={"Authorization": f"Bearer {token}"}).json()
        self.assertEqual([c["name"] for c in cats], [d["name"] for d in DEFAULT_CATEGORIES])
        self.assertEqual([c["position"] for c in cats], list(range(8)))
        self.assertEqual(cats[0]["icon"], "🥕")

    def test_defaults_without_token(self):
        r = self.client.get("/api/categories/defaults")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 8)
        self.assertEqual(set(r.json()[0]), {"name", "description", "icon"})

    def test_requires_token(self):
        self.assertEqual(self.client.get("/api/categories").status_code, 401)
        self.assertEqual(self.client.post("/api/categories", json={"name": "X"}).status_code, 401)
        self.assertEqual(self.client.post("/api/items/move", json={"item_ids": [1]}).status_code, 401)
        self.assertEqual(self.client.get("/api/category-rules").status_code, 401)

    def test_create_and_validation(self):
        """Создать «Косметика»; дубль без учёта регистра → 409; 51-я, 51 символ, «Без категории» → 422."""
        r = self.create("  Косметика ", icon="💄", description="Крем, шампунь, тушь")
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual((body["name"], body["icon"], body["position"]), ("Косметика", "💄", 8))

        r_dup = self.create("косметика")
        self.assertEqual(r_dup.status_code, 409)
        self.assertEqual(r_dup.json()["detail"], "Категория «Косметика» уже есть")

        self.assertEqual(self.create("Без КАТЕГОРИИ").status_code, 422)
        self.assertEqual(self.create("x" * 51).status_code, 422)
        self.assertEqual(self.create("   ").status_code, 422)
        self.assertEqual(self.create("Иконка", icon="x" * 9).status_code, 422)
        self.assertEqual(self.create("Описание", description="x" * 301).status_code, 422)

        r_empty = self.create("Пустые поля", icon="", description="  ")
        self.assertEqual((r_empty.json()["icon"], r_empty.json()["description"]), ("◌", None))

        with patch.dict(server.RATE_LIMIT_METHOD_RULES, {("POST", "/api/categories"): (1000, 60)}):
            for i in range(40):  # 8 стандартных + 2 созданных + 40 = 50
                self.assertEqual(self.create(f"Категория {i}").status_code, 201)
            r_limit = self.create("Пятьдесят первая")
        self.assertEqual(r_limit.status_code, 422)
        self.assertEqual(r_limit.json()["detail"], "Можно создать не больше 50 категорий")

    def test_create_rate_limit(self):
        """POST /api/categories — не больше 30 в минуту; GET того же пути не ограничен."""
        codes = [self.create(f"Спам {i}").status_code for i in range(31)]
        self.assertEqual(codes[:30].count(201), 30)
        self.assertEqual(codes[30], 429)
        self.assertEqual(self.client.get("/api/categories", headers=self.headers_a).status_code, 200)

    def test_rename_visible_in_receipts(self):
        """Переименовать «Дом и быт» → позиции показывают новое название."""
        home = self.cat("Дом и быт")
        receipt = self.save([{"name": "Губка", "total_price": 300, "category_id": home}])

        r = self.client.patch(f"/api/categories/{home}", json={"name": "Дом"}, headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "Дом")
        # Описание не прислали — не изменилось
        self.assertEqual(r.json()["description"], DEFAULT_CATEGORIES[2]["description"])

        data = self.get_receipt(receipt["id"])
        self.assertEqual(data["items"][0]["category"], "Дом")
        self.assertEqual(data["category"], "Дом")

        # Своё же название в другом регистре — можно, чужое — 409
        self.assertEqual(self.client.patch(f"/api/categories/{home}", json={"name": "ДОМ"}, headers=self.headers_a).status_code, 200)
        self.assertEqual(self.client.patch(f"/api/categories/{home}", json={"name": "кафе"}, headers=self.headers_a).status_code, 409)

    def test_delete_with_move_to(self):
        """Удалить с move_to → позиции, чеки и правила перешли, лимит бюджета удалён."""
        home, other = self.cat("Дом и быт"), self.cat("Другое")
        receipt = self.save([
            {"name": "Губка", "total_price": 300, "category_id": home},
            {"name": "Мыло", "total_price": 200, "category_id": home},
        ])
        self.client.post("/api/items/move", json={"item_ids": [receipt["items"][0]["id"]], "category_id": home, "remember": True}, headers=self.headers_a)
        self.client.put("/api/budgets", json={"budgets": [{"category_id": home, "limit": 1000}]}, headers=self.headers_a)

        r = self.client.delete(f"/api/categories/{home}?move_to={other}", headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"success": True, "moved_items": 2, "deleted_budget": True})

        data = self.get_receipt(receipt["id"])
        self.assertEqual({i["category_id"] for i in data["items"]}, {other})
        self.assertEqual(data["category_id"], other)
        rules = self.client.get("/api/category-rules", headers=self.headers_a).json()
        self.assertEqual([(x["pattern"], x["category_id"]) for x in rules], [("губка", other)])
        self.assertEqual(self.client.get("/api/budgets", headers=self.headers_a).json(), [])
        self.assertNotIn(home, [c["id"] for c in self.client.get("/api/categories", headers=self.headers_a).json()])

    def test_delete_without_move_to(self):
        """Удалить без move_to → позиции «Без категории», чек пересчитал категорию, правила удалены."""
        home, food = self.cat("Дом и быт"), self.cat("Продукты")
        receipt = self.save([
            {"name": "Губка", "total_price": 500, "category_id": home},
            {"name": "Хлеб", "total_price": 300, "category_id": food},
        ])
        self.assertEqual(receipt["category_id"], home)
        self.client.post("/api/items/move", json={"item_ids": [receipt["items"][0]["id"]], "category_id": home, "remember": True}, headers=self.headers_a)

        r = self.client.delete(f"/api/categories/{home}", headers=self.headers_a)
        self.assertEqual(r.json(), {"success": True, "moved_items": 1, "deleted_budget": False})

        data = self.get_receipt(receipt["id"])
        self.assertEqual([i["category"] for i in data["items"]], [None, "Продукты"])
        # Самая дорогая позиция теперь «Без категории»
        self.assertIsNone(data["category_id"])
        self.assertEqual(self.client.get("/api/category-rules", headers=self.headers_a).json(), [])
        by_cat = self.client.get("/api/analytics", headers=self.headers_a).json()["by_category"]
        self.assertEqual(by_cat, {"Без категории": 500.0, "Продукты": 300.0})

    def test_delete_bad_move_to(self):
        """move_to = сама категория или чужая → 422, ничего не изменилось; чужая категория → 404."""
        home = self.cat("Дом и быт")
        self.save([{"name": "Губка", "total_price": 300, "category_id": home}])
        for move_to in (home, self.cat("Кафе", self.user_b), 999999):
            r = self.client.delete(f"/api/categories/{home}?move_to={move_to}", headers=self.headers_a)
            self.assertEqual(r.status_code, 422, move_to)
        self.assertIn(home, [c["id"] for c in self.client.get("/api/categories", headers=self.headers_a).json()])

        r = self.client.delete(f"/api/categories/{self.cat('Кафе', self.user_b)}", headers=self.headers_a)
        self.assertEqual(r.status_code, 404)
        r = self.client.patch(f"/api/categories/{self.cat('Кафе', self.user_b)}", json={"name": "X"}, headers=self.headers_a)
        self.assertEqual(r.status_code, 404)

    def test_reorder(self):
        """PUT /order: новый порядок; неполный список, дубль или чужой id → 422."""
        ids = [c["id"] for c in self.client.get("/api/categories", headers=self.headers_a).json()]
        new_order = list(reversed(ids))
        r = self.client.put("/api/categories/order", json={"ids": new_order}, headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c["id"] for c in r.json()], new_order)
        self.assertEqual([c["position"] for c in r.json()], list(range(8)))
        self.assertEqual([c["id"] for c in self.client.get("/api/categories", headers=self.headers_a).json()], new_order)

        for bad in (ids[:-1], ids + [ids[0]], ids[:-1] + [self.cat("Кафе", self.user_b)]):
            self.assertEqual(self.client.put("/api/categories/order", json={"ids": bad}, headers=self.headers_a).status_code, 422)

    # --- перенос позиций и правила ---

    def test_move_with_foreign_item_changes_nothing(self):
        """Одна чужая позиция среди своих → 404, ни одна не перенесена."""
        food, cafe = self.cat("Продукты"), self.cat("Кафе")
        mine = self.save([{"name": "Хлеб", "total_price": 300, "category_id": food}])
        theirs = self.save([{"name": "Сыр", "total_price": 900}], headers=self.headers_b)

        r = self.client.post("/api/items/move", json={
            "item_ids": [mine["items"][0]["id"], theirs["items"][0]["id"]], "category_id": cafe, "remember": True,
        }, headers=self.headers_a)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.get_receipt(mine["id"])["items"][0]["category_id"], food)
        self.assertEqual(self.client.get("/api/category-rules", headers=self.headers_a).json(), [])

        # Чужая категория → 422
        r = self.client.post("/api/items/move", json={
            "item_ids": [mine["items"][0]["id"]], "category_id": self.cat("Кафе", self.user_b),
        }, headers=self.headers_a)
        self.assertEqual(r.status_code, 422)

    def test_move_remember_creates_then_updates_rule(self):
        """remember: true → правило создано; повторный перенос в другую категорию — обновлено, не продублировано."""
        cosmetics = self.create("Косметика").json()["id"]
        home = self.cat("Дом и быт")
        receipt = self.save([
            {"name": "Крем NIVEA 50мл", "total_price": 1890, "category_id": home},
            {"name": "Крем Nivea 100 мл", "total_price": 2500, "category_id": home},
            {"name": "1 шт", "total_price": 10, "category_id": home},
        ])
        ids = [i["id"] for i in receipt["items"]]

        r = self.client.post("/api/items/move", json={"item_ids": ids, "category_id": cosmetics, "remember": True}, headers=self.headers_a)
        self.assertEqual(r.json(), {"moved": 3, "rules_created": 1})  # одно название после нормализации, «1 шт» — пусто
        rules = self.client.get("/api/category-rules", headers=self.headers_a).json()
        self.assertEqual([(x["pattern"], x["category"]) for x in rules], [("крем nivea", "Косметика")])
        self.assertEqual(self.get_receipt(receipt["id"])["category_id"], cosmetics)

        r = self.client.post("/api/items/move", json={"item_ids": ids[:1], "category_id": home, "remember": True}, headers=self.headers_a)
        self.assertEqual(r.json(), {"moved": 1, "rules_created": 1})
        rules = self.client.get("/api/category-rules", headers=self.headers_a).json()
        self.assertEqual([(x["pattern"], x["category_id"]) for x in rules], [("крем nivea", home)])
        # Самая дорогая позиция (2500) осталась в «Косметике»
        self.assertEqual(self.get_receipt(receipt["id"])["category_id"], cosmetics)

    def test_move_without_remember_and_to_uncategorized(self):
        food = self.cat("Продукты")
        receipt = self.save([{"name": "Хлеб", "total_price": 300, "category_id": food}])
        item_id = receipt["items"][0]["id"]
        r = self.client.post("/api/items/move", json={"item_ids": [item_id], "category_id": None, "remember": True}, headers=self.headers_a)
        self.assertEqual(r.json(), {"moved": 1, "rules_created": 0})
        data = self.get_receipt(receipt["id"])
        self.assertIsNone(data["items"][0]["category_id"])
        self.assertIsNone(data["category_id"])
        self.assertEqual(self.client.get("/api/category-rules", headers=self.headers_a).json(), [])

    def test_delete_rule(self):
        cafe = self.cat("Кафе")
        receipt = self.save([{"name": "Кофе", "total_price": 900}])
        self.client.post("/api/items/move", json={"item_ids": [receipt["items"][0]["id"]], "category_id": cafe, "remember": True}, headers=self.headers_a)
        rule_id = self.client.get("/api/category-rules", headers=self.headers_a).json()[0]["id"]

        self.assertEqual(self.client.delete(f"/api/category-rules/{rule_id}", headers=self.headers_b).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/category-rules/{rule_id}", headers=self.headers_a).json(), {"success": True})
        self.assertEqual(self.client.get("/api/category-rules", headers=self.headers_a).json(), [])

    def test_rule_applied_on_analyze(self):
        """Правило применяется при распознавании → category_source: "rule"; гость правила не получает."""
        cosmetics = self.create("Косметика").json()["id"]
        receipt = self.save([{"name": "Крем Nivea", "total_price": 1890}])
        self.client.post("/api/items/move", json={"item_ids": [receipt["items"][0]["id"]], "category_id": cosmetics, "remember": True}, headers=self.headers_a)

        def fake_analyze(image_path, ocr=None, categories=None):
            fake_analyze.categories = categories
            return {"store": "Magnum", "raw_lines": ["Крем Nivea 50мл 1890"], "currency": "KZT",
                    "items": [{"name": "Крем Nivea 50мл", "total_price": 1890}, {"name": "Хлеб", "total_price": 300}]}

        with patch("server.get_ocr", return_value=None), patch("server.analyze_receipt", side_effect=fake_analyze):
            files = {"file": ("check.jpg", b"fake-image", "image/jpeg")}
            r = self.client.post("/api/analyze", files=files, headers=self.headers_a)
            self.assertEqual(r.status_code, 200, r.text)
            items = r.json()["data"]["items"]
            self.assertEqual(items[0]["category_id"], cosmetics)
            self.assertEqual(items[0]["category"], "Косметика")
            self.assertEqual(items[0]["category_source"], "rule")
            self.assertEqual((items[1]["category_id"], items[1]["category"], items[1]["category_source"]), (None, None, None))
            # В ML уходят категории пользователя с id
            self.assertIn({"id": cosmetics, "name": "Косметика", "description": None}, fake_analyze.categories)

            r_guest = self.client.post("/api/analyze", files={"file": ("check.jpg", b"fake-image", "image/jpeg")})
            self.assertEqual(r_guest.status_code, 200)
            self.assertIsNone(r_guest.json()["data"]["items"][0]["category_source"])
            self.assertEqual(fake_analyze.categories[0], {"id": None, "name": "Продукты", "description": DEFAULT_CATEGORIES[0]["description"]})

    # --- сохранение чеков ---

    def test_save_resolves_categories(self):
        """Чужой category_id → 422; название без учёта регистра → id; неизвестное название → null."""
        r = self.client.post("/api/receipts/save", json={
            "total": 100, "items": [{"name": "Хлеб", "total_price": 100, "category_id": self.cat("Продукты", self.user_b)}],
        }, headers=self.headers_a)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"], "Категория не найдена")

        data = self.save([
            {"name": "Хлеб", "total_price": 100, "category": "продукты"},
            {"name": "Тушь", "total_price": 50, "category": "Косметика"},
        ])
        self.assertEqual([i["category_id"] for i in data["items"]], [self.cat("Продукты"), None])
        self.assertTrue(all(isinstance(i["id"], int) for i in data["items"]))
        # Категорий новых не появилось
        self.assertEqual(len(self.client.get("/api/categories", headers=self.headers_a).json()), 8)

    def test_receipt_category_rules(self):
        """Категория чека: самая дорогая позиция, при равенстве — первая; без позиций — присланная."""
        food, cafe = self.cat("Продукты"), self.cat("Кафе")
        data = self.save([
            {"name": "Хлеб", "total_price": 300, "category_id": food},
            {"name": "Кофе", "total_price": 300, "category_id": cafe},
        ], category_id=cafe)
        self.assertEqual(data["category_id"], food)  # category_id чека с позициями игнорируется

        empty = self.save([], category_id=cafe)
        self.assertEqual((empty["category_id"], empty["category"]), (cafe, "Кафе"))
        none = self.save([])
        self.assertIsNone(none["category_id"])

        # Редактирование пересчитывает и выдаёт позициям новые id
        r = self.client.put(f"/api/receipts/{data['id']}", json={
            "total": 900, "items": [
                {"name": "Хлеб", "total_price": 300, "category_id": food},
                {"name": "Кофе", "total_price": 600, "category_id": cafe},
            ],
        }, headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["category_id"], cafe)
        self.assertTrue(set(i["id"] for i in r.json()["items"]).isdisjoint(i["id"] for i in data["items"]))

    def test_receipts_filter_by_category_id(self):
        food, cafe = self.cat("Продукты"), self.cat("Кафе")
        r1 = self.save([{"name": "Хлеб", "total_price": 300, "category_id": food}])
        r2 = self.save([{"name": "Кофе", "total_price": 900, "category_id": cafe}, {"name": "?", "total_price": 1}])
        r3 = self.save([], category_id=cafe)

        def ids(category_id):
            res = self.client.get(f"/api/receipts?category_id={category_id}", headers=self.headers_a).json()
            return {x["id"] for x in res}

        self.assertEqual(ids(food), {r1["id"]})
        self.assertEqual(ids(cafe), {r2["id"], r3["id"]})
        self.assertEqual(ids(0), {r2["id"]})

    def test_analytics_and_budget_status_by_category_id(self):
        """Бюджет на свою категорию: перенос позиции в неё увеличивает трату."""
        cosmetics = self.create("Косметика").json()["id"]
        home = self.cat("Дом и быт")
        receipt = self.save([{"name": "Крем", "total_price": 1500, "category_id": home}])
        self.client.put("/api/budgets", json={"budgets": [{"category_id": cosmetics, "limit": 5000}]}, headers=self.headers_a)

        status = self.client.get("/api/budgets/status?month=2026-10", headers=self.headers_a).json()
        self.assertEqual((status["budgets"][0]["category_id"], status["budgets"][0]["spent"]), (cosmetics, 0.0))

        self.client.post("/api/items/move", json={"item_ids": [receipt["items"][0]["id"]], "category_id": cosmetics}, headers=self.headers_a)
        status = self.client.get("/api/budgets/status?month=2026-10", headers=self.headers_a).json()
        self.assertEqual(status["budgets"][0]["spent"], 1500.0)
        self.assertEqual(status["budgets"][0]["category"], "Косметика")
        self.assertEqual(self.client.get("/api/analytics", headers=self.headers_a).json()["by_category"], {"Косметика": 1500.0})

    # --- изоляция и удаление ---

    def test_user_isolation(self):
        """B не видит категории и правила A."""
        self.create("Косметика")
        receipt = self.save([{"name": "Крем", "total_price": 100}])
        self.client.post("/api/items/move", json={"item_ids": [receipt["items"][0]["id"]], "category_id": self.cat("Косметика"), "remember": True}, headers=self.headers_a)

        names_b = [c["name"] for c in self.client.get("/api/categories", headers=self.headers_b).json()]
        self.assertNotIn("Косметика", names_b)
        self.assertEqual(self.client.get("/api/category-rules", headers=self.headers_b).json(), [])

    def test_delete_user_removes_categories_and_rules(self):
        receipt = self.save([{"name": "Кофе", "total_price": 900}])
        self.client.post("/api/items/move", json={"item_ids": [receipt["items"][0]["id"]], "category_id": self.cat("Кафе"), "remember": True}, headers=self.headers_a)
        user_id = self.user_a.id

        r = self.client.request("DELETE", "/api/auth/me", json={"password": "password123"}, headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.db.expire_all()
        self.assertEqual(self.db.query(models.Category).filter(models.Category.user_id == user_id).count(), 0)
        self.assertEqual(self.db.query(models.CategoryRule).filter(models.CategoryRule.user_id == user_id).count(), 0)


class TestCategoriesMigration(unittest.TestCase):
    """Миграция 0005 на старой базе: отдельный файл SQLite, рабочая и тестовая базы не трогаются."""

    def setUp(self):
        from alembic import command

        self.command = command
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}")
        self.conn = self.engine.connect()
        self.cfg = database._alembic_config()
        self.cfg.attributes["connection"] = self.conn

    def tearDown(self):
        self.conn.close()
        self.engine.dispose()
        os.remove(self.path)

    def sql(self, query, **params):
        return self.conn.execute(text(query), params).fetchall()

    def test_upgrade_moves_strings_to_categories_and_downgrade_restores(self):
        self.command.upgrade(self.cfg, "0004_currency")
        self.conn.execute(text("INSERT INTO users (id, email, hashed_password) VALUES (1, 'a@x', 'h'), (2, 'b@x', 'h')"))
        self.conn.execute(text(
            "INSERT INTO receipts (id, user_id, category, total, currency) VALUES "
            "(1, 1, 'Продукты', 600, 'KZT'), (2, 1, 'Прочее', 50, 'KZT'), (3, 1, 'Косметика', 100, 'KZT'), "
            "(4, 2, 'кафе', 10, 'KZT')"
        ))
        self.conn.execute(text(
            "INSERT INTO receipt_items (id, receipt_id, name, total_price, category) VALUES "
            "(1, 1, 'Хлеб', 300, 'Продукты'), (2, 1, 'Губка', 200, ' Дом и быт '), (3, 1, 'Что-то', 100, NULL), "
            "(4, 3, 'Крем', 100, 'Косметика'), (5, 4, 'Кофе', 10, NULL)"
        ))
        self.conn.execute(text(
            'INSERT INTO budgets (id, user_id, category, "limit") VALUES '
            "(1, 1, NULL, 1000), (2, 1, 'Прочее', 100), (3, 1, 'Другое', 200), (4, 1, 'Косметика', 300)"
        ))
        self.conn.commit()

        self.command.upgrade(self.cfg, "head")

        names_a = [r.name for r in self.sql("SELECT name FROM categories WHERE user_id = 1 ORDER BY position")]
        self.assertEqual(names_a, [d["name"] for d in DEFAULT_CATEGORIES] + ["Косметика"])
        self.assertEqual(len(self.sql("SELECT id FROM categories WHERE user_id = 2")), 8)

        def item_cat(item_id):
            return self.sql(
                "SELECT c.name FROM receipt_items i LEFT JOIN categories c ON c.id = i.category_id WHERE i.id = :id",
                id=item_id,
            )[0].name

        self.assertEqual([item_cat(i) for i in (1, 2, 3, 4)], ["Продукты", "Дом и быт", "Продукты", "Косметика"])
        self.assertEqual(item_cat(5), "Кафе")  # позиция без категории получила категорию чека «кафе» → «Кафе»
        receipt_cats = self.sql(
            "SELECT r.id, c.name FROM receipts r LEFT JOIN categories c ON c.id = r.category_id ORDER BY r.id"
        )
        self.assertEqual([r.name for r in receipt_cats], ["Продукты", "Другое", "Косметика", "Кафе"])
        budgets = self.sql(
            'SELECT b.id, c.name, b."limit" FROM budgets b LEFT JOIN categories c ON c.id = b.category_id ORDER BY b.id'
        )
        # «Прочее» и «Другое» слились — остался первый лимит
        self.assertEqual([(b.id, b.name, b.limit) for b in budgets], [(1, None, 1000), (2, "Другое", 100), (4, "Косметика", 300)])
        self.assertNotIn("category", [r[1] for r in self.sql("PRAGMA table_info(receipt_items)")])
        self.assertEqual(self.sql("PRAGMA foreign_key_check"), [])

        self.command.downgrade(self.cfg, "0004_currency")
        self.assertEqual(
            [r.category for r in self.sql("SELECT category FROM receipt_items ORDER BY id")],
            ["Продукты", "Дом и быт", "Продукты", "Косметика", "Кафе"],
        )
        self.assertNotIn("categories", [r.name for r in self.sql("SELECT name FROM sqlite_master WHERE type = 'table'")])


if __name__ == "__main__":
    unittest.main()
