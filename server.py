import io
import os
import re
import time
import uuid
import hashlib
import logging
import math
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, File, UploadFile, Request, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session
import uvicorn

from app import config
from app import database
from app import models
from app import schemas
from app import crud
from app import auth
from app import currencies
from app import rates
from app import insights as insights_service
from app.categories import CategoryError, DEFAULT_CATEGORIES, apply_rules
from app.duplicates import fiscal_id_from
from app.rate_limit import limiter, get_client_ip
from app.dates import normalize_date_time, split_date_time
from app.ocr import init_ocr, analyze_receipt
from app.tasks import task_manager, insights_task_manager

logger = logging.getLogger("checkai")

# Применяем миграции Alembic (создают таблицы в новой базе)
database.init_db()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Фоновая очистка стартует вместе с сервером, а не при импорте модуля (импортируют и тесты)
    stop_cleanup = _start_orphan_cleanup_thread()
    yield
    if stop_cleanup is not None:
        stop_cleanup.set()
    task_manager._executor.shutdown(wait=False)
    insights_task_manager._executor.shutdown(wait=False)


app = FastAPI(title="CheckAI — Анализ кассовых чеков", version="2.1.0", lifespan=lifespan)

BASE_DIR = config.BASE_DIR
UPLOADS_DIR = config.UPLOADS_DIR
os.makedirs(UPLOADS_DIR, exist_ok=True)

RECEIPTS_URL_PREFIX = "/data/receipts"
TEST_IMAGE_PATH = os.path.join(BASE_DIR, "images.jpeg")
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

# Шаблоны интерфейса находятся в папке frontend.
FRONTEND_TEMPLATES_DIR = os.path.join(BASE_DIR, "frontend", "templates")
os.makedirs(FRONTEND_TEMPLATES_DIR, exist_ok=True)

# Статикой раздаём ТОЛЬКО фото чеков и фото профиля. Папка data/ целиком закрыта — там лежит checkai.db.
app.mount(RECEIPTS_URL_PREFIX, StaticFiles(directory=UPLOADS_DIR), name="receipts")
AVATARS_DIR = config.AVATARS_DIR
os.makedirs(AVATARS_DIR, exist_ok=True)
app.mount(config.AVATARS_URL_PREFIX, StaticFiles(directory=AVATARS_DIR), name="avatars")
# Фото профиля всегда сохраняется как <uuid>.jpg
AVATAR_PATH_RE = re.compile(rf"^{re.escape(config.AVATARS_URL_PREFIX)}/([0-9a-f]{{32}}\.jpg)$")
# Фронт присылает квадрат 256×256; больше этого на сервере не храним
AVATAR_MAX_SIDE = 512
AVATAR_MAX_PIXELS = 40_000_000
AVATAR_FORMATS = {"JPEG", "PNG", "WEBP"}
AVATAR_TOO_BIG = f"Фото слишком большое. Максимум — {config.MAX_AVATAR_BYTES // (1024 * 1024)} МБ"
# Стили и скрипты интерфейса
FRONTEND_STATIC_DIR = os.path.join(BASE_DIR, "frontend", "static")
os.makedirs(FRONTEND_STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=FRONTEND_STATIC_DIR), name="static")
templates = Jinja2Templates(directory=FRONTEND_TEMPLATES_DIR)


# Ограничение частоты запросов (макс. запросов, секунд в окне)
RATE_LIMIT_RULES = {
    "/api/auth/login": (10, 60),            # защита от перебора паролей
    "/api/auth/register": (10, 60),         # защита от спама регистраций
    "/api/auth/reset-password": (5, 60),    # защита от перебора кодов восстановления
    "/api/auth/recovery-code": (5, 60),     # защита от перебора пароля
    "/api/analyze": (15, 60),               # защита от перегрузки OCR
    "/api/analyze/async": (15, 60),         # защита от перегрузки OCR
}
# Лимиты, которые зависят и от метода (GET того же пути не ограничиваем)
RATE_LIMIT_METHOD_RULES = {
    ("POST", "/api/categories"): (30, 60),  # защита от спама категориями
    ("POST", "/api/auth/me/avatar"): (20, 60),  # перекодирование картинки — не бесплатное
}


@app.middleware("http")
async def upload_limit_and_headers(request: Request, call_next):
    # Ограничение частоты запросов
    rule = RATE_LIMIT_RULES.get(request.url.path) or RATE_LIMIT_METHOD_RULES.get((request.method, request.url.path))
    if rule:
        max_requests, window_seconds = rule
        client_ip = get_client_ip(request)
        allowed, retry_after = limiter.check_limit(
            f"{request.method}:{request.url.path}:{client_ip}", max_requests, window_seconds
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Слишком много запросов. Пожалуйста, попробуйте позже."},
                headers={"Retry-After": str(retry_after)},
            )

    # Отсекаем заведомо большие загрузки ещё до разбора multipart
    if request.url.path in ("/api/analyze", "/api/analyze/async"):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES + 64 * 1024:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Файл слишком большой. Максимум — {config.MAX_UPLOAD_MB} МБ"},
            )
    if request.url.path == "/api/auth/me/avatar" and request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > config.MAX_AVATAR_BYTES + 64 * 1024:
            return JSONResponse(status_code=413, content={"detail": AVATAR_TOO_BIG})
    response = await call_next(request)
    # Браузер не должен «угадывать» тип загруженных файлов
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Отдаёт ошибки валидации без технической приставки 'Value error, ' / 'Assertion failed, '."""
    errors = []
    for err in exc.errors():
        err_dict = dict(err)
        msg = err_dict.get("msg", "")
        msg = re.sub(r"^(Value error|Assertion failed),\s*", "", msg)
        err_dict["msg"] = msg
        errors.append(err_dict)
    return JSONResponse(
        status_code=422,
        content={"detail": _json_safe(jsonable_encoder(errors))},
    )


def _json_safe(value):
    """NaN и Infinity из присланного input нельзя записать в JSON: без этого ответ 422 стал бы 500."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value


