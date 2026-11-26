"""Приложение FastAPI: логи, миграции, статика, middleware, обработчики ошибок и маршруты из app/routers/.

Запуск: uvicorn app.main:app --reload
"""
import logging
import math
import os
import re
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config, database, ocr, uploads
from app.i18n import MESSAGES, AppError, get_lang, parse_accept_language, tr, tr_validation
from app.rate_limit import RATE_LIMIT_METHOD_RULES, RATE_LIMIT_RULES, get_client_ip, limiter
from app.routers import analyze, auth, budgets, categories, insights, profile, receipts, system
from app.tasks import insights_task_manager, task_manager

logger = logging.getLogger("checkai")


def _setup_logging() -> None:
    """Логи checkai.* в stderr. Без своего обработчика INFO теряются: корневой логгер пропускает только WARNING,
    а uvicorn настраивает лишь свои логгеры. Остальные библиотеки (httpx, paddle) не трогаем — шумят на INFO."""
    logger.setLevel(config.LOG_LEVEL)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    # paddle вешает свой обработчик на корневой логгер — без этого каждая строка checkai.* выводилась дважды
    logger.propagate = False


_setup_logging()

# Применяем миграции Alembic (создают таблицы в новой базе)
database.init_db()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Фоновая очистка стартует вместе с сервером, а не при импорте модуля (импортируют и тесты)
    stop_cleanup = uploads.start_orphan_cleanup_thread()
    if config.OCR_WARMUP:
        # В фоне: сервер отвечает сразу, а чек, пришедший до конца загрузки, подождёт её в get_ocr()
        threading.Thread(target=ocr.warm_up_ocr, name="ocr-warmup", daemon=True).start()
    yield
    if stop_cleanup is not None:
        stop_cleanup.set()
    task_manager._executor.shutdown(wait=False)
    insights_task_manager._executor.shutdown(wait=False)


app = FastAPI(title="CheckAI — Анализ кассовых чеков", version="2.1.0", lifespan=lifespan)

# Статикой раздаём ТОЛЬКО фото чеков и фото профиля. Папка data/ целиком закрыта — там лежит checkai.db.
app.mount(uploads.RECEIPTS_URL_PREFIX, StaticFiles(directory=uploads.UPLOADS_DIR), name="receipts")
app.mount(config.AVATARS_URL_PREFIX, StaticFiles(directory=uploads.AVATARS_DIR), name="avatars")
# Стили и скрипты интерфейса
os.makedirs(config.FRONTEND_TEMPLATES_DIR, exist_ok=True)
os.makedirs(config.FRONTEND_STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=config.FRONTEND_STATIC_DIR), name="static")


@app.middleware("http")
async def upload_limit_and_headers(request: Request, call_next):
    # Ограничение частоты запросов
    rule = RATE_LIMIT_RULES.get(request.url.path) or RATE_LIMIT_METHOD_RULES.get((request.method, request.url.path))
    # Зависимости FastAPI здесь ещё не работают — язык прямо из заголовка
    lang = parse_accept_language(request.headers.get("accept-language"))
    if rule:
        max_requests, window_seconds = rule
        client_ip = get_client_ip(request)
        allowed, retry_after = limiter.check_limit(
            f"{request.method}:{request.url.path}:{client_ip}", max_requests, window_seconds
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content=AppError(429, "too_many_requests").body(lang),
                headers={"Retry-After": str(retry_after)},
            )

    # Отсекаем заведомо большие загрузки ещё до разбора multipart
    if request.url.path in ("/api/analyze", "/api/analyze/async"):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > uploads.MAX_UPLOAD_BYTES + 64 * 1024:
            return JSONResponse(
                status_code=413,
                content=AppError(413, "file_too_big", mb=config.MAX_UPLOAD_MB).body(lang),
            )
    if request.url.path == "/api/auth/me/avatar" and request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > config.MAX_AVATAR_BYTES + 64 * 1024:
            return JSONResponse(
                status_code=413, content=AppError(413, "avatar_too_big", mb=uploads.AVATAR_MAX_MB).body(lang),
            )
    response = await call_next(request)
    # Браузер не должен «угадывать» тип загруженных файлов
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Отдаёт ошибки валидации без технической приставки 'Value error, ' / 'Assertion failed, '.

    msg — на языке запроса. Ошибка своего валидатора (app.i18n.validation_error)
    отдаётся как раньше — type "value_error", ctx {"error": {}}, — плюс поле code с её кодом.
    Встроенные ошибки Pydantic для ru — с прежним текстом Pydantic, для kk и en — с переводом.
    """
    lang = get_lang(request)
    errors = []
    for err in exc.errors():
        err_dict = dict(err)
        msg = err_dict.get("msg", "")
        msg = re.sub(r"^(Value error|Assertion failed),\s*", "", msg)
        error_type = err_dict.get("type")
        ctx = err_dict.get("ctx") if isinstance(err_dict.get("ctx"), dict) else {}
        if error_type in MESSAGES:
            msg = tr(error_type, lang, **ctx)
            err_dict.update(type="value_error", ctx={"error": {}}, code=error_type)
        elif lang != "ru":
            msg = tr_validation(error_type, lang, ctx) or msg
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


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    """Ошибки по коду (AppError, CategoryError) → {"detail": текст на языке запроса, "code": код}."""
    return JSONResponse(status_code=exc.status_code, content=exc.body(get_lang(request)), headers=exc.headers)


# Порядок — как в API: служебное, аккаунт, профиль, распознавание, чеки, бюджеты, категории, рекомендации
for module in (system, auth, profile, analyze, receipts, budgets, categories, insights):
    app.include_router(module.router)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
