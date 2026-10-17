"""教科書の禁止3点だけを見るエリオット数え。斜行三角形とガイドラインは使わない。"""

from __future__ import annotations

from typing import Any

from skabu.indicators.base import IndicatorResult, LineSeries, Marker, ParamSpec, coerce_params, last_number
from skabu.indicators.plugins.doubletop import swing_highs
from skabu.indicators.plugins.downtrend import swing_lows
from skabu.logging_setup import get_logger

SWING = 5
LOOKBACK = 150
BULL_COLOR = "#2dd4bf"
BEAR_COLOR = "#ef5350"
ABC_COLOR = "#fbbf24"


class ElliottPlugin:
    id = "elliott"
    group = "wave"
    title = "エリオット波動"
    description = "山谷から禁止3点の上げ／下げ5波を数える。チャートと門用"
    pane = "price"
    enabled_by_default = False
    surface = ("chart", "gate")
    help = (
        "山と谷を線でつないで、1〜5を自動で数えます。上げは緑、下げは赤です。"
        "人によって数え方が分かれるので、教科書の「やってはいけない3つ」だけ見ます。"
        "\n\n読み取り: 上げの5波は 1・3・5 が山。下げの5波は 1・3・5 が谷です。"
        "2波が1波の始まりを割らない。3波が1と5の両方より短くない。4波が1波の値段に重ならない。"
        "例: 谷100→山140→谷120→山200→谷160→山240なら、禁止を守っているので緑の5です。"
        "\n\n門の上げ3波は、下げの5波の途中では通りません。"
        "下げが終わった安値から、新しい上げが始まったときだけ残します。"
        "最後の波は左右の足が揃うまで仮（3?）です。日足だけでは大きい波は見えません。"
        "点には使いません。門は自分でオンにします。"
    )
    params = [
        ParamSpec("swing", "左右本数", "int", SWING, 2, 15),
        ParamSpec("lookback", "さかのぼる本数", "int", LOOKBACK, 40, 320),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        swing = int(resolved["swing"])
        lookback = int(resolved["lookback"])
        counted = count_elliott(bars, swing=swing, lookback=lookback)
        debug = counted["debug"]
        if counted["wave3_up"] or counted["wave5_done"] or counted["correcting"] or counted["overlays"]:
            get_logger().info(
                "elliott reason=%s wave3_up=%s wave5_done=%s correcting=%s pivots=%s",
                debug.get("reason"),
                counted["wave3_up"],
                counted["wave5_done"],
                counted["correcting"],
                debug.get("pivot_n"),
            )
        else:
            get_logger().debug(
                "elliott miss reason=%s bars=%s pivots=%s",
                debug.get("reason"),
                debug.get("bars"),
                debug.get("pivot_n"),
            )
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=counted["overlays"],
            markers=counted["markers"],
            features={
                "wave3_up": counted["wave3_up"],
                "wave5_done": counted["wave5_done"],
                "correcting": counted["correcting"],
            },
            debug=debug,
        )


