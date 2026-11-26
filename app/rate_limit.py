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


# Лимиты по пути (макс. запросов, секунд в окне) — проверяет middleware в app/main.py
RATE_LIMIT_RULES = {
    "/api/auth/login": (10, 60),            # защита от перебора паролей
    "/api/auth/register": (10, 60),         # защита от спама регистраций
    "/api/auth/reset-password": (5, 60),    # защита от перебора кодов восстановления
    "/api/auth/recovery-code": (5, 60),     # защита от перебора пароля
    "/api/analyze": (15, 60),               # защита от перегрузки OCR
    "/api/analyze/async": (15, 60),         # защита от перегрузки OCR
}
# Лимиты, которые зависят и от метода (GET того же пути не ограничиваем)
RATE_LIMIT_METHOD_RULES = {
    ("POST", "/api/categories"): (30, 60),  # защита от спама категориями
    ("POST", "/api/auth/me/avatar"): (20, 60),  # перекодирование картинки — не бесплатное
}
# POST /api/categorize: запросов на пользователя за столько секунд (проверяет сам эндпоинт)
CATEGORIZE_RATE = (30, 60)
