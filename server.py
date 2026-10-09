import os
import re
import time
import uuid
import logging
import threading
from typing import List, Optional, Tuple

from fastapi import FastAPI, File, UploadFile, Request, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import uvicorn

from app import config
from app import database
from app import models
from app import schemas
from app import crud
from app import auth
from app.rate_limit import limiter, get_client_ip
from app.dates import normalize_date_time
from app.ocr import init_ocr, analyze_receipt

logger = logging.getLogger("checkai")

# Применяем миграции Alembic (создают таблицы в новой базе)
database.init_db()

app = FastAPI(title="CheckAI — Анализ кассовых чеков", version="2.1.0")

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

# Статикой раздаём ТОЛЬКО фото чеков. Папка data/ целиком закрыта — там лежит checkai.db.
app.mount(RECEIPTS_URL_PREFIX, StaticFiles(directory=UPLOADS_DIR), name="receipts")
templates = Jinja2Templates(directory=FRONTEND_TEMPLATES_DIR)


# Ограничение частоты запросов (макс. запросов, секунд в окне)
RATE_LIMIT_RULES = {
    "/api/auth/login": (10, 60),      # защита от перебора паролей
    "/api/auth/register": (10, 60),   # защита от спама регистраций
    "/api/analyze": (15, 60),         # защита от перегрузки OCR
}


@app.middleware("http")
async def upload_limit_and_headers(request: Request, call_next):
    # Ограничение частоты запросов
    rule = RATE_LIMIT_RULES.get(request.url.path)
    if rule:
        max_requests, window_seconds = rule
        client_ip = get_client_ip(request)
        allowed, retry_after = limiter.check_limit(f"{request.url.path}:{client_ip}", max_requests, window_seconds)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Слишком много запросов. Пожалуйста, попробуйте позже."},
                headers={"Retry-After": str(retry_after)},
            )

    # Отсекаем заведомо большие загрузки ещё до разбора multipart
    if request.url.path == "/api/analyze":
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES + 64 * 1024:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Файл слишком большой. Максимум — {config.MAX_UPLOAD_MB} МБ"},
            )
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
        content={"detail": jsonable_encoder(errors)},
    )


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


def _run_ocr(image_path: str) -> dict:
    """Распознаёт чек. Вызывается из синхронных эндпоинтов, т.е. в пуле потоков, а не в event loop."""
    ocr = get_ocr()
    # PaddleOCR не потокобезопасен — параллельные запросы ждут очереди,
    # но остальные эндпоинты сервера в это время отвечают.
    with _ocr_run_lock:
        data = analyze_receipt(image_path, ocr=ocr)
    if not data.get("raw_lines"):
        raise HTTPException(status_code=422, detail="На изображении не удалось найти текст чека")
    data["date"], data["time"] = normalize_date_time(data.get("date"), data.get("time"))
    return data


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


def _save_upload(file: UploadFile) -> Tuple[str, str]:
    """Сохраняет файл как <uuid>.<ext> с проверкой размера. Возвращает (путь на диске, имя файла)."""
    ext = _pick_extension(file)
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOADS_DIR, filename)
    size = 0
    try:
        with open(file_path, "wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Файл слишком большой. Максимум — {config.MAX_UPLOAD_MB} МБ",
                    )
                buffer.write(chunk)
    except BaseException:
        _remove_quietly(file_path)
        raise
    if size == 0:
        _remove_quietly(file_path)
        raise HTTPException(status_code=422, detail="Файл пустой")
    return file_path, filename


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
        if filename.startswith("."):
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


def _start_orphan_cleanup_thread():
    def _worker():
        try:
            with database.SessionLocal() as db:
                count = cleanup_orphan_images(db, max_age_seconds=86400)
                if count:
                    logger.info("Удалено %d осиротевших фото чеков", count)
        except Exception:
            logger.exception("Ошибка фоновой очистки осиротевших фото")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


_start_orphan_cleanup_thread()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

# --- Аутентификация (Регистрация, Вход, Профиль) ---

@app.post("/api/auth/register", response_model=schemas.TokenOut, tags=["Auth"])
def register(user_data: schemas.UserCreate, db: Session = Depends(database.get_db)):
    """Регистрация нового пользователя (email и длина пароля проверяются в схеме → 422)."""
    email = user_data.email.strip().lower()

    existing_user = crud.get_user_by_email(db, email=email)
    if existing_user:
        raise HTTPException(status_code=400, detail="Пользователь с таким email уже зарегистрирован")

    hashed_pw = auth.hash_password(user_data.password)
    user = crud.create_user(
        db=db,
        email=email,
        hashed_password=hashed_pw,
        full_name=user_data.full_name
    )

    access_token = auth.create_access_token(data={"sub": str(user.id), "email": user.email})
    refresh_token = auth.create_refresh_token(data={"sub": str(user.id), "email": user.email})
    return schemas.TokenOut(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=schemas.UserOut.model_validate(user)
    )

