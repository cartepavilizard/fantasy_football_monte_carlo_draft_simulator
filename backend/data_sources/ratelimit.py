# -*- coding: utf-8 -*-
"""
PER-ADAPTER REQUEST SPACING

Every adapter owns a RateLimiter so no source is hit faster than its
configured minimum interval, even when callers fire requests concurrently.
"""
import asyncio
import time


class RateLimiter:
    """Serializes callers and enforces a minimum interval between passes"""

    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = min_interval_seconds
        self._lock = asyncio.Lock()
        self._last_pass = 0.0

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            delay = self._last_pass + self.min_interval_seconds - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_pass = time.monotonic()
