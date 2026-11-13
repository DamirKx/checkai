"""Клиенты языковых моделей для генерации структурированного JSON (NEXT_WEEK_TASKS.md, B.1.3).

Поддерживаются провайдеры:
- gemini: Google Gemini REST API (через httpx, бесплатный flash-тариф)
- ollama: локальный сервер Ollama
"""
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("checkai.ml.llm")


def _call_gemini(
    prompt: str,
    schema: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY не задан в переменных окружения")

    model_name = (model or os.getenv("GEMINI_MODEL", "")).strip() or "gemini-2.5-flash"
    # Очищаем префикс models/, если он передан
    if model_name.startswith("models/"):
        model_name = model_name[len("models/"):]

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    generation_config: Dict[str, Any] = {
        "responseMimeType": "application/json",
        "temperature": 0.2,
    }
    if schema:
        generation_config["responseSchema"] = schema

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }

    # Таймаут 30 с, один повтор при 429/5xx с паузой
    last_exc = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates") or []
                    if not candidates:
                        raise ValueError(f"Gemini вернул пустой список candidates: {data}")
                    part_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    return json.loads(part_text)
                elif resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning("Gemini API вернул статус %d (попытка %d): %s", resp.status_code, attempt + 1, resp.text)
                    if attempt == 0:
                        time.sleep(2.0)
                        continue
                    resp.raise_for_status()
                else:
                    resp.raise_for_status()
        except Exception as e:
            last_exc = e
            if attempt == 0:
                time.sleep(2.0)
                continue
            raise last_exc

    raise last_exc or RuntimeError("Не удалось получить ответ от Gemini")


def _call_ollama(
    prompt: str,
    schema: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    model_name = (model or os.getenv("OLLAMA_MODEL", "")).strip() or "qwen2.5:3b"
    num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "4096"))

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.2,
            "num_ctx": num_ctx,
        },
    }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        raw_text = resp.json().get("response", "")
        return json.loads(raw_text)


def complete_json(
    prompt: str,
    schema: Optional[Dict[str, Any]] = None,
    provider: str = "gemini",
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Универсальный вызов LLM с гарантией возврата dict из JSON."""
    prov = (provider or "gemini").strip().lower()
    if prov.startswith("gemini"):
        return _call_gemini(prompt, schema=schema, model=model)
    elif prov.startswith("ollama"):
        return _call_ollama(prompt, schema=schema, model=model)
    else:
        raise ValueError(f"Неизвестный AI provider: {provider}")
