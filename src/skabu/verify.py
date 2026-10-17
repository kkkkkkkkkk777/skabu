"""ロックした予測を、翌始値から上限日内の目標・損切・期限終値の先着で検証する。"""

from __future__ import annotations

import threading
import time
from typing import Any

from skabu.buys import BUY_SPEC, select_buys
from skabu.levels import STOP_MULT, TARGET_MULT, bars_from_prices, compute_levels, levels_spec, path_from_prices, simulate_trade
from skabu.logging_setup import get_logger
from skabu.ranking import (
    build_ranking,
    cache_key,
    default_rank_selections,
    default_strategy_label,
    normalize_selections,
)
from skabu.store import (
    all_bar_dates,
    get_prediction_run,
    get_verify_cache,
    load_price_map,
    lock_prediction_run,
    put_verify_cache,
)

BENCH_CODE = "13060"
HORIZONS = (5, 20)
_RUN_LOCK = threading.Lock()
_LOCK_AS_OF = threading.Lock()

PRESETS: dict[str, dict[str, Any]] = {
    "supertrend": {
        "title": "Supertrend",
        "selections": [{"id": "supertrend", "params": {}}],
    },
    "ichimoku": {
        "title": "一目",
        "selections": [{"id": "ichimoku", "params": {}}],
    },
    "bbands": {
        "title": "ボリンジャー",
        "selections": [{"id": "bbands", "params": {}}],
    },
    "stoch": {
        "title": "ストキャス",
        "selections": [{"id": "stoch", "params": {}}],
    },
    "supertrend_stoch": {
        "title": "Supertrend + ストキャス",
        "selections": [{"id": "supertrend", "params": {}}, {"id": "stoch", "params": {}}],
    },
    "ichimoku_bbands": {
        "title": "一目 + ボリンジャー",
        "selections": [{"id": "ichimoku", "params": {}}, {"id": "bbands", "params": {}}],
    },
    "filters": {
        "title": "フィルターのみ",
        "selections": [],
        "filters": [
            {"id": "ichimoku", "feature": "above_cloud", "equals": 1},
            {"id": "bbands", "feature": "pct_b", "op": "lt", "value": 0.85},
            {"id": "rsi", "feature": "value", "op": "lt", "value": 70},
        ],
    },
}

_default_id, _default_title, _ = default_strategy_label()
PRESETS[_default_id] = {"title": _default_title, "selections": default_rank_selections()}

_CLOUD = [{"id": "ichimoku", "feature": "above_cloud", "equals": 1}]
_COOL = [
    {"id": "ichimoku", "feature": "above_cloud", "equals": 1},
    {"id": "bbands", "feature": "pct_b", "op": "lt", "value": 0.85},
    {"id": "rsi", "feature": "value", "op": "lt", "value": 70},
]
_RSI70 = [{"id": "rsi", "feature": "value", "op": "lt", "value": 70}]


def _search_presets() -> dict[str, dict[str, Any]]:
    default_id, default_title, _ = default_strategy_label()
    scores: list[tuple[str, str, list[dict[str, Any]]]] = [
        ("none", "点数なし", []),
        ("ichimoku", "一目", [{"id": "ichimoku", "params": {}}]),
        (default_id, default_title, default_rank_selections()),
        ("supertrend", "Supertrend", [{"id": "supertrend", "params": {}}]),
    ]
    filters: list[tuple[str, str, list[dict[str, Any]]]] = [
        ("none", "フィルターなし", []),
        ("cloud", "雲の上", list(_CLOUD)),
        ("cool", "雲の上・過熱なし", list(_COOL)),
        ("rsi", "RSI<70", list(_RSI70)),
    ]
    aliases = {
        ("none", "cool"): ("filters", "フィルターのみ"),
    }
    out: dict[str, dict[str, Any]] = {}
    for score_id, score_title, selections in scores:
        for filt_id, filt_title, gates in filters:
            preset_id, title = aliases.get((score_id, filt_id), (f"{score_id}_{filt_id}", f"{score_title} / {filt_title}"))
            out[preset_id] = {
                "title": title,
                "selections": list(selections),
                "filters": list(gates),
            }
    out["momentum"] = {
        "title": "地合い＋高値モメンタム",
        "selections": [{"id": "momentum", "params": {}}],
        "filters": [{"id": "regime", "feature": "above_ma200", "equals": 1}],
    }
    out["double_top"] = {
        "title": "フィルター＋ダブルトップ除外",
        "selections": [],
        "filters": list(_COOL)
        + [{"id": "doubletop", "feature": "double_top", "equals": 0}],
    }
    out["lower_lows"] = {
        "title": "フィルター＋切り下げ除外",
        "selections": [],
        "filters": list(_COOL)
        + [{"id": "downtrend", "feature": "lower_lows", "equals": 0}],
    }
    return out