@app.post("/api/auth/login", response_model=schemas.TokenOut, tags=["Auth"])
def login(user_data: schemas.UserLogin, db: Session = Depends(database.get_db)):
    """Вход пользователя по email и паролю."""
    email = user_data.email.strip().lower()
    user = crud.get_user_by_email(db, email=email)
    if not user or not auth.verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Неверный email или пароль")

    access_token = auth.create_access_token(data={"sub": str(user.id), "email": user.email})
    refresh_token = auth.create_refresh_token(data={"sub": str(user.id), "email": user.email})
    return schemas.TokenOut(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=schemas.UserOut.model_validate(user)
    )

@app.post("/api/auth/refresh", response_model=schemas.TokenOut, tags=["Auth"])
def refresh_token(data: schemas.RefreshTokenInput, db: Session = Depends(database.get_db)):
    """Обновление access токена с помощью refresh токена."""
    payload = auth.decode_access_token(data.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Недействительный refresh токен")
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Недействительный refresh токен")
    user = crud.get_user_by_id(db, user_id=user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")

    access_token = auth.create_access_token(data={"sub": str(user.id), "email": user.email})
    new_refresh_token = auth.create_refresh_token(data={"sub": str(user.id), "email": user.email})
    return schemas.TokenOut(
        access_token=access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        user=schemas.UserOut.model_validate(user),
    )

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
    """Обновление профиля пользователя (имя, email, пароль)."""
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

    db.commit()
    db.refresh(current_user)

    access_token = auth.create_access_token(data={"sub": str(current_user.id), "email": current_user.email})
    refresh_token = auth.create_refresh_token(data={"sub": str(current_user.id), "email": current_user.email})
    return schemas.TokenOut(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=schemas.UserOut.model_validate(current_user),
    )

@app.delete("/api/auth/me", tags=["Auth"])
def delete_me(
    data: schemas.UserDelete,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Удаление аккаунта текущего пользователя со всеми его чеками и их фото."""
    if not auth.verify_password(data.password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Неверный пароль")

    image_paths = crud.delete_user(db, current_user)
    for path in set(image_paths):
        if crud.count_receipts_with_image(db, path) == 0:
            _delete_image_file(path)

    return {"success": True}

# --- Распознавание чеков (доступно и гостям) ---
# Эндпоинты объявлены через def, а не async def: FastAPI выполняет их в пуле потоков,
# поэтому долгий OCR не блокирует остальные запросы.

@app.post("/api/analyze-test", tags=["OCR"])
def analyze_test_receipt():
    if not os.path.isfile(TEST_IMAGE_PATH):
        raise HTTPException(status_code=404, detail="Демо-файл images.jpeg не найден")
    try:
        data = _run_ocr(TEST_IMAGE_PATH)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Ошибка распознавания демо-чека")
        raise HTTPException(status_code=500, detail="Не удалось распознать чек. Попробуйте ещё раз позже.")
    return {"success": True, "data": data}

@app.post("/api/analyze", tags=["OCR"])
def analyze_receipt_endpoint(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Загрузите изображение чека")

    file_path, filename = _save_upload(file)
    try:
        data = _run_ocr(file_path)
    except HTTPException:
        _remove_quietly(file_path)
        raise
    except Exception:
        _remove_quietly(file_path)
        logger.exception("Ошибка распознавания чека")
        raise HTTPException(status_code=500, detail="Не удалось распознать чек. Попробуйте ещё раз позже.")

    # Путь к картинке клиент вернёт при сохранении чека
    data["image_path"] = f"{RECEIPTS_URL_PREFIX}/{filename}"
    return {"success": True, "data": data}

# --- Сохранение, просмотр и аналитика чеков (только для авторизованных) ---

@app.post("/api/receipts/save", response_model=schemas.ReceiptOut, tags=["Receipts"])
def save_receipt(
    receipt: schemas.ReceiptCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """Сохранение чека в аккаунт текущего пользователя."""
    image_path = _resolve_image_path(receipt.image_path)
    return crud.create_receipt(db=db, data=receipt, user_id=current_user.id, image_path=image_path)

@app.get("/api/receipts", response_model=List[schemas.ReceiptOut], tags=["Receipts"])
def read_receipts(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    from_date: Optional[str] = Query(None, alias="from", description="Начальная дата (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="Конечная дата (YYYY-MM-DD)"),
    category: Optional[str] = Query(None, description="Категория чека"),
    q: Optional[str] = Query(None, description="Поиск по магазину или товарам"),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """Чеки текущего пользователя с поддержкой фильтров, поиска и пагинации."""
    return crud.get_all_receipts(
        db,
        user_id=current_user.id,
        skip=skip,
        limit=limit,
        from_date=from_date,
        to_date=to_date,
        category=category,
        q=q,
    )

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
    return receipt

@app.put("/api/receipts/{receipt_id}", response_model=schemas.ReceiptOut, tags=["Receipts"])
def update_receipt(
    receipt_id: int,
    data: schemas.ReceiptUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Редактирование сохранённого чека (магазин, дата, время, категория, позиции)."""
    receipt = crud.get_receipt_by_id(db, receipt_id=receipt_id, user_id=current_user.id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Чек не найден")
    return crud.update_receipt(db=db, receipt=receipt, data=data)

@app.get("/api/analytics", response_model=schemas.AnalyticsOut, tags=["Analytics"])
def get_analytics(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """Аналитика расходов текущего пользователя."""
    return crud.get_analytics(db, user_id=current_user.id)

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

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
