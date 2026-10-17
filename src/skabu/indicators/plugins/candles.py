from __future__ import annotations

from typing import Any

from skabu.indicators.base import IndicatorResult, Marker, ParamSpec, coerce_params, last_number
from skabu.indicators.plugins.doublebottom import BULL_COLOR
from skabu.indicators.plugins.doubletop import PATTERN_COLOR
from skabu.logging_setup import get_logger

BULL = BULL_COLOR
BEAR = PATTERN_COLOR


class CandlesPlugin:
    id = "candles"
    group = "shape"
    title = "ローソク足"
    description = "直近1〜3本の形。陰は門、陽の2〜3本は＋1"
    pane = "price"
    enabled_by_default = False
    surface = ("chart", "score", "gate")
    help = (
        "直近の足の形を見ます。1本足は印だけです。2〜3本足のうち、上げは点、下げは門です。"
        "\n\n読み取り: 陽の包みや明けの明星は、売りのあと買いが勝った形です。"
        "陰の包みや宵の明星は、買いのあと売りが勝った形です。"
        "ハンマーは下ヒゲが長い1本、流れ星は上ヒゲが長い1本です。"
        "\n\n点: 陽の包みまたは明けの明星なら＋1。ダブルボトムなどと同時でもパターンの点は＋1までです。"
        "門: 陰の包みまたは宵の明星なら =0 で落とします。"
    )
    params: list[ParamSpec] = []

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        coerce_params(self.params, params)
        found = detect_candles(bars)
        debug = found["debug"]
        if found["bullish"] or found["bearish"] or found["hammer"] or found["shooting"]:
            get_logger().info(
                "candles bullish=%s bearish=%s star=%s engulf=%s hammer=%s shooting=%s close=%s",
                found["bullish"],
                found["bearish"],
                debug.get("star"),
                debug.get("engulf"),
                found["hammer"],
                found["shooting"],
                last_number([bar.get("close") for bar in bars]),
            )
        else:
            get_logger().debug("candles miss bars=%s reason=%s", len(bars), debug.get("reason"))
        features: dict[str, Any] = {
            "morning_star": found["morning_star"],
            "evening_star": found["evening_star"],
            "engulf_up": found["engulf_up"],
            "engulf_down": found["engulf_down"],
            "hammer": found["hammer"],
            "shooting": found["shooting"],
            "bullish": found["bullish"],
            "bearish": found["bearish"],
        }
        if found["bullish"]:
            features["score"] = 1
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[],
            markers=found["markers"],
            features=features,
            debug=debug,
        )


def detect_candles(bars: list[dict[str, Any]]) -> dict[str, Any]:
    empty = {
        "morning_star": 0,
        "evening_star": 0,
        "engulf_up": 0,
        "engulf_down": 0,
        "hammer": 0,
        "shooting": 0,
        "bullish": 0,
        "bearish": 0,
        "markers": [],
        "debug": {"reason": "short", "bars": len(bars)},
    }
    if len(bars) < 2:
        return empty
    times = [str(bar.get("date") or bar.get("time") or "") for bar in bars]
    ohlc = [_ohlc(bar) for bar in bars]
    last = ohlc[-1]
    prev = ohlc[-2]
    if last is None or prev is None:
        empty["debug"]["reason"] = "bad_ohlc"
        return empty
    third = ohlc[-3] if len(ohlc) >= 3 else None
    debug: dict[str, Any] = {"reason": "no_pattern", "bars": len(bars), "star": None, "engulf": None}
    morning = 1 if third is not None and _morning_star(third, prev, last) else 0
    evening = 1 if third is not None and _evening_star(third, prev, last) else 0
    up = 1 if _engulf_up(prev, last) else 0
    down = 1 if _engulf_down(prev, last) else 0
    hammer = 1 if _hammer(last) else 0
    shooting = 1 if _shooting(last) else 0
    if morning:
        up = 0
        debug["star"] = "morning"
        debug["reason"] = "morning_star"
    elif evening:
        down = 0
        debug["star"] = "evening"
        debug["reason"] = "evening_star"
    elif up:
        debug["engulf"] = "up"
        debug["reason"] = "engulf_up"
    elif down:
        debug["engulf"] = "down"
        debug["reason"] = "engulf_down"
    elif hammer or shooting:
        debug["reason"] = "hammer" if hammer else "shooting"
    bullish = 1 if morning or up else 0
    bearish = 1 if evening or down else 0
    markers: list[Marker] = []
    time = times[-1] if times and times[-1] else ""
    if time:
        if morning:
            markers.append(Marker(time, "belowBar", "circle", BULL, "明星"))
        elif evening:
            markers.append(Marker(time, "aboveBar", "circle", BEAR, "宵"))
        elif up:
            markers.append(Marker(time, "belowBar", "circle", BULL, "包"))
        elif down:
            markers.append(Marker(time, "aboveBar", "circle", BEAR, "包"))
        elif hammer:
            markers.append(Marker(time, "belowBar", "circle", BULL, "ハ"))
        elif shooting:
            markers.append(Marker(time, "aboveBar", "circle", BEAR, "流"))
    return {
        "morning_star": morning,
        "evening_star": evening,
        "engulf_up": up,
        "engulf_down": down,
        "hammer": hammer,
        "shooting": shooting,
        "bullish": bullish,
        "bearish": bearish,
        "markers": markers,
        "debug": debug,
    }


