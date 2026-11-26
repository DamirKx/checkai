"""Сохранение, просмотр, редактирование, удаление чеков и аналитика (только для авторизованных)."""
import threading
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import auth, crud, database, models, ocr, schemas, uploads
from app.dates import split_date_time
from app.i18n import AppError, Lang

router = APIRouter()

# Проверка на дубликат и запись чека — под одной блокировкой на пользователя: иначе двойной клик
# или повтор после обрыва сети проходят проверку оба. Хватает одного процесса uvicorn;
# с несколькими воркерами или Postgres нужна блокировка в базе (SELECT … FOR UPDATE / advisory lock)
_receipt_locks: dict = {}
_receipt_locks_guard = threading.Lock()


def _receipt_lock(user_id: int) -> threading.Lock:
    with _receipt_locks_guard:
        return _receipt_locks.setdefault(user_id, threading.Lock())


def _duplicate_response(db: Session, user: models.User, duplicate: models.Receipt, lang: str) -> JSONResponse:
    """409 с найденным чеком на верхнем уровне тела, а не внутри detail."""
    out = schemas.ReceiptOut.model_validate(crud.attach_base_rates(db, duplicate, user.base_currency))
    return JSONResponse(status_code=409, content={
        **AppError(409, "duplicate_receipt").body(lang),
        "duplicate_of": duplicate.id,
        "duplicate": jsonable_encoder(out),
    })


@router.post(
    "/api/receipts/save", response_model=schemas.ReceiptOut, tags=["Receipts"],
    responses={409: {"description": "Похоже, этот чек уже сохранён: duplicate_of и duplicate (ReceiptOut)"}},
)
def save_receipt(
    receipt: schemas.ReceiptCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
    lang: str = Lang,
):
    """Сохранение чека в аккаунт текущего пользователя.

    Такой чек уже есть (то же фото, фискальный номер или содержимое) — 409; allow_duplicate: true —
    сохранить всё равно.
    """
    image_path = uploads.resolve_image_path(receipt.image_path)
    # Хеш считает сервер по файлу, с фронта не принимается
    image_hash = uploads.file_sha256(uploads.image_disk_path(image_path)) if image_path else None
    fiscal_id = ocr.fiscal_for(image_path)
    with _receipt_lock(current_user.id):
        if not receipt.allow_duplicate:
            duplicate = crud.find_duplicate(
                db, current_user.id, receipt, image_hash=image_hash, fiscal_id=fiscal_id,
            )
            if duplicate is not None:
                return _duplicate_response(db, current_user, duplicate, lang)
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
        raise AppError(422, "invalid_query_date", name=name)
    return iso_date


@router.get("/api/receipts", response_model=List[schemas.ReceiptOut], tags=["Receipts"])
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


@router.get("/api/receipts/{receipt_id}", response_model=schemas.ReceiptOut, tags=["Receipts"])
def read_receipt(
    receipt_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Получение одного чека по id (только своего)."""
    receipt = crud.get_receipt_by_id(db, receipt_id=receipt_id, user_id=current_user.id)
    if receipt is None:
        raise AppError(404, "receipt_not_found")
    return crud.attach_base_rates(db, receipt, current_user.base_currency)


@router.put(
    "/api/receipts/{receipt_id}", response_model=schemas.ReceiptOut, tags=["Receipts"],
    responses={409: {"description": "Правка сделала чек копией другого: duplicate_of и duplicate"}},
)
def update_receipt(
    receipt_id: int,
    data: schemas.ReceiptUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
    lang: str = Lang,
):
    """Редактирование сохранённого чека (магазин, дата, время, категория, позиции).

    Правка сделала чек копией другого — 409, как при сохранении. Фото и фискальный номер
    правкой не меняются, поэтому проверяется только содержимое.
    """
    receipt = crud.get_receipt_by_id(db, receipt_id=receipt_id, user_id=current_user.id)
    if receipt is None:
        raise AppError(404, "receipt_not_found")
    with _receipt_lock(current_user.id):
        if not data.allow_duplicate:
            duplicate = crud.find_duplicate(db, current_user.id, data, exclude_id=receipt.id)
            if duplicate is not None:
                return _duplicate_response(db, current_user, duplicate, lang)
        updated = crud.update_receipt(db=db, receipt=receipt, data=data)
    return crud.attach_base_rates(db, updated, current_user.base_currency)


@router.get("/api/analytics", response_model=schemas.AnalyticsOut, tags=["Analytics"])
def get_analytics(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """Аналитика расходов текущего пользователя."""
    return crud.get_analytics(db, user_id=current_user.id, base_currency=current_user.base_currency)


@router.delete("/api/receipts/{receipt_id}", tags=["Receipts"])
def delete_receipt(
    receipt_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """Удаление своего чека (чужой чек для пользователя «не существует» → 404)."""
    receipt = crud.get_receipt_by_id(db, receipt_id, user_id=current_user.id)
    if receipt is None:
        raise AppError(404, "receipt_not_found")
    image_path = receipt.image_path
    crud.delete_receipt(db, receipt)
    if image_path and crud.count_receipts_with_image(db, image_path) == 0:
        uploads.delete_image_file(image_path)
    return {"success": True}
