import os
import logging
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import BASE_DIR, DATA_DIR, UPLOADS_DIR, DATABASE_URL

logger = logging.getLogger("checkai.db")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        """SQLite по умолчанию не проверяет внешние ключи — включаем для каждого соединения."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# Ревизия, которая соответствует схеме, созданной до перехода на Alembic
BASELINE_REVISION = "0001_baseline"


def _alembic_config():
    from alembic.config import Config

    cfg = Config(os.path.join(BASE_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BASE_DIR, "migrations"))
    return cfg


def _adopt_legacy_database(cfg) -> None:
    """База, созданная через create_all до Alembic: доводим до baseline и помечаем её."""
    from alembic import command
    from app import models

    tables = set(inspect(engine).get_table_names())
    if "alembic_version" in tables or "receipts" not in tables:
        return

    logger.info("Найдена база без истории миграций — помечаем её ревизией %s", BASELINE_REVISION)
    if "users" not in tables:
        models.User.__table__.create(bind=engine)
    if "receipt_items" not in tables:
        models.ReceiptItem.__table__.create(bind=engine)
    receipt_columns = {c["name"] for c in inspect(engine).get_columns("receipts")}
    if "user_id" not in receipt_columns:
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE receipts ADD COLUMN user_id INTEGER REFERENCES users(id)")
    command.stamp(cfg, BASELINE_REVISION)


def init_db():
    """Применяет миграции Alembic до последней версии (с мягким fallback при отсутствии пакета alembic)."""
    try:
        from alembic import command

        cfg = _alembic_config()
        _adopt_legacy_database(cfg)
        command.upgrade(cfg, "head")
    except ImportError:
        logger.warning("Пакет alembic не установлен в окружении — создаём таблицы через metadata.create_all")
        from app import models  # noqa: F401
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            try:
                item_cols = [c["name"] for c in inspect(engine).get_columns("receipt_items")]
                if "category" not in item_cols:
                    conn.exec_driver_sql("ALTER TABLE receipt_items ADD COLUMN category VARCHAR(100)")
            except Exception:
                pass
            try:
                receipt_cols = [c["name"] for c in inspect(engine).get_columns("receipts")]
                if "user_id" not in receipt_cols:
                    conn.exec_driver_sql("ALTER TABLE receipts ADD COLUMN user_id INTEGER REFERENCES users(id)")
            except Exception:
                pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
