"""Распознавание чеков на сервере вокруг конвейера ml/ocr.py.

Одна модель OCR на процесс (ленивая загрузка или прогрев при старте), распознавание строго по одному:
PaddleOCR не потокобезопасен. Ответ приводится к формату API, к нему добавляются правила пользователя
и признак дубликата. Фискальный номер распознанного, но ещё не сохранённого чека помнится в памяти.
"""
import logging
import threading
import time
from collections import OrderedDict
from typing import List, Optional

from sqlalchemy.orm import Session

from app import crud, currencies, models
from app.categories import DEFAULT_CATEGORIES, apply_rules
from app.dates import normalize_date_time
from app.duplicates import fiscal_id_from
from app.i18n import AppError
from ml.ocr import analyze_receipt, init_ocr

logger = logging.getLogger("checkai")

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


def warm_up_ocr() -> None:
    """Загрузка модели распознавания при старте (OCR_WARMUP). Не вышло — загрузится при первом чеке."""
    started = time.perf_counter()
    try:
        get_ocr()
    except Exception:
        logger.exception("Не удалось заранее загрузить модель распознавания — попробуем при первом чеке")
        return
    logger.info("Модель распознавания загружена за %.1f с", time.perf_counter() - started)


def ocr_status() -> str:
    """standby — модель ещё не загружалась, loading — загружается (прогрев или первый чек), ready — готова."""
    if _ocr_model is not None:
        return "ready"
    if _ocr_init_lock.locked():
        return "loading"
    return "standby"


def categories_for(user: Optional[models.User]) -> List[dict]:
    """Категории, по которым ML раскладывает товары: свои у пользователя, стандартные у гостя (id = None)."""
    # default_key — чтобы встроенный словарь ключевых слов работал и после переименования стандартной категории
    if user is not None:
        return [{"id": c.id, "name": c.name, "description": c.description, "default_key": c.default_key}
                for c in user.categories]
    return [{"id": None, "name": d["name"], "description": d["description"], "default_key": d["key"]}
            for d in DEFAULT_CATEGORIES]


def run_ocr(image_path: str, categories: Optional[List[dict]] = None) -> dict:
    """Распознаёт чек. Вызывается из синхронных эндпоинтов, т.е. в пуле потоков, а не в event loop."""
    ocr = get_ocr()
    # PaddleOCR не потокобезопасен — параллельные запросы ждут очереди,
    # но остальные эндпоинты сервера в это время отвечают.
    with _ocr_run_lock:
        data = analyze_receipt(image_path, ocr=ocr, categories=categories)
    if not data.get("raw_lines"):
        raise AppError(422, "no_receipt_text")
    data["date"], data["time"] = normalize_date_time(data.get("date"), data.get("time"))
    cur = data.get("currency")
    if not cur or cur not in currencies.CURRENCIES:
        data["currency"] = "KZT"
        data["currency_source"] = "default"
    else:
        data["currency_source"] = data.get("currency_source") or "default"
    # Ключи есть всегда, в том числе в асинхронном пути /api/tasks/{id}
    data.setdefault("merchant", None)
    data.setdefault("confidence", None)
    # Реквизиты фискального чека от ML или null
    data.setdefault("fiscal", None)
    for item in data.get("items") or []:
        for key in ("category_id", "category", "category_source"):
            item.setdefault(key, None)
    return data


def analyze(db: Session, image_path: str, user: Optional[models.User], image_hash: Optional[str] = None) -> dict:
    """Распознавание с категориями. Правила пользователя важнее ML и применяются вне блокировки OCR."""
    data = run_ocr(image_path, categories_for(user))
    if user is not None:
        apply_rules(db, user.id, data.get("items") or [])
    mark_duplicate(db, user.id if user else None, data, image_hash)
    return data


def mark_duplicate(db: Session, user_id: Optional[int], data: dict, image_hash: Optional[str]) -> None:
    """duplicate_of в ответе распознавания: у пользователя уже есть чек с тем же фото
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


def remember_fiscal(image_url: str, data: dict) -> None:
    fiscal_id = fiscal_id_from(data.get("fiscal"))
    if not fiscal_id:
        return
    with _fiscal_lock:
        _FISCAL_BY_IMAGE[image_url] = fiscal_id
        _FISCAL_BY_IMAGE.move_to_end(image_url)
        while len(_FISCAL_BY_IMAGE) > _FISCAL_CACHE_MAX:
            _FISCAL_BY_IMAGE.popitem(last=False)


def fiscal_for(image_url: Optional[str]) -> Optional[str]:
    if not image_url:
        return None
    with _fiscal_lock:
        return _FISCAL_BY_IMAGE.get(image_url)
