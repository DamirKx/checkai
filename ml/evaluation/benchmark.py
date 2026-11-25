"""Бенчмарк распознавания: точность и время на эталонных чеках, каждый раз заново (без results_cache.json).

Путь тот же, что у сервера: OCR → Qwen (parse_receipt_with_correction) → категоризация.
Метрики — evaluate.evaluate_parser + группы short/long, p50/p95 времени, доля откатов на regex,
флаг обрезки ответа llm_truncated (ML_TASKS.md, 2.2, 2.3).

Запуск из папки ml:
    python -m evaluation.benchmark --tag baseline
    python -m evaluation.benchmark --tag after --reuse-ocr baseline
    python -m evaluation.benchmark --compare baseline after
"""
import argparse
import difflib
import json
import math
import os
import statistics
import sys
import time
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from app.categorize import categorize_items
from evaluation import evaluate

RESULTS_DIR = evaluate.BASE_DIR / "benchmark_results"
# Задача 2.3: receipt_015 убран из SKIP — чек должен разбираться или честно откатываться по таймауту
SKIP: set = set()


def _image_for(receipt_id: str):
    for ext in (".jpeg", ".jpg", ".png"):
        path = evaluate.IMAGES_DIR / f"{receipt_id}{ext}"
        if path.exists():
            return path
    return None


def _name_similarity(expected_name, predicted_name) -> float:
    a, b = str(expected_name or "").casefold(), str(predicted_name or "").casefold()
    return difflib.SequenceMatcher(None, a, b).ratio()


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return ordered[int(k)]
    return ordered[f] * (c - k) + ordered[c] * (k - f)


def _receipt_group(expected: Dict[str, Any]) -> str:
    explicit = expected.get("length")
    if explicit in ("short", "long"):
        return explicit
    return "long" if len(expected.get("items") or []) > 25 else "short"


def _classify_fallback_reason(error: Exception) -> str:
    name = type(error).__name__.lower()
    msg = str(error).lower()
    if "timeout" in name or "timed out" in msg:
        return "timeout"
    if "truncated" in name:
        return "truncated"
    if "json" in name:
        return "json_error"
    return "error"


def run(tag: str, reuse_ocr: Optional[str] = None, limit: Optional[int] = None) -> None:
    import app.agent_parser as agent
    from app.ocr import init_ocr, ocr_image, resolve_currency
    from app.parser import parse_receipt as parse_receipt_regex

    os.environ.setdefault("USE_AI_PARSER", "1")
    RESULTS_DIR.mkdir(exist_ok=True)
    reused = {}
    if reuse_ocr:
        reused = {
            r["receipt_id"]: r
            for r in json.loads((RESULTS_DIR / f"{reuse_ocr}.json").read_text("utf-8"))["receipts"]
        }

    ocr = None
    receipts = []
    annotations = sorted(evaluate.ANNOTATIONS_DIR.glob("*.json"))
    for path in annotations[:limit]:
        expected = json.loads(path.read_text("utf-8"))
        rid = expected["receipt_id"]
        image = _image_for(rid)
        if rid in SKIP or image is None:
            continue

        group = _receipt_group(expected)

        if rid in reused:
            lines, ocr_s = reused[rid]["ocr_lines"], reused[rid]["ocr_s"]
        else:
            if ocr is None:
                ocr = init_ocr()
            t = time.perf_counter()
            lines = ocr_image(ocr, str(image))
            ocr_s = time.perf_counter() - t

        llm_calls: List[float] = []
        real_call = agent._call_llm

        def counted(prompt, *args, **kwargs):
            t0 = time.perf_counter()
            try:
                return real_call(prompt, *args, **kwargs)
            finally:
                llm_calls.append(time.perf_counter() - t0)

        t = time.perf_counter()
        parser_used = "qwen"
        fallback_reason: Optional[str] = None
        llm_truncated = False

        use_ai = os.getenv("USE_AI_PARSER", "1").strip().lower() not in ("0", "false", "no", "off")
        if not use_ai:
            parsed = parse_receipt_regex(lines)
            parser_used = "regex"
            fallback_reason = "disabled"
        else:
            try:
                with patch.object(agent, "_call_llm", counted):
                    parsed = agent.parse_receipt_with_correction("\n".join(lines))
                parser_used = parsed.get("parser_used") or "qwen"
                llm_truncated = "llm_truncated" in (parsed.get("warnings") or [])
            except Exception as error:
                print(f"{rid}: ошибка Qwen ({type(error).__name__}: {error}) -> regex", file=sys.stderr)
                parsed = parse_receipt_regex(lines)
                parser_used = "regex"
                fallback_reason = _classify_fallback_reason(error)
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
            "length": group,
            "parser_used": parser_used,
            "fallback_reason": fallback_reason,
            "llm_truncated": llm_truncated,
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
            f"{rid} [{group}/{parser_used}]: {m['matched_items']}/{m['expected_items']} позиций, "
            f"категории {m['cat_ai_correct']}/{m['cat_total']}, итог {evaluate.format_status(m['total_correct'])}, "
            f"OCR {ocr_s:.1f} с, Qwen {parse_s:.1f} с ({len(llm_calls)} запр.), категории {categorize_s:.1f} с",
            flush=True,
        )

    out = RESULTS_DIR / f"{tag}.json"
    out.write_text(json.dumps({"tag": tag, "receipts": receipts}, ensure_ascii=False, indent=1), "utf-8")
    print(f"\nСохранено: {out}")
    print_summary(tag, receipts)


