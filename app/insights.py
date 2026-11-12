"""ИИ-рекомендации по расходам: генерация и проверка ответа (NEXT_WEEK_TASKS.md, B.2).

Цифры считает бэкенд (crud.build_finance_snapshot), текст пишет ML — ml.app.advisor.generate_insights.
Пока модуля ML нет, он упал или AI_PROVIDER=off, работают шаблонные советы по фактам (model = "rules"):
так B.2 можно выкатить раньше B.1. Ответ ML всё равно проверяется здесь: фронт показывает его как есть.
"""
import calendar
import logging
import math
import re
import statistics
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from app import config
from app.currencies import CURRENCIES

logger = logging.getLogger("checkai.insights")

RULES_MODEL = "rules"
INSIGHT_TYPES = (
    "over_budget", "budget_forecast", "category_growth", "frequent_small",
    "recurring", "budget_suggestion", "uncategorized", "unconverted",
)
ACTION_KINDS = ("open_history", "set_budget", "open_categories", "none")
RULES_PRIORITY = ("over_budget", "budget_forecast", "category_growth", "frequent_small",
                  "budget_suggestion", "recurring", "uncategorized", "unconverted")
MAX_ITEMS = 6
MIN_ITEMS_FROM_AI = 2
TITLE_MAX, TEXT_MAX, SUMMARY_MAX = 60, 300, 300

# Пороги шаблонных советов — в тенге, для другой основной валюты пересчитываются по курсу
GROWTH_RATIO = 1.25
GROWTH_MIN_KZT = 5000
FREQUENT_SMALL_MIN = 8
UNCATEGORIZED_SHARE = 0.15
STABLE_SPREAD = 0.25
# Прогноз по лимиту — только когда в месяце набралось хоть сколько-то дней
FORECAST_MIN_DAY = 5

MONTHS_RU = ["январе", "феврале", "марте", "апреле", "мае", "июне",
             "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"]


def _advisor() -> Optional[Callable[..., Dict[str, Any]]]:
    try:
        from ml.app.advisor import generate_insights
    except ImportError:
        return None
    return generate_insights


def generate(snapshot: Dict[str, Any], kzt_per_base: float = 1.0) -> Tuple[str, Dict[str, Any]]:
    """(model, {"summary", "items"}) — ответ ИИ после проверки или шаблонные советы."""
    advisor = _advisor() if config.AI_PROVIDER != "off" else None
    if advisor is not None:
        try:
            raw = advisor(snapshot, provider=config.AI_PROVIDER)
            model, insights = sanitize(raw, snapshot)
            if len(insights["items"]) >= MIN_ITEMS_FROM_AI:
                return model, insights
            logger.info("ИИ вернул меньше %d годных советов — показываем шаблонные", MIN_ITEMS_FROM_AI)
        except Exception as e:
            # Сводку в лог не пишем: там траты пользователя
            logger.warning("Рекомендации ИИ не получились (%s) — показываем шаблонные", type(e).__name__)
    return RULES_MODEL, rule_insights(snapshot, kzt_per_base)


# --- Проверка ответа ---

