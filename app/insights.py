"""ИИ-рекомендации по расходам: генерация и проверка ответа.

Цифры считает бэкенд (crud.build_finance_snapshot), текст пишет ML — ml.app.advisor.generate_insights.
Пока модуля ML нет, он упал или AI_PROVIDER=off, работают шаблонные советы по фактам (model = "rules"):
сервер работает и без ML. Ответ ML всё равно проверяется здесь: фронт показывает его как есть.
"""
import calendar
import inspect
import logging
import math
import re
import statistics
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from app import config
from app.currencies import CURRENCIES
from app.i18n import MONTHS_IN, MONTHS_SHORT_EN, looks_like_language, normalize_lang, tr

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

# Прежнее имя: месяцы для «В ноябре потрачено …» (тексты на трёх языках — app/i18n.py)
MONTHS_RU = list(MONTHS_IN["ru"])


def _advisor() -> Optional[Callable[..., Dict[str, Any]]]:
    try:
        from ml.app.advisor import generate_insights
    except ImportError:
        return None
    return generate_insights


def _snapshot_lang(snapshot: Dict[str, Any], lang: Optional[str] = None) -> str:
    return normalize_lang(lang) or normalize_lang(snapshot.get("language")) or "ru"


def _accepts_language(fn: Callable[..., Any]) -> bool:
    """Принимает ли generate_insights параметр language. Не понять — нет."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "language" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def generate(snapshot: Dict[str, Any], kzt_per_base: float = 1.0, lang: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """(model, {"summary", "items"}) — ответ ИИ после проверки или шаблонные советы на языке lang.

    lang не передан — язык сводки (snapshot["language"]), иначе ru.
    """
    lang = _snapshot_lang(snapshot, lang)
    advisor = _advisor() if config.AI_PROVIDER != "off" else None
    if advisor is not None:
        try:
            kwargs: Dict[str, Any] = {"provider": config.AI_PROVIDER}
            # Старый generate_insights без language пишет по-русски: на kk/en его ответ отбракует
            # проверка языка в sanitize, и пользователь увидит шаблоны на своём языке
            if _accepts_language(advisor):
                kwargs["language"] = lang
            raw = advisor(snapshot, **kwargs)
            model, insights = sanitize(raw, snapshot, lang)
            if len(insights["items"]) >= MIN_ITEMS_FROM_AI:
                return model, insights
            logger.info("ИИ вернул меньше %d годных советов — показываем шаблонные", MIN_ITEMS_FROM_AI)
        except Exception as e:
            # Сводку в лог не пишем: там траты пользователя
            logger.warning("Рекомендации ИИ не получились (%s) — показываем шаблонные", type(e).__name__)
    return RULES_MODEL, rule_insights(snapshot, kzt_per_base, lang)


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


def sanitize(raw: Any, snapshot: Dict[str, Any], lang: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Приводит ответ generate_insights к контракту InsightsOut.

    Карточка с неизвестным типом или без текста отбрасывается; category_id не из сводки — null,
    действие с такой категорией — none; id внутри ответа не повторяются; не больше MAX_ITEMS.
    Ответ не на языке lang (summary или больше половины карточек) — ValueError: вызывающий
    покажет шаблонные советы.
    """
    lang = _snapshot_lang(snapshot, lang)
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
    items = items[:MAX_ITEMS]
    summary = _clip(body.get("summary"), SUMMARY_MAX)
    wrong = sum(1 for i in items if not looks_like_language(f"{i['title']} {i['text']}", lang))
    # Короткие куски русского текста проходят проверку на kk (там нужна только кириллица),
    # поэтому весь ответ проверяется ещё и целиком: в казахском ответе найдётся казахская буква
    whole = " ".join([summary] + [f"{i['title']} {i['text']}" for i in items])
    if (not looks_like_language(summary, lang) or wrong * 2 > len(items)
            or (lang == "kk" and not looks_like_language(whole, lang))):
        raise ValueError(f"ответ ИИ не на языке {lang}")
    return model, {"summary": summary, "items": items}


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

def _money(amount: float, currency: str, lang: str = "ru") -> str:
    """ru, kk: «12 500,50 ₸»; en: «12,500.50 ₸». Символ после числа во всех языках, как на фронте."""
    info = CURRENCIES.get(currency, {"symbol": currency, "digits": 2})
    digits = info["digits"] if round(amount) != amount else 0
    text = f"{amount:,.{digits}f}"
    if lang != "en":
        text = text.replace(",", " ").replace(".", ",")
    return f"{text} {info['symbol']}"


def _round_step(currency: str) -> float:
    return 1000 if not CURRENCIES.get(currency, {}).get("digits", 2) else 10