def summary(receipts: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not receipts:
        return {"чеков": 0}
    ms = [r["metrics"] for r in receipts]

    def rate(key: str) -> Optional[float]:
        vals = [m[key] for m in ms if m[key] is not None]
        return sum(vals) / len(vals) if vals else None

    matched = sum(m["matched_items"] for m in ms)
    expected = sum(m["expected_items"] for m in ms)
    predicted = sum(m["predicted_items"] for m in ms)
    cat_ok = sum(m["cat_ai_correct"] for m in ms)
    cat_total = sum(m["cat_total"] for m in ms)
    sims = [s for r in receipts for s in r.get("name_similarity", [])]
    precision = matched / predicted if predicted else 0.0
    recall = matched / expected if expected else 0.0
    totals_s = [r["total_s"] for r in receipts]
    regex_fallbacks = sum(1 for r in receipts if r.get("parser_used") == "regex")
    truncated_cnt = sum(1 for r in receipts if r.get("llm_truncated"))

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
        "откаты на regex": regex_fallbacks / len(receipts),
        "обрезка ответа (truncated)": truncated_cnt / len(receipts),
        "время OCR, с (среднее)": statistics.mean(r["ocr_s"] for r in receipts),
        "время Qwen, с (среднее)": statistics.mean(r["parse_s"] for r in receipts),
        "время категорий, с (среднее)": statistics.mean(r["categorize_s"] for r in receipts),
        "время всего, с (среднее)": statistics.mean(totals_s),
        "время всего, с (p50)": _percentile(totals_s, 50),
        "время всего, с (p95)": _percentile(totals_s, 95),
        "запросов к Qwen на чек": statistics.mean(r["llm_calls"] for r in receipts),
    }


def _fmt(key: str, value: Any) -> str:
    if value is None:
        return "—"
    if key.startswith("время") or key.startswith("запросов"):
        return f"{value:.1f}"
    if key == "чеков":
        return str(value)
    return f"{value:.1%}"


def print_summary(tag: str, receipts: List[Dict[str, Any]]) -> None:
    print(f"\n=== {tag} (всего) ===")
    for key, value in summary(receipts).items():
        print(f"{key:32} {_fmt(key, value)}")

    for grp in ("short", "long"):
        sub = [
            r for r in receipts
            if (r.get("length") or ("long" if r["metrics"].get("expected_items", 0) > 25 else "short")) == grp
        ]
        if sub and len(sub) < len(receipts):
            print(f"\n=== {tag} ({grp}) ===")
            for key, value in summary(sub).items():
                print(f"{key:32} {_fmt(key, value)}")


def compare(tags: List[str]) -> None:
    rows = {t: summary(json.loads((RESULTS_DIR / f"{t}.json").read_text("utf-8"))["receipts"]) for t in tags}
    keys = list(next(iter(rows.values())))
    print(f"{'':32} " + " ".join(f"{t:>12}" for t in tags))
    for key in keys:
        print(f"{key:32} " + " ".join(f"{_fmt(key, rows[t].get(key)):>12}" for t in tags))


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
