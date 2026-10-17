"""ロック済み予測を採点し、条件を満たしたときだけ Boost を入れ替える。"""

from __future__ import annotations

import json
import re
import shutil
import threading
from typing import Any

from skabu.config import Settings
from skabu.levels import bars_from_prices, compute_levels, exit_pair, path_from_prices, simulate_trade
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.model import (
    CANDIDATE_NAME,
    active_feature_selections,
    MODEL_NAME,
    PREV_NAME,
    extract_features,
    latest_model_payload,
    meta_path,
    model_fingerprint,
    model_path,
    train_model,
    use_model,
)
from skabu.ranking import lookback_len
from skabu.store import (
    all_bar_dates,
    connect,
    count_outcomes,
    finish_model_run,
    insert_outcome,
    delete_outcomes,
    latest_learn_run,
    list_model_runs,
    list_outcome_as_ofs,
    list_outcome_probas,
    list_prediction_runs,
    load_daily_bars_since,
    load_price_map,
    outcome_exists,
    outcome_period_stats,
    recent_bar_dates,
)
from skabu.verify import BENCH_CODE, _ret, run_verify

HORIZON = 20
MIN_NEW_AS_OFS = 3
MIN_DAYS_SINCE_TRAIN = 20
AUC_SLACK = 0.01
_PROBA_RE = re.compile(r"P(\d{1,3})")
_GRADE_LOCK = threading.Lock()
_LEARN_LOCK = threading.Lock()


def grade_outcomes(
    settings: Settings,
    *,
    horizon: int = HORIZON,
    bench_code: str = BENCH_CODE,
    rebuild: bool = False,
) -> dict[str, Any]:
    with _GRADE_LOCK:
        return _grade_outcomes_body(settings, horizon=horizon, bench_code=bench_code, rebuild=rebuild)


