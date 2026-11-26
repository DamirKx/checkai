"""Аккаунт: удаление со всеми данными, обновление токенов, демо-чек."""
import os
from datetime import timedelta
from unittest.mock import patch

import testenv  # noqa: F401 — временная база и папки, до импорта app.main

from app import auth, config, models, uploads
from app.routers import analyze as analyze_router
from apptest import PASSWORD, AppTestCase, image_bytes, put_upload


def fake_analyze(image_path, ocr=None, categories=None):
    return {"store": "Magnum", "raw_lines": ["Хлеб 300"], "currency": "KZT", "total": 300,
            "items": [{"name": "Хлеб", "total_price": 300}]}


def upload_files():
    return set(os.listdir(config.UPLOADS_DIR)) if os.path.isdir(config.UPLOADS_DIR) else set()


class TestDeleteAccount(AppTestCase):
    def delete_me(self, password=PASSWORD, headers=None):
        return self.client.request("DELETE", "/api/auth/me", json={"password": password},
                                   headers=self.headers_a if headers is None else headers)

    def disk_path(self, image_path):
        return uploads.image_disk_path(image_path)

    def fill_user_a(self):
        """У user_a: чек с фото и позицией, аватар, лимит, правило категории, ИИ-анализ."""
        image_path = put_upload(image_bytes())
        receipt = self.save(store="Magnum", total=300, image_path=image_path,
                            items=[{"name": "Хлеб", "total_price": 300}])
        r = self.client.post("/api/auth/me/avatar", files={"file": ("me.jpg", image_bytes(), "image/jpeg")},
                             headers=self.headers_a)
        self.assertEqual(r.status_code, 200, r.text)
        avatar_url = r.json()["avatar_url"]

        food = self.category("Продукты")
        self.db.add_all([
            models.Budget(user_id=self.user_a.id, category_id=food.id, limit=50000),
            models.CategoryRule(user_id=self.user_a.id, pattern="хлеб", category_id=food.id),
            models.Insight(user_id=self.user_a.id, model="rules", data={"summary": "", "items": []},
                           snapshot_hash="x" * 64),
        ])
        self.db.commit()
        return receipt, image_path, avatar_url

    def count(self, model, user_id):
        self.db.expire_all()
        return self.db.query(model).filter(model.user_id == user_id).count()

    def test_deletes_user_and_all_data(self):
        receipt, image_path, avatar_url = self.fill_user_a()
        user_id = self.user_a.id
        b_receipt = self.save(headers=self.headers_b, store="Small", total=100)
        avatar_file = os.path.join(config.AVATARS_DIR, avatar_url.rsplit("/", 1)[1])
        self.assertTrue(os.path.exists(self.disk_path(image_path)))
        self.assertTrue(os.path.exists(avatar_file))

        r = self.delete_me()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"success": True})

        self.db.expire_all()
        self.assertIsNone(self.db.get(models.User, user_id))
        for model in (models.Receipt, models.Category, models.Budget, models.CategoryRule, models.Insight):
            self.assertEqual(self.count(model, user_id), 0, model.__name__)
        self.assertEqual(self.db.query(models.ReceiptItem).filter_by(receipt_id=receipt["id"]).count(), 0)
        self.assertFalse(os.path.exists(self.disk_path(image_path)))
        self.assertFalse(os.path.exists(avatar_file))

        # Данные другого пользователя не тронуты
        r = self.client.get(f"/api/receipts/{b_receipt['id']}", headers=self.headers_b)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreater(self.count(models.Category, self.user_b.id), 0)

        # Старый токен удалённого пользователя не действует
        self.assertEqual(self.client.get("/api/auth/me", headers=self.headers_a).status_code, 401)

    def test_keeps_image_used_by_another_receipt(self):
        """Фото, на которое ссылается чек другого пользователя, остаётся на диске."""
        image_path = put_upload(image_bytes())
        self.save(store="Magnum", total=300, image_path=image_path)
        self.save(headers=self.headers_b, store="Magnum", total=300, image_path=image_path)

        self.assertEqual(self.delete_me().status_code, 200)
        self.assertTrue(os.path.exists(self.disk_path(image_path)))

    def test_wrong_password_keeps_everything(self):
        receipt, image_path, _ = self.fill_user_a()

        r = self.delete_me(password="not-my-password")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["code"], "wrong_password")
        self.assertEqual(self.client.get(f"/api/receipts/{receipt['id']}", headers=self.headers_a).status_code, 200)
        self.assertEqual(self.count(models.CategoryRule, self.user_a.id), 1)
        self.assertTrue(os.path.exists(self.disk_path(image_path)))

    def test_requires_token(self):
        self.assertEqual(self.delete_me(headers={}).status_code, 401)
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(models.User, self.user_a.id))


