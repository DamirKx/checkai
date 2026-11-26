"""Пользователи: поиск, создание, удаление вместе с чеками."""
from typing import List, Optional
from sqlalchemy.orm import Session
from app import models
from app.categories import create_default_categories
from app.currencies import DEFAULT_CURRENCY


def get_user_by_email(db: Session, email: str) -> Optional[models.User]:
    """Поиск пользователя по email."""
    return db.query(models.User).filter(models.User.email == email.strip().lower()).first()

def get_user_by_id(db: Session, user_id: int) -> Optional[models.User]:
    """Поиск пользователя по id."""
    return db.query(models.User).filter(models.User.id == user_id).first()

def create_user(
    db: Session,
    email: str,
    hashed_password: str,
    full_name: Optional[str] = None,
    base_currency: str = DEFAULT_CURRENCY,
    recovery_code_hash: Optional[str] = None,
) -> models.User:
    """Создание нового пользователя."""
    user = models.User(
        email=email.strip().lower(),
        hashed_password=hashed_password,
        full_name=full_name.strip() if full_name else None,
        base_currency=base_currency or DEFAULT_CURRENCY,
        recovery_code_hash=recovery_code_hash,
    )
    create_default_categories(db, user)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def delete_user(db: Session, user: models.User) -> List[str]:
    """Удаляет пользователя и его чеки (cascade). Возвращает список image_path для очистки файлов."""
    image_paths = [r.image_path for r in user.receipts if r.image_path]
    db.delete(user)
    db.commit()
    return image_paths
