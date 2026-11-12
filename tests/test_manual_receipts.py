"""Ручной ввод чеков: источник, проверка чисел, итог по позициям, автокатегория (NEXT_WEEK_TASKS.md, A.2)."""
import json
import unittest
from unittest.mock import patch

import testenv  # noqa: F401 — временная база и папки, до импорта server

from apptest import AppTestCase, image_bytes, put_upload
from app import models
from app.categories import normalize_item_name

ITEM = {"name": "Хлеб", "quantity": 1, "price_per_unit": 300, "total_price": 300}


def has_cyrillic(text: str) -> bool:
    return any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in text)


class TestSource(AppTestCase):
    def test_without_photo_is_manual(self):
        saved = self.save(store="Рынок", date="2026-11-10", total=300, items=[ITEM])
        self.assertEqual(saved["source"], "manual")
        listed = self.client.get(f"/api/receipts/{saved['id']}", headers=self.headers_a).json()
        self.assertEqual(listed["source"], "manual")

    def test_photo_cannot_be_claimed_without_image(self):
        # Клиент прислал photo, но фото нет — всё равно manual
        self.assertEqual(self.save(total=100, source="photo")["source"], "manual")

    def test_with_photo_is_photo(self):
        saved = self.save(store="Magnum", total=300, items=[ITEM], image_path=put_upload(image_bytes()), source="photo")
        self.assertEqual(saved["source"], "photo")

    def test_update_accepts_frontend_fields_and_keeps_source(self):
        saved = self.save(store="Рынок", date="2026-11-10", total=300, items=[ITEM])
        r = self.client.put(
            f"/api/receipts/{saved['id']}",
            json={"store": "Рынок", "date": "2026-11-10", "total": 500, "items": [ITEM],
                  "source": "photo", "allow_duplicate": True, "unknown_field": 1},
            headers=self.headers_a,
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["source"], "manual")
        self.assertEqual(r.json()["total"], 500)

    def test_unknown_fields_are_ignored(self):
        self.save(total=100, something_new=True, items=[{**ITEM, "extra": "x"}])


