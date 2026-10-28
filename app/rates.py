"""Получение и кеширование официальных курсов валют Национального банка РК."""
import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable, Dict, Optional, Any
from xml.etree import ElementTree

import httpx
from sqlalchemy.orm import Session

from app import database, models

logger = logging.getLogger("checkai.rates")

NBK_URL = "https://nationalbank.kz/rss/get_rates.cfm?fdate={day:%d.%m.%Y}"

# Сколько секунд в сумме можно ждать Нацбанк за один вызов ensure_rates (все дни назад вместе)
FETCH_BUDGET_SECONDS = 8.0
# После ошибки сети не повторяем запрос за тот же день столько секунд — чтобы без интернета
# каждое сохранение чека не ждало таймаут заново
FAILURE_TTL_SECONDS = 300.0
# В тестах выключают (False), чтобы фоновые задачи выполнялись сразу и предсказуемо
RUN_IN_BACKGROUND = True

_failed_until: Dict[str, float] = {}
_failed_lock = threading.Lock()


def reset_failures() -> None:
    """Сбрасывает память о неудачных загрузках (для тестов)."""
    with _failed_lock:
        _failed_until.clear()


def _recently_failed(day_str: str) -> bool:
    with _failed_lock:
        until = _failed_until.get(day_str)
        if until is None:
            return False
        if time.monotonic() >= until:
            del _failed_until[day_str]
            return False
        return True


def _mark_failed(day_str: str) -> None:
    with _failed_lock:
        _failed_until[day_str] = time.monotonic() + FAILURE_TTL_SECONDS


def _fetch_day(day: date) -> Dict[str, float]:
    """Все курсы на дату одним запросом: {"USD": 486.2, "RUB": 5.9, ...} (тенге за 1 единицу).
    
    Может быть подменена в тестах (unittest.mock.patch).
    """
    url = NBK_URL.format(day=day)
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()

    root = ElementTree.fromstring(resp.content)
    rates: Dict[str, float] = {}
    for item in root.iter("item"):
        title_el = item.find("title")
        desc_el = item.find("description")
        quant_el = item.find("quant")

        code = (title_el.text if title_el is not None and title_el.text else "").strip().upper()
        desc = (desc_el.text if desc_el is not None and desc_el.text else "").strip()
        quant = (quant_el.text if quant_el is not None and quant_el.text else "1").strip()

        if code and desc:
            try:
                val = float(desc.replace(",", "."))
                q = float(quant.replace(",", ".")) if quant else 1.0
                if q > 0:
                    rates[code] = val / q
            except (ValueError, ZeroDivisionError):
                continue

    return rates


def parse_day(day: Any) -> date:
    """Приводит входной день к date. Дата из будущего приводится к сегодня."""
    today = date.today()
    if day is None:
        return today
    if isinstance(day, datetime):
        res = day.date()
    elif isinstance(day, date):
        res = day
    elif isinstance(day, str):
        day_str = day.strip()[:10]
        try:
            res = datetime.strptime(day_str, "%Y-%m-%d").date()
        except ValueError:
            try:
                res = datetime.strptime(day_str, "%d.%m.%Y").date()
            except ValueError:
                res = today
    else:
        res = today

    if res > today:
        return today
    return res


def has_rates(db: Session, day: Any) -> bool:
    """Есть ли в exchange_rates хотя бы один курс за день."""
    day_str = parse_day(day).strftime("%Y-%m-%d")
    return (
        db.query(models.ExchangeRate.id)
        .filter(models.ExchangeRate.date == day_str)
        .first()
    ) is not None


def ensure_rates(db: Session, day: Any) -> bool:
    """Если в exchange_rates нет ни одной строки за day — скачать всю ленту за этот день и сохранить.
    
    - Дата в будущем -> сегодня.
    - Пустой ответ (выходной, праздник) -> пробует предыдущий день (до 7 дней назад).
    - Ошибка сети/парсинга -> лог, без выброса исключения.
    """
    target_date = parse_day(day)
    day_str = target_date.strftime("%Y-%m-%d")

    if has_rates(db, day_str):
        return True
    if _recently_failed(day_str):
        return False

    # Скачиваем курсы с fallback до 7 дней назад (на случай выходных/праздников)
    rates: Dict[str, float] = {}
    curr_date = target_date
    deadline = time.monotonic() + FETCH_BUDGET_SECONDS
    for _ in range(7):
        if time.monotonic() >= deadline:
            logger.warning("Превышено время ожидания курсов валют за %s", target_date)
            break
        try:
            rates = _fetch_day(curr_date)
            if rates:
                break
        except Exception as e:
            logger.warning("Ошибка при загрузке курсов валют за %s: %s", curr_date, e)
            _mark_failed(day_str)
            return False
        curr_date -= timedelta(days=1)

    if not rates:
        logger.warning("Не удалось получить курсы валют за %s (проверено 7 дней)", target_date)
        _mark_failed(day_str)
        return False

    try:
        # Сохраняем полученные курсы для target_date
        now = models.utcnow()
        new_records = []
        for code, rate in rates.items():
            new_records.append(
                models.ExchangeRate(
                    date=day_str,
                    currency=code,
                    rate_to_kzt=rate,
                    fetched_at=now,
                )
            )
        db.add_all(new_records)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        # Параллельный запрос мог уже сохранить курсы за этот день (уникальный индекс)
        if has_rates(db, day_str):
            return True
        logger.exception("Ошибка сохранения курсов валют в базу: %s", e)
        return False


def rate_to_kzt(
    db: Session,
    currency: Optional[str],
    day: Any = None,
    fetch: bool = True,
) -> Optional[float]:
    """Возвращает курс валюты к тенге.
    
    - Если fetch=True — предварительно скачивает всю ленту на день (в т.ч. и для чеков в тенге),
      чтобы курсы валют на этот день уже были в базе для пересчёта при смене базовой валюты.
    - Для KZT -> 1.0.
    - Для других валют — из таблицы exchange_rates.
    - Если курс не найден -> None.
    """
    cur = str(currency).strip().upper() if currency else "KZT"
    target_date = parse_day(day)
    day_str = target_date.strftime("%Y-%m-%d")

    if fetch:
        ensure_rates(db, target_date)

    if cur == "KZT":
        return 1.0

    row = (
        db.query(models.ExchangeRate.rate_to_kzt)
        .filter(
            models.ExchangeRate.date == day_str,
            models.ExchangeRate.currency == cur,
        )
        .first()
    )
    if row is not None:
        return float(row[0])
    return None


def run_in_background(fn: Callable[..., Any], *args: Any) -> None:
    """Запускает fn(db, *args) в отдельном потоке со своей сессией БД.

    Ошибки только пишутся в лог. При RUN_IN_BACKGROUND = False выполняется сразу (для тестов).
    """
    def _worker():
        try:
            with database.SessionLocal() as db:
                fn(db, *args)
        except Exception:
            logger.exception("Ошибка фоновой задачи курсов валют")

    if RUN_IN_BACKGROUND:
        threading.Thread(target=_worker, name="currency-rates", daemon=True).start()
    else:
        _worker()
