"""ИИ-рекомендации по расходам: последний анализ и запуск нового в фоне."""
import logging
import threading
from typing import Optional, Tuple

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import auth, config, crud, currencies, database, models, rates
from app import insights as insights_service
from app.i18n import AppError, Lang
from app.tasks import insights_task_manager

logger = logging.getLogger("checkai")

router = APIRouter(tags=["Insights"])

# (пользователь, язык) → id задачи анализа, пока она идёт: повторный POST не запускает вторую
_insights_running: dict = {}
_insights_running_lock = threading.Lock()


def _insights_wait(last: Optional[models.Insight]) -> int:
    """Через сколько секунд можно запустить новый анализ после last (0 — уже можно)."""
    if last is None or config.INSIGHTS_REFRESH_MINUTES <= 0:
        return 0
    wait = int(config.INSIGHTS_REFRESH_MINUTES * 60 - (models.utcnow() - last.created_at).total_seconds())
    return max(0, wait)


def _insights_out(insight: models.Insight, current_hash: str) -> dict:
    """InsightsOut: сохранённый анализ и stale — после него менялись чеки или лимиты.

    retry_after — через сколько секунд можно обновить (0 — можно сейчас). Фронт берёт срок отсюда,
    а не только из прошлого 429: иначе после смены INSIGHTS_REFRESH_MINUTES кнопка оставалась бы заблокированной
    по старому сроку."""
    return {
        "insights": insight.data,
        "created_at": jsonable_encoder(insight.created_at),
        "model": insight.model,
        "stale": insight.snapshot_hash != current_hash,
        "retry_after": _insights_wait(insight),
    }


def _snapshot(db: Session, user: models.User, lang: str) -> Tuple[dict, str]:
    snapshot = crud.build_finance_snapshot(db, user, lang=lang)
    return snapshot, crud.snapshot_hash(snapshot)


@router.get("/api/insights")
def get_insights(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
    lang: str = Lang,
):
    """Последний анализ на языке запроса ({insights, created_at, model, stale}) или {"insights": null},
    если на этом языке его ещё нет."""
    insight = crud.get_latest_insight(db, current_user.id, lang)
    if insight is None:
        return {"insights": None}
    _, digest = _snapshot(db, current_user, lang)
    return _insights_out(insight, digest)


@router.post("/api/insights", status_code=202, responses={
    200: {"description": "Данные не изменились — прежний анализ (InsightsOut)"},
    403: {"description": "Нет согласия на анализ"},
    429: {"description": "Слишком часто: retry_after — через сколько секунд можно"},
})
def start_insights(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
    lang: str = Lang,
):
    """Запуск нового анализа на языке запроса в фоне: {"task_id"}, статус — GET /api/tasks/{task_id}.

    Данные не изменились с прошлого анализа на этом языке — сразу 200 с прежним анализом, без запроса к ИИ.
    Не чаще одного нового анализа за INSIGHTS_REFRESH_MINUTES минут на каждом языке — иначе 429:
    первый анализ на kk разрешён, даже если час назад был ru.
    """
    if not current_user.ai_insights_consent:
        raise AppError(403, "insights_consent_required")
    user_id = current_user.id
    running_key = (user_id, lang)
    with _insights_running_lock:
        running = _insights_running.get(running_key)
        if running and (task := insights_task_manager.get_task(running)) and task.status in ("pending", "processing"):
            return JSONResponse(status_code=202, content={"task_id": running})

        snapshot, digest = _snapshot(db, current_user, lang)
        last = crud.get_latest_insight(db, user_id, lang)
        if last is not None and last.snapshot_hash == digest:
            return JSONResponse(status_code=200, content=_insights_out(last, digest))
        if last is not None:
            wait = _insights_wait(last)
            if wait > 0:
                error = (AppError(429, "insights_retry_hours", hours=-(-wait // 3600)) if wait >= 3600
                         else AppError(429, "insights_retry_minutes", minutes=-(-wait // 60)))
                return JSONResponse(
                    status_code=429,
                    content={**error.body(lang), "retry_after": wait},
                    headers={"Retry-After": str(wait)},
                )

        base = current_user.base_currency or currencies.DEFAULT_CURRENCY
        kzt_per_base = 1.0 if base == "KZT" else (rates.rate_to_kzt(db, base, snapshot["today"], fetch=False) or 1.0)
        task = insights_task_manager.create_task(step="ai")
        _insights_running[running_key] = task.id

    def _worker(_task_id: str) -> dict:
        try:
            model, result = insights_service.generate(snapshot, kzt_per_base, lang)
            # Сначала запись в базу, потом статус completed: фронт сразу перечитает GET /api/insights
            with database.SessionLocal() as worker_db:
                crud.save_insight(worker_db, user_id, model, result, digest, lang)
        except Exception:
            logger.exception("Не удалось подготовить рекомендации")
            raise AppError(500, "insights_failed", lang=lang)
        finally:
            with _insights_running_lock:
                if _insights_running.get(running_key) == _task_id:
                    del _insights_running[running_key]
        # Задачу видит любой, кто знает id: в result ничего личного
        return {"ok": True}

    insights_task_manager.submit(task.id, _worker)
    return JSONResponse(status_code=202, content={"task_id": task.id})
