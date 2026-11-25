"""Подготовка фото чека перед OCR.

- По умолчанию (3.2) фото масштабируется по целевой ширине OCR_TARGET_WIDTH (ориентир 1200 px),
  а высота сохраняется пропорционально — так длинный чек (например 1500x6000) не превращается
  в нечитаемую полоску. Если в окружении явно задан OCR_MAX_SIDE (и не задан OCR_TARGET_WIDTH),
  работает обратная совместимость с ограничением по длинной стороне.
- Маленькие фото (меньше OCR_MIN_WIDTH по ширине) увеличиваются (до 3x).
- Длинные фото выше OCR_SLICE_HEIGHT нарезаются на горизонтальные полосы с перекрытием OCR_SLICE_OVERLAP (5.2).
- Контраст выравнивается локально (CLAHE), если включён OCR_CLAHE=1.

OCR_PREPROCESS=0 выключает всё (OCR получает файл как есть).
Без PaddleOCR: модуль импортируют и ML (app.preprocess), и бэкенд (ml.app.preprocess).
"""
import logging
import os
from typing import Any, List, Optional, Tuple

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
    target_width_env = os.getenv("OCR_TARGET_WIDTH", "").strip()
    max_side_env = os.getenv("OCR_MAX_SIDE", "").strip()
    min_width = _int_env("OCR_MIN_WIDTH", 0)
    clahe_on = os.getenv("OCR_CLAHE", "0").strip().lower() in ("1", "true", "yes", "on")

    h, w = image.shape[:2]

    if max_side_env and not target_width_env:
        # Явно заданный OCR_MAX_SIDE (обратная совместимость и тесты)
        max_side = _int_env("OCR_MAX_SIDE", 2000)
        if max_side and max(h, w) > max_side:
            scale = max_side / max(h, w)
            image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        elif min_width and w < min_width:
            scale = min(min_width / w, 3.0)
            image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_CUBIC)
    else:
        # Новый режим по ширине (задача 3.2): длинные чеки сохраняют читаемый размер шрифта
        target_width = _int_env("OCR_TARGET_WIDTH", 1200)
        if target_width and w > target_width:
            scale = target_width / w
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


def slice_long_image(
    image: np.ndarray,
    slice_height: Optional[int] = None,
    overlap: Optional[int] = None,
) -> List[Tuple[np.ndarray, int]]:
    """Режет высокое фото на горизонтальные полосы с перекрытием (задача 5.2).

    Возвращает список (crop_bgr, y_offset). Для обычных фото возвращает [(image, 0)].
    """
    h, _ = image.shape[:2]
    max_h = slice_height if slice_height is not None else _int_env("OCR_SLICE_HEIGHT", 1600)
    ov = overlap if overlap is not None else _int_env("OCR_SLICE_OVERLAP", 140)

    if max_h <= 0 or h <= max_h:
        return [(image, 0)]

    ov = max(0, min(ov, max_h // 3))
    step = max(200, max_h - ov)
    slices: List[Tuple[np.ndarray, int]] = []
    y = 0
    while y < h:
        y_end = min(y + max_h, h)
        slices.append((image[y:y_end], y))
        if y_end >= h:
            break
        y += step
    return slices


def load_prepared_image(image_path: str) -> Optional[np.ndarray]:
    """Читает фото и применяет prepare() (если OCR_PREPROCESS != 0). При ошибке — None."""
    try:
        image = _read(image_path)
        if image is None:
            return None
        return prepare(image) if enabled() else image
    except Exception as error:
        logger.warning("Предобработка фото не удалась (%s), OCR получит исходный файл", error)
        return None


def prepare_for_ocr(image_path: str) -> Any:
    """Путь к фото → массив для PaddleOCR. При ошибке или OCR_PREPROCESS=0 — исходный путь."""
    if not enabled():
        return image_path
    img = load_prepared_image(image_path)
    return img if img is not None else image_path