class TestRefreshToken(AppTestCase):
    def refresh(self, token):
        return self.client.post("/api/auth/refresh", json={"refresh_token": token})

    def assert_expired(self, r):
        self.assertEqual(r.status_code, 401, r.text)
        self.assertEqual(r.json()["code"], "session_expired")

    def test_returns_working_tokens(self):
        _, refresh_token = auth.issue_tokens(self.user_a)

        r = self.refresh(refresh_token)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["token_type"], "bearer")
        self.assertEqual(body["user"]["email"], self.user_a.email)
        self.assertIsNone(body["recovery_code"])

        me = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["id"], self.user_a.id)
        # Новый refresh токен тоже рабочий
        self.assertEqual(self.refresh(body["refresh_token"]).status_code, 200)

    def test_access_token_is_not_refresh_token(self):
        access_token, _ = auth.issue_tokens(self.user_a)
        self.assert_expired(self.refresh(access_token))

    def test_refresh_token_is_not_access_token(self):
        _, refresh_token = auth.issue_tokens(self.user_a)
        r = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {refresh_token}"})
        self.assertEqual(r.status_code, 401)

    def test_garbage_token(self):
        self.assert_expired(self.refresh("not-a-jwt"))

    def test_expired_token(self):
        claims = {"sub": str(self.user_a.id), "email": self.user_a.email, "pwd": auth.password_fingerprint(self.user_a)}
        self.assert_expired(self.refresh(auth.create_refresh_token(claims, expires_delta=timedelta(seconds=-1))))

    def test_invalid_after_password_change(self):
        _, refresh_token = auth.issue_tokens(self.user_a)
        self.user_a.hashed_password = auth.hash_password("new-password-456")
        self.db.commit()
        self.assert_expired(self.refresh(refresh_token))

    def test_invalid_after_user_deleted(self):
        _, refresh_token = auth.issue_tokens(self.user_a)
        self.db.delete(self.user_a)
        self.db.commit()
        self.assert_expired(self.refresh(refresh_token))


class TestDemoReceipt(AppTestCase):
    def analyze_test(self, headers=None, analyze=fake_analyze):
        with patch("app.ocr.get_ocr", return_value=None), patch("app.ocr.analyze_receipt", side_effect=analyze):
            return self.client.post("/api/analyze-test", headers=headers or {})

    def test_anonymous(self):
        before = upload_files()
        r = self.analyze_test()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["store"], "Magnum")
        self.assertEqual([i["name"] for i in body["data"]["items"]], ["Хлеб"])
        # Демо-чек читается из images.jpeg — в папку загрузок ничего не копируется
        self.assertEqual(upload_files(), before)
        self.assertTrue(os.path.isfile(analyze_router.TEST_IMAGE_PATH))

    def test_with_user_categories(self):
        r = self.analyze_test(headers=self.headers_a)
        self.assertEqual(r.status_code, 200, r.text)
        item = r.json()["data"]["items"][0]
        self.assertIn("category", item)

    def test_missing_demo_image(self):
        with patch.object(analyze_router, "TEST_IMAGE_PATH", os.path.join(config.UPLOADS_DIR, "no-such.jpeg")):
            r = self.analyze_test()
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(r.json()["code"], "demo_not_found")

    def test_ocr_failure(self):
        def broken(*args, **kwargs):
            raise RuntimeError("OCR упал")

        with self.assertLogs("checkai", level="ERROR"):
            r = self.analyze_test(analyze=broken)
        self.assertEqual(r.status_code, 500, r.text)
        self.assertEqual(r.json()["code"], "ocr_failed")
