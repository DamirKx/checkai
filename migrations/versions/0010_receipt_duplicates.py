"""Защита от дубликатов: хеш фото и фискальный номер чека (NEXT_WEEK_TASKS.md, C.2.1)

Revision ID: 0010_receipt_duplicates
Revises: 0009_insights
Create Date: 2026-11-12
"""
import hashlib
import logging
import os

from alembic import op
import sqlalchemy as sa

revision = "0010_receipt_duplicates"
down_revision = "0009_insights"
branch_labels = None
depends_on = None

logger = logging.getLogger("checkai.db")
RECEIPTS_URL_PREFIX = "/data/receipts/"


def _file_hash(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fill_image_hashes(bind) -> None:
    """Хеш для старых чеков, у которых файл фото ещё лежит в UPLOADS_DIR."""
    from app import config

    rows = bind.execute(sa.text(
        "SELECT id, image_path FROM receipts WHERE image_path IS NOT NULL AND image_hash IS NULL"
    )).fetchall()
    filled = 0
    for receipt_id, image_path in rows:
        if not image_path.startswith(RECEIPTS_URL_PREFIX):
            continue
        filename = image_path[len(RECEIPTS_URL_PREFIX):]
        # Только имя файла внутри UPLOADS_DIR
        if not filename or os.path.basename(filename) != filename:
            continue
        path = os.path.join(config.UPLOADS_DIR, filename)
        if not os.path.isfile(path):
            continue
        try:
            digest = _file_hash(path)
        except OSError:
            continue
        bind.execute(sa.text("UPDATE receipts SET image_hash = :h WHERE id = :id"), {"h": digest, "id": receipt_id})
        filled += 1
    if filled:
        logger.info("Посчитан хеш фото для %d сохранённых чеков", filled)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("receipts")}
    if "image_hash" not in columns:
        op.add_column("receipts", sa.Column("image_hash", sa.String(64), nullable=True))
    if "fiscal_id" not in columns:
        op.add_column("receipts", sa.Column("fiscal_id", sa.String(64), nullable=True))
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("receipts")}
    if "ix_receipts_user_image_hash" not in indexes:
        op.create_index("ix_receipts_user_image_hash", "receipts", ["user_id", "image_hash"])
    if "ix_receipts_user_fiscal_id" not in indexes:
        op.create_index("ix_receipts_user_fiscal_id", "receipts", ["user_id", "fiscal_id"])
    _fill_image_hashes(bind)


def downgrade() -> None:
    op.drop_index("ix_receipts_user_fiscal_id", table_name="receipts")
    op.drop_index("ix_receipts_user_image_hash", table_name="receipts")
    with op.batch_alter_table("receipts") as batch:
        batch.drop_column("fiscal_id")
        batch.drop_column("image_hash")
