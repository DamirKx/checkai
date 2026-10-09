import time
import threading
from typing import Dict, List, Tuple
from fastapi import Request


class InMemoryRateLimiter:
    """Потокобезопасный ограничитель частоты запросов по IP (скользящее окно)."""

    def __init__(self):
        self._history: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def check_limit(self, key: str, max_requests: int, window_seconds: int) -> Tuple[bool, int]:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            timestamps = [t for t in self._history.get(key, []) if t > cutoff]
            if len(timestamps) >= max_requests:
                retry_after = int(window_seconds - (now - timestamps[0])) + 1
                self._history[key] = timestamps
                return False, max(1, retry_after)
            timestamps.append(now)
            self._history[key] = timestamps
            return True, 0

    def clear(self):
        """Очистка истории (удобно для тестов)."""
        with self._lock:
            self._history.clear()


limiter = InMemoryRateLimiter()


def get_client_ip(request: Request) -> str:
    """Извлекает IP клиента с учётом заголовка X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"
