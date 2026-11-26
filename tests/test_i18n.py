"""Три языка ответов API: ru, kk, en.

Без Accept-Language всё по-русски и как раньше — это проверяют остальные тесты, не меняя ожиданий.
"""
import os
import re
import string
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import testenv  # noqa: F401 — временная база и папки, до импорта app.main

from sqlalchemy import create_engine, text

from apptest import AppTestCase
from app import config, crud, database, insights, models
from app.categories import DEFAULT_CATEGORIES
from app.currencies import CURRENCIES
from app.i18n import (
    CATEGORY_NAMES, LANGS, MESSAGES, VALIDATION_MESSAGES, looks_like_language, normalize_lang,
    parse_accept_language, tr,
)

TODAY = datetime.now(timezone.utc).date()
MONTH = TODAY.isoformat()[:7]
KK = {"Accept-Language": "kk"}
EN = {"Accept-Language": "en"}
CYRILLIC = re.compile(r"[а-яё]", re.I)
KAZAKH = re.compile(r"[әғқңөұүһі]", re.I)


def placeholders(template: str) -> set:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


class TestMessages(unittest.TestCase):
    def test_every_code_has_three_languages_with_same_params(self):
        for code, texts in MESSAGES.items():
            with self.subTest(code=code):
                self.assertEqual(set(texts), set(LANGS))
                self.assertTrue(all(t.strip() for t in texts.values()))
                self.assertEqual(placeholders(texts["kk"]), placeholders(texts["ru"]))
                self.assertEqual(placeholders(texts["en"]), placeholders(texts["ru"]))

    def test_translations_are_in_their_language(self):
        for code, texts in MESSAGES.items():
            with self.subTest(code=code):
                self.assertTrue(looks_like_language(texts["en"], "en"), texts["en"])
                self.assertNotRegex(texts["en"], CYRILLIC)

    def test_pydantic_messages(self):
        # Для ru намеренно нет: остаётся прежний текст Pydantic (правило 0.1)
        for code, texts in VALIDATION_MESSAGES.items():
            with self.subTest(code=code):
                self.assertEqual(set(texts), {"kk", "en"})
                self.assertEqual(placeholders(texts["kk"]), placeholders(texts["en"]))

    def test_category_names_match_defaults(self):
        self.assertEqual([d["key"] for d in DEFAULT_CATEGORIES], list(CATEGORY_NAMES)[:-1])
        for d in DEFAULT_CATEGORIES:
            self.assertEqual(CATEGORY_NAMES[d["key"]]["ru"], d["name"])
        self.assertEqual(CATEGORY_NAMES["uncategorized"]["kk"], "Санатсыз")

    def test_tr(self):
        self.assertEqual(tr("file_too_big", "en", mb=10), "File is too large. Maximum is 10 MB")
        self.assertEqual(tr("file_too_big", None, mb=10), "Файл слишком большой. Максимум — 10 МБ")
        self.assertEqual(tr("file_too_big", "de", mb=10), "Файл слишком большой. Максимум — 10 МБ")
        self.assertEqual(tr("no_such_code", "en"), "no_such_code")


class TestAcceptLanguage(unittest.TestCase):
    def test_parse(self):
        cases = {
            None: "ru", "": "ru", "ru": "ru", "kk": "kk", "kk-KZ": "kk", "kz": "kk", "KZ": "kk",
            "en-US,en;q=0.9": "en", "de": "ru", "fr, kk;q=0.5": "kk", "en;q=0.2, kk;q=0.8": "kk",
            "kk;q=0, en": "en", "*": "ru", "ru-RU,ru;q=0.9,en;q=0.8": "ru", "en;q=abc": "en",
        }
        for header, lang in cases.items():
            with self.subTest(header=header):
                self.assertEqual(parse_accept_language(header), lang)

    def test_normalize(self):
        self.assertEqual(normalize_lang("KK"), "kk")
        self.assertEqual(normalize_lang("kz"), "kk")
        self.assertIsNone(normalize_lang("de"))
        self.assertIsNone(normalize_lang(None))


