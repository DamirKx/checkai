"""Получение и кеширование официальных курсов валют Национального банка РК."""
import logging
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Any
from xml.etree import ElementTree

import httpx
from sqlalchemy.orm import Session

from app import models

logger = logging.getLogger("checkai.rates")

NBK_URL = "https://nationalbank.kz/rss/get_rates.cfm?fdate={day:%d.%m.%Y}"


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


def ensure_rates(db: Session, day: Any) -> bool:
    """Если в exchange_rates нет ни одной строки за day — скачать всю ленту за этот день и сохранить.
    
    - Дата в будущем -> сегодня.
    - Пустой ответ (выходной, праздник) -> пробует предыдущий день (до 7 дней назад).
    - Ошибка сети/парсинга -> лог, без выброса исключения.
    """
    target_date = parse_day(day)
    day_str = target_date.strftime("%Y-%m-%d")

    # Проверяем, есть ли уже сохранённые курсы за этот день
    existing_count = (
        db.query(models.ExchangeRate.id)
        .filter(models.ExchangeRate.date == day_str)
        .count()
    )
    if existing_count > 0:
        return True

    # Скачиваем курсы с fallback до 7 дней назад (на случай выходных/праздников)
    rates: Dict[str, float] = {}
    curr_date = target_date
    for _ in range(7):
        try:
            rates = _fetch_day(curr_date)
            if rates:
                break
        except Exception as e:
            logger.warning("Ошибка при загрузке курсов валют за %s: %s", curr_date, e)
            return False
        curr_date -= timedelta(days=1)

    if not rates:
        logger.warning("Не удалось получить курсы валют за %s (проверено 7 дней)", target_date)
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