def _ohlc(bar: dict[str, Any]) -> tuple[float, float, float, float] | None:
    try:
        open_px = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if high < low:
        return None
    return open_px, high, low, close


def _body(open_px: float, close: float) -> float:
    return abs(close - open_px)


def _range(high: float, low: float) -> float:
    return high - low


def _hammer(bar: tuple[float, float, float, float]) -> bool:
    open_px, high, low, close = bar
    span = _range(high, low)
    body = _body(open_px, close)
    if span <= 0 or body <= 0:
        return False
    lower = min(open_px, close) - low
    upper = high - max(open_px, close)
    return lower >= 2 * body and upper <= body and body <= 0.4 * span


def _shooting(bar: tuple[float, float, float, float]) -> bool:
    open_px, high, low, close = bar
    span = _range(high, low)
    body = _body(open_px, close)
    if span <= 0 or body <= 0:
        return False
    lower = min(open_px, close) - low
    upper = high - max(open_px, close)
    return upper >= 2 * body and lower <= body and body <= 0.4 * span


def _engulf_up(prev: tuple[float, float, float, float], curr: tuple[float, float, float, float]) -> bool:
    po, _ph, _pl, pc = prev
    o, _h, _l, c = curr
    if pc >= po or c <= o:
        return False
    return o <= pc and c >= po


def _engulf_down(prev: tuple[float, float, float, float], curr: tuple[float, float, float, float]) -> bool:
    po, _ph, _pl, pc = prev
    o, _h, _l, c = curr
    if pc <= po or c >= o:
        return False
    return o >= pc and c <= po


def _morning_star(
    first: tuple[float, float, float, float],
    mid: tuple[float, float, float, float],
    last: tuple[float, float, float, float],
) -> bool:
    fo, fh, fl, fc = first
    mo, mh, ml, mc = mid
    lo, lh, ll, lc = last
    if fc >= fo or lc <= lo:
        return False
    first_body = _body(fo, fc)
    mid_body = _body(mo, mc)
    last_body = _body(lo, lc)
    if first_body <= 0 or last_body <= 0:
        return False
    if first_body < 0.3 * _range(fh, fl):
        return False
    if _range(mh, ml) > 0 and mid_body > 0.35 * _range(mh, ml):
        return False
    return lc > (fo + fc) / 2


def _evening_star(
    first: tuple[float, float, float, float],
    mid: tuple[float, float, float, float],
    last: tuple[float, float, float, float],
) -> bool:
    fo, fh, fl, fc = first
    mo, mh, ml, mc = mid
    lo, lh, ll, lc = last
    if fc <= fo or lc >= lo:
        return False
    first_body = _body(fo, fc)
    mid_body = _body(mo, mc)
    last_body = _body(lo, lc)
    if first_body <= 0 or last_body <= 0:
        return False
    if first_body < 0.3 * _range(fh, fl):
        return False
    if _range(mh, ml) > 0 and mid_body > 0.35 * _range(mh, ml):
        return False
    return lc < (fo + fc) / 2
