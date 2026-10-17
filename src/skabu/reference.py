"""日足から、チャートで見やすい値段を出す。売買の判定には使わない。"""

from __future__ import annotations

import math
from typing import Any

from skabu.indicators.mathutil import ichimoku, sma
from skabu.indicators.plugins.doubletop import swing_highs
from skabu.indicators.plugins.downtrend import swing_lows
from skabu.logging_setup import get_logger

SWING = 5
WEEK_BARS = 260
SMA_LENGTHS = (5, 25, 75, 200)
KIJUN_LEN = 26
CLOUD_DISPLACEMENT = 26
RECENT_WINDOWS = (
    (20, "high_20", "low_20", "20日"),
    (60, "high_60", "low_60", "60日"),
)
ROUND_STEP = 100
ROUND_STEP_BIG = 1000
ROUND_BIG_FROM = 10000


def reference_levels(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """押し安値、近い高安、移動平均、一目、前日、キリ番。足りない項目は省く。"""
    log = get_logger()
    close = _last(bars, "close")
    if close is None or close == 0:
        log.warning("reference levels skipped reason=no_close bars=%s", len(bars))
        return []
    skipped: list[str] = []
    items: list[dict[str, Any]] = []
    items.extend(_swings(bars, close, skipped))
    items.extend(_recent_extremes(bars, close, skipped))
    items.extend(_year_extremes(bars, close, skipped))
    items.extend(_averages(bars, close, skipped))
    items.extend(_ichimoku_levels(bars, close, skipped))
    items.extend(_previous_bar(bars, close, skipped))
    items.extend(_round_levels(close, skipped))
    log.info(
        "reference levels n=%s close=%s skipped=%s",
        len(items),
        close,
        ",".join(skipped) if skipped else "-",
    )
    return items


def _swings(bars: list[dict[str, Any]], close: float, skipped: list[str]) -> list[dict[str, Any]]:
    lows = [_num(bar.get("low")) for bar in bars]
    highs = [_num(bar.get("high")) for bar in bars]
    items: list[dict[str, Any]] = []
    troughs = [item for item in swing_lows(lows, SWING) if item[1] < close]
    if troughs:
        index, price = troughs[-1]
        items.append(_item("pullback_low", "押し安値", price, close, date=_date(bars, index), marker="押"))
    else:
        skipped.append("pullback_low:no_trough")
    peaks = [item for item in swing_highs(highs, SWING) if item[1] > close]
    if peaks:
        index, price = peaks[-1]
        items.append(_item("rebound_high", "戻り高値", price, close, date=_date(bars, index), marker="戻"))
    else:
        skipped.append("rebound_high:no_peak")
    return items


def _recent_extremes(bars: list[dict[str, Any]], close: float, skipped: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for length, high_id, low_id, name in RECENT_WINDOWS:
        items.extend(
            _span_extremes(
                bars,
                close,
                skipped,
                length=length,
                high_id=high_id,
                low_id=low_id,
                full_high=f"{name}高値",
                full_low=f"{name}安値",
                short_high=f"高値（{name}・{{start}}〜）",
                short_low=f"安値（{name}・{{start}}〜）",
                skip_key=name,
            )
        )
    return items


def _span_extremes(
    bars: list[dict[str, Any]],
    close: float,
    skipped: list[str],
    *,
    length: int,
    high_id: str,
    low_id: str,
    full_high: str,
    full_low: str,
    short_high: str,
    short_low: str,
    skip_key: str,
) -> list[dict[str, Any]]:
    window = bars[-length:]
    highs = [price for price in (_num(bar.get("high")) for bar in window) if price is not None]
    lows = [price for price in (_num(bar.get("low")) for bar in window) if price is not None]
    if not highs or not lows:
        skipped.append(f"{skip_key}:no_range")
        return []
    start = str(window[0].get("date") or "")
    full = len(window) >= length
    if not full:
        get_logger().info("reference span short key=%s bars=%s start=%s", skip_key, len(window), start)
    return [
        _item(high_id, full_high if full else short_high.format(start=start), max(highs), close),
        _item(low_id, full_low if full else short_low.format(start=start), min(lows), close),
    ]


def _year_extremes(bars: list[dict[str, Any]], close: float, skipped: list[str]) -> list[dict[str, Any]]:
    window = bars[-WEEK_BARS:]
    highs = [price for price in (_num(bar.get("high")) for bar in window) if price is not None]
    lows = [price for price in (_num(bar.get("low")) for bar in window) if price is not None]
    if not highs or not lows:
        skipped.append("year:no_range")
        return []
    start = str(window[0].get("date") or "")
    full = len(window) >= WEEK_BARS
    high_label = "52週高値" if full else f"高値（{start}〜）"
    low_label = "52週安値" if full else f"安値（{start}〜）"
    if not full:
        get_logger().info("reference year window short bars=%s start=%s", len(window), start)
    return [
        _item("high_52", high_label, max(highs), close),
        _item("low_52", low_label, min(lows), close),
    ]


def _averages(bars: list[dict[str, Any]], close: float, skipped: list[str]) -> list[dict[str, Any]]:
    closes = [_num(bar.get("close")) for bar in bars]
    items: list[dict[str, Any]] = []
    for length in SMA_LENGTHS:
        if len(bars) < length:
            skipped.append(f"sma_{length}:short")
            continue
        values = sma(closes, length)
        last = values[-1] if values else None
        if last is None:
            skipped.append(f"sma_{length}:empty")
            continue
        items.append(_item(f"sma_{length}", f"{length}日線", float(last), close))
    return items


def _ichimoku_levels(bars: list[dict[str, Any]], close: float, skipped: list[str]) -> list[dict[str, Any]]:
    if len(bars) < KIJUN_LEN:
        skipped.append("kijun:short")
        skipped.append("cloud:short")
        return []
    highs = [_num(bar.get("high")) for bar in bars]
    lows = [_num(bar.get("low")) for bar in bars]
    closes = [_num(bar.get("close")) for bar in bars]
    series = ichimoku(highs, lows, closes, kijun_len=KIJUN_LEN, displacement=CLOUD_DISPLACEMENT)
    items: list[dict[str, Any]] = []
    kijun = series["kijun"][-1] if series["kijun"] else None
    if kijun is None:
        skipped.append("kijun:empty")
    else:
        items.append(_item("kijun", "基準線", float(kijun), close))
    span_a = series["span_a"][-1] if series["span_a"] else None
    span_b = series["span_b"][-1] if series["span_b"] else None
    if span_a is None or span_b is None:
        skipped.append("cloud:empty")
        return items
    top = max(float(span_a), float(span_b))
    bottom = min(float(span_a), float(span_b))
    items.append(_item("cloud_top", "雲の上", top, close))
    items.append(_item("cloud_bottom", "雲の下", bottom, close))
    return items


def _previous_bar(bars: list[dict[str, Any]], close: float, skipped: list[str]) -> list[dict[str, Any]]:
    if len(bars) < 2:
        skipped.append("prev:short")
        return []
    high = _num(bars[-2].get("high"))
    low = _num(bars[-2].get("low"))
    prev_close = _num(bars[-2].get("close"))
    items: list[dict[str, Any]] = []
    if high is None:
        skipped.append("prev_high:empty")
    else:
        items.append(_item("prev_high", "前日高値", high, close))
    if low is None:
        skipped.append("prev_low:empty")
    else:
        items.append(_item("prev_low", "前日安値", low, close))
    if prev_close is None:
        skipped.append("prev_close:empty")
    else:
        items.append(_item("prev_close", "前日終値", prev_close, close))
    return items


def _round_levels(close: float, skipped: list[str]) -> list[dict[str, Any]]:
    step = ROUND_STEP_BIG if close >= ROUND_BIG_FROM else ROUND_STEP
    units = close / step
    below = math.floor(units + 1e-9) * step
    above = math.ceil(units - 1e-9) * step
    if below == above:
        prices = (("round_below", below - step), ("round_at", below), ("round_above", above + step))
    else:
        prices = (("round_below", below), ("round_above", above))
    items: list[dict[str, Any]] = []
    for item_id, price in prices:
        if price <= 0:
            skipped.append(f"{item_id}:non_positive")
            continue
        items.append(_item(item_id, "キリ番", float(price), close))
    if not items:
        get_logger().info("reference round levels empty close=%s step=%s", close, step)
    return items


def _item(
    item_id: str,
    label: str,
    price: float,
    close: float,
    *,
    date: str | None = None,
    marker: str | None = None,
) -> dict[str, Any]:
    diff = float(price) - float(close)
    item: dict[str, Any] = {
        "id": item_id,
        "label": label,
        "price": round(float(price), 4),
        "diff": round(diff, 4),
        "diff_pct": round(diff / float(close), 6),
    }
    if date:
        item["date"] = date
    if marker:
        item["marker"] = marker
    return item


def _last(bars: list[dict[str, Any]], key: str) -> float | None:
    for bar in reversed(bars):
        value = _num(bar.get(key))
        if value is not None:
            return value
    return None


def _date(bars: list[dict[str, Any]], index: int) -> str | None:
    if index < 0 or index >= len(bars):
        return None
    raw = bars[index].get("date")
    if raw is None:
        return None
    return str(raw)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