def _clip(value: Any, limit: int) -> str:
    # Неразрывный пробел в суммах («7 000 ₸») оставляем, остальные пробелы и переводы строк — один пробел
    text = re.sub(r"[^\S ]+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def sanitize(raw: Any, snapshot: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Приводит ответ generate_insights к контракту InsightsOut (B.1.4, «Контракты API»).

    Карточка с неизвестным типом или без текста отбрасывается; category_id не из сводки — null,
    действие с такой категорией — none; id внутри ответа не повторяются; не больше MAX_ITEMS.
    """
    if not isinstance(raw, dict):
        raise ValueError("ответ ИИ — не объект")
    body = raw.get("insights") if isinstance(raw.get("insights"), dict) else raw
    model = _clip(raw.get("model") or config.AI_PROVIDER, 100) or config.AI_PROVIDER
    known = {c["id"] for c in snapshot.get("categories") or []}

    items, seen = [], set()
    for it in body.get("items") or []:
        if not isinstance(it, dict) or it.get("type") not in INSIGHT_TYPES:
            continue
        item_id = _clip(it.get("id"), 100)
        title, text = _clip(it.get("title"), TITLE_MAX), _clip(it.get("text"), TEXT_MAX)
        if not item_id or item_id in seen or not title or not text:
            continue
        category_id = it.get("category_id") if it.get("category_id") in known else None
        items.append({
            "id": item_id,
            "type": it["type"],
            "priority": int(_number(it.get("priority")) or len(items) + 1),
            "title": title,
            "text": text,
            "category_id": category_id,
            "action": _sanitize_action(it.get("action"), known),
        })
        seen.add(item_id)
    items.sort(key=lambda i: i["priority"])
    return model, {"summary": _clip(body.get("summary"), SUMMARY_MAX), "items": items[:MAX_ITEMS]}


def _sanitize_action(action: Any, known: set) -> Dict[str, Any]:
    if not isinstance(action, dict) or action.get("kind") not in ACTION_KINDS:
        return {"kind": "none"}
    kind = action["kind"]
    category_id = action.get("category_id")
    if kind == "set_budget":
        amount = _number(action.get("amount"))
        if category_id not in known or amount is None or amount <= 0:
            return {"kind": "none"}
        return {"kind": kind, "category_id": category_id, "amount": round(amount, 2)}
    if kind == "open_history":
        return {"kind": kind, "category_id": category_id if category_id in known else None}
    return {"kind": kind}


# --- Шаблонные советы ---

def _money(amount: float, currency: str) -> str:
    info = CURRENCIES.get(currency, {"symbol": currency, "digits": 2})
    digits = info["digits"] if round(amount) != amount else 0
    text = f"{amount:,.{digits}f}".replace(",", " ").replace(".", ",")
    return f"{text} {info['symbol']}"


def _round_step(currency: str) -> float:
    return 1000 if not CURRENCIES.get(currency, {}).get("digits", 2) else 10


def _median(values: List[float]) -> float:
    return float(statistics.median(values))


def rule_insights(snapshot: Dict[str, Any], kzt_per_base: float = 1.0) -> Dict[str, Any]:
    """Советы по готовым фактам сводки, без ИИ. Числа в тексте — только из сводки."""
    cur = snapshot.get("base_currency") or "KZT"
    money = lambda v: _money(v, cur)  # noqa: E731
    per_base = kzt_per_base or 1.0
    today = date.fromisoformat(snapshot["today"])
    month = snapshot["today"][:7]
    day, month_days = today.day, calendar.monthrange(today.year, today.month)[1]
    items: List[Dict[str, Any]] = []

    def add(type_: str, category_id: Optional[int], title: str, text: str, action: Dict[str, Any]) -> None:
        items.append({
            "id": f"{type_}:{category_id if category_id is not None else ''}:{month}",
            "type": type_, "priority": 0, "title": _clip(title, TITLE_MAX), "text": _clip(text, TEXT_MAX),
            "category_id": category_id, "action": action,
        })

    total_limit = snapshot.get("total_limit")
    month_total = next((m["total"] for m in snapshot.get("months") or [] if m["month"] == month), 0.0)
    if total_limit and month_total > total_limit:
        add("over_budget", None, f"Общий лимит превышен на {money(month_total - total_limit)}",
            f"В этом месяце потрачено {money(month_total)} при лимите {money(total_limit)}.",
            {"kind": "open_history", "category_id": None})

    for c in snapshot.get("categories") or []:
        cid, name, spent, limit = c["id"], c["name"], c["this_month"], c.get("limit")
        prev = [v for v in c.get("prev_months") or [] if v > 0]
        history = {"kind": "open_history", "category_id": cid}
        if limit and spent > limit:
            add("over_budget", cid, f"{name}: лимит превышен на {money(spent - limit)}",
                f"Потрачено {money(spent)} при лимите {money(limit)}.", history)
        elif limit and day >= FORECAST_MIN_DAY and spent > 0 and spent / day * month_days > limit:
            run_out = min(month_days, max(day, math.ceil(limit / (spent / day))))
            add("budget_forecast", cid, f"{name}: лимит может закончиться {run_out:02d}.{today.month:02d}",
                f"Уже потрачено {money(spent)} из {money(limit)}. При таком темпе лимита не хватит до конца месяца.",
                history)
        if prev:
            median = _median(prev)
            if spent > median * GROWTH_RATIO and spent - median > GROWTH_MIN_KZT / per_base:
                growth = round((spent / median - 1) * 100)
                add("category_growth", cid, f"{name}: на {growth}% больше обычного",
                    f"В этом месяце — {money(spent)}, обычно около {money(median)} в месяц.", history)
        if not limit and len(prev) == len(c.get("prev_months") or []) and len(prev) >= 3:
            median = _median(prev)
            if (max(prev) - min(prev)) <= median * STABLE_SPREAD:
                step = _round_step(cur)
                amount = math.ceil(median * 1.1 / step) * step
                add("budget_suggestion", cid, f"Поставить лимит на «{name}»",
                    f"Последние {len(prev)} месяца вы тратите от {money(min(prev))} до {money(max(prev))}. "
                    f"Лимит {money(amount)} поможет держать это под контролем.",
                    {"kind": "set_budget", "category_id": cid, "amount": amount})

    known = {c["id"] for c in snapshot.get("categories") or []}
    for g in snapshot.get("small_purchases") or []:
        if g["count"] >= FREQUENT_SMALL_MIN:
            cid = g.get("category_id") if g.get("category_id") in known else None
            where = f" в «{g['store']}»" if g.get("store") else ""
            add("frequent_small", cid, f"{g['count']} мелких покупок{where}",
                f"За этот месяц они сложились в {money(g['total'])}.",
                {"kind": "open_history", "category_id": cid} if cid is not None else {"kind": "none"})
            break

    share = snapshot.get("uncategorized_share") or 0
    if share > UNCATEGORIZED_SHARE:
        add("uncategorized", None, f"{round(share * 100)}% трат без категории",
            "Разложите их по категориям — советы и лимиты станут точнее.", {"kind": "open_categories"})

    unconverted = snapshot.get("unconverted") or []
    if unconverted:
        count = sum(u["count"] for u in unconverted)
        currencies = ", ".join(u["currency"] for u in unconverted)
        add("unconverted", None, f"Не учтено чеков: {count} ({currencies})",
            f"Для них пока нет курса к {cur}, поэтому суммы в советах без них.", {"kind": "none"})

    # Сначала то, что требует действия сейчас; sort устойчивый — внутри типа порядок категорий
    items.sort(key=lambda i: RULES_PRIORITY.index(i["type"]))
    items = items[:MAX_ITEMS]
    for n, it in enumerate(items, start=1):
        it["priority"] = n

    summary = f"В {MONTHS_RU[today.month - 1]} потрачено {money(month_total)}." if month_total > 0 else ""
    return {"summary": summary, "items": items}
