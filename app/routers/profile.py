"""Профиль: фото профиля и сводка (цифры, активность, привычки)."""
from datetime import timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app import auth, crud, database, models, schemas, uploads
from app.i18n import AppError

router = APIRouter()


@router.post("/api/auth/me/avatar", response_model=schemas.UserOut, tags=["Auth"])
def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Загрузка фото профиля (multipart, поле file). Сохраняется JPEG без метаданных под новым именем."""
    avatar_path, file_path = uploads.save_avatar(file)

    old_path = current_user.avatar_path
    current_user.avatar_path = avatar_path
    try:
        db.commit()
    except Exception:
        uploads.remove_quietly(file_path)
        raise
    uploads.delete_avatar_file(old_path)
    db.refresh(current_user)
    return current_user


@router.delete("/api/auth/me/avatar", response_model=schemas.UserOut, tags=["Auth"])
def delete_avatar(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Удаление фото профиля. Без фото — тоже 200, avatar_url: null."""
    old_path = current_user.avatar_path
    current_user.avatar_path = None
    db.commit()
    uploads.delete_avatar_file(old_path)
    db.refresh(current_user)
    return current_user


@router.get("/api/profile/stats", response_model=schemas.ProfileStatsOut, tags=["Profile"])
def get_profile_stats(
    tz: Optional[str] = Query(None, description="Часовой пояс IANA, например Asia/Almaty (по умолчанию UTC)"),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Цифры, тепловая карта и привычки профиля одним запросом."""
    try:
        zone = ZoneInfo(tz.strip()) if tz and tz.strip() else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        raise AppError(422, "invalid_tz")
    return crud.get_profile_stats(db, current_user, zone)
