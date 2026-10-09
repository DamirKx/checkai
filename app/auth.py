import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app import config
from app import database
from app import models

logger = logging.getLogger("checkai.auth")

# Секретный ключ для подписи JWT — только из окружения, без значения по умолчанию.
SECRET_KEY = config.SECRET_KEY
if not SECRET_KEY:
    raise RuntimeError(
        "Не задана переменная окружения SECRET_KEY. "
        "Скопируйте .env.example в .env и укажите случайный ключ, например: "
        "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    )
if len(SECRET_KEY) < 32:
    logger.warning("SECRET_KEY короче 32 символов — используйте более длинный случайный ключ")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

def hash_password(password: str) -> str:
    """Хэширование пароля с помощью bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Сверка пароля с хэшем."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Создание JWT access токена (короткий срок жизни — 60 минут)."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Создание долгоживущего JWT refresh токена (30 дней)."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str) -> Optional[dict]:
    """Декодирование и проверка JWT токена."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


def _user_from_token(token: Optional[str], db: Session) -> Optional[models.User]:
    if not token:
        return None
    payload = decode_access_token(token)
    if payload is None:
        return None
    # Refresh токен нельзя использовать для доступа к обычным защищённым эндпоинтам
    if payload.get("type") == "refresh":
        return None
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None
    return db.query(models.User).filter(models.User.id == user_id).first()


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(database.get_db)
) -> models.User:
    """Получение текущего авторизованного пользователя из токена."""
    user = _user_from_token(token, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Необходима авторизация",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

def get_optional_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(database.get_db)
) -> Optional[models.User]:
    """Опциональный пользователь: возвращает User, если токен передан и валиден, иначе None."""
    return _user_from_token(token, db)
