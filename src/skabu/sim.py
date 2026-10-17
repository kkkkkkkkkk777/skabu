"""1か月前の予測日で一度買い、現金＋評価額の日次推移を出す。未来足は選びに使わない。"""

from __future__ import annotations

from typing import Any

from skabu.config import Settings
from skabu.daily import describe_freshness
from skabu.errors import SkabuError
from skabu.buys import (
    DEFAULT_RISK_POINTS,
    SKIP_LABELS,
    active_holdout_ok,
    parse_risk_pct,
    select_buys,
    size_position,
    stop_width_at,
)
from skabu.levels import STOP_MULT, TARGET_MULT, bars_from_prices, compute_levels, exit_pair, path_from_prices, simulate_exit, simulate_trade
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.store import all_bar_dates, connect, latest_bar_date, load_price_map
from skabu.strategy import active_rank_filters, active_rank_selections
from skabu.verify import BENCH_CODE, _ret, lock_as_of

LOOKBACK_BARS = 20
LONG_LOOKBACK_BARS = 250
DEFAULT_CASH = 1_000_000.0
DEFAULT_TOP = 5
DEFAULT_HORIZON = 20
LONG_CANDIDATE_TOP = 20


def default_as_of(conn: Any, lookback: int = LOOKBACK_BARS) -> str | None:
    dates = all_bar_dates(conn)
    if len(dates) <= lookback:
        return dates[0] if dates else None
    return dates[-1 - lookback]


