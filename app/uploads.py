"""Файлы на диске: фото чеков (загрузка, проверка пути, удаление, очистка несохранённых) и фото профиля."""
import hashlib
import io
import logging
import os
import re
import threading
import time
import uuid
from typing import Optional, Tuple

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app import config, database, models
from app.i18n import AppError

logger = logging.getLogger("checkai")

# --- Фото чеков ---

UPLOADS_DIR = config.UPLOADS_DIR
os.makedirs(UPLOADS_DIR, exist_ok=True)

RECEIPTS_URL_PREFIX = "/data/receipts"
MAX_UPLOAD_BYTES = config.MAX_UPLOAD_MB * 1024 * 1024

# Расширения, которые принимаем от клиента, и расширение по MIME-типу, если в имени его нет
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
EXTENSION_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}
# Файл на диске называется только <uuid>.<ext>
IMAGE_PATH_RE = re.compile(
    rf"^{re.escape(RECEIPTS_URL_PREFIX)}/([0-9a-f]{{32}}(?:{'|'.join(re.escape(e) for e in sorted(ALLOWED_EXTENSIONS))}))$"
)


def file_sha256(path: str) -> Optional[str]:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _pick_extension(file: UploadFile) -> str:
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext in ALLOWED_EXTENSIONS:
        return ".jpg" if ext == ".jpeg" else ext
    ext = EXTENSION_BY_MIME.get((file.content_type or "").lower())
    if ext:
        return ext
    raise AppError(415, "unsupported_image_type")


