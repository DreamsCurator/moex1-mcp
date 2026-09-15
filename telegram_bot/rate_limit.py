"""Простой in-memory rate limit по user_id."""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    """Не более ``max_requests`` событий за ``window_sec`` на ключ."""

    def __init__(self, max_requests: int = 10, window_sec: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int, now: float | None = None) -> bool:
        """True, если запрос можно обработать."""

        ts = now if now is not None else time.monotonic()
        bucket = self._hits[user_id]
        cutoff = ts - self.window_sec
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            return False
        bucket.append(ts)
        return True
