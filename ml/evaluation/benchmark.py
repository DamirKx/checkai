"""Бенчмарк распознавания: точность и время на эталонных чеках, каждый раз заново (без results_cache.json).

Путь тот же, что у сервера: OCR → Qwen (parse_receipt_with_correction) → категоризация.
Метрики — evaluate.evaluate_parser; категории берутся из результата конвейера, а не из отдельного прогона.

Запуск из папки ml:
    python -m evaluation.benchmark --tag baseline
    python -m evaluation.benchmark --tag after --reuse-ocr baseline   # OCR из прошлого прогона (если OCR не менялся)
    python -m evaluation.benchmark --compare baseline after

Результаты — evaluation/benchmark_results/<tag>.json (в .gitignore не нужен: файлы маленькие и полезны для истории).
"""
import argparse
import difflib
import json
import os
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

from evaluation import evaluate
from app.categorize import categorize_items, normalize_item_name

RESULTS_DIR = evaluate.BASE_DIR / "benchmark_results"
SKIP = {"receipt_015"}  # как в evaluate.py: на нём зависает Ollama


def _image_for(receipt_id):
    for ext in (".jpeg", ".jpg", ".png"):
        path = evaluate.IMAGES_DIR / f"{receipt_id}{ext}"
        if path.exists():
            return path
    return None


def _name_similarity(expected_name, predicted_name):
    a, b = str(expected_name or "").casefold(), str(predicted_name or "").casefold()
    return difflib.SequenceMatcher(None, a, b).ratio()


def run(tag, reuse_ocr=None, limit=None):
    from app.ocr import init_ocr, ocr_image, resolve_currency
    import app.agent_parser as agent

    os.environ.setdefault("USE_AI_PARSER", "1")
    RESULTS_DIR.mkdir(exist_ok=True)
    reused = {}
    if reuse_ocr:
        reused = {r["receipt_id"]: r for r in json.loads((RESULTS_DIR / f"{reuse_ocr}.json").read_text("utf-8"))["receipts"]}

    ocr = None
    receipts = []
    annotations = sorted(evaluate.ANNOTATIONS_DIR.glob("*.json"))
    for path in annotations[:limit]:
        expected = json.loads(path.read_text("utf-8"))
        rid = expected["receipt_id"]
        image = _image_for(rid)
        if rid in SKIP or image is None:
            continue

        if rid in reused:
            lines, ocr_s = reused[rid]["ocr_lines"], reused[rid]["ocr_s"]
        else:
            if ocr is None:
                ocr = init_ocr()
            t = time.perf_counter()
            lines = ocr_image(ocr, str(image))
            ocr_s = time.perf_counter() - t

        llm_calls = []
        real_call = agent._call_llm

        def counted(prompt, *args, **kwargs):
            t0 = time.perf_counter()
            try:
                return real_call(prompt, *args, **kwargs)
            finally:
                llm_calls.append(time.perf_counter() - t0)

        t = time.perf_counter()
        try:
            with patch.object(agent, "_call_llm", counted):
                parsed = agent.parse_receipt_with_correction("\n".join(lines))
        except Exception as error:  # чек не разобрался — честно считаем как пустой результат
            print(f"{rid}: ошибка Qwen: {error}", file=sys.stderr)
            parsed = {"items": []}
        parse_s = time.perf_counter() - t

        t = time.perf_counter()
        categorize_items(parsed.get("items") or [], evaluate.EVAL_CATEGORIES)
        categorize_s = time.perf_counter() - t
        parsed["currency"], _ = resolve_currency(lines, parsed)

        pairs_seen = []

        def from_pipeline(matched_pairs):
            pairs_seen.extend(matched_pairs)
            annotated = [(e, p) for e, p in matched_pairs if "category" in e]
            correct = sum(1 for e, p in annotated if p.get("category") == e.get("category"))
            return 0, correct, len(annotated)

        with patch.object(evaluate, "_eval_categories_on_matched", from_pipeline):
            metrics = evaluate.evaluate_parser(parsed, expected)

        similarities = [_name_similarity(e.get("name"), p.get("name")) for e, p in pairs_seen]
        receipts.append({
            "receipt_id": rid,
            "ocr_lines": lines,
            "ocr_s": round(ocr_s, 2),
            "parse_s": round(parse_s, 2),
            "llm_calls": len(llm_calls),
            "categorize_s": round(categorize_s, 2),
            "total_s": round(ocr_s + parse_s + categorize_s, 2),
            "metrics": metrics,
            "store": parsed.get("store"),
            "date": parsed.get("date"),
            "total": parsed.get("total"),
            "name_similarity": similarities,
            "items": [
                {"name": i.get("name"), "total_price": i.get("total_price"), "category": i.get("category")}
                for i in parsed.get("items") or [] if isinstance(i, dict)
            ],
        })
        m = metrics
        print(
            f"{rid}: {m['matched_items']}/{m['expected_items']} позиций, "
            f"категории {m['cat_ai_correct']}/{m['cat_total']}, итог {evaluate.format_status(m['total_correct'])}, "
            f"OCR {ocr_s:.1f} с, Qwen {parse_s:.1f} с ({len(llm_calls)} запр.), категории {categorize_s:.1f} с",
            flush=True,
        )

    out = RESULTS_DIR / f"{tag}.json"
    out.write_text(json.dumps({"tag": tag, "receipts": receipts}, ensure_ascii=False, indent=1), "utf-8")
    print(f"\nСохранено: {out}")
    print_summary(tag, receipts)


