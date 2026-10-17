"""ATR から損切・目標を出し、上限日内の先着で手仕舞う。未来足は見ない。"""

from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from skabu.errors import SkabuError
from skabu.indicators.mathutil import atr
from skabu.logging_setup import dump_debug_event, get_logger

ATR_LEN = 14
STOP_MULT = 1.5
TARGET_MULT = 3.0
EXIT_SPEC = "first_touch_atr"
LABEL_SPEC = "target_first"
ANCHOR_SPEC = "entry"
DEFAULT_HORIZON = 20
LEVELS_NOTE = "画面の損切・目標は予測日終値の仮数字。執行は翌始値 −/+ ATR で確定する"

LEVELS_SPEC: dict[str, Any] = {
    "atr_len": ATR_LEN,
    "stop_mult": STOP_MULT,
    "target_mult": TARGET_MULT,
    "exit": EXIT_SPEC,
    "anchor": ANCHOR_SPEC,
    "label": LABEL_SPEC,
}
EXIT_LEVELS_NAME = "exit_levels.json"
EXIT_MULT_MIN = 0.1
EXIT_MULT_MAX = 10.0


def levels_spec(stop_mult: float = STOP_MULT, target_mult: float = TARGET_MULT) -> dict[str, Any]:
    spec = dict(LEVELS_SPEC)
    spec["stop_mult"] = float(stop_mult)
    spec["target_mult"] = float(target_mult)
    return spec