def _median(values: List[float]) -> float:
    return float(statistics.median(values))


def rule_insights(snapshot: Dict[str, Any], kzt_per_base: float = 1.0, lang: Optional[str] = None) -> Dict[str, Any]:
    """Советы по готовым фактам сводки, без ИИ, на языке lang. Числа в тексте — только из сводки.

    Название категории — display_name из сводки (перевод стандартной), без него — name.
    id, type, priority, category_id и action от языка не зависят.
    """
    lang = _snapshot_lang(snapshot, lang)
    cur = snapshot.get("base_currency") or "KZT"
    money = lambda v: _money(v, cur, lang)  # noqa: E731
    t = lambda code, **params: tr(f"insight.{code}", lang, **params)  # noqa: E731
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
        add("over_budget", None, t("over_budget_total.title", over=money(month_total - total_limit)),
            t("over_budget_total.text", spent=money(month_total), limit=money(total_limit)),
            {"kind": "open_history", "category_id": None})

    for c in snapshot.get("categories") or []:
        cid, spent, limit = c["id"], c["this_month"], c.get("limit")
        name = c.get("display_name") or c["name"]
        prev = [v for v in c.get("prev_months") or [] if v > 0]
        history = {"kind": "open_history", "category_id": cid}
        if limit and spent > limit:
            add("over_budget", cid, t("over_budget.title", name=name, over=money(spent - limit)),
                t("over_budget.text", spent=money(spent), limit=money(limit)), history)
        elif limit and day >= FORECAST_MIN_DAY and spent > 0 and spent / day * month_days > limit:
            run_out = min(month_days, max(day, math.ceil(limit / (spent / day))))
            when = (f"{MONTHS_SHORT_EN[today.month - 1]} {run_out}" if lang == "en"
                    else f"{run_out:02d}.{today.month:02d}")
            add("budget_forecast", cid, t("budget_forecast.title", name=name, date=when),
                t("budget_forecast.text", spent=money(spent), limit=money(limit)), history)
        if prev:
            median = _median(prev)
            if spent > median * GROWTH_RATIO and spent - median > GROWTH_MIN_KZT / per_base:
                growth = round((spent / median - 1) * 100)
                add("category_growth", cid, t("category_growth.title", name=name, growth=growth),
                    t("category_growth.text", spent=money(spent), median=money(median)), history)
        if not limit and len(prev) == len(c.get("prev_months") or []) and len(prev) >= 3:
            median = _median(prev)
            if (max(prev) - min(prev)) <= median * STABLE_SPREAD:
                step = _round_step(cur)
                amount = math.ceil(median * 1.1 / step) * step
                add("budget_suggestion", cid, t("budget_suggestion.title", name=name),
                    t("budget_suggestion.text", months=len(prev), min=money(min(prev)), max=money(max(prev)),
                      amount=money(amount)),
                    {"kind": "set_budget", "category_id": cid, "amount": amount})

    known = {c["id"] for c in snapshot.get("categories") or []}
    for g in snapshot.get("small_purchases") or []:
        if g["count"] >= FREQUENT_SMALL_MIN:
            cid = g.get("category_id") if g.get("category_id") in known else None
            title = (t("frequent_small.title_store", count=g["count"], store=g["store"]) if g.get("store")
                     else t("frequent_small.title", count=g["count"]))
            add("frequent_small", cid, title, t("frequent_small.text", total=money(g["total"])),
                {"kind": "open_history", "category_id": cid} if cid is not None else {"kind": "none"})
            break

    share = snapshot.get("uncategorized_share") or 0
    if share > UNCATEGORIZED_SHARE:
        add("uncategorized", None, t("uncategorized.title", share=round(share * 100)),
            t("uncategorized.text"), {"kind": "open_categories"})

    unconverted = snapshot.get("unconverted") or []
    if unconverted:
        count = sum(u["count"] for u in unconverted)
        currencies = ", ".join(u["currency"] for u in unconverted)
        add("unconverted", None, t("unconverted.title", count=count, currencies=currencies),
            t("unconverted.text", currency=cur), {"kind": "none"})

    # Сначала то, что требует действия сейчас; sort устойчивый — внутри типа порядок категорий
    items.sort(key=lambda i: RULES_PRIORITY.index(i["type"]))
    items = items[:MAX_ITEMS]
    for n, it in enumerate(items, start=1):
        it["priority"] = n

    summary = (t("summary", month=MONTHS_IN[lang][today.month - 1], amount=money(month_total))
               if month_total > 0 else "")
    return {"summary": summary, "items": items}
