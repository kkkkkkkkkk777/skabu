from __future__ import annotations

from typing import Any

from skabu.indicators.base import IndicatorResult, LineSeries, Marker, ParamSpec, coerce_params, last_number
from skabu.indicators.plugins.doubletop import (
    PATTERN_COLOR,
    RECENT,
    SWING,
    _chart_marker,
    _chart_point,
    swing_highs,
)
from skabu.logging_setup import get_logger


class DowntrendPlugin:
    id = "downtrend"
    group = "shape"
    title = "切り下げ"
    description = "高値切り下げかつ安値切り下げ。買わない門用"
    pane = "price"
    enabled_by_default = False
    surface = ("chart", "gate")
    help = (
        "高値も安値も、前より低い位置になっている形です。山も谷も下がっているので、下降の途中と見ます。"
        "当たったときだけ、直近2つの山と谷を赤の線で日付順につなぎます。"
        "\n\n読み取り: 例: 高値が1200→1100、安値が1000→900なら切り下げです。まだ買い時ではない、という見方です。"
        "山の上に LH、谷の下に LL が付きます。"
        "\n\n門: =0 にすると、その形の株を落とします。点には使いません。"
    )
    params = [
        ParamSpec("swing", "左右本数", "int", SWING, 2, 15),
        ParamSpec("recent", "新しい山または谷の期限", "int", RECENT, 5, 60),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        swing = int(resolved["swing"])
        recent = int(resolved["recent"])
        highs = [bar.get("high") for bar in bars]
        lows = [bar.get("low") for bar in bars]
        closes = [bar.get("close") for bar in bars]
        found, debug = detect_lower_lows(highs, lows, closes, swing=swing, recent=recent)
        last = last_number(closes)
        if found:
            get_logger().info(
                "downtrend hit h1=%s/%s h2=%s/%s l1=%s/%s l2=%s/%s close=%s",
                debug.get("hi1"),
                debug.get("hv1"),
                debug.get("hi2"),
                debug.get("hv2"),
                debug.get("li1"),
                debug.get("lv1"),
                debug.get("li2"),
                debug.get("lv2"),
                last,
            )
        else:
            get_logger().debug(
                "downtrend miss reason=%s close=%s bars=%s",
                debug.get("reason"),
                last,
                len(bars),
            )
        overlays, markers = ([], [])
        if found:
            overlays, markers = draw_lower_lows(bars, debug)
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=overlays,
            markers=markers,
            features={"lower_lows": int(found)},
            debug=debug,
        )


def swing_lows(lows: list[Any], swing: int) -> list[tuple[int, float]]:
    troughs: list[tuple[int, float]] = []
    if swing < 1 or len(lows) < swing * 2 + 1:
        return troughs
    last = len(lows) - swing
    for index in range(swing, last):
        low = lows[index]
        if low is None:
            continue
        value = float(low)
        window = list(lows[index - swing : index]) + list(lows[index + 1 : index + 1 + swing])
        neighbors = [float(item) for item in window if item is not None]
        if len(neighbors) < swing * 2:
            continue
        if all(value < item for item in neighbors):
            troughs.append((index, value))
    return troughs


def detect_lower_lows(
    highs: list[Any],
    lows: list[Any],
    closes: list[Any],
    *,
    swing: int = SWING,
    recent: int = RECENT,
) -> tuple[int, dict[str, Any]]:
    last = last_number(closes)
    need = swing * 2 + 3
    debug: dict[str, Any] = {
        "reason": "no_pattern",
        "swing": swing,
        "recent": recent,
        "bars": len(closes),
        "need": need,
        "hi1": None,
        "hv1": None,
        "hi2": None,
        "hv2": None,
        "li1": None,
        "lv1": None,
        "li2": None,
        "lv2": None,
    }
    if last is None or len(closes) < need:
        debug["reason"] = "short"
        return 0, debug
    peaks = swing_highs(highs, swing)
    troughs = swing_lows(lows, swing)
    debug["peaks"] = len(peaks)
    debug["troughs"] = len(troughs)
    if len(peaks) < 2 or len(troughs) < 2:
        debug["reason"] = "few_swings"
        return 0, debug
    high_left, high_right = peaks[-2], peaks[-1]
    low_left, low_right = troughs[-2], troughs[-1]
    debug["hi1"], debug["hv1"] = high_left[0], high_left[1]
    debug["hi2"], debug["hv2"] = high_right[0], high_right[1]
    debug["li1"], debug["lv1"] = low_left[0], low_left[1]
    debug["li2"], debug["lv2"] = low_right[0], low_right[1]
    last_i = len(closes) - 1
    newest = max(high_right[0], low_right[0])
    if last_i - newest > recent:
        debug["reason"] = "stale"
        return 0, debug
    if high_right[1] >= high_left[1]:
        debug["reason"] = "high_not_lower"
        return 0, debug
    if low_right[1] >= low_left[1]:
        debug["reason"] = "low_not_lower"
        return 0, debug
    if last >= high_right[1]:
        debug["reason"] = "above_high"
        return 0, debug
    debug["reason"] = "hit"
    return 1, debug


def draw_lower_lows(bars: list[dict[str, Any]], debug: dict[str, Any]) -> tuple[list[LineSeries], list[Marker]]:
    times = [str(bar.get("date") or bar.get("time") or "") for bar in bars]
    swings = [
        (debug.get("hi1"), debug.get("hv1"), "H"),
        (debug.get("li1"), debug.get("lv1"), "L"),
        (debug.get("hi2"), debug.get("hv2"), "H"),
        (debug.get("li2"), debug.get("lv2"), "L"),
    ]
    if any(index is None or price is None for index, price, _kind in swings):
        return [], []
    ordered = sorted(((int(index), float(price), kind) for index, price, kind in swings), key=lambda item: item[0])
    points = [_chart_point(times, index, price) for index, price, _kind in ordered]
    overlays = [LineSeries("downtrend_ll", "切り下げ", PATTERN_COLOR, [item for item in points if item], width=2)]
    markers: list[Marker] = []
    for index, _price, kind in ordered:
        marker = _chart_marker(
            times,
            index,
            "aboveBar" if kind == "H" else "belowBar",
            "LH" if kind == "H" else "LL",
        )
        if marker is not None:
            markers.append(marker)
    return overlays, markers
