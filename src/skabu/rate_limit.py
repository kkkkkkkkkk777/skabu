"""分間リクエスト上限を超えないための簡易スライディングウィンドウ。"""

from __future__ import annotations

import time
from collections import deque

from skabu.logging_setup import get_logger


class SlidingWindowRateLimiter:
    def __init__(self, max_calls: int, window_sec: float = 60.0) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        self.max_calls = max_calls
        self.window_sec = window_sec
        self._times: deque[float] = deque()

    def acquire(self, now: float | None = None) -> float:
        """必要なら待ち、取得できた時刻を返す。返値は待った秒数。"""
        waited = 0.0
        while True:
            current = time.monotonic() if now is None else now
            self._evict(current)
            if len(self._times) < self.max_calls:
                self._times.append(current)
                if waited > 0:
                    get_logger().info(
                        "rate limit wait done: waited=%.2fs remaining_slots=%s/%s",
                        waited,
                        self.max_calls - len(self._times),
                        self.max_calls,
                    )
                return waited
            sleep_for = self.window_sec - (current - self._times[0]) + 0.05
            sleep_for = max(sleep_for, 0.05)
            get_logger().warning(
                "rate limit reached (%s/%ss). sleeping %.2fs",
                self.max_calls,
                self.window_sec,
                sleep_for,
            )
            time.sleep(sleep_for)
            waited += sleep_for
            now = None

    def _evict(self, current: float) -> None:
        while self._times and current - self._times[0] >= self.window_sec:
            self._times.popleft()
