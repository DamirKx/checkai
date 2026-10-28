import json
from pathlib import Path

from app.agent_parser import extract_total_from_ocr

BASE = Path(__file__).resolve().parents[1]
CACHE = json.loads(
    (BASE / "evaluation/results_cache.json").read_text(encoding="utf-8")
)


def test_receipt_totals():
    expected = {
        "receipt_001": 225900.0,
        "receipt_002": 350.0,
        "receipt_003": 16628.0,
        "receipt_004": 1500.0,
        "receipt_005": 4345.0,
        "receipt_006": 4199.0,
    }

    for receipt_id, correct_total in expected.items():
        lines = CACHE[receipt_id]["ocr_lines"]
        actual = extract_total_from_ocr("\n".join(lines))

        assert actual is not None, f"{receipt_id}: сумма не найдена"
        assert abs(actual - correct_total) < 0.01, (
            f"{receipt_id}: ожидалось {correct_total}, получено {actual}"
        )


if __name__ == "__main__":
    test_receipt_totals()
    print("Все 6 тестов пройдены!")
