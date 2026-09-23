"""
Тестовый запуск проверки пайплайна CheckAI на тестовом чеке images.jpeg.
Запуск: python test_ocr.py
"""
import os
import sys
from main import analyze_receipt


def test_receipt_analysis():
    img_path = 'images.jpeg'
    if not os.path.exists(img_path):
        print(f"Файл {img_path} не найден, тест пропущен.")
        return

    print("Запуск теста анализа чека...")
    result = analyze_receipt(img_path)

    assert result is not None, "Результат не должен быть пустым"
    assert 'items' in result, "В результате должен быть ключ 'items'"
    assert len(result['items']) > 0, "Должны быть распознаны товарные позиции"
    assert result.get('total') is not None, "Итоговая сумма должна быть определена"

    print("Проверка пройдена успешно:")
    print(f"  Организация: {result.get('store')}")
    print(f"  Позиций: {len(result['items'])}")
    print(f"  Итог: {result.get('total')} {result.get('currency', '₸')}")


if __name__ == '__main__':
    test_receipt_analysis()