def count_elliott(
    bars: list[dict[str, Any]],
    *,
    swing: int = SWING,
    lookback: int = LOOKBACK,
) -> dict[str, Any]:
    closes = [bar.get("close") for bar in bars]
    highs = [bar.get("high") for bar in bars]
    lows = [bar.get("low") for bar in bars]
    times = [str(bar.get("date") or bar.get("time") or "") for bar in bars]
    last = last_number(closes)
    last_i = len(closes) - 1
    need = swing * 2 + 3
    debug: dict[str, Any] = {
        "reason": "no_pattern",
        "swing": swing,
        "lookback": lookback,
        "bars": len(closes),
        "need": need,
        "pivot_n": 0,
        "bull": None,
        "bear": None,
        "abc": None,
    }
    empty = {
        "wave3_up": 0,
        "wave5_done": 0,
        "correcting": 0,
        "overlays": [],
        "markers": [],
        "debug": debug,
    }
    if last is None or last_i + 1 < need:
        debug["reason"] = "short"
        return empty

    start = max(0, last_i + 1 - lookback)
    peaks = [(i, p) for i, p in swing_highs(highs, swing) if i >= start]
    troughs = [(i, p) for i, p in swing_lows(lows, swing) if i >= start]
    pivots = alternate_pivots(peaks, troughs)
    debug["pivot_n"] = len(pivots)
    debug["peaks"] = len(peaks)
    debug["troughs"] = len(troughs)
    if len(pivots) < 3:
        debug["reason"] = "few_swings"
        return empty

    bull5 = latest_bull_complete(pivots)
    bear5 = latest_bear_complete(pivots)
    bear_open = latest_bear_open(pivots, complete=bear5)
    bull3 = latest_bull_wave3(
        pivots,
        last,
        after=bull5[-1]["index"] if bull5 else -1,
        bear5=bear5,
        bear_open=bear_open,
    )
    abc = None
    if bull5:
        abc = abc_after(pivots, bull5[-1], last)

    wave3_up = 0
    wave5_done = 0
    correcting = 0
    if bull3 is not None:
        wave3_up = 1
        debug["reason"] = "wave3_up"
        debug["bull"] = _pivot_debug(bull3 + [_tent_point(last_i, last, "H")])
    elif bull5 is not None:
        wave5_done = 1
        confirm_end = bull5[-1]["index"] + swing
        broke = last_i > confirm_end and last < bull5[-1]["price"]
        if abc is not None or broke:
            correcting = 1
            debug["reason"] = "correcting"
        else:
            debug["reason"] = "wave5_done"
        debug["bull"] = _pivot_debug(bull5)
        if abc:
            debug["abc"] = _pivot_debug(abc)
    else:
        debug["reason"] = "no_bull"

    if bear5:
        debug["bear"] = _pivot_debug(bear5)
    if bear_open:
        debug["bear_open"] = _pivot_debug(bear_open)

    overlays: list[LineSeries] = []
    markers: list[Marker] = []
    if bear5:
        overlays.append(_line("elliott_bear", "下降波", BEAR_COLOR, times, bear5))
        markers.extend(_impulse_markers(times, bear5, up=False, tentative=None))
    if wave3_up and bull3:
        tent = _tent_point(last_i, last, "H")
        overlays.append(_line("elliott_bull", "上昇波", BULL_COLOR, times, bull3 + [tent]))
        markers.extend(_impulse_markers(times, bull3, up=True, tentative="3"))
        if tent["index"] < len(times) and times[tent["index"]]:
            markers.append(Marker(times[tent["index"]], "aboveBar", "circle", BULL_COLOR, "3?"))
    elif bull5:
        overlays.append(_line("elliott_bull", "上昇波", BULL_COLOR, times, bull5))
        markers.extend(_impulse_markers(times, bull5, up=True, tentative=None))
        if abc:
            overlays.append(_line("elliott_abc", "修正波", ABC_COLOR, times, abc, style="dashed"))
            markers.extend(_abc_markers(times, abc))
    return {
        "wave3_up": wave3_up,
        "wave5_done": wave5_done,
        "correcting": correcting,
        "overlays": overlays,
        "markers": markers,
        "debug": debug,
    }


def alternate_pivots(
    peaks: list[tuple[int, float]],
    troughs: list[tuple[int, float]],
) -> list[dict[str, Any]]:
    raw = [{"index": i, "price": float(p), "kind": "H"} for i, p in peaks]
    raw.extend({"index": i, "price": float(p), "kind": "L"} for i, p in troughs)
    raw.sort(key=lambda item: (item["index"], item["kind"]))
    out: list[dict[str, Any]] = []
    for item in raw:
        if out and out[-1]["kind"] == item["kind"]:
            if item["kind"] == "H" and item["price"] >= out[-1]["price"]:
                out[-1] = item
            elif item["kind"] == "L" and item["price"] <= out[-1]["price"]:
                out[-1] = item
        else:
            out.append(item)
    return out


