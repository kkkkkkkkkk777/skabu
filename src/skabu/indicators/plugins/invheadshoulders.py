from __future__ import annotations

from typing import Any

from skabu.indicators.base import IndicatorResult, LineSeries, Marker, ParamSpec, coerce_params, last_number
from skabu.indicators.plugins.doublebottom import BULL_COLOR
from skabu.indicators.plugins.doubletop import RECENT, SWING, TOL, _chart_marker, _chart_point
from skabu.indicators.plugins.downtrend import swing_lows
from skabu.indicators.plugins.headshoulders import _extreme
from skabu.logging_setup import get_logger


class InvHeadShouldersPlugin:
    id = "invheadshoulders"
    group = "shape"
    title = "逆ヘッドアンドショルダーズ"
    description = "左肩・頭・右肩の逆。当たったら＋1。ネック割れは見ない"
    pane = "price"
    enabled_by_default = False
    surface = ("chart", "score")
    help = (
        "谷が3つ並び、真ん中の頭がいちばん安い形です。"
        "当たったときだけ、3つの谷を緑の線でつなぎ、あいだの山に破線（ネック）を引きます。"
        "\n\n読み取り: 頭のあと右肩が頭まで下がらないと、下値が堅くて上がりやすい、とよく言われます。"
        "例: 谷800→頭600→谷820ならこの形です。谷に LS・H・RS が付きます。"
        "\n\n点: 当たったら＋1です。ダブルボトムと同時でも、パターンの点は合計で＋1までです。"
        "形が無いときは点を付けません。頭は両肩よりはっきり安いときだけ数えます。"
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
        found, debug = detect_inv_head_shoulders(highs, lows, closes, swing=swing, tol=tol, recent=recent)
        last = last_number(closes)
        if found:
            get_logger().info(
                "invheadshoulders hit ls=%s/%s h=%s/%s rs=%s/%s close=%s score=1",
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
                "invheadshoulders miss reason=%s close=%s bars=%s",
                debug.get("reason"),
                last,
                len(bars),
            )
        overlays, markers = ([], [])
        if found:
            overlays, markers = draw_inv_head_shoulders(bars, debug)
        features: dict[str, Any] = {"inv_head_shoulders": int(found)}
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


def detect_inv_head_shoulders(
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
    troughs = swing_lows(lows, swing)
    debug["troughs"] = len(troughs)
    if len(troughs) < 3:
        debug["reason"] = "few_troughs"
        return 0, debug
    left, mid, right = troughs[-3], troughs[-2], troughs[-1]
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
    left_l, head, right_l = left[1], mid[1], right[1]
    shallower = max(left_l, right_l)
    if shallower <= 0 or head <= 0:
        debug["reason"] = "bad_depth"
        return 0, debug
    if abs(left_l - right_l) / shallower > tol:
        debug["reason"] = "shoulder_spread"
        return 0, debug
    if (left_l - head) / left_l <= tol or (right_l - head) / right_l <= tol:
        debug["reason"] = "head_not_clear"
        return 0, debug
    n1_i, n1 = _extreme(highs, left[0] + 1, mid[0], want_min=False)
    n2_i, n2 = _extreme(highs, mid[0] + 1, right[0], want_min=False)
    if n1 is None or n2 is None or n1_i is None or n2_i is None:
        debug["reason"] = "no_neck"
        return 0, debug
    debug["n1i"], debug["n1"] = n1_i, n1
    debug["n2i"], debug["n2"] = n2_i, n2
    if n1 <= max(left_l, head) or n2 <= max(head, right_l):
        debug["reason"] = "no_neck"
        return 0, debug
    if last <= head:
        debug["reason"] = "broke_head"
        return 0, debug
    debug["reason"] = "hit"
    return 1, debug


def draw_inv_head_shoulders(bars: list[dict[str, Any]], debug: dict[str, Any]) -> tuple[list[LineSeries], list[Marker]]:
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
        LineSeries("invheadshoulders_hs", "逆ヘッドアンドショルダーズ", BULL_COLOR, [item for item in points if item], width=2),
        LineSeries("invheadshoulders_neck", "ネック", BULL_COLOR, [item for item in neck if item], width=2, style="dashed"),
    ]
    markers = [
        item
        for item in (
            _chart_marker(times, int(debug["lsi"]), "belowBar", "LS", BULL_COLOR),
            _chart_marker(times, int(debug["hi"]), "belowBar", "H", BULL_COLOR),
            _chart_marker(times, int(debug["rsi"]), "belowBar", "RS", BULL_COLOR),
        )
        if item is not None
    ]
    return overlays, markers
