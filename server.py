import os
import shutil
import uuid
import traceback
from typing import List

from fastapi import FastAPI, File, UploadFile, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import uvicorn

from app import database
from app import models
from app import schemas
from app import crud
from app.ocr import init_ocr, analyze_receipt

# Создаём таблицы БД
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="CheckAI — Анализ кассовых чеков", version="2.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOADS_DIR = os.path.join(DATA_DIR, "receipts")
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Папка templates
os.makedirs(os.path.join(BASE_DIR, "templates"), exist_ok=True)

app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

ocr_model = None
def get_ocr():
    global ocr_model
    if ocr_model is None:
        ocr_model = init_ocr()
    return ocr_model

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/api/analyze-test")
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

@app.post("/api/analyze")
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

@app.post("/api/receipts/save", response_model=schemas.ReceiptOut)
def save_receipt(receipt: schemas.ReceiptCreate, db: Session = Depends(database.get_db)):
    # Сохраняем в БД!
    return crud.create_receipt(db=db, data=receipt, image_path=None)

@app.get("/api/receipts", response_model=List[schemas.ReceiptOut])
def read_receipts(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    return crud.get_all_receipts(db, skip=skip, limit=limit)

@app.get("/api/analytics", response_model=schemas.AnalyticsOut)
def get_analytics(db: Session = Depends(database.get_db)):
    return crud.get_analytics(db)

@app.delete("/api/receipts/{receipt_id}")
def delete_receipt(receipt_id: int, db: Session = Depends(database.get_db)):
    success = crud.delete_receipt(db, receipt_id)
    if not success:
        raise HTTPException(status_code=404, detail="Чек не найден")
    return {"success": True}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