def save_upload(file: UploadFile) -> Tuple[str, str, str]:
    """Сохраняет файл как <uuid>.<ext> с проверкой размера. Возвращает (путь на диске, имя файла, SHA-256)."""
    ext = _pick_extension(file)
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOADS_DIR, filename)
    size = 0
    # Хеш считается, пока файл пишется на диск: по нему ищется тот же чек
    digest = hashlib.sha256()
    try:
        with open(file_path, "wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise AppError(413, "file_too_big", mb=config.MAX_UPLOAD_MB)
                digest.update(chunk)
                buffer.write(chunk)
    except BaseException:
        remove_quietly(file_path)
        raise
    if size == 0:
        remove_quietly(file_path)
        raise AppError(422, "file_empty")
    return file_path, filename, digest.hexdigest()


def resolve_image_path(image_path: Optional[str]) -> Optional[str]:
    """Принимаем путь к изображению чека (включая старые форматы) при условии существования файла."""
    if not image_path:
        return None
    match = IMAGE_PATH_RE.match(image_path)
    if match and os.path.isfile(os.path.join(UPLOADS_DIR, match.group(1))):
        return image_path
    prefix = f"{RECEIPTS_URL_PREFIX}/"
    if image_path.startswith(prefix):
        filename = image_path[len(prefix):]
        # Защита от path traversal: только имя файла в папке UPLOADS_DIR
        if filename and os.path.basename(filename) == filename:
            ext = os.path.splitext(filename)[1].lower()
            if ext in ALLOWED_EXTENSIONS and os.path.isfile(os.path.join(UPLOADS_DIR, filename)):
                return image_path
    raise AppError(422, "invalid_image_path")


def image_disk_path(image_path: Optional[str]) -> Optional[str]:
    """Файл на диске для пути, уже проверенного resolve_image_path."""
    prefix = f"{RECEIPTS_URL_PREFIX}/"
    if not image_path or not image_path.startswith(prefix):
        return None
    filename = image_path[len(prefix):]
    if not filename or os.path.basename(filename) != filename:
        return None
    return os.path.join(UPLOADS_DIR, filename)


def delete_image_file(image_path: Optional[str]) -> None:
    if not image_path:
        return
    match = IMAGE_PATH_RE.match(image_path)
    if match:
        remove_quietly(os.path.join(UPLOADS_DIR, match.group(1)))
        return
    prefix = f"{RECEIPTS_URL_PREFIX}/"
    if image_path.startswith(prefix):
        filename = image_path[len(prefix):]
        if filename and os.path.basename(filename) == filename:
            ext = os.path.splitext(filename)[1].lower()
            if ext in ALLOWED_EXTENSIONS:
                remove_quietly(os.path.join(UPLOADS_DIR, filename))


def cleanup_orphan_images(db: Session, max_age_seconds: int = 86400) -> int:
    """Удаляет файлы из UPLOADS_DIR старше max_age_seconds, не сохранённые в чеках."""
    if not os.path.isdir(UPLOADS_DIR):
        return 0
    now = time.time()
    deleted_count = 0
    for filename in os.listdir(UPLOADS_DIR):
        # Трогаем только фото чеков (служебные файлы вроде .gitkeep пропускаем)
        if filename.startswith(".") or os.path.splitext(filename)[1].lower() not in ALLOWED_EXTENSIONS:
            continue
        file_path = os.path.join(UPLOADS_DIR, filename)
        if not os.path.isfile(file_path):
            continue
        try:
            mtime = os.path.getmtime(file_path)
            if now - mtime < max_age_seconds:
                continue
        except OSError:
            continue
        rel_path = f"{RECEIPTS_URL_PREFIX}/{filename}"
        count = db.query(models.Receipt.id).filter(models.Receipt.image_path == rel_path).count()
        if count == 0:
            remove_quietly(file_path)
            deleted_count += 1
    return deleted_count


def start_orphan_cleanup_thread() -> Optional[threading.Event]:
    """Сразу после старта и затем раз в ORPHAN_CLEANUP_HOURS часов. Возвращает событие для остановки."""
    if config.ORPHAN_CLEANUP_HOURS <= 0:
        return None
    stop = threading.Event()

    def _worker():
        while not stop.is_set():
            try:
                with database.SessionLocal() as db:
                    count = cleanup_orphan_images(db, max_age_seconds=86400)
                    if count:
                        logger.info("Удалено %d осиротевших фото чеков", count)
            except Exception:
                logger.exception("Ошибка фоновой очистки осиротевших фото")
            stop.wait(config.ORPHAN_CLEANUP_HOURS * 3600)

    threading.Thread(target=_worker, name="orphan-cleanup", daemon=True).start()
    return stop


# --- Фото профиля ---

AVATARS_DIR = config.AVATARS_DIR
os.makedirs(AVATARS_DIR, exist_ok=True)
# Фото профиля всегда сохраняется как <uuid>.jpg
AVATAR_PATH_RE = re.compile(rf"^{re.escape(config.AVATARS_URL_PREFIX)}/([0-9a-f]{{32}}\.jpg)$")
# Фронт присылает квадрат 256×256; больше этого на сервере не храним
AVATAR_MAX_SIDE = 512
AVATAR_MAX_PIXELS = 40_000_000
AVATAR_FORMATS = {"JPEG", "PNG", "WEBP"}
AVATAR_MAX_MB = config.MAX_AVATAR_BYTES // (1024 * 1024)


def _avatar_file(avatar_path: Optional[str]) -> Optional[str]:
    """Путь на диске по avatar_path или None, если путь не наш."""
    match = AVATAR_PATH_RE.match(avatar_path or "")
    return os.path.join(AVATARS_DIR, match.group(1)) if match else None


def delete_avatar_file(avatar_path: Optional[str]) -> None:
    path = _avatar_file(avatar_path)
    if path:
        remove_quietly(path)


def _read_avatar(file: UploadFile) -> Image.Image:
    """Читает загруженное фото и проверяет, что это действительно JPEG/PNG/WEBP — по содержимому, а не по Content-Type."""
    data = file.file.read(config.MAX_AVATAR_BYTES + 1)
    if len(data) > config.MAX_AVATAR_BYTES:
        raise AppError(413, "avatar_too_big", mb=AVATAR_MAX_MB)
    if not data:
        raise AppError(400, "file_empty")
    unsupported = AppError(400, "avatar_unsupported")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in AVATAR_FORMATS:
                raise unsupported
            if probe.width * probe.height > AVATAR_MAX_PIXELS:
                raise AppError(400, "avatar_resolution")
            probe.verify()
        # После verify() картинку нужно открыть заново
        image = Image.open(io.BytesIO(data))
        image.load()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise unsupported
    return image


def save_avatar(file: UploadFile) -> Tuple[str, str]:
    """Сохраняет фото профиля как JPEG без метаданных под новым именем. Возвращает (avatar_path, путь на диске)."""
    image = _read_avatar(file)
    try:
        # Поворот по EXIF, затем без EXIF (там бывают координаты съёмки)
        image = ImageOps.exif_transpose(image)
        if image.mode != "RGB":
            background = Image.new("RGB", image.size, (255, 255, 255))
            rgba = image.convert("RGBA")
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        image.thumbnail((AVATAR_MAX_SIDE, AVATAR_MAX_SIDE))
        # Каждый раз новое имя: браузер не покажет старое фото из кэша
        filename = f"{uuid.uuid4().hex}.jpg"
        file_path = os.path.join(AVATARS_DIR, filename)
        image.save(file_path, "JPEG", quality=88, optimize=True)
    finally:
        image.close()
    return f"{config.AVATARS_URL_PREFIX}/{filename}", file_path