def _grade_outcomes_body(
    settings: Settings,
    *,
    horizon: int,
    bench_code: str,
    rebuild: bool = False,
) -> dict[str, Any]:
    log = get_logger()
    stop_mult, target_mult = exit_pair(settings)
    with connect(settings.db_path) as conn:
        if rebuild:
            deleted = delete_outcomes(conn)
            log.info("grade rebuild deleted=%s", deleted)
        locks = list_prediction_runs(conn)
        dates = all_bar_dates(conn)
        if not locks or not dates:
            payload = {
                "status": "ok",
                "graded": 0,
                "pending_runs": 0,
                "skipped": 0,
                "errors": 0,
                "as_ofs": [],
                "pending_as_ofs": [],
            }
            log.info("grade skip locks=%s dates=%s", len(locks), len(dates))
            return payload
        index = {day: i for i, day in enumerate(dates)}
        pending_as_ofs: list[str] = []
        ready: list[dict[str, Any]] = []
        codes: set[str] = {bench_code}
        skipped = 0
        for run in locks:
            as_of = run["as_of"]
            if as_of not in index:
                pending_as_ofs.append(as_of)
                continue
            exit_i = index[as_of] + 1 + horizon
            if exit_i >= len(dates):
                pending_as_ofs.append(as_of)
                continue
            rows = run.get("rows") or []
            if count_outcomes(conn, run["strategy_key"], as_of) >= len(rows):
                skipped += len(rows)
                continue
            ready.append(run)
            for row in rows:
                code = row.get("code")
                if code:
                    codes.add(str(code))
        prices = load_price_map(conn, codes, dates[0], dates[-1]) if ready else {}
        lookback = lookback_len(active_feature_selections())
        graded = 0
        errors = 0
        graded_as_ofs: set[str] = set()
        for run in ready:
            as_of = run["as_of"]
            pred_i = index[as_of]
            entry_i = pred_i + 1
            exit_i = entry_i + horizon
            entry_date = dates[entry_i]
            exit_date = dates[exit_i]
            window = recent_bar_dates(conn, lookback, as_of)
            row_codes = [str(item.get("code")) for item in (run.get("rows") or []) if item.get("code")]
            by_code = {}
            if window and row_codes:
                by_code = load_daily_bars_since(conn, window[0], as_of, row_codes)
            bench = prices.get(bench_code, {})
            bench_ret = _ret(
                (bench.get(entry_date) or {}).get("open"),
                (bench.get(exit_date) or {}).get("close"),
            )
            model_fp = str((run.get("config") or {}).get("model_fp") or "")
            for row in run.get("rows") or []:
                code = str(row.get("code") or "")
                if not code:
                    skipped += 1
                    continue
                if outcome_exists(conn, run["strategy_key"], as_of, code):
                    skipped += 1
                    continue
                try:
                    series = prices.get(code, {})
                    entry = (series.get(entry_date) or {}).get("open")
                    time_exit = (series.get(exit_date) or {}).get("close")
                    time_ret = _ret(entry, time_exit)
                    time_excess = None if time_ret is None or bench_ret is None else time_ret - bench_ret
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
                    touch_ret = trade.get("ret")
                    matched_bench = _ret(
                        (bench.get(entry_date) or {}).get("open"),
                        (bench.get(trade.get("exit_date") or exit_date) or {}).get("close"),
                    )
                    touch_excess = None if touch_ret is None or matched_bench is None else touch_ret - matched_bench
                    if time_ret is None and touch_ret is None:
                        skipped += 1
                        continue
                    feats = None
                    bars = by_code.get(code) or []
                    if bars:
                        feats = extract_features(bars)
                    inserted = insert_outcome(
                        conn,
                        {
                            "strategy_key": run["strategy_key"],
                            "as_of": as_of,
                            "code": code,
                            "rank": row.get("rank"),
                            "proba": _row_proba(row),
                            "y_time": None if time_excess is None else int(time_excess > 0),
                            "y_touch": None if touch_ret is None else int(touch_ret > 0),
                            "excess": None if touch_excess is None else round(touch_excess, 6),
                            "time_excess": None if time_excess is None else round(time_excess, 6),
                            "ret": None if touch_ret is None else round(touch_ret, 6),
                            "bench_ret": None if matched_bench is None else round(matched_bench, 6),
                            "exit_reason": trade.get("reason"),
                            "exit_date": trade.get("exit_date"),
                            "stop": trade.get("stop"),
                            "target": trade.get("target"),
                            "features": feats,
                            "model_fp": model_fp or model_fingerprint(settings),
                        },
                    )
                    if inserted:
                        graded += 1
                        graded_as_ofs.add(as_of)
                    else:
                        skipped += 1
                except Exception:
                    errors += 1
                    log.exception("grade failed as_of=%s code=%s", as_of, code)
                    dump_debug_event(
                        settings.debug_dir,
                        {"kind": "grade_row", "as_of": as_of, "code": code},
                    )
        pending_as_ofs = sorted(set(pending_as_ofs))
        payload = {
            "status": "ok",
            "graded": graded,
            "pending_runs": len(pending_as_ofs),
            "skipped": skipped,
            "errors": errors,
            "as_ofs": sorted(graded_as_ofs),
            "pending_as_ofs": pending_as_ofs,
            "total": count_outcomes(conn),
        }
        log.info(
            "grade done graded=%s skipped=%s pending=%s errors=%s total=%s",
            graded,
            skipped,
            len(pending_as_ofs),
            errors,
            payload["total"],
        )
        return payload