class TestLanguageCheck(unittest.TestCase):
    def test_en(self):
        self.assertTrue(looks_like_language("Cafés: limit exceeded by 7,000 ₸", "en"))
        self.assertFalse(looks_like_language("Кафе: лимит превышен на 7 000 ₸", "en"))
        # Название магазина на кириллице в английском тексте не мешает
        self.assertTrue(looks_like_language("12 small purchases at “Магнум” this month, they added up", "en"))

    def test_kk(self):
        self.assertTrue(looks_like_language("Осы айда шамамен 50 000 ₸ жұмсалды, бұл әдеттегіден көп.", "kk"))
        self.assertFalse(looks_like_language(
            "В этом месяце потрачено 50 000 ₸, это заметно больше, чем обычно бывает у вас.", "kk"))
        self.assertFalse(looks_like_language("This month you spent a lot more than usual on cafés.", "kk"))
        self.assertTrue(looks_like_language("Кафе", "kk"))  # короткий текст — только кириллица

    def test_ru_and_empty(self):
        self.assertTrue(looks_like_language("anything", "ru"))
        self.assertTrue(looks_like_language("", "en"))
        self.assertTrue(looks_like_language("12 500 ₸", "kk"))


class TestErrors(AppTestCase):
    def test_404_in_three_languages(self):
        for headers, detail in (({}, "Чек не найден"), (KK, "Чек табылмады"), (EN, "Receipt not found")):
            r = self.client.get("/api/receipts/999999", headers={**self.headers_a, **headers})
            self.assertEqual(r.status_code, 404)
            self.assertEqual(r.json(), {"detail": detail, "code": "receipt_not_found"})

    def test_401(self):
        r = self.client.get("/api/auth/me", headers=EN)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"detail": "Authorization required", "code": "auth_required"})
        self.assertEqual(r.headers.get("WWW-Authenticate"), "Bearer")

    def test_422_own_validator(self):
        payload = {"items": [{"name": "Хлеб", "quantity": 0}]}
        expected = {None: "Количество должно быть больше нуля", "kk": "Саны нөлден үлкен болуы керек",
                    "en": "Quantity must be greater than zero"}
        for lang, msg in expected.items():
            headers = {**self.headers_a, **({"Accept-Language": lang} if lang else {})}
            r = self.client.post("/api/receipts/save", json=payload, headers=headers)
            self.assertEqual(r.status_code, 422)
            detail = r.json()["detail"]
            self.assertIsInstance(detail, list)
            self.assertEqual(detail[0]["msg"], msg)
            self.assertEqual(detail[0]["type"], "value_error")
            self.assertEqual(detail[0]["code"], "quantity_not_positive")
            self.assertEqual(detail[0]["loc"], ["body", "items", 0, "quantity"])

    def test_422_params(self):
        r = self.client.post("/api/auth/register", json={"email": "x@example.com", "password": "short"}, headers=EN)
        self.assertEqual(r.json()["detail"][0]["msg"], "Password must be at least 8 characters")
        r = self.client.patch("/api/auth/me", json={"base_currency": "XXX"}, headers={**self.headers_a, **KK})
        self.assertEqual(r.json()["detail"][0]["msg"], "«XXX» валютасы қолданылмайды")

    def test_422_builtin(self):
        for lang, msg in ((None, "Field required"), ("kk", "Міндетті өріс"), ("en", "Required field")):
            headers = {"Accept-Language": lang} if lang else {}
            r = self.client.post("/api/auth/login", json={"email": "a@b.c"}, headers=headers)
            self.assertEqual(r.status_code, 422)
            detail = r.json()["detail"]
            self.assertIsInstance(detail, list)
            self.assertEqual(detail[0]["type"], "missing")
            self.assertEqual(detail[0]["msg"], msg)
            self.assertNotIn("code", detail[0])

    def test_429_translated_with_retry_after(self):
        for _ in range(10):
            self.client.post("/api/auth/login", json={"email": "a@b.c", "password": "x"})
        r = self.client.post("/api/auth/login", json={"email": "a@b.c", "password": "x"}, headers=KK)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["code"], "too_many_requests")
        self.assertTrue(KAZAKH.search(r.json()["detail"]))
        self.assertIn("Retry-After", r.headers)

    def test_413_in_middleware(self):
        r = self.client.post("/api/analyze", content=b"x", headers={
            **EN, "content-length": str(config.MAX_UPLOAD_MB * 1024 * 1024 * 2), "content-type": "multipart/form-data"})
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()["code"], "file_too_big")
        self.assertEqual(r.json()["detail"], f"File is too large. Maximum is {config.MAX_UPLOAD_MB} MB")

    def test_category_conflict_uses_display_name(self):
        r = self.client.post("/api/categories", json={"name": "продукты"}, headers={**self.headers_a, **EN})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json(), {"detail": "Category “Groceries” already exists", "code": "category_exists"})
        r = self.client.post("/api/categories", json={"name": "продукты"}, headers=self.headers_a)
        self.assertEqual(r.json()["detail"], "Категория «Продукты» уже есть")

    def test_duplicate_409(self):
        fields = {"store": "Magnum", "date": "2026-11-01", "total": 500, "items": [{"name": "Хлеб", "total_price": 500}]}
        self.save(**fields)
        r = self.client.post("/api/receipts/save", json=fields, headers={**self.headers_a, **EN})
        self.assertEqual(r.status_code, 409)
        body = r.json()
        self.assertEqual(body["detail"], "Looks like this receipt is already saved")
        self.assertEqual(body["code"], "duplicate_receipt")
        self.assertIn("duplicate_of", body)


