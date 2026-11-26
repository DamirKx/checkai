"""Служебное: состояние сервера, справочник валют, страница интерфейса и файлы PWA."""
import logging
import os
from typing import List

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import config, currencies, database, ocr, schemas
from app.i18n import Lang

logger = logging.getLogger("checkai")

router = APIRouter()
templates = Jinja2Templates(directory=config.FRONTEND_TEMPLATES_DIR)


@router.get("/api/health", response_model=schemas.HealthCheckOut, tags=["System"])
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
    ocr_status = ocr.ocr_status()

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


@router.get("/api/currencies", response_model=List[schemas.CurrencyOut], tags=["Currencies"])
def get_currencies(lang: str = Lang):
    """Список поддерживаемых валют (без авторизации). name — на языке запроса."""
    return currencies.get_currencies_list(lang)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


# --- Мобильная версия (PWA): манифест и service worker отдаются из корня сайта ---
# Service worker управляет только путями ниже того, откуда загружен: из /static/sw.js он не увидел бы «/».
# no-cache — браузер каждый раз сверяет файл с сервером, и обновление приложения доходит до телефонов сразу
_PWA_NO_CACHE = {"Cache-Control": "no-cache"}


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        os.path.join(config.FRONTEND_STATIC_DIR, "sw.js"), media_type="application/javascript", headers=_PWA_NO_CACHE,
    )


@router.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest():
    return FileResponse(
        os.path.join(config.FRONTEND_STATIC_DIR, "manifest.webmanifest"),
        media_type="application/manifest+json", headers=_PWA_NO_CACHE,
    )
