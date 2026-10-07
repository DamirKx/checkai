import os
import re
import uuid
import logging
import threading
from typing import List, Optional, Tuple

from fastapi import FastAPI, File, UploadFile, Request, Depends, HTTPException, Query
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


@app.middleware("http")
async def upload_limit_and_headers(request: Request, call_next):
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
    """Принимаем только путь, который выдал /api/analyze, и только если файл существует."""
    if not image_path:
        return None
    match = IMAGE_PATH_RE.match(image_path)
    if not match or not os.path.isfile(os.path.join(UPLOADS_DIR, match.group(1))):
        raise HTTPException(status_code=422, detail="Некорректный путь к изображению чека")
    return image_path


def _delete_image_file(image_path: Optional[str]) -> None:
    match = IMAGE_PATH_RE.match(image_path or "")
    if match:
        _remove_quietly(os.path.join(UPLOADS_DIR, match.group(1)))


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
    return schemas.TokenOut(
        access_token=access_token,
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
    return schemas.TokenOut(
        access_token=access_token,
        token_type="bearer",
        user=schemas.UserOut.model_validate(user)
    )

@app.get("/api/auth/me", response_model=schemas.UserOut, tags=["Auth"])
def get_me(current_user: models.User = Depends(auth.get_current_user)):
    """Получение информации о текущем пользователе по JWT токену."""
    return current_user

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
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """Чеки текущего пользователя, новые по дате покупки — первыми."""
    return crud.get_all_receipts(db, user_id=current_user.id, skip=skip, limit=limit)

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
