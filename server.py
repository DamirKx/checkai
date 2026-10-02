import os
import shutil
import uuid
import traceback
from typing import List, Optional

from fastapi import FastAPI, File, UploadFile, Request, Depends, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import uvicorn

from app import database
from app import models
from app import schemas
from app import crud
from app import auth
from app.ocr import init_ocr, analyze_receipt

# Создаём таблицы БД и применяем миграции
database.init_db()

app = FastAPI(title="CheckAI — Анализ кассовых чеков", version="2.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOADS_DIR = os.path.join(DATA_DIR, "receipts")
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Шаблоны интерфейса находятся в папке frontend.
FRONTEND_TEMPLATES_DIR = os.path.join(BASE_DIR, "frontend", "templates")
os.makedirs(FRONTEND_TEMPLATES_DIR, exist_ok=True)

app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
templates = Jinja2Templates(directory=FRONTEND_TEMPLATES_DIR)

ocr_model = None
def get_ocr():
    global ocr_model
    if ocr_model is None:
        ocr_model = init_ocr()
    return ocr_model

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

# --- Аутентификация (Регистрация, Вход, Профиль) ---

@app.post("/api/auth/register", response_model=schemas.TokenOut, tags=["Auth"])
def register(user_data: schemas.UserCreate, db: Session = Depends(database.get_db)):
    """Регистрация нового пользователя."""
    email = user_data.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Введите корректный email")
    if len(user_data.password) < 4:
        raise HTTPException(status_code=400, detail="Пароль должен содержать минимум 4 символа")

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

# --- Распознавание чеков ---

@app.post("/api/analyze-test", tags=["OCR"])
async def analyze_test_receipt():
    test_path = "images.jpeg"
    if not os.path.exists(test_path):
        raise HTTPException(status_code=404, detail="Файл images.jpeg не найден")
    try:
        ocr = get_ocr()
        data = analyze_receipt(test_path, ocr=ocr)
        return {"success": True, "data": data}
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}

@app.post("/api/analyze", tags=["OCR"])
async def analyze_receipt_endpoint(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Должно быть изображением")
    try:
        filename = f"{uuid.uuid4()}_{file.filename}"
        file_path = os.path.join(UPLOADS_DIR, filename)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        ocr = get_ocr()
        data = analyze_receipt(file_path, ocr=ocr)
        
        # Добавляем путь к картинке в данные
        data["image_path"] = f"/data/receipts/{filename}"
        return {"success": True, "data": data}
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}

# --- Сохранение, просмотр и аналитика чеков ---

@app.post("/api/receipts/save", response_model=schemas.ReceiptOut, tags=["Receipts"])
def save_receipt(
    receipt: schemas.ReceiptCreate,
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_optional_current_user)
):
    """Сохранение чека в БД (привязывается к пользователю, если передан токен)."""
    user_id = current_user.id if current_user else None
    return crud.create_receipt(db=db, data=receipt, image_path=None, user_id=user_id)

@app.get("/api/receipts", response_model=List[schemas.ReceiptOut], tags=["Receipts"])
def read_receipts(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_optional_current_user)
):
    """Список чеков (фильтруется по пользователю, если авторизован)."""
    user_id = current_user.id if current_user else None
    return crud.get_all_receipts(db, skip=skip, limit=limit, user_id=user_id)

@app.get("/api/analytics", response_model=schemas.AnalyticsOut, tags=["Analytics"])
def get_analytics(
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_optional_current_user)
):
    """Аналитика расходов (персональная, если авторизован)."""
    user_id = current_user.id if current_user else None
    return crud.get_analytics(db, user_id=user_id)

@app.delete("/api/receipts/{receipt_id}", tags=["Receipts"])
def delete_receipt(
    receipt_id: int,
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_optional_current_user)
):
    """Удаление чека."""
    user_id = current_user.id if current_user else None
    success = crud.delete_receipt(db, receipt_id, user_id=user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Чек не найден")
    return {"success": True}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
