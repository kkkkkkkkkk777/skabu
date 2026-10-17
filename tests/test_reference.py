from datetime import date, timedelta

from skabu.reference import reference_levels


def _bar(offset: int, **kwargs) -> dict:
    day = date(2024, 1, 1) + timedelta(days=offset)
    base = {"date": day.isoformat(), "open": 100.0, "high": 110.0, "low": 100.0, "close": 105.0}
    base.update(kwargs)
    return base


def test_reference_levels_from_short_pattern() -> None:
    bars = [_bar(i) for i in range(40)]
    bars[10].update(high=90.0, low=80.0, close=85.0)
    bars[20].update(high=160.0, low=140.0, close=150.0)
    bars[30].update(high=100.0, low=90.0, close=95.0)
    bars[35].update(close=100.0)
    bars[36].update(close=100.0)
    bars[37].update(close=100.0)
    bars[38].update(high=112.0, low=108.0, close=110.0)
    bars[39].update(high=125.0, low=115.0, close=120.0)

    items = {item["id"]: item for item in reference_levels(bars)}
    assert items["pullback_low"]["price"] == 90.0
    assert items["pullback_low"]["date"] == bars[30]["date"]
    assert items["pullback_low"]["marker"] == "押"
    assert items["pullback_low"]["diff"] == -30.0
    assert items["rebound_high"]["price"] == 160.0
    assert items["rebound_high"]["marker"] == "戻"
    assert items["high_52"]["price"] == 160.0
    assert items["high_52"]["label"] == "高値（2024-01-01〜）"
    assert items["low_52"]["price"] == 80.0
    assert items["sma_5"]["price"] == 106.0
    assert "sma_75" not in items
    assert "sma_200" not in items
    assert items["high_20"]["price"] == 160.0
    assert items["high_20"]["label"] == "20日高値"
    assert items["low_20"]["price"] == 90.0
    assert items["low_20"]["label"] == "20日安値"
    assert items["high_60"]["price"] == 160.0
    assert items["high_60"]["label"] == "高値（60日・2024-01-01〜）"
    assert items["low_60"]["price"] == 80.0
    assert items["low_60"]["label"] == "安値（60日・2024-01-01〜）"
    assert items["prev_high"]["price"] == 112.0
    assert items["prev_low"]["price"] == 108.0
    assert items["prev_close"]["price"] == 110.0
    assert items["round_below"]["price"] == 100.0
    assert items["round_above"]["price"] == 200.0
    assert "round_at" not in items
    assert "kijun" in items
    assert "cloud_top" not in items


def test_reference_year_uses_last_260_bars() -> None:
    bars = [_bar(i, high=10.0, low=5.0, close=8.0) for i in range(261)]
    bars[0].update(high=99.0, low=1.0)
    items = {item["id"]: item for item in reference_levels(bars)}
    assert items["high_52"]["label"] == "52週高値"
    assert items["high_52"]["price"] == 10.0
    assert items["low_52"]["price"] == 5.0
    assert items["high_20"]["label"] == "20日高値"
    assert items["high_20"]["price"] == 10.0
    assert items["low_20"]["price"] == 5.0
    assert items["high_60"]["label"] == "60日高値"
    assert items["high_60"]["price"] == 10.0
    assert items["low_60"]["price"] == 5.0
    assert items["prev_close"]["price"] == 8.0
    assert items["round_above"]["price"] == 100.0
    assert "round_below" not in items


def test_reference_levels_empty_without_close() -> None:
    assert reference_levels([]) == []
    assert reference_levels([{"date": "2024-01-01", "high": 1, "low": 1, "close": None}]) == []