class TestValidation(AppTestCase):
    def post(self, body, raw=False):
        if raw:
            return self.client.post("/api/receipts/save", content=body,
                                    headers={**self.headers_a, "Content-Type": "application/json"})
        return self.client.post("/api/receipts/save", json=body, headers=self.headers_a)

    def assert_422(self, response):
        self.assertEqual(response.status_code, 422, response.text)
        detail = response.json()["detail"]
        messages = [d["msg"] for d in detail] if isinstance(detail, list) else [detail]
        self.assertTrue(all(has_cyrillic(m) for m in messages), messages)
        self.assertFalse(any(m.startswith("Value error") for m in messages), messages)

    def test_bad_numbers(self):
        cases = [
            {"items": [{**ITEM, "price_per_unit": -1}]},
            {"items": [{**ITEM, "total_price": -500}]},
            {"items": [{**ITEM, "quantity": 0}]},
            {"items": [{**ITEM, "quantity": -2}]},
            {"items": [{**ITEM, "quantity": 100001}]},
            {"items": [{**ITEM, "total_price": 1e9 + 1}]},
            {"total": -1},
            {"total": 1e308},
            {"items": [{**ITEM, "name": "   "}]},
        ]
        for body in cases:
            with self.subTest(body=body):
                self.assert_422(self.post(body))

    def test_nan_and_infinity(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                self.assert_422(self.post('{"items": [{"name": "a", "price_per_unit": %s}]}' % value, raw=True))
                self.assert_422(self.post('{"total": %s}' % value, raw=True))

    def test_limits_are_inclusive(self):
        saved = self.save(items=[{"name": "Слиток", "quantity": 100000, "price_per_unit": 1, "total_price": 1e9}])
        self.assertEqual(saved["items"][0]["quantity"], 100000)

    def test_quantity_zero_not_replaced(self):
        # Раньше 0 молча становился 1
        self.assertEqual(self.post({"items": [{**ITEM, "quantity": 0}]}).status_code, 422)
        # Нет поля или null — одна штука
        saved = self.save(items=[{"name": "Чай", "total_price": 100}, {"name": "Сахар", "quantity": None, "total_price": 50}])
        self.assertEqual([i["quantity"] for i in saved["items"]], [1, 1])

    def test_name_is_trimmed(self):
        self.assertEqual(self.save(items=[{**ITEM, "name": "  Молоко  "}])["items"][0]["name"], "Молоко")

    def test_update_validates_too(self):
        saved = self.save(total=100)
        r = self.client.put(f"/api/receipts/{saved['id']}", json={"items": [{**ITEM, "quantity": 0}]}, headers=self.headers_a)
        self.assert_422(r)


class TestTotal(AppTestCase):
    ITEMS = [{"name": "Хлеб", "total_price": 300}, {"name": "Молоко", "quantity": 2, "price_per_unit": 450.5, "total_price": 901}]

    def test_total_from_items(self):
        self.assertEqual(self.save(items=self.ITEMS)["total"], 1201)
        # Тот же состав — это дубликат первого, поэтому allow_duplicate
        self.assertEqual(self.save(items=self.ITEMS, total=0, allow_duplicate=True)["total"], 1201)

    def test_given_total_kept(self):
        self.assertEqual(self.save(items=self.ITEMS, total=1500)["total"], 1500)

    def test_update_total_from_items(self):
        saved = self.save(total=100)
        r = self.client.put(f"/api/receipts/{saved['id']}", json={"items": self.ITEMS}, headers=self.headers_a)
        self.assertEqual(r.json()["total"], 1201)


class TestOwnership(AppTestCase):
    def test_other_user_cannot_edit(self):
        saved = self.save(total=100)
        r = self.client.put(f"/api/receipts/{saved['id']}", json={"total": 1}, headers=self.headers_b)
        self.assertEqual(r.status_code, 404)


def fake_categorize(items, categories):
    """ML: «кофе» → Кафе (ai), «мыло» → Дом и быт (keywords), остальное — не нашли."""
    by_name = {c["name"]: c for c in categories}
    for item in items:
        name = item["name"].lower()
        cat, source = (by_name["Кафе"], "ai") if "кофе" in name else (by_name["Дом и быт"], "keywords") if "мыло" in name else (None, None)
        item.update(category_id=cat and cat["id"], category=cat and cat["name"], category_source=source)


class TestCategorize(AppTestCase):
    def categorize(self, names, headers=None):
        return self.client.post("/api/categorize", json={"items": [{"name": n} for n in names]},
                                headers=self.headers_a if headers is None else headers)

    def test_one_item_per_name_in_order(self):
        with patch("ml.app.categorize.categorize_items", fake_categorize):
            r = self.categorize(["кофе американо", "абвгд", "Мыло детское", ""])
        self.assertEqual(r.status_code, 200, r.text)
        items = r.json()["items"]
        self.assertEqual([i["name"] for i in items], ["кофе американо", "абвгд", "Мыло детское", ""])
        self.assertEqual(items[0], {"name": "кофе американо", "category_id": self.category("Кафе").id,
                                    "category": "Кафе", "category_source": "ai"})
        self.assertEqual(items[1], {"name": "абвгд", "category_id": None, "category": None, "category_source": None})
        self.assertEqual(items[2]["category_source"], "keywords")
        self.assertIsNone(items[3]["category_id"])

    def test_rule_beats_ml(self):
        transport = self.category("Транспорт")
        self.db.add(models.CategoryRule(user_id=self.user_a.id, pattern=normalize_item_name("Яндекс Go"), category_id=transport.id))
        self.db.commit()
        seen = []

        def spy(items, categories):
            seen.extend(i["name"] for i in items)
            fake_categorize(items, categories)

        with patch("ml.app.categorize.categorize_items", spy):
            items = self.categorize(["Яндекс Go", "кофе"]).json()["items"]
        self.assertEqual(items[0]["category_id"], transport.id)
        self.assertEqual(items[0]["category_source"], "rule")
        # В ML уходит только то, что не нашли правила
        self.assertEqual(seen, ["кофе"])

    def test_ml_failure_is_not_500(self):
        def broken(items, categories):
            items[0]["category_id"] = 999  # успела проставить мусор и упала
            raise ConnectionError("ollama down")

        with patch("ml.app.categorize.categorize_items", broken):
            r = self.categorize(["кофе"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["items"], [{"name": "кофе", "category_id": None, "category": None, "category_source": None}])

    def test_uses_own_categories(self):
        own = self.category("Кафе", self.user_b)
        with patch("ml.app.categorize.categorize_items", fake_categorize):
            items = self.categorize(["кофе"], headers=self.headers_b).json()["items"]
        self.assertEqual(items[0]["category_id"], own.id)

    def test_limits(self):
        self.assertEqual(self.categorize(["кофе"], headers={}).status_code, 401)
        self.assertEqual(self.categorize([]).status_code, 422)
        self.assertEqual(self.categorize(["x"] * 51).status_code, 422)
        with patch("ml.app.categorize.categorize_items", fake_categorize):
            codes = [self.categorize(["кофе"]).status_code for _ in range(31)]
            self.assertEqual(codes[:30], [200] * 30)
            self.assertEqual(codes[30], 429)
            # Лимит — на пользователя, а не на всех
            self.assertEqual(self.categorize(["кофе"], headers=self.headers_b).status_code, 200)

    def test_real_ml_without_ollama(self):
        # Настоящая категоризация с выключенным ИИ — по ключевым словам, без ошибки
        with patch.dict("os.environ", {"USE_AI_PARSER": "off"}):
            r = self.categorize(["Такси до дома", "абвгд"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["items"]), 2)
        json.dumps(r.json())


if __name__ == "__main__":
    unittest.main()