def run_sim(
    settings: Settings,
    *,
    as_of: str | None = None,
    cash: float = DEFAULT_CASH,
    top: int = DEFAULT_TOP,
    horizon: int = DEFAULT_HORIZON,
    market: str = "プライム",
    min_avg_volume: float = 50_000,
    min_bars: int = 60,
    selections: list[dict[str, Any]] | None = None,
    filters: list[dict[str, Any]] | None = None,
    risk_pct: float | None = None,
    take_profit: bool = True,
) -> dict[str, Any]:
    log = get_logger()
    start_cash = float(cash)
    risk_frac = parse_risk_pct(risk_pct if risk_pct is not None else DEFAULT_RISK_POINTS)
    if start_cash <= 0:
        raise SkabuError("開始資金は正の数にしてください。")
    if top < 1:
        raise SkabuError("銘柄数は1以上にしてください。")
    if not take_profit and not 1 <= int(horizon) <= 250:
        raise SkabuError("保有日数は1から250の営業日にしてください。")
    try:
        with connect(settings.db_path) as conn:
            dates = all_bar_dates(conn)
            if not dates:
                raise SkabuError("日足がありません。先に `python -m skabu sync all` を実行してください。")
            pred = latest_bar_date(conn, as_of) if as_of else default_as_of(conn)
            if not pred or pred not in {day: i for i, day in enumerate(dates)}:
                raise SkabuError(f"予測日 {as_of or pred} の日足がありません。")
            index = {day: i for i, day in enumerate(dates)}
            pred_i = index[pred]
            entry_i = pred_i + 1
            exit_i = entry_i + horizon
            if entry_i >= len(dates):
                raise SkabuError(f"予測日 {pred} の翌営業日がありません。")
            if exit_i >= len(dates):
                exit_i = len(dates) - 1
            entry_date = dates[entry_i]
            end_date = dates[exit_i]
            picks = active_rank_selections(settings) if selections is None else selections
            gates = active_rank_filters(settings) if filters is None else filters
            if selections is not None:
                log.info(
                    "sim explicit selections n=%s filters=%s (None would use adopted)",
                    len(picks),
                    len(gates),
                )
            live_holdout_ok = active_holdout_ok(settings)
            stop_mult, target_mult = exit_pair(settings)
            locked = lock_as_of(
                conn,
                as_of=pred,
                selections=picks,
                filters=gates,
                settings=settings,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                top=top,
                holdout_ok=True,
                stop_mult=stop_mult,
                target_mult=target_mult,
            )
            rows = list(locked.get("rows") or [])
            codes = {str(row["code"]) for row in rows if row.get("code")}
            codes.add(BENCH_CODE)
            prices = load_price_map(conn, codes, dates[0], dates[-1])
            payload = _walk(
                rows,
                dates,
                index,
                prices,
                pred,
                entry_date,
                end_date,
                start_cash,
                horizon,
                risk_frac,
                stop_mult=stop_mult,
                target_mult=target_mult,
                take_profit=take_profit,
            )
            payload["risk_pct"] = risk_frac
            payload["risk_points"] = round(risk_frac * 100.0, 4)
            payload["freshness"] = describe_freshness(settings.jquants_plan, latest_bar_date(conn))
            payload["lookback_note"] = (
                f"予測日は倉庫の最新足から{LOOKBACK_BARS}営業日前です。"
                "カレンダーの今日から1か月前ではありません。"
            )
            payload["mode"] = "month"
            payload["strategy"] = picks
            payload["filters"] = gates
            payload["market"] = market
            if "gate_passed" in locked:
                payload["gate_passed"] = locked.get("gate_passed")
            if "scored" in locked:
                payload["scored"] = locked.get("scored")
            if locked.get("universe") is not None:
                payload["universe"] = locked.get("universe")
            payload["strategy_key"] = locked.get("strategy_key")
            payload["holdout_ok"] = True
            payload["live_holdout_ok"] = live_holdout_ok
            payload["skipped"] = locked.get("skipped_n") or 0
            payload["skip_reasons"] = locked.get("reasons") or {}
            payload["skip_labels"] = SKIP_LABELS
            payload["cash"] = not rows
            payload["cash_note"] = (
                "条件を満たす銘柄がないため、現金のままです。" if not rows else None
            )
            payload["take_profit"] = bool(take_profit)
            if not take_profit:
                log.info("sim no take profit horizon=%s as_of=%s", horizon, pred)
            log.info(
                "sim as_of=%s entry=%s end=%s names=%s live_holdout_ok=%s start=%.0f end_eq=%.0f",
                pred,
                entry_date,
                end_date,
                len(payload.get("trades") or []),
                live_holdout_ok,
                start_cash,
                payload.get("equity_end") or 0,
            )
            return payload
    except SkabuError:
        raise
    except Exception as exc:
        log.exception("sim failed")
        dump_debug_event(settings.debug_dir, {"kind": "sim", "as_of": as_of, "error": str(exc)})
        raise SkabuError(f"シミュレーションに失敗しました: {exc}") from exc