class TestPreferencesLanguage(AppTestCase):
    def me(self):
        return self.client.get("/api/auth/me", headers=self.headers_a).json()

    def patch_me(self, prefs):
        return self.client.patch("/api/auth/me", headers=self.headers_a, json={"preferences": prefs})

    def test_not_set_by_default(self):
        # Ключа нет: фронт оставляет язык устройства, а не переключает на русский
        self.assertNotIn("language", self.me()["preferences"])

    def test_change_keeps_theme_and_accent(self):
        self.assertEqual(self.patch_me({"theme": "light", "accent": "red"}).status_code, 200)
        r = self.patch_me({"language": "kk"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["user"]["preferences"],
                         {"theme": "light", "accent": "red", "ai_insights": False, "language": "kk"})
        self.assertEqual(self.me()["preferences"]["language"], "kk")
        # Смена темы не сбрасывает язык
        self.patch_me({"theme": "dark"})
        self.assertEqual(self.me()["preferences"]["language"], "kk")

    def test_alias_and_case(self):
        self.assertEqual(self.patch_me({"language": "KZ"}).status_code, 200)
        self.assertEqual(self.me()["preferences"]["language"], "kk")
        self.assertEqual(self.patch_me({"language": "EN"}).status_code, 200)
        self.assertEqual(self.me()["preferences"]["language"], "en")

    def test_unknown(self):
        r = self.patch_me({"language": "de"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"][0]["msg"], "Неизвестный язык. Доступны: ru, kk, en")
        r = self.client.patch("/api/auth/me", headers={**self.headers_a, **EN}, json={"preferences": {"language": "kk-KZ"}})
        self.assertEqual(r.json()["detail"][0]["msg"], "Unknown language. Available: ru, kk, en")
        self.assertNotIn("language", self.me()["preferences"])

    def test_null_resets(self):
        self.patch_me({"language": "en"})
        self.assertEqual(self.patch_me({"language": None}).status_code, 200)
        self.assertNotIn("language", self.me()["preferences"])

    def test_broken_stored_value(self):
        self.user_a.preferences = {"theme": "dark", "language": "de"}
        self.db.commit()
        self.assertEqual(self.me()["preferences"], {"theme": "dark", "accent": "violet", "ai_insights": False})

    def test_login_returns_language(self):
        self.patch_me({"language": "en"})
        r = self.client.post("/api/auth/login", json={"email": self.EMAILS[0], "password": "password123"})
        self.assertEqual(r.json()["user"]["preferences"]["language"], "en")


class TestCategoryDefaultKey(AppTestCase):
    def categories(self):
        return {c["name"]: c for c in self.client.get("/api/categories", headers=self.headers_a).json()}

    def test_defaults_have_keys(self):
        cats = self.categories()
        self.assertEqual({d["name"]: d["key"] for d in DEFAULT_CATEGORIES},
                         {name: c["default_key"] for name, c in cats.items()})
        r = self.client.get("/api/categories/defaults")
        self.assertEqual([c["default_key"] for c in r.json()], [d["key"] for d in DEFAULT_CATEGORIES])
        self.assertEqual(r.json()[0]["name"], "Продукты")
        self.assertNotIn("key", r.json()[0])

    def test_new_category_has_no_key(self):
        r = self.client.post("/api/categories", json={"name": "Pets"}, headers=self.headers_a)
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(r.json()["default_key"])

    def test_rename_resets_icon_does_not(self):
        cafe, health = self.category("Кафе"), self.category("Здоровье")
        r = self.client.patch(f"/api/categories/{cafe.id}", json={"name": "Food"}, headers=self.headers_a)
        self.assertIsNone(r.json()["default_key"])
        r = self.client.patch(f"/api/categories/{health.id}", json={"icon": "💊"}, headers=self.headers_a)
        self.assertEqual(r.json()["default_key"], "health")

    def test_same_values_do_not_reset(self):
        c = self.category("Транспорт")
        r = self.client.patch(f"/api/categories/{c.id}", headers=self.headers_a,
                              json={"name": c.name, "description": c.description, "icon": c.icon})
        self.assertEqual(r.json()["default_key"], "transport")

    def test_description_change_resets(self):
        c = self.category("Одежда")
        r = self.client.patch(f"/api/categories/{c.id}", json={"description": "Только обувь"}, headers=self.headers_a)
        self.assertIsNone(r.json()["default_key"])
        self.assertEqual(r.json()["name"], "Одежда")

    def test_guest_category_by_russian_name(self):
        out = self.save(headers={**self.headers_a, **KK}, store="Magnum", date="2026-11-02", total=300,
                        items=[{"name": "Нан", "total_price": 300, "category": "Продукты"}])
        self.assertEqual(out["items"][0]["category_id"], self.category("Продукты").id)
        self.assertEqual(out["items"][0]["category"], "Продукты")


class TestCurrencies(AppTestCase):
    def names(self, headers=None):
        return {c["code"]: c["name"] for c in self.client.get("/api/currencies", headers=headers or {}).json()}

    def test_languages(self):
        self.assertEqual(self.names()["KZT"], "Казахстанский тенге")
        self.assertEqual(self.names(EN)["KZT"], "Kazakhstani tenge")
        self.assertEqual(self.names(KK)["USD"], "АҚШ доллары")
        self.assertEqual(set(self.names(EN)), set(CURRENCIES))

    def test_format_unchanged(self):
        data = self.client.get("/api/currencies", headers=EN).json()
        self.assertEqual(set(data[0]), {"code", "symbol", "name", "digits"})


def fake_advisor(snapshot, provider="gemini"):
    """Старый generate_insights без language: пишет по-русски."""
    cat = snapshot["categories"][0]["id"]
    return {"insights": {"summary": "Траты под контролем, но кафе растёт.", "items": [
        {"id": f"over_budget:{cat}:{MONTH}", "type": "over_budget", "priority": 1, "title": "Кафе: лимит превышен",
         "text": "Потрачено больше лимита в этом месяце.", "category_id": cat, "action": {"kind": "none"}},
        {"id": "r1", "type": "recurring", "priority": 2, "title": "Подписка", "text": "Каждый месяц одна сумма.",
         "action": {"kind": "none"}},
    ]}, "model": "gemini-test"}


class InsightsI18nCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        advisor = patch.object(insights, "_advisor", lambda: fake_advisor)
        advisor.start()
        self.addCleanup(advisor.stop)
        self.cafe = self.category("Кафе")
        r = self.client.patch("/api/auth/me", headers=self.headers_a, json={"ai_insights_consent": True})
        self.assertEqual(r.status_code, 200)
        self.save(store="Coffee Boom", date=TODAY.isoformat(), total=30000, allow_duplicate=True,
                  items=[{"name": "Кофе", "total_price": 30000, "category_id": self.cafe.id}])
        self.client.put("/api/budgets", headers=self.headers_a,
                        json={"budgets": [{"category_id": self.cafe.id, "limit": 23000}]})

    def run_analysis(self, headers):
        r = self.client.post("/api/insights", headers={**self.headers_a, **headers})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(self.wait_task(r.json()["task_id"])["status"], "completed")
        return self.client.get("/api/insights", headers={**self.headers_a, **headers}).json()


class TestInsightsLanguages(InsightsI18nCase):
    def test_get_other_language_is_null(self):
        out = self.run_analysis({})
        self.assertEqual(out["model"], "gemini-test")
        self.assertEqual(self.client.get("/api/insights", headers={**self.headers_a, **EN}).json(), {"insights": None})

    def test_kk_after_ru_is_not_429(self):
        self.run_analysis({})
        out = self.run_analysis(KK)
        # Русский ответ ИИ на казахском интерфейсе отбракован — шаблоны на казахском
        self.assertEqual(out["model"], "rules")
        over = next(i for i in out["insights"]["items"] if i["type"] == "over_budget")
        self.assertEqual(over["title"], "Кафе: лимит 7 000 ₸ асып кетті")
        self.assertEqual(over["id"], f"over_budget:{self.cafe.id}:{MONTH}")
        self.assertTrue(KAZAKH.search(out["insights"]["summary"]))
        # Повтор на kk — уже лимит; русский анализ на месте
        r = self.client.post("/api/insights", headers={**self.headers_a, **KK})
        self.assertIn(r.status_code, (200, 429))
        self.assertEqual(self.client.get("/api/insights", headers=self.headers_a).json()["model"], "gemini-test")

    def test_en_rules_text(self):
        out = self.run_analysis(EN)
        self.assertEqual(out["model"], "rules")
        over = next(i for i in out["insights"]["items"] if i["type"] == "over_budget")
        self.assertEqual(over["title"], "Cafés & restaurants: limit exceeded by 7,000 ₸")
        self.assertEqual(over["text"], "Spent 30,000 ₸ against a limit of 23,000 ₸.")
        for item in out["insights"]["items"]:
            self.assertNotRegex(item["title"] + item["text"], CYRILLIC)
        self.assertNotRegex(out["insights"]["summary"], CYRILLIC)

    def test_429_text_translated(self):
        with patch.object(config, "INSIGHTS_REFRESH_MINUTES", 360):
            self.run_analysis(EN)
            self.save(store="Coffee Boom", date=TODAY.isoformat(), total=1000, allow_duplicate=True,
                      items=[{"name": "Кофе", "total_price": 1000, "category_id": self.cafe.id}])
            r = self.client.post("/api/insights", headers={**self.headers_a, **EN})
        self.assertEqual(r.status_code, 429)
        self.assertRegex(r.json()["detail"], r"^You can refresh the tips in \d+ h$")
        self.assertEqual(r.json()["code"], "insights_retry_hours")
        self.assertIn("retry_after", r.json())

    def test_advisor_with_language_gets_it(self):
        seen = {}

        def advisor(snapshot, provider="gemini", language="ru"):
            seen.update(language=language, snapshot_language=snapshot.get("language"),
                        names={c["name"]: c.get("display_name") for c in snapshot["categories"]})
            cat = snapshot["categories"][0]["id"]
            return {"insights": {"summary": "Spending is under control, cafés are growing.", "items": [
                {"id": "a", "type": "recurring", "priority": 1, "title": "Subscription", "text": "Same amount monthly.",
                 "action": {"kind": "none"}},
                {"id": "b", "type": "over_budget", "priority": 2, "title": "Cafés over limit", "text": "Spent more.",
                 "category_id": cat, "action": {"kind": "none"}},
            ]}, "model": "gemini-en"}

        with patch.object(insights, "_advisor", lambda: advisor):
            out = self.run_analysis(EN)
        self.assertEqual(seen["language"], "en")
        self.assertEqual(seen["snapshot_language"], "en")
        self.assertEqual(seen["names"], {"Кафе": "Cafés & restaurants"})
        self.assertEqual(out["model"], "gemini-en")
        self.assertFalse(out["stale"])


class TestSnapshotLanguage(InsightsI18nCase):
    def test_display_name_and_hash(self):
        plain = crud.build_finance_snapshot(self.db, self.user_a)
        ru = crud.build_finance_snapshot(self.db, self.user_a, lang="ru")
        kk = crud.build_finance_snapshot(self.db, self.user_a, lang="kk")
        self.assertNotIn("language", plain)
        self.assertEqual(kk["language"], "kk")
        self.assertEqual(kk["categories"][0]["name"], "Кафе")
        self.assertEqual(kk["categories"][0]["display_name"], "Кафе")
        en = crud.build_finance_snapshot(self.db, self.user_a, lang="en")
        self.assertEqual(en["categories"][0]["display_name"], "Cafés & restaurants")
        # Хеш русского анализа — тот же, что до перевода: после выкладки анализы не устаревают
        self.assertEqual(crud.snapshot_hash(plain), crud.snapshot_hash(ru))
        self.assertNotEqual(crud.snapshot_hash(plain), crud.snapshot_hash(en))

    def test_renamed_category_keeps_own_name(self):
        self.client.patch(f"/api/categories/{self.cafe.id}", json={"name": "Кофейни"}, headers=self.headers_a)
        self.db.expire_all()
        en = crud.build_finance_snapshot(self.db, self.user_a, lang="en")
        self.assertEqual(en["categories"][0]["display_name"], "Кофейни")


class TestSanitizeLanguage(unittest.TestCase):
    SNAP = {"categories": [{"id": 1, "name": "Кафе"}]}

    def raw(self, summary, texts):
        return {"insights": {"summary": summary, "items": [
            {"id": str(n), "type": "recurring", "title": t, "text": t} for n, t in enumerate(texts)]}}

    def test_wrong_language_rejected(self):
        with self.assertRaises(ValueError):
            insights.sanitize(self.raw("Траты под контролем.", ["Подписка каждый месяц"] * 2), self.SNAP, "en")
        with self.assertRaises(ValueError):
            insights.sanitize(self.raw("Fine.", ["Подписка", "Подписка", "Subscription"]), self.SNAP, "en")

    def test_right_language_passes(self):
        _, out = insights.sanitize(self.raw("All good.", ["Subscription", "Подписка"]), self.SNAP, "en")
        self.assertEqual(len(out["items"]), 2)
        _, out = insights.sanitize(self.raw("Траты под контролем.", ["Подписка"]), self.SNAP)
        self.assertEqual(out["summary"], "Траты под контролем.")

    def test_rules_kk_money_and_months(self):
        snap = {"base_currency": "USD", "today": "2026-11-16", "language": "kk",
                "months": [{"month": "2026-11", "total": 12500.5, "receipts": 1}],
                "categories": [], "total_limit": None, "small_purchases": [], "unconverted": []}
        self.assertEqual(insights.rule_insights(snap)["summary"], "Қарашада 12 500,50 $ жұмсалды.")
        self.assertEqual(insights.rule_insights(snap, lang="en")["summary"], "You spent 12,500.50 $ in November.")
        self.assertEqual(insights.rule_insights(snap, lang="ru")["summary"], "В ноябре потрачено 12 500,50 $.")


class TestI18nMigrations(unittest.TestCase):
    """Миграции 0011 и 0012 на отдельной базе SQLite: рабочая и тестовая базы не трогаются."""

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

    def test_default_key_filled_only_for_untouched(self):
        self.command.upgrade(self.cfg, "0010_receipt_duplicates")
        self.conn.execute(text("INSERT INTO users (id, email, hashed_password) VALUES (1, 'a@x', 'h')"))
        rows = [(n, d["name"], d["description"]) for n, d in enumerate(DEFAULT_CATEGORIES)]
        rows[1] = (1, "Одежда и обувь", DEFAULT_CATEGORIES[1]["description"])     # переименована
        rows[2] = (2, DEFAULT_CATEGORIES[2]["name"], "Только посуда")             # другое описание
        for pos, name, description in rows:
            self.conn.execute(text(
                "INSERT INTO categories (user_id, name, description, icon, position) VALUES (1, :n, :d, '◌', :p)"
            ), {"n": name, "d": description, "p": pos})
        self.conn.execute(text(
            "INSERT INTO categories (user_id, name, description, icon, position) VALUES (1, 'Косметика', NULL, '◌', 9)"))
        self.conn.execute(text(
            "INSERT INTO insights (user_id, created_at, model, data, snapshot_hash) "
            "VALUES (1, '2026-11-01 00:00:00', 'rules', '{}', 'h')"))
        self.conn.commit()

        self.command.upgrade(self.cfg, "head")
        keys = dict(self.sql("SELECT name, default_key FROM categories ORDER BY position"))
        self.assertEqual(keys["Продукты"], "groceries")
        self.assertEqual(keys["Другое"], "other")
        self.assertIsNone(keys["Одежда и обувь"])
        self.assertIsNone(keys["Дом и быт"])
        self.assertIsNone(keys["Косметика"])
        self.assertEqual(self.sql("SELECT language FROM insights")[0].language, "ru")

        # Вниз-вверх: без ошибок, данные на месте
        self.command.downgrade(self.cfg, "-1")
        self.command.downgrade(self.cfg, "-1")
        self.assertNotIn("default_key", [r[1] for r in self.sql("PRAGMA table_info(categories)")])
        self.assertNotIn("language", [r[1] for r in self.sql("PRAGMA table_info(insights)")])
        self.command.upgrade(self.cfg, "head")
        self.assertEqual(len(self.sql("SELECT id FROM categories")), 9)
        self.assertEqual(dict(self.sql("SELECT name, default_key FROM categories"))["Кафе"], "cafe")
        self.assertEqual(len(self.sql("SELECT id FROM insights")), 1)


if __name__ == "__main__":
    unittest.main()