def learn_status(settings: Settings) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        periods = outcome_period_stats(conn)
        graded_as_ofs = list_outcome_as_ofs(conn)
        locks = list_prediction_runs(conn)
        dates = all_bar_dates(conn)
        last_learn = latest_learn_run(conn)
        history = list_model_runs(conn, 20)
        trigger = should_retrain(conn, last_learn=last_learn, dates=dates, graded_as_ofs=graded_as_ofs)
        pending_as_ofs = _pending_as_ofs(locks, dates, HORIZON)
        recent = periods[-5:] if periods else []
        recent_excesses = [item["avg_excess"] for item in recent if item.get("avg_excess") is not None]
        calibration = _calibration(list_outcome_probas(conn))
        model = latest_model_payload(settings)
    return {
        "graded": sum(item["n"] for item in periods),
        "graded_as_ofs": graded_as_ofs,
        "pending_as_ofs": pending_as_ofs,
        "pending_runs": len(pending_as_ofs),
        "last_graded_as_of": graded_as_ofs[-1] if graded_as_ofs else None,
        "rolling": periods,
        "top20_excess_recent": None if not recent_excesses else round(sum(recent_excesses) / len(recent_excesses), 6),
        "calibration": calibration,
        "trigger": trigger,
        "last_learn": last_learn,
        "history": history,
        "ready": bool(model.get("ready")),
        "model": {
            "ready": model.get("ready"),
            "path": model.get("path"),
            "meta": model.get("meta"),
            "last": model.get("last"),
        },
    }


def should_retrain(
    conn: Any,
    *,
    last_learn: dict[str, Any] | None,
    dates: list[str],
    graded_as_ofs: list[str],
    min_as_ofs: int = MIN_NEW_AS_OFS,
    min_days: int = MIN_DAYS_SINCE_TRAIN,
    force: bool = False,
) -> dict[str, Any]:
    marker = _learn_marker(last_learn)
    new_as_ofs = [day for day in graded_as_ofs if marker is None or day > marker]
    days_since = 0 if marker is None else len([day for day in dates if day > marker])
    ready = force or len(new_as_ofs) >= min_as_ofs or (marker is not None and days_since >= min_days)
    reason = "force" if force else None
    if reason is None and len(new_as_ofs) >= min_as_ofs:
        reason = "new_as_ofs"
    elif reason is None and marker is not None and days_since >= min_days:
        reason = "days_since_train"
    elif reason is None:
        reason = "held"
    return {
        "ready": ready,
        "reason": reason,
        "new_as_ofs": new_as_ofs,
        "new_as_of_count": len(new_as_ofs),
        "need_as_ofs": min_as_ofs,
        "days_since_train": days_since,
        "need_days": min_days,
        "marker": marker,
    }


def decide_promotion(
    *,
    production: dict[str, Any] | None,
    candidate: dict[str, Any],
    auc_slack: float = AUC_SLACK,
) -> dict[str, Any]:
    cand_excess = candidate.get("avg_excess")
    cand_auc = candidate.get("auc")
    prod_excess = None if production is None else production.get("avg_excess")
    prod_auc = None if production is None else production.get("auc")
    if cand_excess is None:
        return _verdict(False, "candidate_excess_missing", production, candidate)
    if cand_excess <= 0:
        return _verdict(False, "candidate_holdout_not_positive", production, candidate)
    if production is not None and prod_excess is not None and cand_excess <= prod_excess:
        return _verdict(False, "candidate_not_better_excess", production, candidate)
    if production is not None and prod_auc is not None and cand_auc is not None:
        if cand_auc < float(prod_auc) - auc_slack:
            return _verdict(False, "candidate_auc_worse", production, candidate)
    return _verdict(True, "better", production, candidate)


def maybe_learn(
    settings: Settings,
    *,
    force: bool = False,
    horizon: int = HORIZON,
    market: str = "プライム",
    min_avg_volume: float = 50_000,
    min_bars: int = 60,
    stride: int = 20,
    min_history: int = 80,
    min_samples: int = 20,
    top: int = 20,
) -> dict[str, Any]:
    with _LEARN_LOCK:
        return _maybe_learn_body(
            settings,
            force=force,
            horizon=horizon,
            market=market,
            min_avg_volume=min_avg_volume,
            min_bars=min_bars,
            stride=stride,
            min_history=min_history,
            min_samples=min_samples,
            top=top,
        )


