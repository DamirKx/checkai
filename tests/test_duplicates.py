"""Защита от дубликатов: фото, фискальный номер, содержимое, 409 (NEXT_WEEK_TASKS.md, C.2)."""
import json
import os
import unittest
from unittest.mock import patch

import testenv  # noqa: F401 — временная база и папки, до импорта server

import server
from apptest import AppTestCase, image_bytes, put_upload
from app import duplicates, models

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "duplicates.json")
ITEMS = [{"name": "Хлеб", "total_price": 300}, {"name": "Молоко", "total_price": 4200}]
RECEIPT = {"store": "Magnum", "date": "2026-11-05", "time": "18:30", "total": 4500, "items": ITEMS}


class TestRuleFixtures(unittest.TestCase):
    """Общие с фронтом примеры: findDuplicate в app.js должен давать то же самое."""

    def test_fixtures(self):
        with open(FIXTURES, encoding="utf-8") as f:
            cases = json.load(f)
        self.assertGreaterEqual(len(cases), 10)
        for case in cases:
            with self.subTest(case["name"]):
                self.assertEqual(duplicates.match_score(case["a"], case["b"]) is not None, case["expected"])
                # Правило симметрично
                self.assertEqual(duplicates.match_score(case["b"], case["a"]) is not None, case["expected"])

    def test_norm_text(self):
        self.assertEqual(duplicates.norm_text("ТОО «MAGNUM»"), "magnum")
        self.assertEqual(duplicates.norm_text("ИП  Ёлочка-2"), "елочка 2")
        self.assertEqual(duplicates.norm_text("Қазақ Өнім LLP"), "қазақ өнім")
        self.assertEqual(duplicates.norm_text(None), "")

    def test_fiscal_id(self):
        self.assertEqual(duplicates.fiscal_id_from({"kkm": "0101-2345", "number": "000123", "fp": "98 76"}), "01012345:000123:9876")
        self.assertEqual(duplicates.fiscal_id_from({"kkm": "010123", "number": "15"}), "010123:15:")
        # Без РНМ или номера чека — не склеиваем
        self.assertIsNone(duplicates.fiscal_id_from({"number": "15", "fp": "1"}))
        self.assertIsNone(duplicates.fiscal_id_from({"kkm": "010123"}))
        self.assertIsNone(duplicates.fiscal_id_from(None))


class TestSave(AppTestCase):
    def test_same_receipt_twice(self):
        first = self.save(**RECEIPT)
        r = self.client.post("/api/receipts/save", json=RECEIPT, headers=self.headers_a)
        self.assertEqual(r.status_code, 409, r.text)
        body = r.json()
        self.assertEqual(body["detail"], "Похоже, этот чек уже сохранён")
        self.assertEqual(body["duplicate_of"], first["id"])
        # Полный ReceiptOut на верхнем уровне, с позициями
        self.assertEqual(body["duplicate"]["id"], first["id"])
        self.assertEqual([i["name"] for i in body["duplicate"]["items"]], ["Хлеб", "Молоко"])
        self.assertEqual(body["duplicate"]["rate_to_base"], 1.0)
        # «Всё равно сохранить»
        second = self.save(**RECEIPT, allow_duplicate=True)
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(len(self.client.get("/api/receipts", headers=self.headers_a).json()), 2)

    def test_store_spelling_does_not_matter(self):
        self.save(**{**RECEIPT, "store": "ТОО «MAGNUM»"})
        self.save(**{**RECEIPT, "store": "Magnum", "time": "18:31"}, status=409)

    def test_not_duplicates(self):
        self.save(**RECEIPT)
        cases = [
            {"time": "21:30"},                                   # время на 3 часа позже
            {"total": 4501},                                     # другой итог
            {"date": "2026-11-06"},                              # другой день
            {"store": "Small"},                                  # другой магазин
            {"currency": "USD"},                                 # другая валюта
            {"items": [{"name": "Сыр", "total_price": 4500}]},   # другие позиции
        ]
        for change in cases:
            with self.subTest(change=change):
                saved = self.save(**{**RECEIPT, **change})
                self.client.delete(f"/api/receipts/{saved['id']}", headers=self.headers_a)
        # У другого пользователя такого чека нет
        self.save(headers=self.headers_b, **RECEIPT)

    def test_same_photo(self):
        content = image_bytes()
        self.save(**RECEIPT, image_path=put_upload(content))
        # Тот же файл под другим именем, а содержимое чека — другое: дубликат по фото
        other = {"store": "Small", "date": "2026-10-01", "total": 999, "image_path": put_upload(content)}
        r = self.client.post("/api/receipts/save", json=other, headers=self.headers_a)
        self.assertEqual(r.status_code, 409, r.text)
        # Другое фото — не дубликат
        self.save(**{**other, "image_path": put_upload(image_bytes(color=(0, 0, 255)))})

    def test_hash_is_computed_by_server(self):
        content = image_bytes()
        saved = self.save(**RECEIPT, image_path=put_upload(content), image_hash="0" * 64)
        stored = self.db.get(models.Receipt, saved["id"])
        import hashlib
        self.assertEqual(stored.image_hash, hashlib.sha256(content).hexdigest())

    def test_manual_receipt_has_no_hash(self):
        saved = self.save(**RECEIPT)
        self.assertIsNone(self.db.get(models.Receipt, saved["id"]).image_hash)