@app.exception_handler(CategoryError)
async def category_error_handler(request: Request, exc: CategoryError):
    """Ошибки из crud по категориям (чужая категория, дубль названия, лимит 50) → {"detail": "текст"}."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


# --- OCR: одна модель на процесс, распознавание по одному запросу за раз ---

_ocr_model = None
_ocr_init_lock = threading.Lock()
_ocr_run_lock = threading.Lock()


def get_ocr():
    global _ocr_model
    if _ocr_model is None:
        with _ocr_init_lock:
            if _ocr_model is None:
                _ocr_model = init_ocr()
    return _ocr_model


def _categories_for(user: Optional[models.User]) -> List[dict]:
    """Категории, по которым ML раскладывает товары: свои у пользователя, стандартные у гостя (id = None)."""
    if user is not None:
        return [{"id": c.id, "name": c.name, "description": c.description} for c in user.categories]
    return [{"id": None, "name": d["name"], "description": d["description"]} for d in DEFAULT_CATEGORIES]


def _run_ocr(image_path: str, categories: Optional[List[dict]] = None) -> dict:
    """Распознаёт чек. Вызывается из синхронных эндпоинтов, т.е. в пуле потоков, а не в event loop."""
    ocr = get_ocr()
    # PaddleOCR не потокобезопасен — параллельные запросы ждут очереди,
    # но остальные эндпоинты сервера в это время отвечают.
    with _ocr_run_lock:
        data = analyze_receipt(image_path, ocr=ocr, categories=categories)
    if not data.get("raw_lines"):
        raise HTTPException(status_code=422, detail="На изображении не удалось найти текст чека")
    data["date"], data["time"] = normalize_date_time(data.get("date"), data.get("time"))
    cur = data.get("currency")
    if not cur or cur not in currencies.CURRENCIES:
        data["currency"] = "KZT"
        data["currency_source"] = "default"
    else:
        data["currency_source"] = data.get("currency_source") or "default"
    # Ключи есть всегда, в том числе в асинхронном пути /api/tasks/{id} (PROFILE_TASKS.md, п. 2.1, 2.2, 2.3)
    data.setdefault("merchant", None)
    data.setdefault("confidence", None)
    # Реквизиты фискального чека от ML (NEXT_WEEK_TASKS.md, C.3) или null
    data.setdefault("fiscal", None)
    for item in data.get("items") or []:
        for key in ("category_id", "category", "category_source"):
            item.setdefault(key, None)
    return data


def _analyze(db: Session, image_path: str, user: Optional[models.User], image_hash: Optional[str] = None) -> dict:
    """Распознавание с категориями. Правила пользователя важнее ML и применяются вне блокировки OCR."""
    data = _run_ocr(image_path, _categories_for(user))
    if user is not None:
        apply_rules(db, user.id, data.get("items") or [])
    _mark_duplicate(db, user.id if user else None, data, image_hash)
    return data


def _mark_duplicate(db: Session, user_id: Optional[int], data: dict, image_hash: Optional[str]) -> None:
    """duplicate_of в ответе распознавания (C.2.2): у пользователя уже есть чек с тем же фото
    или фискальным номером. По содержимому здесь не ищем — это делает фронт, а при сохранении сервер.
    У гостя и без совпадений — null. Распознавание не прерывается."""
    data["duplicate_of"] = None
    if user_id is None:
        return
    found = crud.find_duplicate(
        db, user_id, data, image_hash=image_hash, fiscal_id=fiscal_id_from(data.get("fiscal")), by_content=False,
    )
    data["duplicate_of"] = found.id if found is not None else None


# Фискальный номер распознанного, но ещё не сохранённого чека: фронт его обратно не присылает.
# В памяти процесса — после перезапуска сервера номер теряется, и чек просто проверяется по содержимому
_FISCAL_BY_IMAGE: "OrderedDict[str, str]" = OrderedDict()
_FISCAL_CACHE_MAX = 2000
_fiscal_lock = threading.Lock()


def _remember_fiscal(image_url: str, data: dict) -> None:
    fiscal_id = fiscal_id_from(data.get("fiscal"))
    if not fiscal_id:
        return
    with _fiscal_lock:
        _FISCAL_BY_IMAGE[image_url] = fiscal_id
        _FISCAL_BY_IMAGE.move_to_end(image_url)
        while len(_FISCAL_BY_IMAGE) > _FISCAL_CACHE_MAX:
            _FISCAL_BY_IMAGE.popitem(last=False)


def _fiscal_for(image_url: Optional[str]) -> Optional[str]:
    if not image_url:
        return None
    with _fiscal_lock:
        return _FISCAL_BY_IMAGE.get(image_url)


def _file_sha256(path: str) -> Optional[str]:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _remove_quietly(path: str) -> None:
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
    raise HTTPException(status_code=415, detail="Поддерживаются изображения JPG, PNG, WEBP, BMP или TIFF")


def _save_upload(file: UploadFile) -> Tuple[str, str, str]:
    """Сохраняет файл как <uuid>.<ext> с проверкой размера. Возвращает (путь на диске, имя файла, SHA-256)."""
    ext = _pick_extension(file)
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOADS_DIR, filename)
    size = 0
    # Хеш считается, пока файл пишется на диск: по нему ищется тот же чек (C.2.2)
    digest = hashlib.sha256()
    try:
        with open(file_path, "wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Файл слишком большой. Максимум — {config.MAX_UPLOAD_MB} МБ",
                    )
                digest.update(chunk)
                buffer.write(chunk)
    except BaseException:
        _remove_quietly(file_path)
        raise
    if size == 0:
        _remove_quietly(file_path)
        raise HTTPException(status_code=422, detail="Файл пустой")
    return file_path, filename, digest.hexdigest()


def _resolve_image_path(image_path: Optional[str]) -> Optional[str]:
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
    raise HTTPException(status_code=422, detail="Некорректный путь к изображению чека")


def _image_disk_path(image_path: Optional[str]) -> Optional[str]:
    """Файл на диске для пути, уже проверенного _resolve_image_path."""
    prefix = f"{RECEIPTS_URL_PREFIX}/"
    if not image_path or not image_path.startswith(prefix):
        return None
    filename = image_path[len(prefix):]
    if not filename or os.path.basename(filename) != filename:
        return None
    return os.path.join(UPLOADS_DIR, filename)


def _delete_image_file(image_path: Optional[str]) -> None:
    if not image_path:
        return
    match = IMAGE_PATH_RE.match(image_path)
    if match:
        _remove_quietly(os.path.join(UPLOADS_DIR, match.group(1)))
        return
    prefix = f"{RECEIPTS_URL_PREFIX}/"
    if image_path.startswith(prefix):
        filename = image_path[len(prefix):]
        if filename and os.path.basename(filename) == filename:
            ext = os.path.splitext(filename)[1].lower()
            if ext in ALLOWED_EXTENSIONS:
                _remove_quietly(os.path.join(UPLOADS_DIR, filename))


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
            _remove_quietly(file_path)
            deleted_count += 1
    return deleted_count


def _start_orphan_cleanup_thread() -> Optional[threading.Event]:
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


@app.get("/api/health", response_model=schemas.HealthCheckOut, tags=["System"])
def health_check(db: Session = Depends(database.get_db)):
    """Health-check эндпоинт для Docker и мониторинга: статус БД, OCR и Ollama."""
    from sqlalchemy import text

    # 1. База данных
    db_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("Healthcheck: ошибка соединения с БД: %s", e)
        db_status = f"error: {e}"

    # 2. OCR
    ocr_status = "ready" if _ocr_model is not None else "standby"

    # 3. Ollama (ИИ-парсер)
    use_ai = os.getenv("USE_AI_PARSER", "auto").strip().lower()
    if use_ai in ("0", "false", "no", "off"):
        ollama_status = "disabled"
    else:
        # Тот же адрес по умолчанию, что у клиента ollama в парсере (localhost). В Docker OLLAMA_HOST задаёт compose
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        if "://" not in ollama_host:
            ollama_host = "http://" + ollama_host
        try:
            import httpx
            resp = httpx.get(f"{ollama_host}/api/version", timeout=1.0)
            ollama_status = "available" if resp.status_code == 200 else f"http_{resp.status_code}"
        except Exception:
            ollama_status = "unavailable"

    is_healthy = db_status == "connected"
    overall_status = "healthy" if is_healthy else "unhealthy"

    if not is_healthy:
        return JSONResponse(
            status_code=503,
            content={
                "status": overall_status,
                "database": db_status,
                "ocr": ocr_status,
                "ollama": ollama_status,
            },
        )

    return schemas.HealthCheckOut(
        status=overall_status,
        database=db_status,
        ocr=ocr_status,
        ollama=ollama_status,
    )


@app.get("/api/currencies", response_model=List[schemas.CurrencyOut], tags=["Currencies"])
def get_currencies():
    """Список поддерживаемых валют (без авторизации)."""
    return currencies.get_currencies_list()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

def _token_response(user: models.User, recovery_code: Optional[str] = None) -> schemas.TokenOut:
    access_token, refresh_token = auth.issue_tokens(user)
    return schemas.TokenOut(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=schemas.UserOut.model_validate(user),
        recovery_code=recovery_code,
    )

# --- Аутентификация (Регистрация, Вход, Профиль) ---

@app.post("/api/auth/register", response_model=schemas.TokenOut, tags=["Auth"])
def register(user_data: schemas.UserCreate, db: Session = Depends(database.get_db)):
    """Регистрация нового пользователя (email и длина пароля проверяются в схеме → 422)."""
    email = user_data.email.strip().lower()

    existing_user = crud.get_user_by_email(db, email=email)
    if existing_user:
        raise HTTPException(status_code=400, detail="Пользователь с таким email уже зарегистрирован")

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

@app.post("/api/auth/login", response_model=schemas.TokenOut, tags=["Auth"])
def login(user_data: schemas.UserLogin, db: Session = Depends(database.get_db)):
    """Вход пользователя по email и паролю."""
    email = user_data.email.strip().lower()
    user = crud.get_user_by_email(db, email=email)
    if not user or not auth.verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Неверный email или пароль")

    user.last_login_at = models.utcnow()
    db.commit()
    db.refresh(user)
    return _token_response(user)

@app.post("/api/auth/refresh", response_model=schemas.TokenOut, tags=["Auth"])
def refresh_token(data: schemas.RefreshTokenInput, db: Session = Depends(database.get_db)):
    """Обновление access токена с помощью refresh токена."""
    user = auth.user_from_refresh_token(data.refresh_token, db)
    if user is None:
        # Токен истёк, подделан, пользователь удалён или сменил пароль — нужно войти заново
        raise HTTPException(status_code=401, detail="Сессия истекла, войдите снова")

    return _token_response(user)

@app.get("/api/auth/me", response_model=schemas.UserOut, tags=["Auth"])
def get_me(current_user: models.User = Depends(auth.get_current_user)):
    """Получение информации о текущем пользователе по JWT токену."""
    return current_user

@app.patch("/api/auth/me", response_model=schemas.TokenOut, tags=["Auth"])
def update_me(
    user_data: schemas.UserUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Обновление профиля пользователя (имя, email, пароль, основная валюта)."""
    if user_data.email is not None or user_data.new_password is not None:
        if not user_data.current_password:
            raise HTTPException(status_code=400, detail="Введите текущий пароль для смены email или пароля")
        if not auth.verify_password(user_data.current_password, current_user.hashed_password):
            raise HTTPException(status_code=400, detail="Неверный текущий пароль")

    if user_data.email and user_data.email != current_user.email:
        existing_user = crud.get_user_by_email(db, email=user_data.email)
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(status_code=400, detail="Пользователь с таким email уже зарегистрирован")
        current_user.email = user_data.email

    if user_data.new_password:
        current_user.hashed_password = auth.hash_password(user_data.new_password)

    if "full_name" in user_data.model_fields_set:
        current_user.full_name = user_data.full_name.strip() if user_data.full_name else None

    base_changed = bool(user_data.base_currency) and user_data.base_currency != current_user.base_currency
    if base_changed:
        current_user.base_currency = user_data.base_currency

    # Тема и акцент — не данные входа, текущий пароль для них не нужен (PROFILE_TASKS.md, п. 1.2)
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

    # Согласие на ИИ-анализ (B.2.5) — то же поле preferences.ai_insights, пароль не нужен
    if user_data.ai_insights_consent is not None:
        prefs = schemas.Preferences.from_stored(current_user.preferences).model_dump()
        current_user.preferences = {**prefs, "ai_insights": user_data.ai_insights_consent}

    db.commit()
    if base_changed:
        # Курсы на дни старых чеков докачиваются в фоне; пока их нет, чеки уходят в unconverted,
        # а UserOut.rates_pending = true (п. 1.6)
        rates.run_user_backfill(crud.backfill_user_rates, current_user.id)
    db.refresh(current_user)

    return _token_response(current_user)

