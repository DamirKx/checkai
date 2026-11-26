"""Распознавание чеков (доступно и гостям) и статус фоновых задач.

Эндпоинты объявлены через def, а не async def: FastAPI выполняет их в пуле потоков,
поэтому долгий OCR не блокирует остальные запросы.
"""
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import auth, config, database, models, ocr, schemas, uploads
from app.categories import apply_rules
from app.i18n import AppError, Lang
from app.tasks import insights_task_manager, task_manager

logger = logging.getLogger("checkai")

router = APIRouter(tags=["OCR"])

TEST_IMAGE_PATH = os.path.join(config.BASE_DIR, "images.jpeg")


@router.post("/api/analyze-test")
def analyze_test_receipt(
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_optional_current_user),
):
    if not os.path.isfile(TEST_IMAGE_PATH):
        raise AppError(404, "demo_not_found")
    try:
        data = ocr.analyze(db, TEST_IMAGE_PATH, current_user, uploads.file_sha256(TEST_IMAGE_PATH))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Ошибка распознавания демо-чека")
        raise AppError(500, "ocr_failed")
    return {"success": True, "data": data}


@router.post("/api/analyze")
def analyze_receipt_endpoint(
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_optional_current_user),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise AppError(415, "image_required")

    file_path, filename, image_hash = uploads.save_upload(file)
    try:
        data = ocr.analyze(db, file_path, current_user, image_hash)
    except HTTPException:
        uploads.remove_quietly(file_path)
        raise
    except Exception:
        uploads.remove_quietly(file_path)
        logger.exception("Ошибка распознавания чека")
        raise AppError(500, "ocr_failed")

    # Путь к картинке клиент вернёт при сохранении чека
    data["image_path"] = f"{uploads.RECEIPTS_URL_PREFIX}/{filename}"
    ocr.remember_fiscal(data["image_path"], data)
    return {"success": True, "data": data}


@router.post("/api/analyze/async")
def analyze_receipt_async_endpoint(
    file: UploadFile = File(...),
    current_user: Optional[models.User] = Depends(auth.get_optional_current_user),
    lang: str = Lang,
):
    """Асинхронное распознавание чека через очередь фоновых задач.
    Возвращает task_id для периодического опроса статуса через GET /api/tasks/{task_id}."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise AppError(415, "image_required")

    file_path, filename, image_hash = uploads.save_upload(file)
    image_url = f"{uploads.RECEIPTS_URL_PREFIX}/{filename}"
    # Сессия запроса закроется раньше, чем задача выполнится: категории берём сейчас,
    # а правила задача читает в своей сессии
    categories = ocr.categories_for(current_user)
    user_id = current_user.id if current_user else None
    # Задача помнит владельца: чужой task_id не отдаёт распознанный чек (GET /api/tasks/{id})
    task = task_manager.create_task(file_path=file_path, image_url=image_url, user_id=user_id)

    def _worker_fn(task_id: str) -> dict:
        task_manager.update_task(task_id, step="ocr", progress=40)
        try:
            data = ocr.run_ocr(file_path, categories)
        except AppError as e:
            # Как в синхронном /api/analyze: фото нераспознанного чека не храним
            uploads.remove_quietly(file_path)
            # Текст ошибки задачи (task.error) — на языке запроса: запроса к этому времени уже нет
            raise e.in_lang(lang) from None
        except Exception:
            uploads.remove_quietly(file_path)
            # Пользователю — понятный текст, а не текст исключения; подробности в логе (_run_wrapper)
            logger.exception("Ошибка распознавания чека (фоновая задача)")
            raise AppError(500, "ocr_failed").in_lang(lang) from None
        with database.SessionLocal() as db:
            if user_id is not None:
                apply_rules(db, user_id, data.get("items") or [])
            ocr.mark_duplicate(db, user_id, data, image_hash)
        data["image_path"] = image_url
        ocr.remember_fiscal(image_url, data)
        return data

    task_manager.submit(task.id, _worker_fn)
    return JSONResponse(
        status_code=202,
        content={
            "success": True,
            "task_id": task.id,
            "status": "pending",
            "check_url": f"/api/tasks/{task.id}",
        },
    )


@router.get("/api/tasks/{task_id}", response_model=schemas.TaskStatusOut)
def get_task_status(
    task_id: str,
    current_user: Optional[models.User] = Depends(auth.get_optional_current_user),
):
    """Статус фоновой задачи: распознавание чека или ИИ-анализ расходов.

    ИИ-анализ и распознавание у гостя — без авторизации (в result анализа ничего личного нет).
    Распознавание у вошедшего пользователя — только с его токеном: в result его чек и duplicate_of.
    Без токена или с истёкшим — 401 (фронт обновит токен и повторит), с чужим — 404, как у чеков."""
    task = task_manager.get_task(task_id) or insights_task_manager.get_task(task_id)
    if not task:
        raise AppError(404, "task_not_found")
    if task.user_id is not None:
        if current_user is None:
            raise AppError(401, "auth_required", headers={"WWW-Authenticate": "Bearer"})
        if current_user.id != task.user_id:
            raise AppError(404, "task_not_found")
    return schemas.TaskStatusOut(
        task_id=task.id,
        status=task.status,
        step=task.step,
        progress=task.progress,
        result=task.result,
        error=task.error,
    )
