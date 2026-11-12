"""ИИ-рекомендации: сводка, согласие, генерация в фоне, кеш, 429, stale (NEXT_WEEK_TASKS.md, B.2).

ИИ подменяется заглушкой (insights._advisor): в тестах наружу ничего не уходит.
"""
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import testenv  # noqa: F401 — временная база и папки, до импорта server

from apptest import AppTestCase, PASSWORD
from app import config, crud, insights, models

# Сводка считает «сегодня» в UTC
TODAY = datetime.now(timezone.utc).date()
MONTH = TODAY.isoformat()[:7]


def month_day(months_back: int, day: int = 3) -> str:
    """День в прошлом месяце (months_back = 0 — этот месяц)."""
    y, m = TODAY.year, TODAY.month - months_back
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, min(day, 28)).isoformat()


def fake_advisor(snapshot, provider="gemini"):
    cat = snapshot["categories"][0]["id"] if snapshot["categories"] else None
    return {
        "insights": {
            "summary": "Тест",
            "items": [
                {"id": f"over_budget:{cat}:{MONTH}", "type": "over_budget", "priority": 2, "title": "Кафе",
                 "text": "Потрачено много.", "category_id": cat, "action": {"kind": "open_history", "category_id": cat}},
                {"id": "x1", "type": "budget_suggestion", "priority": 1, "title": "Лимит", "text": "Поставьте лимит.",
                 "category_id": 999999, "action": {"kind": "set_budget", "category_id": 999999, "amount": 25000}},
                {"id": "x2", "type": "made_up", "title": "Нет такого типа", "text": "..."},
                {"id": "x1", "type": "recurring", "title": "Повтор id", "text": "..."},
                {"id": "x3", "type": "recurring", "title": "Т" * 200, "text": "Подписка.", "action": {"kind": "hack"}},
            ],
        },
        "model": "gemini-test",
    }


class InsightsCase(AppTestCase):
    def setUp(self):
        super().setUp()
        advisor = patch.object(insights, "_advisor", lambda: fake_advisor)
        advisor.start()
        self.addCleanup(advisor.stop)
        self.cafe = self.category("Кафе")

    def consent(self, headers=None, value=True):
        prefs = self.client.get("/api/auth/me", headers=headers or self.headers_a).json()["preferences"]
        r = self.client.patch("/api/auth/me", headers=headers or self.headers_a,
                              json={"preferences": {**prefs, "ai_insights": value}, "ai_insights_consent": value})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["user"]

    def receipt(self, day, amount, category=None, store="Coffee Boom", **extra):
        category = category or self.cafe
        return self.save(store=store, date=day, total=amount, allow_duplicate=True,
                         items=[{"name": "Кофе", "total_price": amount, "category_id": category.id}], **extra)

    def run_analysis(self):
        r = self.client.post("/api/insights", headers=self.headers_a)
        self.assertEqual(r.status_code, 202, r.text)
        task = self.wait_task(r.json()["task_id"])
        self.assertEqual(task["status"], "completed", task)
        return task