@app.delete("/api/auth/me", tags=["Auth"])
def delete_me(
    data: schemas.UserDelete,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Удаление аккаунта текущего пользователя со всеми его чеками, их фото и фото профиля."""
    if not auth.verify_password(data.password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Неверный пароль")

    avatar_path = current_user.avatar_path
    image_paths = crud.delete_user(db, current_user)
    for path in set(image_paths):
        if crud.count_receipts_with_image(db, path) == 0:
            _delete_image_file(path)
    _delete_avatar_file(avatar_path)

    return {"success": True}


# --- Фото профиля (PROFILE_TASKS.md, п. 1.1) ---

def _avatar_file(avatar_path: Optional[str]) -> Optional[str]:
    """Путь на диске по avatar_path или None, если путь не наш."""
    match = AVATAR_PATH_RE.match(avatar_path or "")
    return os.path.join(AVATARS_DIR, match.group(1)) if match else None


def _delete_avatar_file(avatar_path: Optional[str]) -> None:
    path = _avatar_file(avatar_path)
    if path:
        _remove_quietly(path)


def _read_avatar(file: UploadFile) -> Image.Image:
    """Читает загруженное фото и проверяет, что это действительно JPEG/PNG/WEBP — по содержимому, а не по Content-Type."""
    data = file.file.read(config.MAX_AVATAR_BYTES + 1)
    if len(data) > config.MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail=AVATAR_TOO_BIG)
    if not data:
        raise HTTPException(status_code=400, detail="Файл пустой")
    unsupported = HTTPException(status_code=400, detail="Загрузите фото в формате JPG, PNG или WEBP")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in AVATAR_FORMATS:
                raise unsupported
            if probe.width * probe.height > AVATAR_MAX_PIXELS:
                raise HTTPException(status_code=400, detail="Фото слишком большого разрешения")
            probe.verify()
        # После verify() картинку нужно открыть заново
        image = Image.open(io.BytesIO(data))
        image.load()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise unsupported
    return image


@app.post("/api/auth/me/avatar", response_model=schemas.UserOut, tags=["Auth"])
def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Загрузка фото профиля (multipart, поле file). Сохраняется JPEG без метаданных под новым именем."""
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

    old_path = current_user.avatar_path
    current_user.avatar_path = f"{config.AVATARS_URL_PREFIX}/{filename}"
    try:
        db.commit()
    except Exception:
        _remove_quietly(file_path)
        raise
    _delete_avatar_file(old_path)
    db.refresh(current_user)
    return current_user


@app.delete("/api/auth/me/avatar", response_model=schemas.UserOut, tags=["Auth"])
def delete_avatar(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Удаление фото профиля. Без фото — тоже 200, avatar_url: null."""
    old_path = current_user.avatar_path
    current_user.avatar_path = None
    db.commit()
    _delete_avatar_file(old_path)
    db.refresh(current_user)
    return current_user


# --- Профиль: сводка ---

@app.get("/api/profile/stats", response_model=schemas.ProfileStatsOut, tags=["Profile"])
def get_profile_stats(
    tz: Optional[str] = Query(None, description="Часовой пояс IANA, например Asia/Almaty (по умолчанию UTC)"),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Цифры, тепловая карта и привычки профиля одним запросом (PROFILE_TASKS.md, п. 1.3)."""
    try:
        zone = ZoneInfo(tz.strip()) if tz and tz.strip() else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail="Параметр tz: укажите часовой пояс, например Asia/Almaty")
    return crud.get_profile_stats(db, current_user, zone)


@app.post("/api/auth/recovery-code", response_model=schemas.RecoveryCodeOut, tags=["Auth"])
def create_recovery_code(
    data: schemas.RecoveryCodeRequest,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Новый код восстановления (старый перестаёт действовать). Нужен текущий пароль."""
    if not auth.verify_password(data.password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Неверный пароль")
    recovery_code = auth.generate_recovery_code()
    current_user.recovery_code_hash = auth.hash_recovery_code(recovery_code)
    db.commit()
    return {"recovery_code": recovery_code}


@app.post("/api/auth/reset-password", response_model=schemas.TokenOut, tags=["Auth"])
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
        raise HTTPException(status_code=400, detail="Неверный email или код восстановления")

    recovery_code = auth.generate_recovery_code()
    user.hashed_password = auth.hash_password(data.new_password)
    user.recovery_code_hash = auth.hash_recovery_code(recovery_code)
    user.last_login_at = models.utcnow()
    db.commit()
    db.refresh(user)

    logger.info("Пароль сброшен по коду восстановления, пользователь id=%s", user.id)
    return _token_response(user, recovery_code)

# --- Распознавание чеков (доступно и гостям) ---
# Эндпоинты объявлены через def, а не async def: FastAPI выполняет их в пуле потоков,
# поэтому долгий OCR не блокирует остальные запросы.

@app.post("/api/analyze-test", tags=["OCR"])
def analyze_test_receipt(
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_optional_current_user),
):
    if not os.path.isfile(TEST_IMAGE_PATH):
        raise HTTPException(status_code=404, detail="Демо-файл images.jpeg не найден")
    try:
        data = _analyze(db, TEST_IMAGE_PATH, current_user, _file_sha256(TEST_IMAGE_PATH))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Ошибка распознавания демо-чека")
        raise HTTPException(status_code=500, detail="Не удалось распознать чек. Попробуйте ещё раз позже.")
    return {"success": True, "data": data}

@app.post("/api/analyze", tags=["OCR"])
def analyze_receipt_endpoint(
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_optional_current_user),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Загрузите изображение чека")

    file_path, filename, image_hash = _save_upload(file)
    try:
        data = _analyze(db, file_path, current_user, image_hash)
    except HTTPException:
        _remove_quietly(file_path)
        raise
    except Exception:
        _remove_quietly(file_path)
        logger.exception("Ошибка распознавания чека")
        raise HTTPException(status_code=500, detail="Не удалось распознать чек. Попробуйте ещё раз позже.")

    # Путь к картинке клиент вернёт при сохранении чека
    data["image_path"] = f"{RECEIPTS_URL_PREFIX}/{filename}"
    _remember_fiscal(data["image_path"], data)
    return {"success": True, "data": data}


@app.post("/api/analyze/async", tags=["OCR"])
def analyze_receipt_async_endpoint(
    file: UploadFile = File(...),
    current_user: Optional[models.User] = Depends(auth.get_optional_current_user),
):
    """Асинхронное распознавание чека через очередь фоновых задач.
    Возвращает task_id для периодического опроса статуса через GET /api/tasks/{task_id}."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Загрузите изображение чека")

    file_path, filename, image_hash = _save_upload(file)
    image_url = f"{RECEIPTS_URL_PREFIX}/{filename}"
    task = task_manager.create_task(file_path=file_path, image_url=image_url)
    # Сессия запроса закроется раньше, чем задача выполнится: категории берём сейчас,
    # а правила задача читает в своей сессии
    categories = _categories_for(current_user)
    user_id = current_user.id if current_user else None

    def _worker_fn(task_id: str) -> dict:
        task_manager.update_task(task_id, step="ocr", progress=40)
        data = _run_ocr(file_path, categories)
        with database.SessionLocal() as db:
            if user_id is not None:
                apply_rules(db, user_id, data.get("items") or [])
            _mark_duplicate(db, user_id, data, image_hash)
        data["image_path"] = image_url
        _remember_fiscal(image_url, data)
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


@app.get("/api/tasks/{task_id}", response_model=schemas.TaskStatusOut, tags=["OCR"])
def get_task_status(task_id: str):
    """Статус фоновой задачи: распознавание чека или ИИ-анализ расходов. Без авторизации —
    поэтому в result анализа ничего личного нет (B.2.4)."""
    task = task_manager.get_task(task_id) or insights_task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена или устарела")
    return schemas.TaskStatusOut(
        task_id=task.id,
        status=task.status,
        step=task.step,
        progress=task.progress,
        result=task.result,
        error=task.error,
    )

# --- Сохранение, просмотр и аналитика чеков (только для авторизованных) ---

# Проверка на дубликат и запись чека — под одной блокировкой на пользователя: иначе двойной клик
# или повтор после обрыва сети проходят проверку оба (C.2.6). Хватает одного процесса uvicorn;
# с несколькими воркерами или Postgres нужна блокировка в базе (SELECT … FOR UPDATE / advisory lock)
_receipt_locks: dict = {}
_receipt_locks_guard = threading.Lock()


def _receipt_lock(user_id: int) -> threading.Lock:
    with _receipt_locks_guard:
        return _receipt_locks.setdefault(user_id, threading.Lock())


def _duplicate_response(db: Session, user: models.User, duplicate: models.Receipt) -> JSONResponse:
    """409 с найденным чеком на верхнем уровне тела, а не внутри detail (C.2.4)."""
    out = schemas.ReceiptOut.model_validate(crud.attach_base_rates(db, duplicate, user.base_currency))
    return JSONResponse(status_code=409, content={
        "detail": "Похоже, этот чек уже сохранён",
        "duplicate_of": duplicate.id,
        "duplicate": jsonable_encoder(out),
    })


@app.post(
    "/api/receipts/save", response_model=schemas.ReceiptOut, tags=["Receipts"],
    responses={409: {"description": "Похоже, этот чек уже сохранён: duplicate_of и duplicate (ReceiptOut)"}},
)
def save_receipt(
    receipt: schemas.ReceiptCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """Сохранение чека в аккаунт текущего пользователя.

    Такой чек уже есть (то же фото, фискальный номер или содержимое) — 409; allow_duplicate: true —
    сохранить всё равно.
    """
    image_path = _resolve_image_path(receipt.image_path)
    # Хеш считает сервер по файлу, с фронта не принимается
    image_hash = _file_sha256(_image_disk_path(image_path)) if image_path else None
    fiscal_id = _fiscal_for(image_path)
    with _receipt_lock(current_user.id):
        if not receipt.allow_duplicate:
            duplicate = crud.find_duplicate(
                db, current_user.id, receipt, image_hash=image_hash, fiscal_id=fiscal_id,
            )
            if duplicate is not None:
                return _duplicate_response(db, current_user, duplicate)
        saved = crud.create_receipt(
            db=db, data=receipt, user_id=current_user.id, image_path=image_path,
            image_hash=image_hash, fiscal_id=fiscal_id,
        )
    return crud.attach_base_rates(db, saved, current_user.base_currency)

def _iso_query_date(value: Optional[str], name: str) -> Optional[str]:
    if not value:
        return None
    iso_date, _ = split_date_time(value)
    if iso_date is None:
        raise HTTPException(status_code=422, detail=f"Параметр {name}: укажите дату в формате ГГГГ-ММ-ДД")
    return iso_date

@app.get("/api/receipts", response_model=List[schemas.ReceiptOut], tags=["Receipts"])
def read_receipts(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    from_date: Optional[str] = Query(None, alias="from", description="Начальная дата (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="Конечная дата (YYYY-MM-DD)"),
    category_id: Optional[int] = Query(None, ge=0, description="Категория позиций чека; 0 — «Без категории»"),
    q: Optional[str] = Query(None, description="Поиск по магазину или товарам"),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """Чеки текущего пользователя с поддержкой фильтров, поиска и пагинации."""
    # Даты в базе — строки YYYY-MM-DD и сравниваются как строки, поэтому другой формат дал бы тихо неверный результат
    from_date, to_date = _iso_query_date(from_date, "from"), _iso_query_date(to_date, "to")
    receipts = crud.get_all_receipts(
        db,
        user_id=current_user.id,
        skip=skip,
        limit=limit,
        from_date=from_date,
        to_date=to_date,
        category_id=category_id,
        q=q,
    )
    return crud.attach_base_rates(db, receipts, current_user.base_currency)

@app.get("/api/receipts/{receipt_id}", response_model=schemas.ReceiptOut, tags=["Receipts"])
def read_receipt(
    receipt_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Получение одного чека по id (только своего)."""
    receipt = crud.get_receipt_by_id(db, receipt_id=receipt_id, user_id=current_user.id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Чек не найден")
    return crud.attach_base_rates(db, receipt, current_user.base_currency)

@app.put(
    "/api/receipts/{receipt_id}", response_model=schemas.ReceiptOut, tags=["Receipts"],
    responses={409: {"description": "Правка сделала чек копией другого: duplicate_of и duplicate"}},
)
def update_receipt(
    receipt_id: int,
    data: schemas.ReceiptUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Редактирование сохранённого чека (магазин, дата, время, категория, позиции).

    Правка сделала чек копией другого — 409, как при сохранении (C.2.5). Фото и фискальный номер
    правкой не меняются, поэтому проверяется только содержимое.
    """
    receipt = crud.get_receipt_by_id(db, receipt_id=receipt_id, user_id=current_user.id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Чек не найден")
    with _receipt_lock(current_user.id):
        if not data.allow_duplicate:
            duplicate = crud.find_duplicate(db, current_user.id, data, exclude_id=receipt.id)
            if duplicate is not None:
                return _duplicate_response(db, current_user, duplicate)
        updated = crud.update_receipt(db=db, receipt=receipt, data=data)
    return crud.attach_base_rates(db, updated, current_user.base_currency)

@app.get("/api/analytics", response_model=schemas.AnalyticsOut, tags=["Analytics"])
def get_analytics(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """Аналитика расходов текущего пользователя."""
    return crud.get_analytics(db, user_id=current_user.id, base_currency=current_user.base_currency)

@app.delete("/api/receipts/{receipt_id}", tags=["Receipts"])
def delete_receipt(
    receipt_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """Удаление своего чека (чужой чек для пользователя «не существует» → 404)."""
    receipt = crud.get_receipt_by_id(db, receipt_id, user_id=current_user.id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Чек не найден")
    image_path = receipt.image_path
    crud.delete_receipt(db, receipt)
    if image_path and crud.count_receipts_with_image(db, image_path) == 0:
        _delete_image_file(image_path)
    return {"success": True}


# --- Бюджеты и лимиты расходов (только для авторизованных) ---

_MONTH_REGEX = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@app.get("/api/budgets", response_model=List[schemas.BudgetOut], tags=["Budgets"])
def get_budgets(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Список лимитов текущего пользователя. Если лимитов нет, возвращает пустой список []."""
    return crud.get_user_budgets(db, user_id=current_user.id)


@app.put("/api/budgets", response_model=List[schemas.BudgetOut], tags=["Budgets"])
def set_budgets(
    data: schemas.BudgetListUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Установка списка лимитов пользователя в одной транзакции (полная замена списка)."""
    return crud.set_user_budgets(db, user_id=current_user.id, budgets_data=data.budgets)


@app.get("/api/budgets/status", response_model=schemas.BudgetStatusOut, tags=["Budgets"])
def get_budgets_status(
    month: Optional[str] = Query(None, description="Месяц в формате YYYY-MM (по умолчанию текущий)"),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Расчёт расходов по бюджетам текущего пользователя за указанный месяц."""
    if not month:
        target_month = datetime.now(timezone.utc).strftime("%Y-%m")
    else:
        target_month = month.strip()
        if not _MONTH_REGEX.match(target_month):
            raise HTTPException(status_code=422, detail="Параметр month: укажите месяц в формате ГГГГ-ММ")
    return crud.get_budget_status(db, user_id=current_user.id, month=target_month, base_currency=current_user.base_currency)


# --- Категории и перенос позиций (только для авторизованных, кроме стандартного списка) ---

def _own_category(db: Session, user: models.User, category_id: int) -> models.Category:
    category = crud.get_user_category(db, user.id, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    return category


@app.get("/api/categories", response_model=List[schemas.CategoryOut], tags=["Categories"])
def list_categories(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Категории пользователя в порядке position."""
    return crud.get_categories(db, current_user.id)


@app.get("/api/categories/defaults", response_model=List[schemas.CategoryDefaultOut], tags=["Categories"])
def list_default_categories():
    """Стандартные категории — для гостя (без авторизации)."""
    return DEFAULT_CATEGORIES


@app.post("/api/categories", response_model=schemas.CategoryOut, status_code=201, tags=["Categories"])
def create_category(
    data: schemas.CategoryCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Новая категория в конце списка. Повтор названия без учёта регистра — 409."""
    return crud.create_category(db, current_user.id, data)


@app.put("/api/categories/order", response_model=List[schemas.CategoryOut], tags=["Categories"])
def reorder_categories(
    data: schemas.CategoryOrder,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Новый порядок: все id пользователя, каждый один раз."""
    return crud.reorder_categories(db, current_user.id, data.ids)


@app.patch("/api/categories/{category_id}", response_model=schemas.CategoryOut, tags=["Categories"])
def update_category(
    category_id: int,
    data: schemas.CategoryUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Переименование, иконка, описание — любой, в том числе стандартной категории."""
    category = _own_category(db, current_user, category_id)
    return crud.update_category(db, category, data)


@app.delete("/api/categories/{category_id}", response_model=schemas.CategoryDeleteOut, tags=["Categories"])
def delete_category(
    category_id: int,
    move_to: Optional[int] = Query(None, description="Куда перенести позиции, чеки и правила"),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Удаление категории. Без move_to позиции остаются без категории. Лимит бюджета удаляется всегда."""
    category = _own_category(db, current_user, category_id)
    return crud.delete_category(db, current_user.id, category, move_to)


@app.post("/api/items/move", response_model=schemas.ItemsMoveOut, tags=["Categories"])
def move_items(
    data: schemas.ItemsMove,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Перенос позиций в категорию (null — «Без категории»); remember — запомнить правило."""
    return crud.move_items(db, current_user.id, data.item_ids, data.category_id, data.remember)


@app.get("/api/category-rules", response_model=List[schemas.CategoryRuleOut], tags=["Categories"])
def list_category_rules(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Запомненные правила «товар → категория»."""
    return crud.get_rules(db, current_user.id)


@app.delete("/api/category-rules/{rule_id}", tags=["Categories"])
def delete_category_rule(
    rule_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Удаление своего правила (чужое — 404)."""
    if not crud.delete_rule(db, current_user.id, rule_id):
        raise HTTPException(status_code=404, detail="Правило не найдено")
    return {"success": True}



# --- Автокатегория для товаров, вписанных руками (NEXT_WEEK_TASKS.md, A.2.3) ---

CATEGORIZE_RATE = (30, 60)  # запросов на пользователя за столько секунд


@app.post("/api/categorize", response_model=schemas.CategorizeOut, tags=["Categories"])
def categorize(
    data: schemas.CategorizeIn,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Категории для названий без фото: правила пользователя, затем ML (ИИ или ключевые слова).

    Ответ — по элементу на каждое название в том же порядке; не нашли — category_id, category
    и category_source равны null. ИИ недоступен — то, что нашлось по правилам и ключевым словам, а не 500.
    """
    allowed, retry_after = limiter.check_limit(f"categorize:{current_user.id}", *CATEGORIZE_RATE)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Слишком много запросов. Пожалуйста, попробуйте позже."},
            headers={"Retry-After": str(retry_after)},
        )
    items = [
        {"name": it.name.strip(), "category_id": None, "category": None, "category_source": None}
        for it in data.items
    ]
    named = [it for it in items if it["name"]]
    apply_rules(db, current_user.id, named)
    rest = [it for it in named if it["category_source"] != "rule"]
    if rest:
        try:
            from ml.app.categorize import categorize_items
            categorize_items(rest, _categories_for(current_user))
        except Exception as e:
            logger.warning("Автокатегория не сработала (%s): %s", type(e).__name__, e)
            for it in rest:
                it.update(category_id=None, category=None, category_source=None)
    return {"items": items}


# --- ИИ-рекомендации по расходам (NEXT_WEEK_TASKS.md, B.2) ---

# Пользователь → id его задачи анализа, пока она идёт: повторный POST не запускает вторую
_insights_running: dict = {}
_insights_running_lock = threading.Lock()


def _insights_out(insight: models.Insight, current_hash: str) -> dict:
    """InsightsOut: сохранённый анализ и stale — после него менялись чеки или лимиты."""
    return {
        "insights": insight.data,
        "created_at": jsonable_encoder(insight.created_at),
        "model": insight.model,
        "stale": insight.snapshot_hash != current_hash,
    }


def _snapshot(db: Session, user: models.User) -> Tuple[dict, str]:
    snapshot = crud.build_finance_snapshot(db, user)
    return snapshot, crud.snapshot_hash(snapshot)


@app.get("/api/insights", tags=["Insights"])
def get_insights(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Последний анализ ({insights, created_at, model, stale}) или {"insights": null}, если его ещё нет."""
    insight = crud.get_latest_insight(db, current_user.id)
    if insight is None:
        return {"insights": None}
    _, digest = _snapshot(db, current_user)
    return _insights_out(insight, digest)


@app.post("/api/insights", tags=["Insights"], status_code=202, responses={
    200: {"description": "Данные не изменились — прежний анализ (InsightsOut)"},
    403: {"description": "Нет согласия на анализ"},
    429: {"description": "Слишком часто: retry_after — через сколько секунд можно"},
})
def start_insights(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Запуск нового анализа в фоне: {"task_id"}, статус — GET /api/tasks/{task_id}.

    Данные не изменились с прошлого анализа — сразу 200 с прежним анализом, без запроса к ИИ.
    Не чаще одного нового анализа за INSIGHTS_REFRESH_HOURS часов — иначе 429.
    """
    if not current_user.ai_insights_consent:
        raise HTTPException(status_code=403, detail="Нужно согласие на анализ")
    user_id = current_user.id
    with _insights_running_lock:
        running = _insights_running.get(user_id)
        if running and (task := insights_task_manager.get_task(running)) and task.status in ("pending", "processing"):
            return JSONResponse(status_code=202, content={"task_id": running})

        snapshot, digest = _snapshot(db, current_user)
        last = crud.get_latest_insight(db, user_id)
        if last is not None and last.snapshot_hash == digest:
            return JSONResponse(status_code=200, content=_insights_out(last, digest))
        if last is not None and config.INSIGHTS_REFRESH_HOURS > 0:
            wait = int(config.INSIGHTS_REFRESH_HOURS * 3600 - (models.utcnow() - last.created_at).total_seconds())
            if wait > 0:
                hours = -(-wait // 3600)
                text = f"{hours} ч" if wait >= 3600 else f"{-(-wait // 60)} мин"
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Обновить советы можно через {text}", "retry_after": wait},
                    headers={"Retry-After": str(wait)},
                )

        base = current_user.base_currency or currencies.DEFAULT_CURRENCY
        kzt_per_base = 1.0 if base == "KZT" else (rates.rate_to_kzt(db, base, snapshot["today"], fetch=False) or 1.0)
        task = insights_task_manager.create_task(step="ai")
        _insights_running[user_id] = task.id

    def _worker(_task_id: str) -> dict:
        try:
            model, result = insights_service.generate(snapshot, kzt_per_base)
            # Сначала запись в базу, потом статус completed: фронт сразу перечитает GET /api/insights
            with database.SessionLocal() as worker_db:
                crud.save_insight(worker_db, user_id, model, result, digest)
        except Exception:
            logger.exception("Не удалось подготовить рекомендации")
            raise HTTPException(status_code=500, detail="Не удалось подготовить советы. Попробуйте позже")
        finally:
            with _insights_running_lock:
                if _insights_running.get(user_id) == _task_id:
                    del _insights_running[user_id]
        # Задачу видит любой, кто знает id: в result ничего личного
        return {"ok": True}

    insights_task_manager.submit(task.id, _worker)
    return JSONResponse(status_code=202, content={"task_id": task.id})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
