import logging

from alembic import context

from app.database import Base, engine
from app import models  # noqa: F401  — регистрирует таблицы в Base.metadata

target_metadata = Base.metadata
logger = logging.getLogger("checkai.db")


def run_migrations_offline() -> None:
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_on(connection) -> None:
    is_sqlite = connection.dialect.name == "sqlite"
    if is_sqlite:
        # Batch-режим пересоздаёт таблицу: DROP старой при включённых внешних ключах
        # упадёт (на receipts ссылаются receipt_items) или каскадно удалит строки.
        # PRAGMA внутри транзакции SQLite игнорирует, поэтому — до неё.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
    try:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite не умеет большинство ALTER TABLE — batch-режим пересоздаёт таблицу
            render_as_batch=is_sqlite,
        )
        with context.begin_transaction():
            context.run_migrations()
        if is_sqlite:
            # Не падаем: в старых базах ключи не проверялись, и «висячие» ссылки могли остаться
            broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if broken:
                logger.warning("В базе нарушены внешние ключи (первые 5): %s", broken[:5])
    finally:
        if is_sqlite:
            # Соединение вернётся в пул — приложению нужны включённые внешние ключи
            connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def run_migrations_online() -> None:
    # Тест миграций передаёт своё соединение к отдельной базе через Config.attributes
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run_on(connection)
        return
    with engine.connect() as connection:
        _run_on(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
