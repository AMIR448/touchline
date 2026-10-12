"""Guards for a public demo: the AI can't be used to run up the bill.

- DailyBudget: a cap on *fresh* AI runs per day (crew runs, recaps, questions). Cached results are
  free and don't count. Past the cap, viewers get the stats engine's verified text instead.
- RateLimiter: a sliding window per visitor, used for "Ask the match".
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Callable


class DailyBudget:
    def __init__(self, limit: int, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.limit = limit
        self.now = now
        self.day = now().date()
        self.used = 0

    def take(self) -> bool:
        today = self.now().date()
        if today != self.day:
            self.day, self.used = today, 0
        if self.used >= self.limit:
            return False
        self.used += 1
        return True

    def status(self) -> dict:
        return {"ai_runs_today": self.used, "daily_limit": self.limit}


class RateLimiter:
    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self.clock = limit, window_s, clock
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now, hits = self.clock(), self.hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True