def summary(receipts):
    ms = [r["metrics"] for r in receipts]

    def rate(key):
        vals = [m[key] for m in ms if m[key] is not None]
        return sum(vals) / len(vals) if vals else None

    matched = sum(m["matched_items"] for m in ms)
    expected = sum(m["expected_items"] for m in ms)
    predicted = sum(m["predicted_items"] for m in ms)
    cat_ok = sum(m["cat_ai_correct"] for m in ms)
    cat_total = sum(m["cat_total"] for m in ms)
    sims = [s for r in receipts for s in r["name_similarity"]]
    precision = matched / predicted if predicted else 0.0
    recall = matched / expected if expected else 0.0
    return {
        "чеков": len(receipts),
        "магазин": rate("store_correct"),
        "дата": rate("date_correct"),
        "итог": rate("total_correct"),
        "валюта": rate("currency_correct"),
        "позиции найдены (recall)": recall,
        "лишние позиции (precision)": precision,
        "F1 позиций": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "сумма позиций = итог": rate("sum_matches_total"),
        "категории верно": cat_ok / cat_total if cat_total else None,
        "похожесть названий": statistics.mean(sims) if sims else None,
        "время OCR, с (среднее)": statistics.mean(r["ocr_s"] for r in receipts),
        "время Qwen, с (среднее)": statistics.mean(r["parse_s"] for r in receipts),
        "время категорий, с (среднее)": statistics.mean(r["categorize_s"] for r in receipts),
        "время всего, с (среднее)": statistics.mean(r["total_s"] for r in receipts),
        "запросов к Qwen на чек": statistics.mean(r["llm_calls"] for r in receipts),
    }


def _fmt(key, value):
    if value is None:
        return "—"
    if key.startswith("время") or key.startswith("запросов"):
        return f"{value:.1f}"
    if key == "чеков":
        return str(value)
    return f"{value:.1%}"


def print_summary(tag, receipts):
    print(f"\n=== {tag} ===")
    for key, value in summary(receipts).items():
        print(f"{key:32} {_fmt(key, value)}")


def compare(tags):
    rows = {t: summary(json.loads((RESULTS_DIR / f"{t}.json").read_text("utf-8"))["receipts"]) for t in tags}
    keys = list(next(iter(rows.values())))
    print(f"{'':32} " + " ".join(f"{t:>12}" for t in tags))
    for key in keys:
        print(f"{key:32} " + " ".join(f"{_fmt(key, rows[t][key]):>12}" for t in tags))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    parser.add_argument("--reuse-ocr", help="взять OCR-строки и время OCR из прогона с этим тегом")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--compare", nargs="+")
    args = parser.parse_args()
    if args.compare:
        compare(args.compare)
    else:
        run(args.tag, args.reuse_ocr, args.limit)
