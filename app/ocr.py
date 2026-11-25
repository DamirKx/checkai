"""Тонкая обёртка для сервера: единая реализация OCR живёт в ml/app/ocr.py (ML_TASKS.md, 3.0)."""
import sys

from ml.app.ocr import (  # noqa: F401
    analyze_receipt,
    init_ocr,
    logger,
    merge_ocr_fragments,
    merge_pages,
    ocr_image,
    ocr_image_with_scores,
    receipt_size,
    resolve_currency,
)

if __name__ == "__main__":
    img_path = sys.argv[1] if len(sys.argv) > 1 else "images.jpeg"
    print(f"=== Распознавание: {img_path} ===")
    ocr_instance = init_ocr()
    data = analyze_receipt(img_path, ocr=ocr_instance)

    print("\n" + "=" * 50)
    print("           ИТОГОВЫЕ ДАННЫЕ ЧЕКА")
    print("=" * 50)
    print(f"Магазин:      {data.get('store') or '—'}")
    print(f"Дата/Время:   {data.get('date') or '—'}  {data.get('time') or '—'}")
    print("-" * 50)
    print(f"{'Товар':<28} | {'Кол':<4} | {'Цена':>7} | {'Сумма':>8}")
    print("-" * 50)
    for item in data.get("items", []):
        name = item["name"][:26] + ".." if len(item["name"]) > 28 else item["name"]
        q = item["quantity"]
        p = f"{item['price_per_unit']:.2f}" if item.get("price_per_unit") else "--"
        s = f"{item['total_price']:.2f}" if item.get("total_price") else "--"
        print(f"{name:<28} | {q:<4} | {p:>7} | {s:>8}")
    print("-" * 50)
    total = data.get("total") or 0.0
    print(f"ИТОГО: {total:.2f} {data.get('currency') or '₸'}")