class TestSnapshot(InsightsCase):
    def test_no_personal_data(self):
        self.receipt(month_day(0), 1500)
        snapshot = crud.build_finance_snapshot(self.db, self.user_a)
        text = json.dumps(snapshot, ensure_ascii=False)
        for secret in (self.user_a.email, "Алия", "Тестова", "user_a"):
            self.assertNotIn(secret, text)
        self.assertNotIn("user_id", text)
        self.assertEqual(set(snapshot), {
            "base_currency", "today", "months", "categories", "total_limit", "top_stores",
            "small_purchases", "uncategorized_share", "unconverted",
        })

    def test_numbers(self):
        for back, amount in ((3, 21000), (2, 23500), (1, 22800)):
            self.receipt(month_day(back), amount)
        self.receipt(month_day(0, 1), 20000)
        for _ in range(3):
            self.receipt(month_day(0, 2), 1000)
        # Без категории и в долларах без курса
        self.save(store="Рынок", date=month_day(0, 2), total=500, items=[{"name": "Что-то", "total_price": 500}])
        self.save(store="Amazon", date=month_day(0, 2), total=10, currency="USD")
        self.client.put("/api/budgets", headers=self.headers_a, json={"budgets": [
            {"category_id": self.cafe.id, "limit": 25000}, {"category_id": None, "limit": 300000}]})

        s = crud.build_finance_snapshot(self.db, self.user_a)
        self.assertEqual(s["today"], TODAY.isoformat())
        self.assertEqual(len(s["months"]), 6)
        self.assertEqual(s["months"][-1], {"month": MONTH, "total": 23500.0, "receipts": 5})
        cafe = next(c for c in s["categories"] if c["id"] == self.cafe.id)
        self.assertEqual(cafe, {"id": self.cafe.id, "name": "Кафе", "this_month": 23000.0,
                                "prev_months": [22800.0, 23500.0, 21000.0], "limit": 25000, "receipts": 4})
        # Категории без трат и лимита не нужны
        self.assertNotIn("Одежда", [c["name"] for c in s["categories"]])
        self.assertEqual(s["total_limit"], 300000)
        self.assertEqual(s["top_stores"][0]["name"], "Coffee Boom")
        self.assertEqual(s["small_purchases"][0], {"category_id": self.cafe.id, "store": "Coffee Boom", "count": 3, "total": 3000.0})
        self.assertEqual(s["unconverted"], [{"currency": "USD", "count": 1}])
        self.assertAlmostEqual(s["uncategorized_share"], round(500 / (21000 + 23500 + 22800 + 23000 + 500), 3))

    def test_hash_ignores_day_within_month(self):
        s = crud.build_finance_snapshot(self.db, self.user_a)
        self.assertEqual(crud.snapshot_hash(s), crud.snapshot_hash({**s, "today": MONTH + "-28"}))
        self.assertNotEqual(crud.snapshot_hash(s), crud.snapshot_hash({**s, "total_limit": 1}))


class TestConsent(InsightsCase):
    def test_without_consent_403(self):
        r = self.client.post("/api/insights", headers=self.headers_a)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {"detail": "Нужно согласие на анализ"})

    def test_consent_saved_without_password(self):
        me = self.client.get("/api/auth/me", headers=self.headers_a).json()
        self.assertFalse(me["preferences"]["ai_insights"])
        self.assertFalse(me["ai_insights_consent"])
        user = self.consent()
        self.assertTrue(user["preferences"]["ai_insights"])
        self.assertTrue(user["ai_insights_consent"])
        # Остальные настройки не сбросились
        self.assertEqual(user["preferences"]["theme"], me["preferences"]["theme"])

    def test_either_field_is_enough(self):
        r = self.client.patch("/api/auth/me", headers=self.headers_a, json={"ai_insights_consent": True})
        self.assertTrue(r.json()["user"]["preferences"]["ai_insights"])
        r = self.client.patch("/api/auth/me", headers=self.headers_a, json={"preferences": {"ai_insights": False}})
        self.assertFalse(r.json()["user"]["ai_insights_consent"])

    def test_revoked_consent(self):
        self.consent()
        self.consent(value=False)
        self.assertEqual(self.client.post("/api/insights", headers=self.headers_a).status_code, 403)


