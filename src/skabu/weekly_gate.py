"""予測日までの日足を週足にまとめ、門用の3つの数字を出す。未来の日は受け取った足に含まれない。"""

from __future__ import annotations

from datetime import date
from typing import Any

from skabu.indicators.mathutil import rsi, sma
from skabu.indicators.plugins.doubletop import RECENT, SWING
from skabu.indicators.plugins.uptrend import detect_higher_highs
from skabu.logging_setup import get_logger
from skabu.store import week_end_friday

WEEKLY_ID = "weekly"
WEEKLY_SMA = 13
WEEKLY_RSI = 14


def bars_to_weeks(daily_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """日足を金曜締めの週足にする。途中の週は、渡された日だけで作る。"""
    ordered = sorted(daily_bars, key=lambda bar: str(bar.get("date") or ""))
    grouped: dict[str, list[dict[str, Any]]] = {}
    log = get_logger()
    for bar in ordered:
        raw = bar.get("date")
        if not raw:
            continue
        try:
            day = date.fromisoformat(str(raw)[:10])
        except ValueError:
            log.warning("weekly gate skip bad date=%s", raw)
            continue
        try:
            ohlc = {
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
            }
        except (KeyError, TypeError, ValueError):
            log.warning("weekly gate skip bad bar date=%s", raw)
            continue
        key = week_end_friday(day).isoformat()
        grouped.setdefault(key, []).append(ohlc)
    weeks: list[dict[str, Any]] = []
    for week_end, series in grouped.items():
        weeks.append(
            {
                "date": week_end,
                "open": series[0]["open"],
                "high": max(item["high"] for item in series),
                "low": min(item["low"] for item in series),
                "close": series[-1]["close"],
            }
        )
    return weeks


def weekly_features(daily_bars: list[dict[str, Any]]) -> dict[str, Any]:
    """切り上げ、13週線の上、週足RSI。週が足りない項目は None。"""
    weeks = bars_to_weeks(daily_bars)
    highs = [bar["high"] for bar in weeks]
    lows = [bar["low"] for bar in weeks]
    closes = [bar["close"] for bar in weeks]
    found, debug = detect_higher_highs(highs, lows, closes, swing=SWING, recent=RECENT)
    higher = None if debug.get("reason") == "short" else int(found)
    sma_values = sma(closes, WEEKLY_SMA)
    sma_last = sma_values[-1] if sma_values else None
    if sma_last is None or not closes:
        above = None
        sma_reason = "short"
    else:
        above = int(closes[-1] > sma_last)
        sma_reason = "above" if above else "not_above"
    rsi_values = rsi(closes, WEEKLY_RSI)
    rsi_last = rsi_values[-1] if rsi_values else None
    out = {
        "higher_highs": higher,
        "above_sma": above,
        "rsi": rsi_last,
        "weeks": len(weeks),
        "sma": sma_last,
        "higher_highs_reason": debug.get("reason"),
        "above_sma_reason": sma_reason,
        "rsi_reason": "short" if rsi_last is None else "ready",
    }
    return out