def _walk(
    rows: list[dict[str, Any]],
    dates: list[str],
    index: dict[str, int],
    prices: dict[str, dict[str, dict[str, float | None]]],
    as_of: str,
    entry_date: str,
    end_date: str,
    start_cash: float,
    horizon: int,
    risk_pct: float,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
    take_profit: bool = True,
) -> dict[str, Any]:
    entry_i = index[entry_date]
    end_i = index[end_date]
    path_dates = dates[entry_i : end_i + 1]
    positions: list[dict[str, Any]] = []
    leftover = start_cash
    for row in rows:
        code = str(row.get("code") or "")
        series = prices.get(code, {})
        entry = (series.get(entry_date) or {}).get("open")
        hist = bars_from_prices(series, dates, as_of)
        levels = compute_levels(hist, anchor=entry, stop_mult=stop_mult, target_mult=target_mult)
        bars = []
        for day in path_dates:
            px = series.get(day) or {}
            bars.append(
                {
                    "date": day,
                    "open": px.get("open"),
                    "high": px.get("high"),
                    "low": px.get("low"),
                    "close": px.get("close"),
                }
            )
        if entry is None or entry <= 0:
            get_logger().warning("sim skip no entry as_of=%s code=%s", as_of, code)
            continue
        preview = simulate_trade(
            float(entry),
            levels.get("atr"),
            bars,
            horizon=horizon,
            stop_mult=stop_mult,
            target_mult=target_mult,
        )
        width = stop_width_at({"atr": levels.get("atr")}, float(entry), stop_mult=stop_mult)
        sized = size_position(
            cash=start_cash,
            risk_pct=risk_pct,
            stop_width=width,
            price=float(entry),
            leftover=leftover,
        )
        if sized is None:
            get_logger().info(
                "sim skip size as_of=%s code=%s leftover=%.2f stop_pct=%s",
                as_of,
                code,
                leftover,
                width,
            )
            continue
        leftover -= sized["alloc"]
        positions.append(
            {
                "row": row,
                "code": code,
                "co_name": row.get("co_name"),
                "s33_nm": row.get("s33_nm"),
                "entry": float(entry),
                "qty": sized["qty"],
                "alloc": sized["alloc"],
                "risk_yen": sized["risk_yen"],
                "stop": preview.get("stop"),
                "target": preview.get("target"),
                "bars": bars,
                "closed": False,
                "exit": None,
                "exit_date": None,
                "exit_reason": None,
            }
        )
    cash = leftover
    bench_entry = ((prices.get(BENCH_CODE) or {}).get(entry_date) or {}).get("open")
    curve: list[dict[str, Any]] = []
    peak = start_cash
    max_dd = 0.0
    for offset, day in enumerate(path_dates):
        marked = 0.0
        for pos in positions:
            if pos["closed"]:
                continue
            prefix = pos["bars"][: offset + 1]
            sim = simulate_exit(
                pos["entry"],
                pos["stop"],
                pos["target"],
                prefix,
                take_profit=take_profit,
            )
            reason = sim.get("reason")
            last_day = offset == len(path_dates) - 1
            if reason in {"stop", "target"} or last_day:
                exit_px = sim.get("exit")
                if exit_px is None:
                    exit_px = (prefix[-1] or {}).get("close")
                if exit_px is not None:
                    cash += pos["qty"] * float(exit_px)
                    pos["exit"] = float(exit_px)
                    pos["exit_date"] = sim.get("exit_date") or day
                    pos["exit_reason"] = "time" if last_day and reason not in {"stop", "target"} else reason
                pos["closed"] = True
            else:
                mark = (prefix[-1] or {}).get("close")
                if mark is not None:
                    marked += pos["qty"] * float(mark)
        total = cash + marked
        peak = max(peak, total)
        if peak:
            max_dd = min(max_dd, total / peak - 1.0)
        bench_close = ((prices.get(BENCH_CODE) or {}).get(day) or {}).get("close")
        bench = None
        if bench_entry not in (None, 0) and bench_close is not None:
            bench = start_cash * (float(bench_close) / float(bench_entry))
        curve.append(
            {
                "date": day,
                "cash": round(cash, 2),
                "positions": round(marked, 2),
                "total": round(total, 2),
                "bench": None if bench is None else round(bench, 2),
            }
        )
    trades = []
    for pos in positions:
        ret = _ret(pos["entry"], pos.get("exit"))
        pnl = None if pos.get("exit") is None else (pos["exit"] - pos["entry"]) * pos["qty"]
        trades.append(
            {
                "rank": pos["row"].get("rank"),
                "code": pos["code"],
                "co_name": pos["co_name"],
                "as_of": as_of,
                "entry_date": entry_date,
                "entry": _round(pos["entry"]),
                "exit": _round(pos.get("exit")),
                "exit_date": pos.get("exit_date"),
                "exit_reason": pos.get("exit_reason"),
                "stop": _round(pos.get("stop")),
                "target": _round(pos.get("target")),
                "s33_nm": pos.get("s33_nm"),
                "qty": pos["qty"],
                "alloc": round(pos["alloc"], 2),
                "risk_yen": None if pos.get("risk_yen") is None else round(pos["risk_yen"], 2),
                "pnl": None if pnl is None else round(pnl, 2),
                "return": None if ret is None else round(ret, 6),
                "win": None if ret is None else ret > 0,
                "reason": pos["row"].get("reason"),
                "proba": pos["row"].get("proba"),
                "total": pos["row"].get("total"),
            }
        )
    equity_end = curve[-1]["total"] if curve else start_cash
    wins = [1 for item in trades if item.get("win")]
    return {
        "status": "ok",
        "as_of": as_of,
        "entry_date": entry_date,
        "end_date": end_date,
        "horizon": horizon,
        "top": len(rows),
        "cash_start": round(start_cash, 2),
        "equity_end": round(equity_end, 2),
        "pnl": round(equity_end - start_cash, 2),
        "return": round((equity_end / start_cash) - 1.0, 6) if start_cash else None,
        "max_drawdown": round(max_dd, 6),
        "wins": len(wins),
        "n": len(trades),
        "curve": curve,
        "trades": trades,
        "bench_end": None if not curve else curve[-1].get("bench"),
    }


