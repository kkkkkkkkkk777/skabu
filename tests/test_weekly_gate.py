from datetime import date, timedelta

from skabu.ranking import passes_filters
from skabu.weekly_gate import bars_to_weeks, weekly_features


def _fridays(n: int, start: date | None = None) -> list[date]:
    day = start or date(2023, 1, 6)
    assert day.isoweekday() == 5
    return [day + timedelta(days=7 * i) for i in range(n)]


def _week_bars(highs: list[float], lows: list[float], closes: list[float]) -> list[dict]:
    days = _fridays(len(closes))
    return [
        {
            "date": day.isoformat(),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1,
        }
        for day, high, low, close in zip(days, highs, lows, closes, strict=True)
    ]


def _higher_weeks() -> list[dict]:
    n = 65
    highs = [118.0] * n
    lows = [112.0] * n
    closes = [115.0] * n
    highs[20] = 122.0
    lows[30] = 100.0
    highs[42] = 132.0
    lows[52] = 108.0
    closes[-1] = 120.0
    highs[-1] = 121.0
    lows[-1] = 119.0
    return _week_bars(highs, lows, closes)


def test_partial_week_uses_only_days_given() -> None:
    seen = [
        {"date": "2024-01-08", "open": 100, "high": 112, "low": 99, "close": 110, "volume": 1},
        {"date": "2024-01-10", "open": 110, "high": 111, "low": 107, "close": 108, "volume": 1},
    ]
    week = bars_to_weeks(seen)[0]
    assert week["date"] == "2024-01-12"
    assert week["open"] == 100
    assert week["high"] == 112
    assert week["low"] == 99
    assert week["close"] == 108
    later = seen + [
        {"date": "2024-01-12", "open": 108, "high": 130, "low": 80, "close": 90, "volume": 1}
    ]
    finished = bars_to_weeks(later)[0]
    assert finished["high"] == 130
    assert finished["close"] == 90
    assert week["high"] == 112


def test_weekly_uptrend_gate() -> None:
    bars = _higher_weeks()
    feats = weekly_features(bars)
    assert feats["weeks"] == 65
    assert feats["higher_highs"] == 1
    assert feats["above_sma"] == 1
    assert passes_filters(bars, [{"id": "weekly", "feature": "higher_highs", "op": "eq", "value": 1}]) is True
    falling = _week_bars(
        [151.0 - i * 0.8 for i in range(65)],
        [149.0 - i * 0.8 for i in range(65)],
        [150.0 - i * 0.8 for i in range(65)],
    )
    assert weekly_features(falling)["higher_highs"] == 0
    assert weekly_features(falling)["above_sma"] == 0
    assert passes_filters(falling, [{"id": "weekly", "feature": "higher_highs", "op": "eq", "value": 1}]) is False


def test_weekly_rsi_and_short_history(caplog) -> None:
    rising = _week_bars(
        [101.0 + i for i in range(20)],
        [99.0 + i for i in range(20)],
        [100.0 + i for i in range(20)],
    )
    hot = weekly_features(rising)
    assert hot["rsi"] is not None
    assert hot["rsi"] > 70
    assert passes_filters(rising, [{"id": "weekly", "feature": "rsi", "op": "lt", "value": 70}]) is False
    short = rising[:3]
    missing = weekly_features(short)
    assert missing["higher_highs"] is None
    assert missing["above_sma"] is None
    assert missing["rsi"] is None
    with caplog.at_level("INFO"):
        assert passes_filters(short, [{"id": "weekly", "feature": "above_sma", "op": "eq", "value": 1}]) is False
    assert any("weekly gate fail" in rec.message and "reason=short" in rec.message for rec in caplog.records)
