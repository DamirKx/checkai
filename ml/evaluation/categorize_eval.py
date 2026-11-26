"""Точность категоризации на эталонных позициях (categorize_cases.json) в двух режимах:
«ключевые слова первыми» (CATEGORIZE_FAST_FIRST=1, как в Docker) и «Qwen первым».

Нужна запущенная Ollama. Запуск из папки ml:
    python -m ml.evaluation.categorize_eval
    OLLAMA_CATEGORIZE_MODEL=qwen2.5:3b python -m ml.evaluation.categorize_eval   # сравнить с другой моделью
"""
import importlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

CASES_FILE = Path(__file__).resolve().parent / "categorize_cases.json"

# Стандартные категории в том виде, в каком их передаёт сервер (app.ocr.categories_for)
CATEGORIES = [
    {"id": 1, "name": "Продукты", "default_key": "groceries",
     "description": "Еда и напитки для дома: молочные продукты, хлеб, мясо, овощи, фрукты, крупы, сладости"},
    {"id": 2, "name": "Одежда", "default_key": "clothing", "description": "Одежда, обувь, аксессуары"},
    {"id": 3, "name": "Дом и быт", "default_key": "home", "description": "Бытовая химия, хозтовары, посуда, мелочи для дома"},
    {"id": 4, "name": "Транспорт", "default_key": "transport", "description": "Топливо, такси, проезд, парковка"},
    {"id": 5, "name": "Кафе", "default_key": "cafe",
     "description": "Готовая еда и напитки вне дома: кафе, рестораны, кофейни, доставка еды"},
    {"id": 6, "name": "Здоровье", "default_key": "health", "description": "Аптека, лекарства, витамины, медицинские услуги"},
    {"id": 7, "name": "Развлечения", "default_key": "entertainment", "description": "Кино, театр, концерты, игры, подписки"},
    {"id": 8, "name": "Другое", "default_key": "other", "description": "Всё, что не подошло в другие категории"},
]
KEY_BY_NAME = {c["name"]: c["default_key"] for c in CATEGORIES}


def load_cases():
    return json.loads(CASES_FILE.read_text("utf-8"))["cases"]


def run_mode(cases, fast_first: bool):
    os.environ["CATEGORIZE_FAST_FIRST"] = "1" if fast_first else "0"
    import ml.categorize as cz
    importlib.reload(cz)  # сбросить кэш и перечитать настройки
    # Чек — один магазин: позиции с одинаковым store идут одним вызовом, как в analyze_receipt
    groups = defaultdict(list)
    for i, case in enumerate(cases):
        groups[case.get("store")].append(i)
    got = [None] * len(cases)
    started = time.perf_counter()
    for store, idxs in groups.items():
        items = [{"name": cases[i]["name"]} for i in idxs]
        cz.categorize_items(items, CATEGORIES, store=store)
        for i, item in zip(idxs, items):
            got[i] = (KEY_BY_NAME.get(item.get("category")), item.get("category_source"))
    return got, time.perf_counter() - started


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cases = load_cases()
    import ml.categorize as cz
    print(f"Модель категорий: {cz.CATEGORIZE_MODEL}, позиций: {len(cases)}\n")
    results = {}
    for label, fast in (("ключевые слова первыми", True), ("Qwen первым", False)):
        got, seconds = run_mode(cases, fast)
        results[label] = got
        parts = []
        # holdout — отложенные позиции: по ним словарь не подбирался, поэтому это честная оценка
        for part, flag in (("основной", False), ("отложенный", True)):
            idx = [i for i, c in enumerate(cases) if bool(c.get("holdout")) == flag]
            if idx:
                ok = sum(1 for i in idx if got[i][0] == cases[i]["expected"])
                parts.append(f"{part}: {ok}/{len(idx)} ({ok / len(idx):.0%})")
        print(f"{label:24} " + ", ".join(parts) + f", {seconds:.1f} с")
    print("\nОшибки:")
    labels = list(results)
    for i, case in enumerate(cases):
        marks = []
        for label in labels:
            key, source = results[label][i]
            marks.append("ок" if key == case["expected"] else f"{key} ({source})")
        if any(m != "ок" for m in marks):
            print(f"  {case['name']} | ожидается {case['expected']} | " + " | ".join(marks))


if __name__ == "__main__":
    main()