def default_long_range(conn: Any, lookback: int = LONG_LOOKBACK_BARS) -> tuple[str | None, str | None]:
    dates = all_bar_dates(conn)
    if not dates:
        return None, None
    end = dates[-1]
    if len(dates) <= lookback + 1:
        start = dates[0]
    else:
        start = dates[-1 - lookback]
    if start >= end:
        start = dates[0]
    return start, end


def _open_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [pos for pos in positions if not pos.get("closed")]


def _ensure_prices(
    conn: Any,
    prices: dict[str, dict[str, dict[str, float | None]]],
    codes: set[str],
    from_date: str,
    to_date: str,
) -> None:
    missing = [code for code in codes if code and code not in prices]
    if not missing:
        return
    prices.update(load_price_map(conn, missing, from_date, to_date))


def _mark_open(
    positions: list[dict[str, Any]],
    prices: dict[str, dict[str, dict[str, float | None]]],
    day: str | None,
) -> float:
    if not day:
        return 0.0
    marked = 0.0
    for pos in _open_positions(positions):
        close = ((prices.get(pos["code"]) or {}).get(day) or {}).get("close")
        if close is not None:
            marked += pos["qty"] * float(close)
    return marked


def _close_position(pos: dict[str, Any], exit_px: float | None, exit_date: str | None, reason: str) -> float:
    if exit_px is None:
        get_logger().warning("long sim close missing price code=%s day=%s", pos.get("code"), exit_date)
        pos["closed"] = True
        pos["exit_reason"] = reason
        pos["exit_date"] = exit_date
        return 0.0
    pos["exit"] = float(exit_px)
    pos["exit_date"] = exit_date
    pos["exit_reason"] = reason
    pos["closed"] = True
    return pos["qty"] * float(exit_px)


def _trade_row(pos: dict[str, Any]) -> dict[str, Any]:
    ret = _ret(pos["entry"], pos.get("exit"))
    pnl = None if pos.get("exit") is None else (pos["exit"] - pos["entry"]) * pos["qty"]
    return {
        "rank": pos["row"].get("rank"),
        "code": pos["code"],
        "co_name": pos["co_name"],
        "as_of": pos.get("as_of"),
        "entry_date": pos.get("entry_date"),
        "entry": _round(pos["entry"]),
        "exit": _round(pos.get("exit")),
        "exit_date": pos.get("exit_date"),
        "exit_reason": pos.get("exit_reason"),
        "stop": _round(pos.get("stop")),
        "target": _round(pos.get("target")),
        "s33_nm": pos.get("s33_nm"),
        "qty": pos["qty"],
        "alloc": round(pos["alloc"], 2),
        "risk_yen": None if pos.get("risk_yen") is None else round(pos["risk_yen"], 2),
        "pnl": None if pnl is None else round(pnl, 2),
        "return": None if ret is None else round(ret, 6),
        "win": None if ret is None else ret > 0,
        "reason": pos["row"].get("reason"),
        "proba": pos["row"].get("proba"),
        "total": pos["row"].get("total"),
    }


