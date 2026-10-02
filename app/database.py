import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "receipts"), exist_ok=True)

DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'checkai.db')}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def init_db():
    """Создание таблиц и мягкая авто-миграция для SQLite."""
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        try:
            result = conn.exec_driver_sql("PRAGMA table_info(receipts)").fetchall()
            cols = [r[1] for r in result]
            if cols and "user_id" not in cols:
                conn.exec_driver_sql("ALTER TABLE receipts ADD COLUMN user_id INTEGER REFERENCES users(id)")
                conn.commit()
        except Exception:
            pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
