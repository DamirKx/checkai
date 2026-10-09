import time
import threading
from typing import Dict, List, Tuple
from fastapi import Request

from app import config

# Раз в столько проверок удаляем ключи, по которым давно не было запросов, чтобы память не росла
SWEEP_EVERY = 1000


class InMemoryRateLimiter:
    """Потокобезопасный ограничитель частоты запросов по IP (скользящее окно)."""

    def __init__(self):
        self._history: Dict[str, List[float]] = {}
        self._lock = threading.Lock()
        self._checks = 0

    def check_limit(self, key: str, max_requests: int, window_seconds: int) -> Tuple[bool, int]:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            self._checks += 1
            if self._checks % SWEEP_EVERY == 0:
                self._sweep(now)
            timestamps = [t for t in self._history.get(key, []) if t > cutoff]
            if len(timestamps) >= max_requests:
                retry_after = int(window_seconds - (now - timestamps[0])) + 1
                self._history[key] = timestamps
                return False, max(1, retry_after)
            timestamps.append(now)
            self._history[key] = timestamps
            return True, 0

    def _sweep(self, now: float, max_window_seconds: int = 3600) -> None:
        cutoff = now - max_window_seconds
        for key in [k for k, ts in self._history.items() if not ts or ts[-1] <= cutoff]:
            del self._history[key]

    def clear(self):
        """Очистка истории (удобно для тестов)."""
        with self._lock:
            self._history.clear()


limiter = InMemoryRateLimiter()


def get_client_ip(request: Request) -> str:
    """IP клиента. X-Forwarded-For учитываем только за своим прокси (TRUST_PROXY=1):
    иначе клиент подставит в заголовок любой адрес и обойдёт лимит."""
    if config.TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"