class TestFlow(InsightsCase):
    def test_empty(self):
        r = self.client.get("/api/insights", headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"insights": None})
        self.assertEqual(self.client.get("/api/insights").status_code, 401)

    def test_generate_and_read(self):
        self.receipt(month_day(0), 1500)
        self.consent()
        task = self.run_analysis()
        # GET /api/tasks/{id} без авторизации: в result ничего личного
        self.assertEqual(task["result"], {"ok": True})

        out = self.client.get("/api/insights", headers=self.headers_a).json()
        self.assertEqual(set(out), {"insights", "created_at", "model", "stale"})
        self.assertFalse(out["stale"])
        self.assertEqual(out["model"], "gemini-test")
        items = out["insights"]["items"]
        # Неизвестный тип и повтор id отброшены, порядок — по priority, длина заголовка обрезана
        self.assertEqual([i["id"] for i in items], ["x1", f"over_budget:{self.cafe.id}:{MONTH}", "x3"])
        suggestion = items[0]
        # Категории нет у пользователя: category_id → null, действие → none
        self.assertIsNone(suggestion["category_id"])
        self.assertEqual(suggestion["action"], {"kind": "none"})
        self.assertEqual(items[1]["action"], {"kind": "open_history", "category_id": self.cafe.id})
        self.assertLessEqual(len(items[2]["title"]), insights.TITLE_MAX)
        self.assertEqual(items[2]["action"], {"kind": "none"})
        # Другой пользователь чужой анализ не видит
        self.assertEqual(self.client.get("/api/insights", headers=self.headers_b).json(), {"insights": None})

    def test_unchanged_data_returns_previous(self):
        self.receipt(month_day(0), 1500)
        self.consent()
        self.run_analysis()
        r = self.client.post("/api/insights", headers=self.headers_a)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["model"], "gemini-test")
        self.assertNotIn("task_id", r.json())
        self.assertEqual(self.db.query(models.Insight).filter_by(user_id=self.user_a.id).count(), 1)

    def test_stale_and_429(self):
        self.receipt(month_day(0), 1500)
        self.consent()
        self.run_analysis()
        self.receipt(month_day(0, 2), 700)
        self.assertTrue(self.client.get("/api/insights", headers=self.headers_a).json()["stale"])
        r = self.client.post("/api/insights", headers=self.headers_a)
        self.assertEqual(r.status_code, 429, r.text)
        body = r.json()
        self.assertIsInstance(body["retry_after"], int)
        self.assertTrue(0 < body["retry_after"] <= config.INSIGHTS_REFRESH_HOURS * 3600)
        self.assertTrue(body["detail"].startswith("Обновить советы можно через"))

        with patch.object(config, "INSIGHTS_REFRESH_HOURS", 0):
            self.run_analysis()
        self.assertFalse(self.client.get("/api/insights", headers=self.headers_a).json()["stale"])

    def test_budget_change_makes_stale(self):
        self.receipt(month_day(0), 1500)
        self.consent()
        self.run_analysis()
        self.client.put("/api/budgets", headers=self.headers_a, json={"budgets": [{"category_id": self.cafe.id, "limit": 1000}]})
        self.assertTrue(self.client.get("/api/insights", headers=self.headers_a).json()["stale"])

    def test_delete_receipt_makes_stale(self):
        saved = self.receipt(month_day(0), 1500)
        self.consent()
        self.run_analysis()
        self.client.delete(f"/api/receipts/{saved['id']}", headers=self.headers_a)
        self.assertTrue(self.client.get("/api/insights", headers=self.headers_a).json()["stale"])

    def test_ai_failure_falls_back_to_rules(self):
        def broken(snapshot, provider="gemini"):
            raise TimeoutError("quota")

        self.receipt(month_day(0), 30000)
        self.client.put("/api/budgets", headers=self.headers_a, json={"budgets": [{"category_id": self.cafe.id, "limit": 25000}]})
        self.consent()
        with patch.object(insights, "_advisor", lambda: broken):
            self.run_analysis()
        out = self.client.get("/api/insights", headers=self.headers_a).json()
        self.assertEqual(out["model"], "rules")
        self.assertEqual(out["insights"]["items"][0]["type"], "over_budget")

    def test_provider_off(self):
        self.receipt(month_day(0), 1500)
        self.consent()
        with patch.object(config, "AI_PROVIDER", "off"):
            self.run_analysis()
        self.assertEqual(self.client.get("/api/insights", headers=self.headers_a).json()["model"], "rules")

    def test_account_deletion_removes_insights(self):
        self.receipt(month_day(0), 1500)
        self.consent()
        self.run_analysis()
        user_id = self.user_a.id
        r = self.client.request("DELETE", "/api/auth/me", headers=self.headers_a, json={"password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertEqual(self.db.query(models.Insight).filter_by(user_id=user_id).count(), 0)

    def test_keeps_only_last_analyses(self):
        self.consent()
        with patch.object(config, "INSIGHTS_REFRESH_HOURS", 0):
            for amount in range(1, insights_keep() + 3):
                self.receipt(month_day(0), amount * 100)
                self.run_analysis()
        self.assertEqual(self.db.query(models.Insight).filter_by(user_id=self.user_a.id).count(), insights_keep())


def insights_keep():
    return crud.INSIGHTS_KEEP


class TestRules(unittest.TestCase):
    """Шаблонные советы — пока ML (B.1) не готов или ИИ недоступен."""

    def snapshot(self, **changes):
        today = date(2026, 11, 12)
        base = {
            "base_currency": "KZT", "today": today.isoformat(),
            "months": [{"month": "2026-11", "total": 152000, "receipts": 31}],
            "categories": [
                {"id": 4, "name": "Кафе", "this_month": 32000, "prev_months": [21000, 23500, 22800], "limit": 25000, "receipts": 14},
                {"id": 3, "name": "Транспорт", "this_month": 10000, "prev_months": [22000, 23000, 24000], "limit": None, "receipts": 5},
                {"id": 1, "name": "Продукты", "this_month": 50000, "prev_months": [60000, 61000, 59000], "limit": 80000, "receipts": 9},
            ],
            "total_limit": 300000,
            "top_stores": [],
            "small_purchases": [{"category_id": 4, "store": "Coffee Boom", "count": 14, "total": 21000}],
            "uncategorized_share": 0.18,
            "unconverted": [{"currency": "USD", "count": 2}],
        }
        base.update(changes)
        return base

    def test_facts(self):
        out = insights.rule_insights(self.snapshot())
        by_type = {i["type"]: i for i in out["items"]}
        self.assertEqual(by_type["over_budget"]["title"], "Кафе: лимит превышен на 7 000 ₸")
        self.assertEqual(by_type["over_budget"]["id"], "over_budget:4:2026-11")
        self.assertEqual(by_type["budget_suggestion"]["action"], {"kind": "set_budget", "category_id": 3, "amount": 26000})
        self.assertEqual(by_type["frequent_small"]["category_id"], 4)
        self.assertEqual(by_type["uncategorized"]["action"], {"kind": "open_categories"})
        self.assertEqual(len(out["items"]), insights.MAX_ITEMS)
        self.assertEqual([i["priority"] for i in out["items"]], list(range(1, insights.MAX_ITEMS + 1)))
        self.assertEqual(len({i["id"] for i in out["items"]}), len(out["items"]))
        self.assertIn("ноябре", out["summary"])

    def test_forecast(self):
        # 12-е число, потрачено 60 000 из 80 000: темп 5 000 в день → лимит кончится 16-го
        snap = self.snapshot(categories=[{"id": 1, "name": "Продукты", "this_month": 60000, "prev_months": [], "limit": 80000, "receipts": 9}])
        items = insights.rule_insights(snap)["items"]
        self.assertEqual(items[0]["type"], "budget_forecast")
        self.assertIn("16.11", items[0]["title"])

    def test_quiet_user(self):
        snap = self.snapshot(categories=[], small_purchases=[], uncategorized_share=0.01, unconverted=[], total_limit=None)
        self.assertEqual(insights.rule_insights(snap)["items"], [])

    def test_sanitize_rejects_non_object(self):
        with self.assertRaises(ValueError):
            insights.sanitize(["nope"], self.snapshot())

    def test_too_few_ai_items_use_rules(self):
        one = lambda snapshot, provider: {"insights": {"summary": "", "items": [  # noqa: E731
            {"id": "a", "type": "recurring", "title": "Один", "text": "Только один совет."}]}, "model": "gemini"}
        with patch.object(insights, "_advisor", lambda: one), patch.object(config, "AI_PROVIDER", "gemini"):
            model, _ = insights.generate(self.snapshot())
        self.assertEqual(model, "rules")

    def test_month_rollover(self):
        snap = self.snapshot(today=(date(2026, 12, 1) - timedelta(days=1)).isoformat())
        self.assertTrue(insights.rule_insights(snap)["items"])


if __name__ == "__main__":
    unittest.main()