class TestUpdate(AppTestCase):
    def put(self, receipt_id, body):
        return self.client.put(f"/api/receipts/{receipt_id}", json=body, headers=self.headers_a)

    def test_edit_into_copy_of_other(self):
        first = self.save(**RECEIPT)
        second = self.save(**{**RECEIPT, "date": "2026-11-06"})
        r = self.put(second["id"], RECEIPT)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["duplicate_of"], first["id"])
        # Ничего не поменялось
        self.assertEqual(self.client.get(f"/api/receipts/{second['id']}", headers=self.headers_a).json()["date"], "2026-11-06")
        self.assertEqual(self.put(second["id"], {**RECEIPT, "allow_duplicate": True}).status_code, 200)

    def test_edit_itself_unchanged(self):
        saved = self.save(**RECEIPT)
        r = self.put(saved["id"], {**RECEIPT, "source": "manual", "currency": None})
        self.assertEqual(r.status_code, 200, r.text)


def fake_analyze(fiscal=None, store="MAGNUM", total=4500.0):
    def analyze(image_path, ocr=None, categories=None):
        return {"raw_lines": [store], "store": store, "date": "05.11.2026 18:30", "total": total,
                "items": [], "fiscal": fiscal}
    return analyze


class TestAnalyze(AppTestCase):
    def analyze(self, content, headers=None, fiscal=None, store="MAGNUM", total=4500.0):
        with patch.object(server, "analyze_receipt", fake_analyze(fiscal, store, total)), \
                patch.object(server, "get_ocr", lambda: None):
            r = self.client.post("/api/analyze", files={"file": ("r.jpg", content, "image/jpeg")},
                                 headers=headers or {})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    def test_same_photo_marked(self):
        content = image_bytes()
        data = self.analyze(content, self.headers_a)
        self.assertIsNone(data["duplicate_of"])
        saved = self.save(store=data["store"], date=data["date"], total=data["total"], image_path=data["image_path"])
        self.assertEqual(self.analyze(content, self.headers_a)["duplicate_of"], saved["id"])
        # Чужой пользователь и гость — null
        self.assertIsNone(self.analyze(content, self.headers_b)["duplicate_of"])
        self.assertIsNone(self.analyze(content)["duplicate_of"])

    def test_same_content_other_photo_not_marked(self):
        # По содержимому при распознавании не ищем: это делает фронт, а при сохранении — сервер (409)
        self.save(store="MAGNUM", date="2026-11-05", time="18:30", total=4500)
        data = self.analyze(image_bytes(color=(10, 200, 10)), self.headers_a)
        self.assertIsNone(data["duplicate_of"])
        r = self.client.post("/api/receipts/save", headers=self.headers_a, json={
            "store": data["store"], "date": data["date"], "time": data["time"], "total": data["total"],
            "image_path": data["image_path"]})
        self.assertEqual(r.status_code, 409)

    def test_same_fiscal_number(self):
        fiscal = {"kkm": "010101234567", "number": "000123", "fp": "1234567890", "qr": None}
        first = self.analyze(image_bytes(), self.headers_a, fiscal=fiscal)
        saved = self.save(store="MAGNUM", date=first["date"], total=4500, image_path=first["image_path"])
        self.assertEqual(self.db.get(models.Receipt, saved["id"]).fiscal_id, "010101234567:000123:1234567890")
        # Второй снимок того же чека: другое фото, тот же фискальный номер
        again = self.analyze(image_bytes(color=(0, 0, 255)), self.headers_a, fiscal=fiscal, store="Другой", total=1.0)
        self.assertEqual(again["duplicate_of"], saved["id"])
        r = self.client.post("/api/receipts/save", headers=self.headers_a, json={
            "store": "Другой", "date": "2026-01-01", "total": 1, "image_path": again["image_path"]})
        self.assertEqual(r.status_code, 409)

    def test_fiscal_key_always_present(self):
        data = self.analyze(image_bytes(), self.headers_a)
        self.assertIn("fiscal", data)


if __name__ == "__main__":
    unittest.main()