SEARCH_PRESETS: dict[str, dict[str, Any]] = _search_presets()


def strategy_key(
    selections: list[dict[str, Any]],
    market: str,
    min_avg_volume: float,
    min_bars: int,
    top: int,
    filters: list[dict[str, Any]] | None = None,
    holdout_ok: bool = True,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> str:
    from skabu.ranking import boost_fingerprint

    return cache_key(
        {
            "selections": selections,
            "filters": filters or [],
            "model_fp": boost_fingerprint(selections),
            "market": market,
            "min_avg_volume": min_avg_volume,
            "min_bars": min_bars,
            "top": top,
            "buys": BUY_SPEC,
            "holdout_ok": bool(holdout_ok),
            "stop_mult": float(stop_mult),
            "target_mult": float(target_mult),
        }
    )


def pick_as_of_dates(dates: list[str], *, min_history: int, stride: int, horizon: int) -> list[str]:
    if len(dates) < min_history + horizon + 2:
        return []
    first = min_history
    last = len(dates) - horizon - 2
    if last < first:
        return []
    picked = list(dates[first : last + 1 : stride])
    if dates[last] not in picked:
        picked.append(dates[last])
    return picked


def _index(dates: list[str]) -> dict[str, int]:
    return {day: i for i, day in enumerate(dates)}


def _ret(entry: float | None, exit_: float | None) -> float | None:
    if entry is None or exit_ is None or entry == 0:
        return None
    return (exit_ / entry) - 1.0


def _summarize(returns: list[float], excesses: list[float], period_returns: list[float]) -> dict[str, Any]:
    wins = sum(1 for item in returns if item > 0)
    vs_wins = sum(1 for item in excesses if item > 0)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    curve = []
    for item in period_returns:
        equity *= 1.0 + item
        peak = max(peak, equity)
        if peak:
            max_dd = min(max_dd, equity / peak - 1.0)
        curve.append(round(equity, 6))
    return {
        "trades": len(returns),
        "periods": len(period_returns),
        "win_rate": None if not returns else round(wins / len(returns), 4),
        "win_rate_vs_bench": None if not excesses else round(vs_wins / len(excesses), 4),
        "avg_return": None if not returns else round(sum(returns) / len(returns), 6),
        "avg_excess": None if not excesses else round(sum(excesses) / len(excesses), 6),
        "max_drawdown": round(max_dd, 6),
        "equity_end": round(equity, 6) if period_returns else None,
        "equity_curve": curve,
    }


def _expose_gate_counts(locked: dict[str, Any]) -> dict[str, Any]:
    config = locked.get("config") or {}
    if "gate_passed" in config:
        locked["gate_passed"] = config.get("gate_passed")
    if "scored" in config:
        locked["scored"] = config.get("scored")
    return locked


def lock_as_of(
    conn: Any,
    *,
    as_of: str,
    selections: list[dict[str, Any]],
    market: str,
    min_avg_volume: float,
    min_bars: int,
    top: int,
    filters: list[dict[str, Any]] | None = None,
    holdout_ok: bool = True,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
    settings: Any | None = None,
) -> dict[str, Any]:
    key = strategy_key(
        selections,
        market,
        min_avg_volume,
        min_bars,
        top,
        filters=filters,
        holdout_ok=holdout_ok,
        stop_mult=stop_mult,
        target_mult=target_mult,
    )
    with _LOCK_AS_OF:
        existing = get_prediction_run(conn, key, as_of)
        if existing:
            return _expose_gate_counts(existing)
        ranking = build_ranking(
            conn,
            selections=selections,
            filters=filters,
            market=market,
            min_avg_volume=min_avg_volume,
            settings=settings,
            min_bars=min_bars,
            top=top,
            as_of=as_of,
            refresh=False,
            holdout_ok=holdout_ok,
            stop_mult=stop_mult,
            target_mult=target_mult,
        )
        picked = select_buys(
            ranking.get("rows") or [],
            top=top,
            holdout_ok=holdout_ok,
            stop_mult=stop_mult,
            target_mult=target_mult,
        )
        slim_rows = [
            {
                "rank": row.get("rank"),
                "code": row.get("code"),
                "co_name": row.get("co_name"),
                "mkt_nm": row.get("mkt_nm"),
                "s33": row.get("s33"),
                "s33_nm": row.get("s33_nm"),
                "total": row.get("total"),
                "scores": row.get("scores"),
                "close": row.get("close"),
                "reason": row.get("reason"),
                "atr": row.get("atr"),
                "stop": row.get("stop"),
                "target": row.get("target"),
                "rr": row.get("rr"),
                "entry_ref": row.get("entry_ref"),
                "proba": row.get("proba"),
                "buyable": row.get("buyable"),
                "skip_reason": row.get("skip_reason"),
            }
            for row in picked["bought"]
        ]
        from skabu.ranking import boost_fingerprint

        config = {
            "selections": selections,
            "filters": filters or [],
            "market": market,
            "min_avg_volume": min_avg_volume,
            "min_bars": min_bars,
            "top": top,
            "model_fp": boost_fingerprint(selections),
            "buys": BUY_SPEC,
            "holdout_ok": bool(holdout_ok),
            "stop_mult": float(stop_mult),
            "target_mult": float(target_mult),
            "skipped_n": picked["skipped_n"],
            "reasons": picked["reasons"],
        }
        if "gate_passed" in ranking:
            config["gate_passed"] = int(ranking.get("gate_passed") or 0)
        if "scored" in ranking:
            config["scored"] = int(ranking.get("scored") or 0)
        locked = lock_prediction_run(
            conn,
            key,
            as_of,
            config,
            slim_rows,
            int(ranking.get("universe") or 0),
        )
        locked["skipped_n"] = picked["skipped_n"]
        locked["reasons"] = picked["reasons"]
        locked["scanned"] = picked["scanned"]
        locked = _expose_gate_counts(locked)
    get_logger().info(
        "prediction locked as_of=%s names=%s skipped=%s key=%s",
        as_of,
        len(slim_rows),
        picked["skipped_n"],
        key[:12],
    )
    return locked


def evaluate_run(
    run: dict[str, Any],
    dates: list[str],
    index: dict[str, int],
    prices: dict[str, dict[str, dict[str, float | None]]],
    horizon: int,
    bench_code: str,
    *,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> dict[str, Any] | None:
    as_of = run["as_of"]
    if as_of not in index:
        return None
    pred_i = index[as_of]
    entry_i = pred_i + 1
    exit_i = entry_i + horizon
    if exit_i >= len(dates):
        return None
    entry_date = dates[entry_i]
    exit_date = dates[exit_i]
    bench = prices.get(bench_code, {})
    trades = []
    rets: list[float] = []
    excesses: list[float] = []
    bench_rets: list[float] = []
    for row in run.get("rows") or []:
        code = row["code"]
        series = prices.get(code, {})
        entry = (series.get(entry_date) or {}).get("open")
        hist = bars_from_prices(series, dates, as_of)
        atr_value = compute_levels(hist).get("atr")
        if atr_value is None:
            atr_value = row.get("atr")
        path = path_from_prices(series, dates, entry_date, exit_date)
        trade = simulate_trade(
            entry,
            atr_value,
            path,
            horizon=horizon,
            stop_mult=stop_mult,
            target_mult=target_mult,
        )
        exit_ = trade.get("exit")
        ret = trade.get("ret")
        bench_ret = _ret(
            (bench.get(entry_date) or {}).get("open"),
            (bench.get(trade.get("exit_date") or exit_date) or {}).get("close"),
        )
        item = {
            "rank": row.get("rank"),
            "code": code,
            "co_name": row.get("co_name"),
            "total": row.get("total"),
            "entry": entry,
            "exit": exit_,
            "exit_date": trade.get("exit_date"),
            "exit_reason": trade.get("reason"),
            "stop": trade.get("stop"),
            "target": trade.get("target"),
            "return": None if ret is None else round(ret, 6),
            "excess": None if ret is None or bench_ret is None else round(ret - bench_ret, 6),
            "win": None if ret is None else ret > 0,
            "win_vs_bench": None if ret is None or bench_ret is None else ret > bench_ret,
            "y_hit": trade.get("y_hit"),
        }
        trades.append(item)
        if ret is not None:
            rets.append(ret)
            if bench_ret is not None:
                excesses.append(ret - bench_ret)
                bench_rets.append(bench_ret)
    if not (run.get("rows") or []):
        period_ret = 0.0
        period_bench = 0.0
    else:
        period_ret = None if not rets else sum(rets) / len(rets)
        period_bench = None if not bench_rets else sum(bench_rets) / len(bench_rets)
    return {
        "as_of": as_of,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "horizon": horizon,
        "n": len(trades),
        "complete": len(rets),
        "bench_return": None if period_bench is None else round(period_bench, 6),
        "period_return": None if period_ret is None else round(period_ret, 6),
        "trades": trades,
    }


def run_verify(
    conn: Any,
    *,
    selections: list[dict[str, Any]] | None = None,
    filters: list[dict[str, Any]] | None = None,
    market: str = "プライム",
    min_avg_volume: float = 50_000,
    min_bars: int = 60,
    top: int = 20,
    stride: int = 20,
    min_history: int = 80,
    bench_code: str = BENCH_CODE,
    refresh: bool = False,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
    settings: Any | None = None,
) -> dict[str, Any]:
    from skabu.ranking import boost_fingerprint

    picks = normalize_selections(default_rank_selections() if selections is None else selections)
    gates = filters or []
    dates = all_bar_dates(conn)
    config = {
        "selections": picks,
        "filters": gates,
        "model_fp": boost_fingerprint(picks),
        "market": market,
        "min_avg_volume": min_avg_volume,
        "min_bars": min_bars,
        "top": top,
        "stride": stride,
        "min_history": min_history,
        "bench_code": bench_code,
        "horizons": list(HORIZONS),
        "levels": levels_spec(stop_mult, target_mult),
        "buys": BUY_SPEC,
    }
    key = cache_key(config)
    with _RUN_LOCK:
        if not refresh:
            cached = get_verify_cache(conn, key)
            if cached:
                get_logger().info("verify cache hit key=%s", key[:12])
                return cached
        return _run_verify_body(
            conn,
            picks=picks,
            filters=gates,
            config=config,
            key=key,
            dates=dates,
            market=market,
            min_avg_volume=min_avg_volume,
            min_bars=min_bars,
            top=top,
            stride=stride,
            min_history=min_history,
            bench_code=bench_code,
            stop_mult=stop_mult,
            target_mult=target_mult,
            settings=settings,
        )


def _run_verify_body(
    conn: Any,
    *,
    picks: list[dict[str, Any]],
    filters: list[dict[str, Any]],
    config: dict[str, Any],
    key: str,
    dates: list[str],
    market: str,
    min_avg_volume: float,
    min_bars: int,
    top: int,
    stride: int,
    min_history: int,
    bench_code: str,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
    settings: Any | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    as_ofs = pick_as_of_dates(dates, min_history=min_history, stride=stride, horizon=max(HORIZONS))
    locked = []
    for as_of in as_ofs:
        locked.append(
            lock_as_of(
                conn,
                as_of=as_of,
                selections=picks,
                filters=filters,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                top=top,
                stop_mult=stop_mult,
                target_mult=target_mult,
                settings=settings,
            )
        )
    codes = {BENCH_CODE}
    for run in locked:
        for row in run.get("rows") or []:
            codes.add(row["code"])
    prices = load_price_map(conn, codes, dates[0], dates[-1]) if dates else {}
    index = _index(dates)
    horizons: dict[str, Any] = {}
    for horizon in HORIZONS:
        periods = []
        all_rets: list[float] = []
        all_excess: list[float] = []
        period_rets: list[float] = []
        for run in locked:
            evaluated = evaluate_run(
                run,
                dates,
                index,
                prices,
                horizon,
                bench_code,
                stop_mult=stop_mult,
                target_mult=target_mult,
            )
            if evaluated is None:
                continue
            periods.append({k: evaluated[k] for k in evaluated if k != "trades"} | {"trades": evaluated["trades"]})
            period_trade_rets = [t["return"] for t in evaluated["trades"] if t["return"] is not None]
            all_rets.extend(period_trade_rets)
            all_excess.extend(t["excess"] for t in evaluated["trades"] if t["excess"] is not None)
            if evaluated["period_return"] is not None:
                period_rets.append(evaluated["period_return"])
        horizons[str(horizon)] = {
            "horizon": horizon,
            "summary": _summarize(all_rets, all_excess, period_rets),
            "periods": periods,
        }

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    payload = {
        "strategy_key": strategy_key(
            picks,
            market,
            min_avg_volume,
            min_bars,
            top,
            filters=filters,
            stop_mult=stop_mult,
            target_mult=target_mult,
        ),
        "stop_mult": float(stop_mult),
        "target_mult": float(target_mult),
        "selections": picks,
        "filters": filters,
        "market": market,
        "min_avg_volume": min_avg_volume,
        "min_bars": min_bars,
        "top": top,
        "stride": stride,
        "bench_code": bench_code,
        "bench_name": "TOPIX ETF (1306)",
        "as_of_dates": as_ofs,
        "locked_runs": len(locked),
        "horizons": horizons,
        "elapsed_ms": elapsed_ms,
        "cached": False,
        "rules": {
            "entry": "予測日の翌営業日始値",
            "exit": "損切・目標・上限日終値の先着（同一日は損切優先。ギャップは始値。水準は翌始値とATR）",
            "win": "銘柄リターンがプラス",
            "win_vs_bench": "銘柄リターンが、同じ出口日のベンチマークより上",
            "label": "上限日内に目標へ先に届いたら成功",
            "portfolio": "上位Nを等金額",
        },
    }
    put_verify_cache(conn, key, payload)
    get_logger().info(
        "verify done dates=%s locked=%s ms=%s",
        len(as_ofs),
        len(locked),
        elapsed_ms,
    )
    return payload


def run_compare(
    conn: Any,
    *,
    refresh: bool = False,
    presets: dict[str, dict[str, Any]] | None = None,
    include_periods: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    items = []
    rules = None
    specs = presets or PRESETS
    for preset_id, spec in specs.items():
        get_logger().info("compare preset id=%s", preset_id)
        result = run_verify(
            conn,
            selections=spec["selections"],
            filters=spec.get("filters") or [],
            refresh=refresh,
            **kwargs,
        )
        rules = result.get("rules")
        horizons: dict[str, Any] = {}
        for name, body in (result.get("horizons") or {}).items():
            if include_periods:
                horizons[name] = {"summary": body.get("summary"), "periods": body.get("periods") or []}
            else:
                horizons[name] = body.get("summary")
        items.append(
            {
                "id": preset_id,
                "title": spec["title"],
                "selections": spec.get("selections"),
                "filters": spec.get("filters") or [],
                "horizons": horizons,
                "locked_runs": result.get("locked_runs"),
            }
        )
    return {"items": items, "rules": rules, "bench_code": BENCH_CODE}