def _maybe_learn_body(
    settings: Settings,
    *,
    force: bool,
    horizon: int,
    market: str,
    min_avg_volume: float,
    min_bars: int,
    stride: int,
    min_history: int,
    min_samples: int,
    top: int,
) -> dict[str, Any]:
    log = get_logger()
    with connect(settings.db_path) as conn:
        trigger = should_retrain(
            conn,
            last_learn=latest_learn_run(conn),
            dates=all_bar_dates(conn),
            graded_as_ofs=list_outcome_as_ofs(conn),
            force=force,
        )
    if not trigger["ready"]:
        log.info("learn held reason=%s new=%s days=%s", trigger["reason"], trigger["new_as_of_count"], trigger["days_since_train"])
        return {"status": "held", "reason": trigger["reason"], "trigger": trigger}
    try:
        trained = train_model(
            settings,
            market=market,
            min_avg_volume=min_avg_volume,
            min_bars=min_bars,
            stride=stride,
            min_history=min_history,
            horizon=horizon,
            min_samples=min_samples,
            name=CANDIDATE_NAME,
        )
    except Exception as exc:
        log.exception("candidate train failed")
        dump_debug_event(settings.debug_dir, {"kind": "learn_train", "error": str(exc)})
        return {"status": "failed", "reason": "train_failed", "error": str(exc), "trigger": trigger}
    cand_auc = ((trained.get("meta") or {}).get("metrics") or {}).get("valid_auc")
    try:
        production = None
        if model_path(settings, MODEL_NAME).exists():
            production = _evaluate_boost(
                settings,
                name=MODEL_NAME,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                top=top,
                stride=stride,
            )
            production["auc"] = _production_auc(settings)
        candidate = _evaluate_boost(
            settings,
            name=CANDIDATE_NAME,
            market=market,
            min_avg_volume=min_avg_volume,
            min_bars=min_bars,
            top=top,
            stride=stride,
        )
        candidate["auc"] = cand_auc
        verdict = decide_promotion(production=production, candidate=candidate)
    except Exception as exc:
        log.exception("learn compare failed")
        dump_debug_event(settings.debug_dir, {"kind": "learn_compare", "error": str(exc)})
        _stamp_run(settings, trained.get("id"), "skipped", {**(trained.get("meta") or {}), "learn": {"error": str(exc)}})
        return {
            "status": "failed",
            "reason": "compare_failed",
            "error": str(exc),
            "trigger": trigger,
            "train": _slim_train(trained),
        }
    meta = {**(trained.get("meta") or {}), "learn": {"trigger": trigger, "verdict": verdict}}
    if verdict["promote"]:
        promote_candidate(settings)
        _stamp_run(settings, trained.get("id"), "ok", meta)
        log.info(
            "learn promoted cand_excess=%s prod_excess=%s auc=%s",
            candidate.get("avg_excess"),
            None if production is None else production.get("avg_excess"),
            cand_auc,
        )
        return {
            "status": "promoted",
            "reason": verdict["reason"],
            "trigger": trigger,
            "verdict": verdict,
            "train": _slim_train(trained),
        }
    _stamp_run(settings, trained.get("id"), "skipped", meta)
    log.info("learn skipped reason=%s cand_excess=%s", verdict["reason"], candidate.get("avg_excess"))
    return {
        "status": "skipped",
        "reason": verdict["reason"],
        "trigger": trigger,
        "verdict": verdict,
        "train": _slim_train(trained),
    }


def promote_candidate(settings: Settings) -> dict[str, str]:
    settings.ensure_dirs()
    model_dir = settings.skabu_data_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    cand = model_path(settings, CANDIDATE_NAME)
    cand_meta = meta_path(settings, CANDIDATE_NAME)
    if not cand.exists() or not cand_meta.exists():
        raise FileNotFoundError("候補モデルがありません")
    prod = model_path(settings, MODEL_NAME)
    prod_meta = meta_path(settings, MODEL_NAME)
    prev = model_path(settings, PREV_NAME)
    prev_meta = meta_path(settings, PREV_NAME)
    if prod.exists():
        shutil.copy2(prod, prev)
    if prod_meta.exists():
        shutil.copy2(prod_meta, prev_meta)
    shutil.copy2(cand, prod)
    shutil.copy2(cand_meta, prod_meta)
    from skabu.model import _clear_cache

    _clear_cache()
    get_logger().info("boost promoted prev=%s", prev.exists())
    return {"production": str(prod), "previous": str(prev), "candidate": str(cand)}