def _fill_slots(
    conn: Any,
    *,
    as_of: str,
    buy_date: str,
    dates: list[str],
    prices: dict[str, dict[str, dict[str, float | None]]],
    positions: list[dict[str, Any]],
    cash: float,
    equity: float,
    top: int,
    risk_pct: float,
    picks: list[dict[str, Any]],
    gates: list[dict[str, Any]],
    market: str,
    min_avg_volume: float,
    min_bars: int,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
    settings: Any | None = None,
) -> tuple[float, int, dict[str, int]]:
    opened = _open_positions(positions)
    slots = top - len(opened)
    if slots < 1 or cash <= 0:
        return cash, 0, {}
    candidate_top = max(top * 5, LONG_CANDIDATE_TOP)
    locked = lock_as_of(
        conn,
        as_of=as_of,
        selections=picks,
        filters=gates,
        settings=settings,
        market=market,
        min_avg_volume=min_avg_volume,
        min_bars=min_bars,
        top=candidate_top,
        holdout_ok=True,
        stop_mult=stop_mult,
        target_mult=target_mult,
    )
    rows = list(locked.get("rows") or [])
    codes = {str(row.get("code") or "") for row in rows if row.get("code")}
    codes.add(BENCH_CODE)
    _ensure_prices(conn, prices, codes, dates[0], dates[-1])
    picked = select_buys(
        rows,
        top=slots,
        holdout_ok=True,
        held_codes=[pos["code"] for pos in opened],
        held_sectors=[pos.get("s33_nm") for pos in opened],
        stop_mult=stop_mult,
        target_mult=target_mult,
    )
    leftover = cash
    bought = 0
    for row in picked.get("bought") or []:
        code = str(row.get("code") or "")
        series = prices.get(code, {})
        entry = (series.get(buy_date) or {}).get("open")
        if entry is None or entry <= 0:
            get_logger().warning("long sim skip no entry as_of=%s buy=%s code=%s", as_of, buy_date, code)
            continue
        hist = bars_from_prices(series, dates, as_of)
        levels = compute_levels(hist, anchor=float(entry), stop_mult=stop_mult, target_mult=target_mult)
        width = stop_width_at({"atr": levels.get("atr")}, float(entry), stop_mult=stop_mult)
        sized = size_position(
            cash=equity,
            risk_pct=risk_pct,
            stop_width=width,
            price=float(entry),
            leftover=leftover,
        )
        if sized is None:
            get_logger().info(
                "long sim skip size as_of=%s buy=%s code=%s leftover=%.2f",
                as_of,
                buy_date,
                code,
                leftover,
            )
            continue
        leftover -= sized["alloc"]
        bought += 1
        positions.append(
            {
                "row": row,
                "code": code,
                "co_name": row.get("co_name"),
                "s33_nm": row.get("s33_nm"),
                "as_of": as_of,
                "entry_date": buy_date,
                "entry": float(entry),
                "qty": sized["qty"],
                "alloc": sized["alloc"],
                "risk_yen": sized["risk_yen"],
                "stop": levels.get("stop"),
                "target": levels.get("target"),
                "closed": False,
                "exit": None,
                "exit_date": None,
                "exit_reason": None,
            }
        )
    return leftover, bought, picked.get("reasons") or {}