def round_exit_mult(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        get_logger().warning("exit mult invalid name=%s value=%s", name, value)
        raise SkabuError(f"{name}は 0.1 から 10 の数字にしてください") from exc
    if number != number or number in (float("inf"), float("-inf")):
        get_logger().warning("exit mult not finite name=%s value=%s", name, value)
        raise SkabuError(f"{name}は 0.1 から 10 の数字にしてください")
    try:
        rounded = float(Decimal(str(number)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    except Exception as exc:
        get_logger().warning("exit mult round failed name=%s value=%s error=%s", name, value, exc)
        raise SkabuError(f"{name}は 0.1 から 10 の数字にしてください") from exc
    if rounded < EXIT_MULT_MIN or rounded > EXIT_MULT_MAX:
        get_logger().warning("exit mult out of range name=%s value=%s rounded=%s", name, value, rounded)
        raise SkabuError(f"{name}は 0.1 から 10 の数字にしてください")
    return rounded


def default_exit_levels() -> dict[str, Any]:
    return {
        "stop_mult": STOP_MULT,
        "target_mult": TARGET_MULT,
        "rr": round(TARGET_MULT / STOP_MULT, 4),
        "saved": False,
        "path": None,
    }


def load_exit_levels(settings: Any) -> dict[str, Any]:
    path = settings.skabu_data_dir / EXIT_LEVELS_NAME
    payload = default_exit_levels()
    payload["path"] = str(path)
    if not path.exists():
        return payload
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        get_logger().exception("exit levels read failed path=%s", path)
        dump_debug_event(settings.debug_dir, {"kind": "exit_levels_read", "path": str(path), "error": str(exc)})
        return payload
    if not isinstance(raw, dict):
        get_logger().warning("exit levels not object path=%s", path)
        return payload
    try:
        stop_mult = round_exit_mult(raw.get("stop_mult", STOP_MULT), name="損切の倍率")
        target_mult = round_exit_mult(raw.get("target_mult", TARGET_MULT), name="目標の倍率")
    except SkabuError as exc:
        get_logger().warning("exit levels ignored path=%s error=%s", path, exc)
        return payload
    payload["stop_mult"] = stop_mult
    payload["target_mult"] = target_mult
    payload["rr"] = round(target_mult / stop_mult, 4)
    payload["saved"] = True
    return payload


def save_exit_levels(settings: Any, stop_mult: Any, target_mult: Any) -> dict[str, Any]:
    stop = round_exit_mult(stop_mult, name="損切の倍率")
    target = round_exit_mult(target_mult, name="目標の倍率")
    settings.ensure_dirs()
    path = settings.skabu_data_dir / EXIT_LEVELS_NAME
    body = {"stop_mult": stop, "target_mult": target}
    try:
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        dump_debug_event(settings.debug_dir, {"kind": "exit_levels_write", "path": str(path), "error": str(exc)})
        raise SkabuError(f"exit_levels.json の書き込みに失敗しました: {exc}") from exc
    get_logger().info("exit levels saved stop=%s target=%s path=%s", stop, target, path)
    return load_exit_levels(settings)


def exit_pair(settings: Any) -> tuple[float, float]:
    loaded = load_exit_levels(settings)
    return float(loaded["stop_mult"]), float(loaded["target_mult"])


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _empty_levels() -> dict[str, Any]:
    return {
        "atr": None,
        "stop": None,
        "target": None,
        "rr": None,
        "entry_ref": None,
        "provisional": True,
    }


def last_atr(bars: list[dict[str, Any]]) -> float | None:
    if not bars:
        return None
    highs = [bar.get("high") for bar in bars]
    lows = [bar.get("low") for bar in bars]
    closes = [bar.get("close") for bar in bars]
    widths = atr(highs, lows, closes, ATR_LEN)
    for width in reversed(widths):
        if width is not None and float(width) > 0:
            return float(width)
    return None


def last_close(bars: list[dict[str, Any]]) -> float | None:
    for bar in reversed(bars):
        close = bar.get("close")
        if close is not None:
            return float(close)
    return None


def levels_from_anchor(
    anchor: float | None,
    atr_value: float | None,
    *,
    provisional: bool = False,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> dict[str, Any]:
    empty = _empty_levels()
    empty["atr"] = _round(atr_value)
    empty["entry_ref"] = _round(anchor)
    empty["provisional"] = provisional
    if anchor is None or atr_value is None or atr_value <= 0:
        return empty
    stop = float(anchor) - float(stop_mult) * float(atr_value)
    target = float(anchor) + float(target_mult) * float(atr_value)
    risk = float(anchor) - stop
    reward = target - float(anchor)
    rr = None if risk <= 0 else reward / risk
    return {
        "atr": _round(atr_value),
        "stop": _round(stop),
        "target": _round(target),
        "rr": None if rr is None else round(rr, 4),
        "entry_ref": _round(anchor),
        "provisional": provisional,
    }


def compute_levels(
    bars: list[dict[str, Any]],
    anchor: float | None = None,
    *,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> dict[str, Any]:
    if not bars:
        return _empty_levels()
    atr_value = last_atr(bars)
    close = last_close(bars)
    if anchor is None:
        return levels_from_anchor(close, atr_value, provisional=True, stop_mult=stop_mult, target_mult=target_mult)
    return levels_from_anchor(float(anchor), atr_value, provisional=False, stop_mult=stop_mult, target_mult=target_mult)


def bars_from_prices(
    series: dict[str, dict[str, float | None]],
    dates: list[str],
    as_of: str,
) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for day in dates:
        if day > as_of:
            break
        px = series.get(day)
        if not px:
            continue
        bars.append(
            {
                "date": day,
                "open": px.get("open"),
                "high": px.get("high"),
                "low": px.get("low"),
                "close": px.get("close"),
            }
        )
    return bars


def path_from_prices(
    series: dict[str, dict[str, float | None]],
    dates: list[str],
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    path: list[dict[str, Any]] = []
    for day in dates:
        if day < start_date or day > end_date:
            continue
        px = series.get(day) or {}
        path.append(
            {
                "date": day,
                "open": px.get("open"),
                "high": px.get("high"),
                "low": px.get("low"),
                "close": px.get("close"),
            }
        )
    return path


def resolve_levels(
    row: dict[str, Any],
    series: dict[str, dict[str, float | None]],
    dates: list[str],
    as_of: str,
    entry: float | None = None,
    *,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> dict[str, Any]:
    hist = bars_from_prices(series, dates, as_of)
    computed = compute_levels(hist, anchor=entry, stop_mult=stop_mult, target_mult=target_mult)
    if computed.get("atr") is None and row.get("atr") is not None:
        computed = levels_from_anchor(
            entry if entry is not None else computed.get("entry_ref"),
            row.get("atr"),
            provisional=entry is None,
            stop_mult=stop_mult,
            target_mult=target_mult,
        )
    if computed.get("stop") is None:
        get_logger().warning("levels missing as_of=%s code=%s", as_of, row.get("code"))
    return computed


def simulate_exit(
    entry: float | None,
    stop: float | None,
    target: float | None,
    path: list[dict[str, Any]],
    *,
    take_profit: bool = True,
) -> dict[str, Any]:
    if not path:
        return {"exit": None, "exit_date": None, "reason": None}
    last = path[-1]
    if entry is None:
        return {"exit": last.get("close"), "exit_date": last.get("date"), "reason": "time"}
    if stop is None or (take_profit and target is None):
        get_logger().debug(
            "simulate_exit skip levels entry=%s stop=%s target=%s take_profit=%s",
            entry,
            stop,
            target,
            take_profit,
        )
        return {"exit": last.get("close"), "exit_date": last.get("date"), "reason": "time"}
    for bar in path:
        day = bar.get("date")
        open_ = bar.get("open")
        high = bar.get("high")
        low = bar.get("low")
        if open_ is None:
            continue
        open_px = float(open_)
        if open_px <= stop:
            return {"exit": open_px, "exit_date": day, "reason": "stop"}
        if take_profit and target is not None and open_px >= float(target):
            return {"exit": open_px, "exit_date": day, "reason": "target"}
        hit_stop = low is not None and float(low) <= stop
        hit_target = bool(
            take_profit and target is not None and high is not None and float(high) >= float(target)
        )
        if hit_stop and hit_target:
            return {"exit": float(stop), "exit_date": day, "reason": "stop"}
        if hit_stop:
            return {"exit": float(stop), "exit_date": day, "reason": "stop"}
        if hit_target:
            return {"exit": float(target), "exit_date": day, "reason": "target"}
    return {"exit": last.get("close"), "exit_date": last.get("date"), "reason": "time"}


def _ret(entry: float | None, exit_: float | None) -> float | None:
    if entry is None or exit_ is None or entry == 0:
        return None
    return (float(exit_) / float(entry)) - 1.0


def simulate_trade(
    entry: float | None,
    atr_value: float | None,
    path: list[dict[str, Any]],
    horizon: int = DEFAULT_HORIZON,
    *,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> dict[str, Any]:
    window = path[: max(1, int(horizon) + 1)] if path else []
    levels = levels_from_anchor(entry, atr_value, provisional=False, stop_mult=stop_mult, target_mult=target_mult)
    sim = simulate_exit(entry, levels.get("stop"), levels.get("target"), window)
    reason = sim.get("reason")
    ret = _ret(entry, sim.get("exit"))
    payload = {
        **levels,
        "exit": sim.get("exit"),
        "exit_date": sim.get("exit_date"),
        "reason": reason,
        "y_hit": 1 if reason == "target" else 0,
        "ret": None if ret is None else float(ret),
    }
    if not window:
        get_logger().warning("simulate_trade empty path entry=%s atr=%s", entry, atr_value)
    return payload