def latest_bull_complete(pivots: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    best = None
    for index in range(len(pivots) - 5):
        window = pivots[index : index + 6]
        ok, reason = bull_impulse_ok(window)
        if ok:
            best = window
        elif reason != "kind":
            get_logger().debug("elliott bull5 skip i=%s reason=%s", index, reason)
    return best


def latest_bull_wave3(
    pivots: list[dict[str, Any]],
    close: float,
    *,
    after: int,
    bear5: list[dict[str, Any]] | None = None,
    bear_open: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    best = None
    for index in range(len(pivots) - 2):
        window = pivots[index : index + 3]
        if window[2]["index"] <= after:
            continue
        later_high = any(item["kind"] == "H" and item["index"] > window[2]["index"] for item in pivots[index + 3 :])
        if later_high:
            continue
        blocked, block_reason = wave3_blocked_by_bear(window, bear5, bear_open)
        if blocked:
            get_logger().debug("elliott wave3 blocked i=%s reason=%s", index, block_reason)
            continue
        ok, reason = bull_wave3_ok(window, close)
        if ok:
            best = window
        elif reason != "kind":
            get_logger().debug("elliott wave3 skip i=%s reason=%s", index, reason)
    return best


def latest_bear_open(
    pivots: list[dict[str, Any]],
    *,
    complete: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    best = None
    done_i = complete[0]["index"] if complete else None
    for index in range(len(pivots) - 3):
        window = pivots[index : index + 4]
        if "".join(item["kind"] for item in window) != "HLHL":
            continue
        if done_i is not None and window[0]["index"] == done_i:
            continue
        h0, low1, h2, low3 = window
        if h2["price"] >= h0["price"] or low3["price"] >= low1["price"]:
            continue
        if low1["price"] >= h0["price"] or low3["price"] >= h2["price"]:
            continue
        if index + 6 <= len(pivots) and bear_impulse_ok(pivots[index : index + 6])[0]:
            continue
        best = window
    return best


def wave3_blocked_by_bear(
    window: list[dict[str, Any]],
    bear5: list[dict[str, Any]] | None,
    bear_open: list[dict[str, Any]] | None,
) -> tuple[bool, str]:
    origin = window[0]["index"]
    if bear5 and origin < bear5[-1]["index"]:
        return True, "inside_bear5"
    if bear_open and origin <= bear_open[-1]["index"]:
        if bear5 and bear_open[-1]["index"] <= bear5[-1]["index"]:
            return False, "ok"
        return True, "inside_bear_open"
    return False, "ok"


def latest_bear_complete(pivots: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    best = None
    for index in range(len(pivots) - 5):
        window = pivots[index : index + 6]
        ok, reason = bear_impulse_ok(window)
        if ok:
            best = window
        elif reason != "kind":
            get_logger().debug("elliott bear5 skip i=%s reason=%s", index, reason)
    return best


def abc_after(
    pivots: list[dict[str, Any]],
    wave5: dict[str, Any],
    close: float,
) -> list[dict[str, Any]] | None:
    after = [item for item in pivots if item["index"] > wave5["index"]]
    if len(after) < 3:
        return None
    a, b, c = after[0], after[1], after[2]
    if a["kind"] != "L" or b["kind"] != "H" or c["kind"] != "L":
        return None
    if a["price"] >= wave5["price"]:
        return None
    if b["price"] >= wave5["price"] or b["price"] <= a["price"]:
        return None
    if c["price"] >= b["price"]:
        return None
    return [wave5, a, b, c]


def bull_impulse_ok(window: list[dict[str, Any]]) -> tuple[bool, str]:
    if len(window) != 6:
        return False, "len"
    kinds = "".join(item["kind"] for item in window)
    if kinds != "LHLHLH":
        return False, "kind"
    p0, p1, p2, p3, p4, p5 = window
    if p1["price"] <= p0["price"] or p3["price"] <= p2["price"] or p5["price"] <= p4["price"]:
        return False, "not_up"
    if p2["price"] <= p0["price"]:
        return False, "wave2_break"
    if p4["price"] <= p1["price"]:
        return False, "wave4_overlap"
    if p5["price"] <= p3["price"]:
        return False, "wave5_trunc"
    len1 = p1["price"] - p0["price"]
    len3 = p3["price"] - p2["price"]
    len5 = p5["price"] - p4["price"]
    if len3 < len1 and len3 < len5:
        return False, "wave3_shortest"
    return True, "ok"


def bear_impulse_ok(window: list[dict[str, Any]]) -> tuple[bool, str]:
    if len(window) != 6:
        return False, "len"
    kinds = "".join(item["kind"] for item in window)
    if kinds != "HLHLHL":
        return False, "kind"
    p0, p1, p2, p3, p4, p5 = window
    if p1["price"] >= p0["price"] or p3["price"] >= p2["price"] or p5["price"] >= p4["price"]:
        return False, "not_down"
    if p2["price"] >= p0["price"]:
        return False, "wave2_break"
    if p4["price"] >= p1["price"]:
        return False, "wave4_overlap"
    if p5["price"] >= p3["price"]:
        return False, "wave5_trunc"
    len1 = p0["price"] - p1["price"]
    len3 = p2["price"] - p3["price"]
    len5 = p4["price"] - p5["price"]
    if len3 < len1 and len3 < len5:
        return False, "wave3_shortest"
    return True, "ok"


def bull_wave3_ok(window: list[dict[str, Any]], close: float) -> tuple[bool, str]:
    if len(window) != 3:
        return False, "len"
    kinds = "".join(item["kind"] for item in window)
    if kinds != "LHL":
        return False, "kind"
    p0, p1, p2 = window
    if p1["price"] <= p0["price"]:
        return False, "not_up"
    if p2["price"] <= p0["price"]:
        return False, "wave2_break"
    if p2["price"] >= p1["price"]:
        return False, "wave2_above1"
    if close <= p1["price"]:
        return False, "not_in_wave3"
    return True, "ok"


def _tent_point(index: int, price: float | None, kind: str) -> dict[str, Any]:
    return {"index": index, "price": float(price or 0), "kind": kind, "tentative": True}


def _pivot_debug(pivots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"i": item["index"], "p": item["price"], "k": item["kind"]} for item in pivots]


def _line(
    line_id: str,
    label: str,
    color: str,
    times: list[str],
    pivots: list[dict[str, Any]],
    *,
    style: str = "solid",
) -> LineSeries:
    points = []
    for item in pivots:
        idx = item["index"]
        if 0 <= idx < len(times) and times[idx]:
            points.append({"time": times[idx], "value": round(float(item["price"]), 6)})
    return LineSeries(line_id, label, color, points, width=2, style=style)


def _impulse_markers(
    times: list[str],
    pivots: list[dict[str, Any]],
    *,
    up: bool,
    tentative: str | None,
) -> list[Marker]:
    color = BULL_COLOR if up else BEAR_COLOR
    out: list[Marker] = []
    for wave, item in enumerate(pivots[1:], start=1):
        idx = item["index"]
        if idx < 0 or idx >= len(times) or not times[idx]:
            continue
        label = f"{wave}?" if tentative and wave == int(tentative) else str(wave)
        above = item["kind"] == "H"
        out.append(
            Marker(
                times[idx],
                "aboveBar" if above else "belowBar",
                "circle",
                color,
                label,
            )
        )
    return out


def _abc_markers(times: list[str], pivots: list[dict[str, Any]]) -> list[Marker]:
    out: list[Marker] = []
    labels = ("A", "B", "C")
    for label, item in zip(labels, pivots[1:4], strict=False):
        idx = item["index"]
        if idx < 0 or idx >= len(times) or not times[idx]:
            continue
        out.append(
            Marker(
                times[idx],
                "belowBar" if item["kind"] == "L" else "aboveBar",
                "circle",
                ABC_COLOR,
                label,
            )
        )
    return out