def run_long_sim(
    settings: Settings,
    *,
    start: str | None = None,
    end: str | None = None,
    cash: float = DEFAULT_CASH,
    top: int = DEFAULT_TOP,
    market: str = "プライム",
    min_avg_volume: float = 50_000,
    min_bars: int = 60,
    selections: list[dict[str, Any]] | None = None,
    filters: list[dict[str, Any]] | None = None,
    risk_pct: float | None = None,
    take_profit: bool = True,
    horizon: int = DEFAULT_HORIZON,
) -> dict[str, Any]:
    log = get_logger()
    start_cash = float(cash)
    risk_frac = parse_risk_pct(risk_pct if risk_pct is not None else DEFAULT_RISK_POINTS)
    if start_cash <= 0:
        raise SkabuError("開始資金は正の数にしてください。")
    if top < 1:
        raise SkabuError("銘柄数は1以上にしてください。")
    if not take_profit and not 1 <= int(horizon) <= 250:
        raise SkabuError("保有日数は1から250の営業日にしてください。")
    try:
        with connect(settings.db_path) as conn:
            dates = all_bar_dates(conn)
            if not dates:
                raise SkabuError("日足がありません。先に `python -m skabu sync all` を実行してください。")
            default_start, default_end = default_long_range(conn)
            pred = latest_bar_date(conn, start) if start else default_start
            end_date = latest_bar_date(conn, end) if end else default_end
            index = {day: i for i, day in enumerate(dates)}
            if not pred or pred not in index:
                raise SkabuError(f"開始日 {start or pred} の日足がありません。")
            if not end_date or end_date not in index:
                raise SkabuError(f"終了日 {end or end_date} の日足がありません。")
            if pred >= end_date:
                raise SkabuError("開始日は終了日より前にしてください。")
            pred_i = index[pred]
            entry_i = pred_i + 1
            if entry_i >= len(dates):
                raise SkabuError(f"開始日 {pred} の翌営業日がありません。")
            entry_date = dates[entry_i]
            if entry_date > end_date:
                raise SkabuError("開始の翌営業日が終了日を超えています。")
            end_i = index[end_date]
            path_dates = dates[entry_i : end_i + 1]
            picks = active_rank_selections(settings) if selections is None else selections
            gates = active_rank_filters(settings) if filters is None else filters
            live_holdout_ok = active_holdout_ok(settings)
            stop_mult, target_mult = exit_pair(settings)
            prices: dict[str, dict[str, dict[str, float | None]]] = {}
            _ensure_prices(conn, prices, {BENCH_CODE}, dates[0], dates[-1])
            cash_now = start_cash
            positions: list[dict[str, Any]] = []
            pending_as_of = pred
            skipped = 0
            skip_reasons: dict[str, int] = {}
            curve: list[dict[str, Any]] = []
            peak = start_cash
            max_dd = 0.0
            first_locked_empty = False
            bench_entry = ((prices.get(BENCH_CODE) or {}).get(entry_date) or {}).get("open")
            for offset, day in enumerate(path_dates):
                last_day = offset == len(path_dates) - 1
                if pending_as_of:
                    prev = dates[index[day] - 1] if index[day] > 0 else None
                    marked = _mark_open(positions, prices, prev)
                    equity = cash_now + marked
                    cash_now, bought, reasons = _fill_slots(
                        conn,
                        as_of=pending_as_of,
                        buy_date=day,
                        dates=dates,
                        prices=prices,
                        positions=positions,
                        cash=cash_now,
                        equity=equity,
                        top=top,
                        risk_pct=risk_frac,
                        picks=picks,
                        gates=gates,
                        market=market,
                        stop_mult=stop_mult,
                        target_mult=target_mult,
                        settings=settings,
                        min_avg_volume=min_avg_volume,
                        min_bars=min_bars,
                    )
                    if reasons:
                        skip_reasons = reasons
                        skipped = sum(reasons.values())
                    if offset == 0 and bought == 0 and not positions:
                        first_locked_empty = True
                    pending_as_of = None
                for pos in _open_positions(positions):
                    series = prices.get(pos["code"], {})
                    path = path_from_prices(series, dates, pos["entry_date"], day)
                    sim = simulate_exit(
                        pos["entry"],
                        pos["stop"],
                        pos["target"],
                        path,
                        take_profit=take_profit,
                    )
                    reason = sim.get("reason")
                    held = index[day] - index[pos["entry_date"]]
                    if reason == "stop" or (take_profit and reason == "target"):
                        cash_now += _close_position(pos, sim.get("exit"), sim.get("exit_date") or day, reason)
                    elif (not take_profit) and held >= int(horizon):
                        close = (path[-1] or {}).get("close") if path else None
                        if close is None:
                            close = sim.get("exit")
                        cash_now += _close_position(pos, close, day, "time")
                    elif last_day:
                        close = (path[-1] or {}).get("close") if path else None
                        if close is None:
                            close = sim.get("exit")
                        cash_now += _close_position(pos, close, day, "end")
                if not last_day and len(_open_positions(positions)) < top:
                    pending_as_of = day
                marked = _mark_open(positions, prices, day)
                total = cash_now + marked
                peak = max(peak, total)
                if peak:
                    max_dd = min(max_dd, total / peak - 1.0)
                bench_close = ((prices.get(BENCH_CODE) or {}).get(day) or {}).get("close")
                bench = None
                if bench_entry not in (None, 0) and bench_close is not None:
                    bench = start_cash * (float(bench_close) / float(bench_entry))
                curve.append(
                    {
                        "date": day,
                        "cash": round(cash_now, 2),
                        "positions": round(marked, 2),
                        "total": round(total, 2),
                        "bench": None if bench is None else round(bench, 2),
                    }
                )
            trades = [_trade_row(pos) for pos in positions]
            equity_end = curve[-1]["total"] if curve else start_cash
            wins = [1 for item in trades if item.get("win")]
            payload = {
                "status": "ok",
                "mode": "long",
                "as_of": pred,
                "entry_date": entry_date,
                "end_date": end_date,
                "horizon": None if take_profit else int(horizon),
                "take_profit": bool(take_profit),
                "top": top,
                "cash_start": round(start_cash, 2),
                "equity_end": round(equity_end, 2),
                "pnl": round(equity_end - start_cash, 2),
                "return": round((equity_end / start_cash) - 1.0, 6) if start_cash else None,
                "max_drawdown": round(max_dd, 6),
                "wins": len(wins),
                "n": len(trades),
                "curve": curve,
                "trades": trades,
                "bench_end": None if not curve else curve[-1].get("bench"),
                "risk_pct": risk_frac,
                "risk_points": round(risk_frac * 100.0, 4),
                "freshness": describe_freshness(settings.jquants_plan, latest_bar_date(conn)),
                "lookback_note": "目標か損切まで保有し、売れた枠に別銘柄を入れます。未来の足は選びに使いません。",
                "strategy": picks,
                "filters": gates,
                "market": market,
                "holdout_ok": True,
                "live_holdout_ok": live_holdout_ok,
                "skipped": skipped,
                "skip_reasons": skip_reasons,
                "skip_labels": SKIP_LABELS,
                "cash": first_locked_empty and not trades,
                "cash_note": (
                    "条件を満たす銘柄がないため、現金のままです。"
                    if first_locked_empty and not trades
                    else None
                ),
            }
            log.info(
                "long sim start=%s entry=%s end=%s trades=%s start=%.0f end_eq=%.0f take_profit=%s horizon=%s",
                pred,
                entry_date,
                end_date,
                len(trades),
                start_cash,
                payload.get("equity_end") or 0,
                take_profit,
                None if take_profit else int(horizon),
            )
            return payload
    except SkabuError:
        raise
    except Exception as exc:
        log.exception("long sim failed")
        dump_debug_event(settings.debug_dir, {"kind": "long_sim", "start": start, "end": end, "error": str(exc)})
        raise SkabuError(f"長期シミュレーションに失敗しました: {exc}") from exc


def _round(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)
