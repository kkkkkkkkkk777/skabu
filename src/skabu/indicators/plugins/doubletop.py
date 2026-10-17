from __future__ import annotations

from typing import Any

from skabu.indicators.base import IndicatorResult, LineSeries, Marker, ParamSpec, coerce_params, last_number
from skabu.logging_setup import get_logger

SWING = 5
TOL = 0.03
RECENT = 25
LOOKBACK = 80
PATTERN_COLOR = "#ef5350"


class DoubleTopPlugin:
    id = "doubletop"
    group = "shape"
    title = "ダブルトップ"
    description = "直近の双子山。買わない門用。ネック割れは見ない"
    pane = "price"
    enabled_by_default = False
    surface = ("chart", "gate")
    help = (
        "高い山が2つ並ぶ形です。一度高値を付けて下がり、もう一度ほぼ同じ高さまで戻って、また下がる形です。"
        "当たったときだけ、山と谷を赤の線でつなぎ、谷の高さに破線（ネック）を引きます。"
        "\n\n読み取り: 同じ高さで2回頭を打つと、上値が重くて下がりやすい、とよく言われます。"
        "例: 1500円で2回はね返されて下がっている株は、この形です。山の上に DT が付きます。"
        "\n\n門: =0 にすると、その形の株を落とします。点には使いません。"
    )
    params = [
        ParamSpec("swing", "左右本数", "int", SWING, 2, 15),
        ParamSpec("tol", "山の差", "float", TOL, 0.005, 0.1, 0.005),
        ParamSpec("recent", "2山目の期限", "int", RECENT, 5, 60),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        swing = int(resolved["swing"])
        tol = float(resolved["tol"])
        recent = int(resolved["recent"])
        highs = [bar.get("high") for bar in bars]
        lows = [bar.get("low") for bar in bars]
        closes = [bar.get("close") for bar in bars]
        found, debug = detect_double_top(highs, lows, closes, swing=swing, tol=tol, recent=recent)
        last = last_number(closes)
        if found:
            get_logger().info(
                "doubletop hit i1=%s h1=%s i2=%s h2=%s close=%s",
                debug.get("i1"),
                debug.get("h1"),
                debug.get("i2"),
                debug.get("h2"),
                last,
            )
        else:
            get_logger().debug(
                "doubletop miss reason=%s i1=%s i2=%s close=%s bars=%s",
                debug.get("reason"),
                debug.get("i1"),
                debug.get("i2"),
                last,
                len(bars),
            )
        overlays, markers = ([], [])
        if found:
            overlays, markers = draw_double_top(bars, debug)
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=overlays,
            markers=markers,
            features={"double_top": int(found)},
            debug=debug,
        )


def swing_highs(highs: list[Any], swing: int) -> list[tuple[int, float]]:
    peaks: list[tuple[int, float]] = []
    if swing < 1 or len(highs) < swing * 2 + 1:
        return peaks
    last = len(highs) - swing
    for index in range(swing, last):
        high = highs[index]
        if high is None:
            continue
        value = float(high)
        window = list(highs[index - swing : index]) + list(highs[index + 1 : index + 1 + swing])
        neighbors = [float(item) for item in window if item is not None]
        if len(neighbors) < swing * 2:
            continue
        if all(value > item for item in neighbors):
            peaks.append((index, value))
    return peaks


def detect_double_top(
    highs: list[Any],
    lows: list[Any],
    closes: list[Any],
    *,
    swing: int = SWING,
    tol: float = TOL,
    recent: int = RECENT,
) -> tuple[int, dict[str, Any]]:
    last = last_number(closes)
    need = swing * 2 + 3
    debug: dict[str, Any] = {
        "reason": "no_pattern",
        "swing": swing,
        "tol": tol,
        "recent": recent,
        "bars": len(closes),
        "need": need,
        "i1": None,
        "h1": None,
        "i2": None,
        "h2": None,
        "vi": None,
        "valley": None,
    }
    if last is None or len(closes) < need:
        debug["reason"] = "short"
        return 0, debug
    peaks = swing_highs(highs, swing)
    debug["peaks"] = len(peaks)
    if len(peaks) < 2:
        debug["reason"] = "few_peaks"
        return 0, debug
    left, right = peaks[-2], peaks[-1]
    debug["i1"], debug["h1"] = left[0], left[1]
    debug["i2"], debug["h2"] = right[0], right[1]
    last_i = len(closes) - 1
    if last_i - right[0] > recent:
        debug["reason"] = "stale"
        return 0, debug
    if right[0] - left[0] < swing + 1:
        debug["reason"] = "too_close"
        return 0, debug
    high_a, high_b = left[1], right[1]
    taller = max(high_a, high_b)
    if taller <= 0:
        debug["reason"] = "bad_height"
        return 0, debug
    if abs(high_a - high_b) / taller > tol:
        debug["reason"] = "spread"
        return 0, debug
    valley_i = None
    valley = None
    for index in range(left[0] + 1, right[0]):
        item = lows[index]
        if item is None:
            continue
        value = float(item)
        if valley is None or value < valley:
            valley = value
            valley_i = index
    if valley is None or valley_i is None:
        debug["reason"] = "no_valley"
        return 0, debug
    debug["vi"] = valley_i
    debug["valley"] = valley
    if valley >= min(high_a, high_b):
        debug["reason"] = "no_valley"
        return 0, debug
    if last >= high_a or last >= high_b:
        debug["reason"] = "broke_high"
        return 0, debug
    debug["reason"] = "hit"
    return 1, debug


def draw_double_top(bars: list[dict[str, Any]], debug: dict[str, Any]) -> tuple[list[LineSeries], list[Marker]]:
    times = [str(bar.get("date") or bar.get("time") or "") for bar in bars]
    i1, h1 = debug.get("i1"), debug.get("h1")
    i2, h2 = debug.get("i2"), debug.get("h2")
    vi, valley = debug.get("vi"), debug.get("valley")
    if None in {i1, h1, i2, h2, vi, valley}:
        return [], []
    m_points = [_chart_point(times, int(i1), h1), _chart_point(times, int(vi), valley), _chart_point(times, int(i2), h2)]
    neck_points = [_chart_point(times, int(i1), valley), _chart_point(times, int(i2), valley)]
    overlays = [
        LineSeries("doubletop_m", "ダブルトップ", PATTERN_COLOR, [item for item in m_points if item], width=2),
        LineSeries(
            "doubletop_neck",
            "ネック",
            PATTERN_COLOR,
            [item for item in neck_points if item],
            width=2,
            style="dashed",
        ),
    ]
    markers = [
        item
        for item in (
            _chart_marker(times, int(i1), "aboveBar", "DT"),
            _chart_marker(times, int(i2), "aboveBar", "DT"),
        )
        if item is not None
    ]
    return overlays, markers


def _chart_point(times: list[str], index: int, price: Any) -> dict[str, Any] | None:
    if index < 0 or index >= len(times) or not times[index] or price is None:
        return None
    return {"time": times[index], "value": round(float(price), 6)}


def _chart_marker(
    times: list[str],
    index: int,
    position: str,
    text: str,
    color: str = PATTERN_COLOR,
) -> Marker | None:
    if index < 0 or index >= len(times) or not times[index]:
        return None
    return Marker(times[index], position, "circle", color, text)
