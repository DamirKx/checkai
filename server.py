import os
import shutil
import tempfile
import traceback
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI(title="CheckAI — Анализ кассовых чеков", version="1.0.0")

# Ленивая инициализация OCR (чтобы сервер стартовал моментально без подвисаний)
ocr_model = None


def get_ocr():
    global ocr_model
    if ocr_model is None:
        from main import init_ocr
        ocr_model = init_ocr()
    return ocr_model


@app.get("/", response_class=HTMLResponse)
def index():
    """Отдаёт клиентский интерфейс приложения."""
    return HTMLResponse((Path(__file__).parent / "index.html").read_text(encoding="utf-8"))


@app.post("/api/analyze-test")
async def analyze_test_receipt():
    """Эндпоинт для мгновенного тестирования на локальном файле images.jpeg."""
    test_path = "images.jpeg"
    if not os.path.exists(test_path):
        raise HTTPException(status_code=404, detail="Файл images.jpeg не найден в папке проекта")
    try:
        from main import analyze_receipt
        ocr = get_ocr()
        data = analyze_receipt(test_path, ocr=ocr)
        return data
    except Exception as e:
        traceback.print_exc()
        print(f"\n[CheckAI Ошибка теста]: {e}\n", flush=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analyze")
async def analyze_receipt_endpoint(file: UploadFile = File(...)):
    """API-эндпоинт: принимает загруженный файл изображения чека и возвращает JSON."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Загружаемый файл должен быть изображением")

    try:
        from main import analyze_receipt
        content = await file.read()
        ocr = get_ocr()
        data = analyze_receipt(content, ocr=ocr)
        return data
    except Exception as e:
        traceback.print_exc()
        print(f"\n[CheckAI Ошибка распознавания]: {e}\n", flush=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    print("Запуск CheckAI Web Server на http://127.0.0.1:8000 ...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
