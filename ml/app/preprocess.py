"""Подготовка фото чека перед OCR.

- Большие фото с телефона (4000 px и больше) уменьшаются до OCR_MAX_SIDE по длинной стороне: детектор текста
  PaddleOCR работает почти в исходном разрешении, и время растёт с числом пикселей, а буквы чека и так крупные.
- Маленькие фото (меньше OCR_MIN_WIDTH по ширине) увеличиваются: на снимках шириной ~450 px буквы в 8–10 px,
  и распознаватель путает похожие символы.
- Контраст выравнивается локально (CLAHE) — для тёмных и неравномерно освещённых снимков.

OCR_PREPROCESS=0 выключает всё (OCR получает файл как есть). Значения по умолчанию подобраны на
ml/evaluation/benchmark.py — меняйте вместе с повторным прогоном.

Без PaddleOCR: модуль импортируют и ML (app.preprocess), и бэкенд (ml.app.preprocess).
"""
import logging
import os
from typing import Any, Optional

import cv2
import numpy as np

logger = logging.getLogger("checkai.preprocess")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def enabled() -> bool:
    return os.getenv("OCR_PREPROCESS", "1").strip().lower() not in ("0", "false", "no", "off")


def _read(path: str) -> Optional[np.ndarray]:
    # cv2.imread не открывает пути с кириллицей на Windows — читаем байты сами
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def prepare(image: np.ndarray) -> np.ndarray:
    """Масштаб и контраст. На входе и выходе — BGR."""
    max_side = _int_env("OCR_MAX_SIDE", 2000)
    min_width = _int_env("OCR_MIN_WIDTH", 0)
    clahe_on = os.getenv("OCR_CLAHE", "0").strip().lower() in ("1", "true", "yes", "on")

    h, w = image.shape[:2]
    if max_side and max(h, w) > max_side:
        scale = max_side / max(h, w)
        image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    elif min_width and w < min_width:
        scale = min(min_width / w, 3.0)
        image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_CUBIC)

    if clahe_on:
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        lightness, a, b = cv2.split(lab)
        lightness = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lightness)
        image = cv2.cvtColor(cv2.merge((lightness, a, b)), cv2.COLOR_LAB2BGR)
    return image


def prepare_for_ocr(image_path: str) -> Any:
    """Путь к фото → массив для PaddleOCR. При ошибке или OCR_PREPROCESS=0 — исходный путь."""
    if not enabled():
        return image_path
    try:
        image = _read(image_path)
        if image is None:
            return image_path
        return prepare(image)
    except Exception as error:  # предобработка не должна ломать распознавание
        logger.warning("Предобработка фото не удалась (%s), OCR получит исходный файл", error)
        return image_path
