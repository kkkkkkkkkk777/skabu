from __future__ import annotations

from typing import Any

from skabu.indicators.base import IndicatorResult, LineSeries, Marker, ParamSpec, coerce_params, last_number
from skabu.indicators.plugins.doubletop import (
    PATTERN_COLOR,
    RECENT,
    SWING,
    TOL,
    _chart_marker,
    _chart_point,
    swing_highs,
)
from skabu.logging_setup import get_logger


class HeadShouldersPlugin:
    id = "headshoulders"
    group = "shape"
    title = "ヘッドアンドショルダーズ"
    description = "左肩・頭・右肩。買わない門用。ネック割れは見ない"
    pane = "price"
    enabled_by_default = False
    surface = ("chart", "gate")
    help = (
        "山が3つ並び、真ん中の頭がいちばん高い形です。"
        "当たったときだけ、3つの山を赤の線でつなぎ、あいだの谷に破線（ネック）を引きます。"
        "\n\n読み取り: 頭のあと右肩が頭に届かないと、上値が重くて下がりやすい、とよく言われます。"
        "例: 山1200→頭1500→山1180ならこの形です。山に LS・H・RS が付きます。"
        "\n\n門: =0 にすると、その形の株を落とします。点には使いません。"
        "頭は両肩よりはっきり高いときだけ数えます。"
    )
    params = [
        ParamSpec("swing", "左右本数", "int", SWING, 2, 15),
        ParamSpec("tol", "肩の差／頭の出", "float", TOL, 0.005, 0.1, 0.005),
        ParamSpec("recent", "右肩の期限", "int", RECENT, 5, 60),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        swing = int(resolved["swing"])
        tol = float(resolved["tol"])
        recent = int(resolved["recent"])
        highs = [bar.get("high") for bar in bars]
        lows = [bar.get("low") for bar in bars]
        closes = [bar.get("close") for bar in bars]
        found, debug = detect_head_shoulders(highs, lows, closes, swing=swing, tol=tol, recent=recent)
        last = last_number(closes)
        if found:
            get_logger().info(
                "headshoulders hit ls=%s/%s h=%s/%s rs=%s/%s close=%s",
                debug.get("lsi"),
                debug.get("ls"),
                debug.get("hi"),
                debug.get("head"),
                debug.get("rsi"),
                debug.get("rs"),
                last,
            )
        else:
            get_logger().debug(
                "headshoulders miss reason=%s close=%s bars=%s",
                debug.get("reason"),
                last,
                len(bars),
            )
        overlays, markers = ([], [])
        if found:
            overlays, markers = draw_head_shoulders(bars, debug)
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=overlays,
            markers=markers,
            features={"head_shoulders": int(found)},
            debug=debug,
        )


def detect_head_shoulders(
    highs: list[Any],
    lows: list[Any],
    closes: list[Any],
    *,
    swing: int = SWING,
    tol: float = TOL,
    recent: int = RECENT,
) -> tuple[int, dict[str, Any]]:
    last = last_number(closes)
    need = swing * 2 + 5
    debug: dict[str, Any] = {
        "reason": "no_pattern",
        "swing": swing,
        "tol": tol,
        "recent": recent,
        "bars": len(closes),
        "need": need,
        "lsi": None,
        "ls": None,
        "hi": None,
        "head": None,
        "rsi": None,
        "rs": None,
        "n1i": None,
        "n1": None,
        "n2i": None,
        "n2": None,
    }
    if last is None or len(closes) < need:
        debug["reason"] = "short"
        return 0, debug
    peaks = swing_highs(highs, swing)
    debug["peaks"] = len(peaks)
    if len(peaks) < 3:
        debug["reason"] = "few_peaks"
        return 0, debug
    left, mid, right = peaks[-3], peaks[-2], peaks[-1]
    debug["lsi"], debug["ls"] = left[0], left[1]
    debug["hi"], debug["head"] = mid[0], mid[1]
    debug["rsi"], debug["rs"] = right[0], right[1]
    last_i = len(closes) - 1
    if last_i - right[0] > recent:
        debug["reason"] = "stale"
        return 0, debug
    if mid[0] - left[0] < swing + 1 or right[0] - mid[0] < swing + 1:
        debug["reason"] = "too_close"
        return 0, debug
    left_h, head, right_h = left[1], mid[1], right[1]
    taller_sh = max(left_h, right_h)
    if taller_sh <= 0 or head <= 0:
        debug["reason"] = "bad_height"
        return 0, debug
    if abs(left_h - right_h) / taller_sh > tol:
        debug["reason"] = "shoulder_spread"
        return 0, debug
    if (head - left_h) / left_h <= tol or (head - right_h) / right_h <= tol:
        debug["reason"] = "head_not_clear"
        return 0, debug
    n1_i, n1 = _extreme(lows, left[0] + 1, mid[0], want_min=True)
    n2_i, n2 = _extreme(lows, mid[0] + 1, right[0], want_min=True)
    if n1 is None or n2 is None or n1_i is None or n2_i is None:
        debug["reason"] = "no_neck"
        return 0, debug
    debug["n1i"], debug["n1"] = n1_i, n1
    debug["n2i"], debug["n2"] = n2_i, n2
    if n1 >= min(left_h, head) or n2 >= min(head, right_h):
        debug["reason"] = "no_neck"
        return 0, debug
    if last >= head:
        debug["reason"] = "broke_head"
        return 0, debug
    debug["reason"] = "hit"
    return 1, debug


def draw_head_shoulders(bars: list[dict[str, Any]], debug: dict[str, Any]) -> tuple[list[LineSeries], list[Marker]]:
    times = [str(bar.get("date") or bar.get("time") or "") for bar in bars]
    points = [
        _chart_point(times, int(debug["lsi"]), debug["ls"]),
        _chart_point(times, int(debug["hi"]), debug["head"]),
        _chart_point(times, int(debug["rsi"]), debug["rs"]),
    ]
    neck = [
        _chart_point(times, int(debug["n1i"]), debug["n1"]),
        _chart_point(times, int(debug["n2i"]), debug["n2"]),
    ]
    overlays = [
        LineSeries("headshoulders_hs", "ヘッドアンドショルダーズ", PATTERN_COLOR, [item for item in points if item], width=2),
        LineSeries("headshoulders_neck", "ネック", PATTERN_COLOR, [item for item in neck if item], width=2, style="dashed"),
    ]
    markers = [
        item
        for item in (
            _chart_marker(times, int(debug["lsi"]), "aboveBar", "LS"),
            _chart_marker(times, int(debug["hi"]), "aboveBar", "H"),
            _chart_marker(times, int(debug["rsi"]), "aboveBar", "RS"),
        )
        if item is not None
    ]
    return overlays, markers


def _extreme(
    series: list[Any],
    start: int,
    stop: int,
    *,
    want_min: bool,
) -> tuple[int | None, float | None]:
    best_i = None
    best = None
    for index in range(start, stop):
        item = series[index]
        if item is None:
            continue
        value = float(item)
        if best is None or (value < best if want_min else value > best):
            best = value
            best_i = index
    return best_i, best
