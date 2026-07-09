from collections import defaultdict, deque
from datetime import datetime, timedelta


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[datetime]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = datetime.utcnow()
        window_start = now - timedelta(seconds=window_seconds)
        events = self._events[key]
        while events and events[0] < window_start:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True


rate_limiter = InMemoryRateLimiter()

