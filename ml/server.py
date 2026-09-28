import os
import uuid
import shutil
import traceback
from fastapi import FastAPI, File, UploadFile, HTTPException
import uvicorn

from app.ocr import init_ocr, analyze_receipt

app = FastAPI(title="CheckAI — ML Сервис", version="1.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

ocr_model = None

@app.on_event("startup")
def load_model():
    global ocr_model
    ocr_model = init_ocr()

@app.post("/analyze")
async def analyze_receipt_endpoint(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Должно быть изображением")
    try:
        filename = f"{uuid.uuid4()}_{file.filename}"
        file_path = os.path.join(TEMP_DIR, filename)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        data = analyze_receipt(file_path, ocr=ocr_model)
        
        # Удаляем временный файл
        os.remove(file_path)
        
        return {"success": True, "data": data}
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
