"""Мобильная версия (PWA): манифест, service worker, иконки и владелец фоновой задачи распознавания."""
import os
import re
import time
import unittest
from unittest.mock import patch

import testenv  # noqa: F401 — временная база и папки, до импорта server

import server
from apptest import AppTestCase, image_bytes
from app.i18n import tr


def fake_analyze(image_path, ocr=None, categories=None):
    return {"store": "Magnum", "raw_lines": ["Хлеб 300"], "currency": "KZT", "total": 300,
            "items": [{"name": "Хлеб", "total_price": 300}]}


class TestPwaFiles(AppTestCase):
    def test_service_worker_served_from_root(self):
        """Из /static/sw.js service worker не управлял бы страницей «/» — поэтому отдаётся из корня."""
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("application/javascript"))
        self.assertEqual(r.headers["cache-control"], "no-cache")
        self.assertIn("const VERSION", r.text)

    def test_manifest(self):
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("application/manifest+json"))
        manifest = r.json()
        self.assertEqual(manifest["scope"], "/")
        self.assertTrue(manifest["start_url"].startswith("/"))
        self.assertEqual(manifest["display"], "standalone")
        sizes = {(icon["sizes"], icon.get("purpose", "any")) for icon in manifest["icons"]}
        self.assertLessEqual({("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")}, sizes)
        # Все иконки (и у ярлыков) действительно отдаются
        srcs = [icon["src"] for icon in manifest["icons"]]
        srcs += [icon["src"] for s in manifest.get("shortcuts", []) for icon in s.get("icons", [])]
        for src in srcs:
            icon = self.client.get(src)
            self.assertEqual(icon.status_code, 200, src)
            self.assertEqual(icon.headers["content-type"], "image/png", src)

    def test_index_links_and_assets(self):
        """Страница ссылается на манифест и иконки; всё из /static/, что кэширует service worker, существует."""
        html = self.client.get("/").text
        self.assertIn('rel="manifest" href="/manifest.webmanifest"', html)
        self.assertIn('rel="apple-touch-icon"', html)
        self.assertIn("viewport-fit=cover", html)
        assets = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
        self.assertTrue(assets)
        for url in assets:
            self.assertEqual(self.client.get(url).status_code, 200, url)


class TestAnalyzeTaskOwner(AppTestCase):
    """Фронт распознаёт через /api/analyze/async: результат задачи (чек, duplicate_of) видит только владелец."""

    def run_task(self, headers=None, analyze=fake_analyze):
        with patch("server.get_ocr", return_value=None), patch("server.analyze_receipt", side_effect=analyze):
            r = self.client.post("/api/analyze/async", files={"file": ("check.jpg", image_bytes(), "image/jpeg")},
                                 headers=headers or {})
            self.assertEqual(r.status_code, 202, r.text)
            task_id = r.json()["task_id"]
            deadline = time.time() + 10
            while time.time() < deadline:
                status = self.client.get(f"/api/tasks/{task_id}", headers=headers or {})
                self.assertEqual(status.status_code, 200, status.text)
                if status.json()["status"] in ("completed", "failed"):
                    return task_id, status.json()
                time.sleep(0.05)
        self.fail("Задача распознавания не завершилась")

    def test_owner_gets_result(self):
        task_id, status = self.run_task(self.headers_a)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["result"]["store"], "Magnum")
        self.assertTrue(status["result"]["image_path"].startswith(server.RECEIPTS_URL_PREFIX))
        self.assertIn("duplicate_of", status["result"])

    def test_other_user_and_anonymous_cannot_read(self):
        task_id, _ = self.run_task(self.headers_a)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}", headers=self.headers_b).status_code, 404)
        # Без токена или с истёкшим — 401: фронт обновит токен и повторит запрос
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}").status_code, 401)
        bad = {"Authorization": "Bearer not-a-token"}
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}", headers=bad).status_code, 401)

    def test_guest_task_without_auth(self):
        _, status = self.run_task()
        self.assertEqual(status["status"], "completed")

    def test_failure_hides_exception_and_removes_photo(self):
        def broken(image_path, ocr=None, categories=None):
            raise RuntimeError("внутренняя деталь")

        task_id, status = self.run_task(self.headers_a, analyze=broken)
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["error"], tr("ocr_failed"))
        self.assertNotIn("внутренняя деталь", status["error"])
        self.assertFalse(os.path.exists(server.task_manager.get_task(task_id).file_path))

    def test_no_text_error_in_request_language(self):
        def empty(image_path, ocr=None, categories=None):
            return {"raw_lines": [], "items": []}

        with patch("server.get_ocr", return_value=None), patch("server.analyze_receipt", side_effect=empty):
            headers = {**self.headers_a, "Accept-Language": "en"}
            r = self.client.post("/api/analyze/async", files={"file": ("check.jpg", image_bytes(), "image/jpeg")},
                                 headers=headers)
            task_id = r.json()["task_id"]
            deadline = time.time() + 10
            while time.time() < deadline:
                status = self.client.get(f"/api/tasks/{task_id}", headers=self.headers_a).json()
                if status["status"] == "failed":
                    break
                time.sleep(0.05)
        self.assertEqual(status["error"], tr("no_receipt_text", "en"))


if __name__ == "__main__":
    unittest.main()
