from skabu.rate_limit import SlidingWindowRateLimiter


def test_limiter_does_not_wait_under_capacity(monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("skabu.rate_limit.time.sleep", lambda sec: slept.append(sec))

    ticks = iter([100.0, 100.1, 100.2])
    monkeypatch.setattr("skabu.rate_limit.time.monotonic", lambda: next(ticks))

    limiter = SlidingWindowRateLimiter(max_calls=3, window_sec=60)
    assert limiter.acquire() == 0.0
    assert limiter.acquire() == 0.0
    assert slept == []


def test_limiter_sleeps_when_full(monkeypatch) -> None:
    slept: list[float] = []
    clock = {"t": 10.0}

    monkeypatch.setattr("skabu.rate_limit.time.monotonic", lambda: clock["t"])

    def fake_sleep(sec: float) -> None:
        slept.append(sec)
        clock["t"] += sec

    monkeypatch.setattr("skabu.rate_limit.time.sleep", fake_sleep)
    limiter = SlidingWindowRateLimiter(max_calls=1, window_sec=60)
    assert limiter.acquire() == 0.0
    waited = limiter.acquire()
    assert slept
    assert waited > 0
