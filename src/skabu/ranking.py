"""インジケータ点数を全銘柄で合計し、上昇候補を並べる。"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from skabu.gates import has_fact_gates, is_fact_gate, is_weekly_gate
from skabu.indicators.base import coerce_params
from skabu.indicators.registry import compute_indicators, get_plugin
from skabu.buys import BUY_SPEC, annotate_buys, apply_sector_cap
from skabu.levels import LEVELS_NOTE, STOP_MULT, TARGET_MULT, compute_levels, levels_spec
from skabu.logging_setup import get_logger
from skabu.weekly_gate import weekly_features
from skabu.store import (
    get_ranking_cache,
    latest_bar_date,
    list_universe,
    load_daily_bars_since,
    load_facts_as_of,
    put_ranking_cache,
    recent_bar_dates,
)

REGIME_ID = "regime"
REGIME_CODE = "13060"
REGIME_MA = 200
MOMENTUM_ID = "momentum"
MOMENTUM_WEIGHTS = (("ret_60", 0.4), ("ret_120", 0.3), ("high_ratio", 0.3))
PATTERN_SCORE_IDS = frozenset({"doublebottom", "invheadshoulders", "candles"})

DEFAULT_SELECTIONS: list[dict[str, Any]] = [
    {"id": "ema", "params": {"length": 25}},
    {"id": "rsi", "params": {"length": 14}},
]


def boost_fingerprint(selections: list[dict[str, Any]], settings: Any | None = None) -> str:
    ids = {str(item.get("id") or "") for item in selections}
    parts: list[str] = []
    if "boost" in ids:
        try:
            from skabu.model import model_fingerprint

            parts.append(model_fingerprint(settings) or "")
        except Exception:
            get_logger().exception("model fingerprint failed")
    return "|".join(parts)


def lookback_len(selections: list[dict[str, Any]], filters: list[dict[str, Any]] | None = None) -> int:
    need = 260
    for item in list(selections) + list(filters or []):
        plugin_id = str(item.get("id") or "")
        params = item.get("params") or {}
        if plugin_id == "boost":
            need = max(need, 260)
            continue
        if plugin_id in {MOMENTUM_ID, REGIME_ID}:
            need = max(need, 260)
            continue
        if plugin_id == "facts":
            continue
        if plugin_id == "weekly":
            need = max(need, 260)
            continue
        if plugin_id == "candles":
            need = max(need, 20)
            continue
        if plugin_id in {
            "doubletop",
            "doublebottom",
            "downtrend",
            "uptrend",
            "headshoulders",
            "invheadshoulders",
        }:
            need = max(need, 90)
            continue
        if plugin_id == "elliott":
            try:
                lookback = int(float(params.get("lookback", 150)))
            except (TypeError, ValueError):
                lookback = 150
            need = max(need, lookback + 20)
            continue
        if plugin_id == "macd":
            try:
                slow = int(float(params.get("slow", 26)))
                signal = int(float(params.get("signal", 9)))
            except (TypeError, ValueError):
                slow, signal = 26, 9
            need = max(need, slow + signal + 10)
            continue
        if plugin_id == "ichimoku":
            try:
                senkou_b = int(float(params.get("senkou_b", 52)))
                displacement = int(float(params.get("displacement", 26)))
            except (TypeError, ValueError):
                senkou_b, displacement = 52, 26
            need = max(need, senkou_b + displacement + 10)
            continue
        if plugin_id == "stoch":
            try:
                k_len = int(float(params.get("k_len", 14)))
                k_smooth = int(float(params.get("k_smooth", 3)))
                d_len = int(float(params.get("d_len", 3)))
            except (TypeError, ValueError):
                k_len, k_smooth, d_len = 14, 3, 3
            need = max(need, k_len + k_smooth + d_len + 10)
            continue
        try:
            length = int(float(params.get("length", 25)))
        except (TypeError, ValueError):
            length = 25
        need = max(need, length + 20)
    return min(need, 320)


def normalize_selections(selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for item in selections:
        plugin_id = str(item.get("id") or "").strip()
        plugin = get_plugin(plugin_id)
        params = dict(item.get("params") or {})
        if plugin is not None:
            params = coerce_params(plugin.params, params)
        normalized.append({"id": plugin_id, "params": params})
    return normalized


def cache_key(config: dict[str, Any]) -> str:
    raw = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _reason(plugin_id: str, features: dict[str, Any]) -> str:
    parts: list[str] = []
    if plugin_id in {"sma", "ema"}:
        if features.get("price_above") == 1:
            parts.append("価格>MA")
        elif features.get("price_above") == 0:
            parts.append("価格<MA")
    elif plugin_id == "rsi":
        value = features.get("value")
        if value is not None:
            parts.append(f"RSI{value:.0f}")
        if features.get("above_50") == 1:
            parts.append("50超")
        elif features.get("above_50") == 0:
            parts.append("50未満")
    elif plugin_id == "macd":
        if features.get("above_signal") == 1:
            parts.append("シグナル上")
        elif features.get("above_signal") == 0:
            parts.append("シグナル下")
    elif plugin_id == "adx":
        value = features.get("value")
        if value is not None:
            parts.append(f"ADX{value:.0f}")
        if features.get("plus_above") == 1:
            parts.append("+DI優位")
        elif features.get("plus_above") == 0:
            parts.append("-DI優位")
    elif plugin_id == "boost":
        proba = features.get("proba")
        if proba is not None:
            parts.append(f"P{proba * 100:.0f}")
    elif plugin_id == "atr":
        if features.get("expanding") == 1:
            parts.append("拡大")
        elif features.get("expanding") == 0:
            parts.append("縮小")
    elif plugin_id == "bbands":
        if features.get("outside") == 1 and features.get("price_above") == 1:
            parts.append("バンド上抜け")
        elif features.get("outside") == 1 and features.get("price_above") == 0:
            parts.append("バンド下抜け")
        elif features.get("price_above") == 1:
            parts.append("中心より上")
        elif features.get("price_above") == 0:
            parts.append("中心より下")
    elif plugin_id == "stoch":
        value = features.get("value")
        if value is not None:
            parts.append(f"%K{value:.0f}")
        if features.get("k_above_d") == 1:
            parts.append("K>D")
        elif features.get("k_above_d") == 0:
            parts.append("K<D")
    elif plugin_id == "volma":
        if features.get("above_ma") == 1:
            parts.append("出来高>平均")
        elif features.get("above_ma") == 0:
            parts.append("出来高<平均")
    elif plugin_id == "supertrend":
        if features.get("uptrend") == 1:
            parts.append("上昇")
        elif features.get("uptrend") == 0:
            parts.append("下降")
    elif plugin_id == "ichimoku":
        if features.get("above_cloud") == 1:
            parts.append("雲の上")
        elif features.get("above_cloud") == 0:
            parts.append("雲の下")
        if features.get("tenkan_above") == 1:
            parts.append("転換>基準")
        elif features.get("tenkan_above") == 0:
            parts.append("転換<基準")
    elif plugin_id == MOMENTUM_ID:
        ratio = features.get("high_ratio")
        if ratio is not None:
            parts.append(f"高値比{float(ratio):.2f}")
        ret_60 = features.get("ret_60")
        if ret_60 is not None:
            parts.append(f"60日{float(ret_60) * 100:.1f}%")
    return "・".join(parts)


def score_issue(
    bars: list[dict[str, Any]],
    selections: list[dict[str, Any]],
    *,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> dict[str, Any]:
    last = bars[-1] if bars else {}
    levels = compute_levels(bars, stop_mult=stop_mult, target_mult=target_mult)
    if not selections:
        return {
            "total": 1,
            "scores": {},
            "proba": None,
            "reason": "フィルター通過",
            "close": last.get("close"),
            "volume": last.get("volume"),
            "bar_count": len(bars),
            **levels,
        }
    results = compute_indicators(bars, selections, features_only=True)
    total = 0
    scored = 0
    scores: dict[str, Any] = {}
    reasons: list[str] = []
    pattern_max: int | None = None
    for item in results:
        plugin_id = item.get("id")
        if item.get("error"):
            scores[plugin_id] = None
            continue
        feats = item.get("features") or {}
        if plugin_id == MOMENTUM_ID:
            scores[plugin_id] = feats.get("high_ratio")
            if feats.get("high_ratio") is None:
                continue
            scored += 1
            bit = _reason(str(plugin_id), feats)
            title = item.get("title") or plugin_id
            reasons.append(f"{title}: {bit}" if bit else title)
            continue
        value = feats.get("score")
        scores[plugin_id] = value
        if value is None:
            continue
        ivalue = int(value)
        scored += 1
        bit = _reason(str(plugin_id), feats)
        title = item.get("title") or plugin_id
        if bit:
            reasons.append(f"{title}: {bit}")
        else:
            reasons.append(f"{title}: {ivalue:+d}")
        if plugin_id in PATTERN_SCORE_IDS:
            pattern_max = ivalue if pattern_max is None else max(pattern_max, ivalue)
            continue
        total += ivalue
    if pattern_max is not None:
        total += pattern_max
    boost_proba = None
    for item in results:
        if item.get("id") != "boost" or item.get("error"):
            continue
        raw = (item.get("features") or {}).get("proba")
        if raw is not None:
            boost_proba = float(raw)
    momentum_feats = None
    for item in results:
        if item.get("id") == MOMENTUM_ID and not item.get("error"):
            momentum_feats = item.get("features") or {}
    return {
        "total": total if scored else None,
        "scores": scores,
        "proba": boost_proba,
        "reason": " / ".join(reasons),
        "close": last.get("close"),
        "volume": last.get("volume"),
        "bar_count": len(bars),
        "momentum": momentum_feats,
        **levels,
    }


_FILTER_OPS = {
    "eq": lambda left, right: left == right,
    "lt": lambda left, right: left < right,
    "lte": lambda left, right: left <= right,
    "gt": lambda left, right: left > right,
    "gte": lambda left, right: left >= right,
}


def filter_compare(value: Any, spec: dict[str, Any]) -> bool:
    op = str(spec.get("op") or "eq")
    expected = spec["value"] if "value" in spec else spec.get("equals", 1)
    if value is None or expected is None:
        return False
    compare = _FILTER_OPS.get(op)
    if compare is None:
        get_logger().warning("unknown filter op=%s", op)
        return False
    if op == "eq":
        try:
            return float(value) == float(expected)
        except (TypeError, ValueError):
            return value == expected
    try:
        return bool(compare(float(value), float(expected)))
    except (TypeError, ValueError):
        return False


def stock_filters(filters: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [item for item in (filters or []) if str(item.get("id") or "") != REGIME_ID]


def has_regime(filters: list[dict[str, Any]] | None) -> bool:
    return any(str(item.get("id") or "") == REGIME_ID for item in (filters or []))


def has_momentum(selections: list[dict[str, Any]] | None) -> bool:
    return any(str(item.get("id") or "") == MOMENTUM_ID for item in (selections or []))


def pct_ranks(values: list[Any]) -> list[float | None]:
    ranked = [(index, float(value)) for index, value in enumerate(values) if value is not None]
    out: list[float | None] = [None] * len(values)
    if not ranked:
        return out
    if len(ranked) == 1:
        out[ranked[0][0]] = 1.0
        return out
    ranked.sort(key=lambda item: item[1])
    count = len(ranked)
    cursor = 0
    while cursor < count:
        end = cursor
        while end + 1 < count and ranked[end + 1][1] == ranked[cursor][1]:
            end += 1
        avg = (cursor + end) / 2.0
        share = avg / (count - 1)
        for pos in range(cursor, end + 1):
            out[ranked[pos][0]] = share
        cursor = end + 1
    return out


def momentum_points(composite: float) -> int:
    return max(-2, min(2, int(round((float(composite) - 0.5) * 4))))


def apply_momentum_ranks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cols = {
        name: [(row.get("momentum") or {}).get(name) for row in rows] for name, _weight in MOMENTUM_WEIGHTS
    }
    ranks = {name: pct_ranks(values) for name, values in cols.items()}
    kept: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        composite = 0.0
        ready = True
        for name, weight in MOMENTUM_WEIGHTS:
            value = ranks[name][index]
            if value is None:
                ready = False
                break
            composite += weight * value
        if not ready:
            continue
        item = dict(row)
        mom = momentum_points(composite)
        base = row.get("total")
        item["total"] = (0 if base is None else int(base)) + mom
        scores = dict(item.get("scores") or {})
        scores[MOMENTUM_ID] = mom
        item["scores"] = scores
        feats = item.get("momentum") or {}
        bit = (
            f"高値モメンタム: 高値比{feats.get('high_ratio')} / "
            f"60日{feats.get('ret_60')} / 120日{feats.get('ret_120')}"
        )
        prior = str(row.get("reason") or "").strip()
        item["reason"] = f"{prior} / {bit}" if prior else bit
        kept.append(item)
    return kept


def regime_above_ma(
    conn: Any,
    as_of: str,
    *,
    length: int = REGIME_MA,
    code: str = REGIME_CODE,
) -> dict[str, Any]:
    dates = recent_bar_dates(conn, max(length + 2, 5), as_of)
    bars: list[dict[str, Any]] = []
    if dates:
        bars = load_daily_bars_since(conn, dates[0], as_of, [code]).get(code) or []
    closes = [float(item["close"]) for item in bars if item.get("close") is not None]
    last = closes[-1] if closes else None
    mean = None
    above = None
    reason = "no_bars"
    if last is not None and len(closes) >= length:
        mean = sum(closes[-length:]) / length
        above = int(last > mean)
        reason = "above" if above else "below"
    elif last is not None:
        reason = "short"
    payload = {
        "as_of": as_of,
        "code": code,
        "close": last,
        "ma": None if mean is None else round(mean, 6),
        "above": above,
        "n": len(closes),
        "length": length,
        "reason": reason,
        "ok": above == 1,
    }
    get_logger().info(
        "regime as_of=%s close=%s ma=%s above=%s n=%s reason=%s",
        as_of,
        last,
        payload["ma"],
        above,
        len(closes),
        reason,
    )
    return payload


def evaluate_regime(conn: Any, as_of: str, filters: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    specs = [item for item in (filters or []) if str(item.get("id") or "") == REGIME_ID]
    if not specs:
        return None
    spec = specs[0]
    params = spec.get("params") or {}
    try:
        length = int(float(params.get("length", REGIME_MA)))
    except (TypeError, ValueError):
        length = REGIME_MA
    state = regime_above_ma(conn, as_of, length=max(2, length))
    expected = spec["value"] if "value" in spec else spec.get("equals", 1)
    state["ok"] = bool(state.get("above") is not None and filter_compare(state["above"], {**spec, "value": expected}))
    return state


def fact_snapshot(
    bars: list[dict[str, Any]],
    meta: dict[str, Any] | None = None,
    stored: dict[str, Any] | None = None,
) -> dict[str, Any]:
    last = bars[-1] if bars else {}
    facts = dict(stored or {})
    if facts.get("mkt_cap") is None and last.get("mkt_cap") is not None:
        facts["mkt_cap"] = last.get("mkt_cap")
    if facts.get("avg_vo") is None and (meta or {}).get("avg_vo") is not None:
        facts["avg_vo"] = meta.get("avg_vo")
    close = last.get("close")
    div_ann = facts.get("div_ann")
    yield_v = None
    try:
        close_n = None if close is None else float(close)
        div_n = None if div_ann is None else float(div_ann)
        if close_n is None or close_n == 0 or div_n is None:
            if div_n is not None:
                get_logger().debug("div_yield skip close=%s div_ann=%s", close, div_ann)
        else:
            yield_v = div_n / close_n
    except (TypeError, ValueError):
        get_logger().debug("div_yield bad close=%s div_ann=%s", close, div_ann)
    facts["div_yield"] = yield_v
    facts["div_yield_hi"] = yield_v
    return facts


def passes_filters(
    bars: list[dict[str, Any]],
    filters: list[dict[str, Any]] | None,
    facts: dict[str, Any] | None = None,
) -> bool:
    gates = stock_filters(filters)
    if not gates:
        return True
    plugin_gates = [item for item in gates if not is_fact_gate(item) and not is_weekly_gate(item)]
    fact_gates = [item for item in gates if is_fact_gate(item)]
    weekly_gates = [item for item in gates if is_weekly_gate(item)]
    if plugin_gates:
        selections = [{"id": item.get("id"), "params": item.get("params") or {}} for item in plugin_gates]
        results = compute_indicators(bars, selections, features_only=True)
        by_id = {item.get("id"): item for item in results}
        for spec in plugin_gates:
            plugin_id = spec.get("id")
            item = by_id.get(plugin_id)
            if item is None or item.get("error"):
                return False
            value = (item.get("features") or {}).get(spec.get("feature"))
            if not filter_compare(value, spec):
                return False
    if weekly_gates:
        snap = weekly_features(bars)
        for spec in weekly_gates:
            feature = str(spec.get("feature") or "")
            value = snap.get(feature)
            if filter_compare(value, spec):
                continue
            reason_key = f"{feature}_reason"
            log = get_logger().info if value is None else get_logger().debug
            log(
                "weekly gate fail feature=%s value=%s op=%s want=%s weeks=%s reason=%s",
                feature,
                value,
                spec.get("op"),
                spec.get("value", spec.get("equals")),
                snap.get("weeks"),
                snap.get(reason_key),
            )
            return False
    packed = facts or {}
    for spec in fact_gates:
        value = packed.get(spec.get("feature"))
        if not filter_compare(value, spec):
            get_logger().debug(
                "fact gate fail feature=%s value=%s op=%s want=%s",
                spec.get("feature"),
                value,
                spec.get("op"),
                spec.get("value", spec.get("equals")),
            )
            return False
    return True


def refresh_cached_buys(
    payload: dict[str, Any],
    *,
    holdout_ok: bool,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> dict[str, Any]:
    """保存済みの見送りを信用せず、今の買い判定をかけ直す。"""
    rows = apply_sector_cap(
        annotate_buys(
            list(payload.get("rows") or []),
            holdout_ok=holdout_ok,
            stop_mult=stop_mult,
            target_mult=target_mult,
        ),
        max_per_sector=int(BUY_SPEC["max_per_sector"]),
    )
    payload["rows"] = rows
    payload["bought_n"] = sum(1 for item in rows if item.get("buyable"))
    payload["cached"] = True
    get_logger().info(
        "ranking cache reannotated rows=%s bought=%s holdout_ok=%s",
        len(rows),
        payload["bought_n"],
        holdout_ok,
    )
    return payload


def build_ranking(
    conn: Any,
    *,
    selections: list[dict[str, Any]] | None = None,
    filters: list[dict[str, Any]] | None = None,
    market: str = "プライム",
    min_avg_volume: float = 50_000,
    min_bars: int = 60,
    top: int = 50,
    as_of: str | None = None,
    refresh: bool = False,
    holdout_ok: bool = True,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
    settings: Any | None = None,
) -> dict[str, Any]:
    picks = normalize_selections(default_rank_selections() if selections is None else selections)
    gates = filters or []
    if not picks:
        get_logger().info("ranking flat-score filters=%s (empty selections, not default)", len(gates))
    as_of_date = latest_bar_date(conn, as_of)
    if not as_of_date:
        return {
            "as_of": None,
            "universe": 0,
            "gate_passed": 0,
            "scored": 0,
            "rows": [],
            "skipped": 0,
            "error": "no_bars",
        }
    lookback = lookback_len(picks, gates)
    dates = recent_bar_dates(conn, lookback, as_of_date)
    from_date = dates[0] if dates else as_of_date
    config = {
        "as_of": as_of_date,
        "market": market,
        "min_avg_volume": min_avg_volume,
        "min_bars": min_bars,
        "top": top,
        "from_date": from_date,
        "selections": picks,
        "filters": gates,
        "model_fp": boost_fingerprint(picks, settings),
        "levels": levels_spec(stop_mult, target_mult),
        "buys": BUY_SPEC,
        "holdout_ok": bool(holdout_ok),
        "score_scale": 2,
    }
    key = cache_key(config)
    if not refresh:
        cached = get_ranking_cache(conn, key)
        if cached:
            get_logger().info("ranking cache hit key=%s as_of=%s", key[:12], as_of_date)
            return refresh_cached_buys(
                cached,
                holdout_ok=holdout_ok,
                stop_mult=stop_mult,
                target_mult=target_mult,
            )

    started = time.perf_counter()
    regime = evaluate_regime(conn, as_of_date, gates)
    if regime is not None and not regime.get("ok"):
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        payload = {
            "as_of": as_of_date,
            "from_date": from_date,
            "market": market,
            "min_avg_volume": min_avg_volume,
            "min_bars": min_bars,
            "selections": picks,
            "filters": gates,
            "universe": 0,
            "gate_passed": 0,
            "scored": 0,
            "skipped": 0,
            "errors": 0,
            "elapsed_ms": elapsed_ms,
            "cached": False,
            "rows": [],
            "all_rows": 0,
            "levels_note": LEVELS_NOTE,
            "holdout_ok": bool(holdout_ok),
            "bought_n": 0,
            "regime": regime,
            "cash": True,
        }
        put_ranking_cache(conn, key, as_of_date, config, payload, elapsed_ms)
        get_logger().info(
            "ranking cash as_of=%s close=%s ma=%s reason=%s ms=%s",
            as_of_date,
            regime.get("close"),
            regime.get("ma"),
            regime.get("reason"),
            elapsed_ms,
        )
        return payload
    universe = list_universe(
        conn,
        market=market,
        min_avg_volume=min_avg_volume,
        min_bars=min(min_bars, 20),
        vol_days=20,
        as_of=as_of_date,
    )
    codes = [item["code"] for item in universe]
    by_code = load_daily_bars_since(conn, from_date, as_of_date, codes)
    meta = {item["code"]: item for item in universe}
    stored_facts = load_facts_as_of(conn, as_of_date, codes) if has_fact_gates(gates) else {}
    rows: list[dict[str, Any]] = []
    skipped = 0
    errors = 0
    gate_passed = 0
    for code in codes:
        bars = by_code.get(code) or []
        if len(bars) < min_bars:
            skipped += 1
            continue
        try:
            facts = fact_snapshot(bars, meta.get(code), stored_facts.get(code))
            if not passes_filters(bars, gates, facts):
                skipped += 1
                continue
            gate_passed += 1
            scored = score_issue(bars, picks, stop_mult=stop_mult, target_mult=target_mult)
        except Exception:
            get_logger().exception("ranking failed code=%s", code)
            errors += 1
            continue
        if has_momentum(picks):
            feats = scored.get("momentum") or {}
            if feats.get("high_ratio") is None or feats.get("ret_60") is None or feats.get("ret_120") is None:
                skipped += 1
                continue
        elif scored["total"] is None:
            skipped += 1
            continue
        info = meta[code]
        rows.append(
            {
                "code": code,
                "co_name": info.get("co_name"),
                "mkt_nm": info.get("mkt_nm"),
                "s33": info.get("s33"),
                "s33_nm": info.get("s33_nm"),
                "avg_vo": info.get("avg_vo"),
                **scored,
            }
        )
    if has_momentum(picks):
        rows = apply_momentum_ranks(rows)
    rows.sort(key=lambda item: (-int(item["total"]), -float(item.get("avg_vo") or 0), item["code"]))
    for index, item in enumerate(rows, start=1):
        item["rank"] = index
    rows = apply_sector_cap(
        annotate_buys(rows, holdout_ok=holdout_ok, stop_mult=stop_mult, target_mult=target_mult),
        max_per_sector=int(BUY_SPEC["max_per_sector"]),
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    payload = {
        "as_of": as_of_date,
        "from_date": from_date,
        "market": market,
        "min_avg_volume": min_avg_volume,
        "min_bars": min_bars,
        "selections": picks,
        "filters": gates,
        "universe": len(universe),
        "gate_passed": gate_passed,
        "scored": len(rows),
        "skipped": skipped,
        "errors": errors,
        "elapsed_ms": elapsed_ms,
        "cached": False,
        "rows": rows[: max(1, top)],
        "all_rows": len(rows),
        "levels_note": LEVELS_NOTE,
        "holdout_ok": bool(holdout_ok),
        "bought_n": sum(1 for item in rows[: max(1, top)] if item.get("buyable")),
        "regime": regime,
        "cash": False,
    }
    put_ranking_cache(conn, key, as_of_date, config, payload, elapsed_ms)
    get_logger().info(
        "ranking done as_of=%s universe=%s gate_passed=%s scored=%s skipped=%s errors=%s ms=%s",
        as_of_date,
        len(universe),
        gate_passed,
        len(rows),
        skipped,
        errors,
        elapsed_ms,
    )
    return payload


def default_strategy_label() -> tuple[str, str, str]:
    """採用フォールバックの id、表示名、ログ用の短い名前。"""
    return "ema_rsi", "EMA + RSI", "ema+rsi"


def default_rank_selections() -> list[dict[str, Any]]:
    return normalize_selections(DEFAULT_SELECTIONS)


def selection_titles(selections: list[dict[str, Any]]) -> list[str]:
    titles = []
    for item in selections:
        plugin = get_plugin(str(item.get("id") or ""))
        titles.append(plugin.title if plugin else str(item.get("id")))
    return titles
