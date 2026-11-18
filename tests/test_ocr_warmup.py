"""Прогрев модели распознавания при старте (OCR_WARMUP) и статус OCR в /api/health."""
import threading
import unittest
from unittest.mock import patch

import testenv  # noqa: F401 — временная база и папки, до импорта server

from fastapi.testclient import TestClient

import server
from app import config


class OcrWarmupTest(unittest.TestCase):
    def setUp(self):
        # Модель процесса не трогаем: тесты подменяют её и возвращают как было
        self._model = server._ocr_model
        server._ocr_model = None
        self.addCleanup(setattr, server, "_ocr_model", self._model)

    def health_ocr(self) -> str:
        return TestClient(server.app).get("/api/health").json()["ocr"]

    def test_health_standby_loading_ready(self):
        self.assertEqual(self.health_ocr(), "standby")

        with server._ocr_init_lock:  # так выглядит идущая загрузка в get_ocr()
            self.assertEqual(self.health_ocr(), "loading")

        server._ocr_model = object()
        self.assertEqual(self.health_ocr(), "ready")

    def test_warmup_loads_model_once(self):
        model = object()
        with patch.object(server, "init_ocr", return_value=model) as init:
            server._warm_up_ocr()
            self.assertIs(server.get_ocr(), model)
        init.assert_called_once()

    def test_warmup_failure_keeps_lazy_loading(self):
        with patch.object(server, "init_ocr", side_effect=RuntimeError("нет сети")):
            with self.assertLogs("checkai", level="ERROR"):
                server._warm_up_ocr()  # исключение не выходит наружу
        self.assertIsNone(server._ocr_model)

        model = object()
        with patch.object(server, "init_ocr", return_value=model):
            self.assertIs(server.get_ocr(), model)  # первый чек загрузит модель сам

    def test_lifespan_starts_warmup_only_when_enabled(self):
        started = threading.Event()

        def fake_warmup():
            started.set()

        # Выход из lifespan останавливает пулы фоновых задач процесса — следующим тестам они ещё нужны
        with patch.object(server, "_warm_up_ocr", fake_warmup), \
                patch.object(server.task_manager._executor, "shutdown"), \
                patch.object(server.insights_task_manager._executor, "shutdown"):
            with patch.object(config, "OCR_WARMUP", False):
                with TestClient(server.app):
                    pass
            self.assertFalse(started.is_set())

            with patch.object(config, "OCR_WARMUP", True):
                with TestClient(server.app):
                    self.assertTrue(started.wait(5))


if __name__ == "__main__":
    unittest.main()
