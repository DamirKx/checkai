"""Аккаунт: регистрация, вход, обновление токенов, данные пользователя, удаление, восстановление пароля."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import auth, crud, database, models, rates, schemas, uploads
from app.i18n import AppError

logger = logging.getLogger("checkai")

router = APIRouter(tags=["Auth"])


def _token_response(user: models.User, recovery_code: Optional[str] = None) -> schemas.TokenOut:
    access_token, refresh_token = auth.issue_tokens(user)
    return schemas.TokenOut(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=schemas.UserOut.model_validate(user),
        recovery_code=recovery_code,
    )


@router.post("/api/auth/register", response_model=schemas.TokenOut)
def register(user_data: schemas.UserCreate, db: Session = Depends(database.get_db)):
    """Регистрация нового пользователя (email и длина пароля проверяются в схеме → 422)."""
    email = user_data.email.strip().lower()

    existing_user = crud.get_user_by_email(db, email=email)
    if existing_user:
        raise AppError(400, "email_taken")

    hashed_pw = auth.hash_password(user_data.password)
    # Код восстановления показывается один раз — фронт просит его сохранить
    recovery_code = auth.generate_recovery_code()
    user = crud.create_user(
        db=db,
        email=email,
        hashed_password=hashed_pw,
        full_name=user_data.full_name,
        recovery_code_hash=auth.hash_recovery_code(recovery_code),
    )

    return _token_response(user, recovery_code)


@router.post("/api/auth/login", response_model=schemas.TokenOut)
def login(user_data: schemas.UserLogin, db: Session = Depends(database.get_db)):
    """Вход пользователя по email и паролю."""
    email = user_data.email.strip().lower()
    user = crud.get_user_by_email(db, email=email)
    if not user or not auth.verify_password(user_data.password, user.hashed_password):
        raise AppError(400, "wrong_credentials")

    user.last_login_at = models.utcnow()
    db.commit()
    db.refresh(user)
    return _token_response(user)


@router.post("/api/auth/refresh", response_model=schemas.TokenOut)
def refresh_token(data: schemas.RefreshTokenInput, db: Session = Depends(database.get_db)):
    """Обновление access токена с помощью refresh токена."""
    user = auth.user_from_refresh_token(data.refresh_token, db)
    if user is None:
        # Токен истёк, подделан, пользователь удалён или сменил пароль — нужно войти заново
        raise AppError(401, "session_expired")

    return _token_response(user)


@router.get("/api/auth/me", response_model=schemas.UserOut)
def get_me(current_user: models.User = Depends(auth.get_current_user)):
    """Получение информации о текущем пользователе по JWT токену."""
    return current_user


@router.patch("/api/auth/me", response_model=schemas.TokenOut)
def update_me(
    user_data: schemas.UserUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Обновление профиля пользователя (имя, email, пароль, основная валюта)."""
    if user_data.email is not None or user_data.new_password is not None:
        if not user_data.current_password:
            raise AppError(400, "current_password_required")
        if not auth.verify_password(user_data.current_password, current_user.hashed_password):
            raise AppError(400, "wrong_current_password")

    if user_data.email and user_data.email != current_user.email:
        existing_user = crud.get_user_by_email(db, email=user_data.email)
        if existing_user and existing_user.id != current_user.id:
            raise AppError(400, "email_taken")
        current_user.email = user_data.email

    if user_data.new_password:
        current_user.hashed_password = auth.hash_password(user_data.new_password)

    if "full_name" in user_data.model_fields_set:
        current_user.full_name = user_data.full_name.strip() if user_data.full_name else None

    base_changed = bool(user_data.base_currency) and user_data.base_currency != current_user.base_currency
    if base_changed:
        current_user.base_currency = user_data.base_currency

    # Тема и акцент — не данные входа, текущий пароль для них не нужен
    if "preferences" in user_data.model_fields_set:
        if user_data.preferences is None:
            current_user.preferences = {}
        else:
            merged = {**schemas.Preferences.from_stored(current_user.preferences).model_dump(),
                      **user_data.preferences.model_dump(exclude_unset=True)}
            # null у ключа — значение по умолчанию; новый dict, чтобы SQLAlchemy заметил изменение JSON
            current_user.preferences = schemas.Preferences.from_stored(
                {k: v for k, v in merged.items() if v is not None}
            ).model_dump()

    # Согласие на ИИ-анализ — то же поле preferences.ai_insights, пароль не нужен
    if user_data.ai_insights_consent is not None:
        prefs = schemas.Preferences.from_stored(current_user.preferences).model_dump()
        current_user.preferences = {**prefs, "ai_insights": user_data.ai_insights_consent}

    db.commit()
    if base_changed:
        # Курсы на дни старых чеков докачиваются в фоне; пока их нет, чеки уходят в unconverted,
        # а UserOut.rates_pending = true
        rates.run_user_backfill(crud.backfill_user_rates, current_user.id)
    db.refresh(current_user)

    return _token_response(current_user)


@router.delete("/api/auth/me")
def delete_me(
    data: schemas.UserDelete,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Удаление аккаунта текущего пользователя со всеми его чеками, их фото и фото профиля."""
    if not auth.verify_password(data.password, current_user.hashed_password):
        raise AppError(400, "wrong_password")

    avatar_path = current_user.avatar_path
    image_paths = crud.delete_user(db, current_user)
    for path in set(image_paths):
        if crud.count_receipts_with_image(db, path) == 0:
            uploads.delete_image_file(path)
    uploads.delete_avatar_file(avatar_path)

    return {"success": True}


@router.post("/api/auth/recovery-code", response_model=schemas.RecoveryCodeOut)
def create_recovery_code(
    data: schemas.RecoveryCodeRequest,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Новый код восстановления (старый перестаёт действовать). Нужен текущий пароль."""
    if not auth.verify_password(data.password, current_user.hashed_password):
        raise AppError(400, "wrong_password")
    recovery_code = auth.generate_recovery_code()
    current_user.recovery_code_hash = auth.hash_recovery_code(recovery_code)
    db.commit()
    return {"recovery_code": recovery_code}


@router.post("/api/auth/reset-password", response_model=schemas.TokenOut)
def reset_password(
    data: schemas.ResetPasswordRequest,
    db: Session = Depends(database.get_db),
):
    """Сброс забытого пароля по email и коду восстановления — без почты.

    Код одноразовый: в ответе новый код, пара токенов (пользователь сразу входит) и пользователь.
    Старые токены перестают действовать — пароль сменился.
    """
    user = crud.get_user_by_email(db, email=data.email)
    # Проверка кода идёт и для несуществующего email — по времени ответа не понять, есть ли такой пользователь
    if not auth.verify_recovery_code(data.recovery_code, user.recovery_code_hash if user else None):
        raise AppError(400, "wrong_recovery_code")

    recovery_code = auth.generate_recovery_code()
    user.hashed_password = auth.hash_password(data.new_password)
    user.recovery_code_hash = auth.hash_recovery_code(recovery_code)
    user.last_login_at = models.utcnow()
    db.commit()
    db.refresh(user)

    logger.info("Пароль сброшен по коду восстановления, пользователь id=%s", user.id)
    return _token_response(user, recovery_code)