def _evaluate_boost(
    settings: Settings,
    *,
    name: str,
    market: str,
    min_avg_volume: float,
    min_bars: int,
    top: int,
    stride: int,
) -> dict[str, Any]:
    with use_model(name):
        with connect(settings.db_path) as conn:
            payload = run_verify(
                conn,
                selections=[{"id": "boost", "params": {}}],
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                top=top,
                stride=stride,
                refresh=True,
            )
    body = (payload.get("horizons") or {}).get("20") or {}
    summary = body.get("summary") or {}
    return {
        "avg_excess": summary.get("avg_excess"),
        "win_rate_vs_bench": summary.get("win_rate_vs_bench"),
        "avg_return": summary.get("avg_return"),
        "trades": summary.get("trades"),
        "periods": summary.get("periods"),
    }


def _production_auc(settings: Settings) -> float | None:
    path = meta_path(settings, MODEL_NAME)
    if not path.exists():
        return None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        get_logger().exception("production meta read failed")
        return None
    value = ((meta.get("metrics") or {}).get("valid_auc"))
    return None if value is None else float(value)


def _stamp_run(settings: Settings, run_id: Any, status: str, meta: dict[str, Any]) -> None:
    if run_id is None:
        return
    with connect(settings.db_path) as conn:
        finish_model_run(conn, int(run_id), status=status, meta=meta)


def _slim_train(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload.get("id"),
        "status": payload.get("status"),
        "n_train": payload.get("n_train"),
        "n_valid": payload.get("n_valid"),
        "accuracy": payload.get("accuracy"),
        "auc": payload.get("auc"),
        "model_path": payload.get("model_path"),
    }


def _verdict(
    promote: bool,
    reason: str,
    production: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "promote": promote,
        "reason": reason,
        "production": production,
        "candidate": candidate,
    }


def _learn_marker(last_learn: dict[str, Any] | None) -> str | None:
    if not last_learn:
        return None
    meta = last_learn.get("meta") or {}
    trained = str(meta.get("trained_at") or last_learn.get("finished_at") or "")
    if len(trained) >= 10 and trained[4] == "-" and trained[7] == "-":
        return trained[:10]
    return None


def _pending_as_ofs(locks: list[dict[str, Any]], dates: list[str], horizon: int) -> list[str]:
    if not dates:
        return sorted({run["as_of"] for run in locks})
    index = {day: i for i, day in enumerate(dates)}
    pending = []
    for run in locks:
        as_of = run["as_of"]
        if as_of not in index or index[as_of] + 1 + horizon >= len(dates):
            pending.append(as_of)
    return sorted(set(pending))


def _row_proba(row: dict[str, Any]) -> float | None:
    raw = row.get("proba")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    match = _PROBA_RE.search(str(row.get("reason") or ""))
    if not match:
        return None
    return min(1.0, max(0.0, int(match.group(1)) / 100.0))


def _calibration(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = [
        ("弱気 ≤0.42", 0.0, 0.42),
        ("中下 0.42-0.50", 0.42, 0.50),
        ("中上 0.50-0.58", 0.50, 0.58),
        ("強気 ≥0.58", 0.58, 1.01),
    ]
    out = []
    for label, low, high in buckets:
        picked = [item for item in rows if item.get("proba") is not None and low <= float(item["proba"]) < high]
        hits = [item for item in picked if item.get("exit_reason") == "target"]
        out.append(
            {
                "label": label,
                "n": len(picked),
                "avg_proba": None if not picked else round(sum(float(item["proba"]) for item in picked) / len(picked), 4),
                "realized_hit": None if not picked else round(len(hits) / len(picked), 4),
                "realized_time": None if not picked else round(len(hits) / len(picked), 4),
            }
        )
    return out
