from __future__ import annotations

from typing import Any

from skabu.indicators.base import IndicatorResult, LineSeries, Marker, ParamSpec, coerce_params, last_number
from skabu.indicators.plugins.doubletop import RECENT, SWING, TOL, _chart_marker, _chart_point
from skabu.indicators.plugins.downtrend import swing_lows
from skabu.logging_setup import get_logger

BULL_COLOR = "#2dd4bf"


class DoubleBottomPlugin:
    id = "doublebottom"
    group = "shape"
    title = "ダブルボトム"
    description = "直近の双子谷。当たったら＋1。ネック割れは見ない"
    pane = "price"
    enabled_by_default = False
    surface = ("chart", "score")
    help = (
        "安い谷が2つ並ぶ形です。一度安値を付けて戻り、もう一度ほぼ同じ深さまで下げて、また戻る形です。"
        "当たったときだけ、谷と山を緑の線でつなぎ、山の高さに破線（ネック）を引きます。"
        "\n\n読み取り: 同じ深さで2回支えられると、下値が堅くて上がりやすい、とよく言われます。"
        "例: 800円で2回止まって戻している株は、この形です。谷の下に DB が付きます。"
        "\n\n点: 当たったら＋1です。形が無いときは点を付けません。門には使いません。"
    )
    params = [
        ParamSpec("swing", "左右本数", "int", SWING, 2, 15),
        ParamSpec("tol", "谷の差", "float", TOL, 0.005, 0.1, 0.005),
        ParamSpec("recent", "2谷目の期限", "int", RECENT, 5, 60),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        swing = int(resolved["swing"])
        tol = float(resolved["tol"])
        recent = int(resolved["recent"])
        highs = [bar.get("high") for bar in bars]
        lows = [bar.get("low") for bar in bars]
        closes = [bar.get("close") for bar in bars]
        found, debug = detect_double_bottom(highs, lows, closes, swing=swing, tol=tol, recent=recent)
        last = last_number(closes)
        if found:
            get_logger().info(
                "doublebottom hit i1=%s l1=%s i2=%s l2=%s close=%s score=1",
                debug.get("i1"),
                debug.get("l1"),
                debug.get("i2"),
                debug.get("l2"),
                last,
            )
        else:
            get_logger().debug(
                "doublebottom miss reason=%s i1=%s i2=%s close=%s bars=%s",
                debug.get("reason"),
                debug.get("i1"),
                debug.get("i2"),
                last,
                len(bars),
            )
        overlays, markers = ([], [])
        if found:
            overlays, markers = draw_double_bottom(bars, debug)
        features: dict[str, Any] = {"double_bottom": int(found)}
        if found:
            features["score"] = 1
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=overlays,
            markers=markers,
            features=features,
            debug=debug,
        )


def detect_double_bottom(
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
        "l1": None,
        "i2": None,
        "l2": None,
        "pi": None,
        "peak": None,
    }
    if last is None or len(closes) < need:
        debug["reason"] = "short"
        return 0, debug
    troughs = swing_lows(lows, swing)
    debug["troughs"] = len(troughs)
    if len(troughs) < 2:
        debug["reason"] = "few_troughs"
        return 0, debug
    left, right = troughs[-2], troughs[-1]
    debug["i1"], debug["l1"] = left[0], left[1]
    debug["i2"], debug["l2"] = right[0], right[1]
    last_i = len(closes) - 1
    if last_i - right[0] > recent:
        debug["reason"] = "stale"
        return 0, debug
    if right[0] - left[0] < swing + 1:
        debug["reason"] = "too_close"
        return 0, debug
    low_a, low_b = left[1], right[1]
    shallower = max(low_a, low_b)
    if shallower <= 0:
        debug["reason"] = "bad_depth"
        return 0, debug
    if abs(low_a - low_b) / shallower > tol:
        debug["reason"] = "spread"
        return 0, debug
    peak_i = None
    peak = None
    for index in range(left[0] + 1, right[0]):
        item = highs[index]
        if item is None:
            continue
        value = float(item)
        if peak is None or value > peak:
            peak = value
            peak_i = index
    if peak is None or peak_i is None:
        debug["reason"] = "no_peak"
        return 0, debug
    debug["pi"] = peak_i
    debug["peak"] = peak
    if peak <= max(low_a, low_b):
        debug["reason"] = "no_peak"
        return 0, debug
    if last <= low_a or last <= low_b:
        debug["reason"] = "broke_low"
        return 0, debug
    debug["reason"] = "hit"
    return 1, debug


def draw_double_bottom(bars: list[dict[str, Any]], debug: dict[str, Any]) -> tuple[list[LineSeries], list[Marker]]:
    times = [str(bar.get("date") or bar.get("time") or "") for bar in bars]
    i1, l1 = debug.get("i1"), debug.get("l1")
    i2, l2 = debug.get("i2"), debug.get("l2")
    pi, peak = debug.get("pi"), debug.get("peak")
    if None in {i1, l1, i2, l2, pi, peak}:
        return [], []
    w_points = [_chart_point(times, int(i1), l1), _chart_point(times, int(pi), peak), _chart_point(times, int(i2), l2)]
    neck_points = [_chart_point(times, int(i1), peak), _chart_point(times, int(i2), peak)]
    overlays = [
        LineSeries("doublebottom_w", "ダブルボトム", BULL_COLOR, [item for item in w_points if item], width=2),
        LineSeries(
            "doublebottom_neck",
            "ネック",
            BULL_COLOR,
            [item for item in neck_points if item],
            width=2,
            style="dashed",
        ),
    ]
    markers = [
        item
        for item in (
            _chart_marker(times, int(i1), "belowBar", "DB", BULL_COLOR),
            _chart_marker(times, int(i2), "belowBar", "DB", BULL_COLOR),
        )
        if item is not None
    ]
    return overlays, markers
