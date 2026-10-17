"""引け後の日次運用。同期に失敗したら候補ファイルは触らない。"""

from __future__ import annotations

import csv
import json
import threading
from datetime import date
from pathlib import Path
from typing import Any

from skabu.config import Settings, plan_data_window
from skabu.errors import SkabuError
from skabu.gates import sanitize_gates
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.buys import (
    DEFAULT_RISK_POINTS,
    MAX_PER_SECTOR,
    SKIP_LABELS,
    active_holdout_ok,
    parse_risk_pct,
    select_buys,
    size_rows,
)
from skabu.levels import STOP_MULT, TARGET_MULT, exit_pair
from skabu.ranking import build_ranking, normalize_selections
from skabu.strategy import active_rank_filters, active_rank_selections
from skabu.store import (
    connect,
    finish_daily_run,
    latest_bar_date,
    latest_daily_run,
    start_daily_run,
)
from skabu.verify import lock_as_of

def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def daily_needs_rerun(
    report: dict[str, Any] | None,
    *,
    selections: list[dict[str, Any]] | None,
    filters: list[dict[str, Any]] | None,
    stop_mult: float | None = None,
    target_mult: float | None = None,
) -> bool:
    """確認見送りの古いバッチ、または採用中の点と門が違うときだけ再出力する。"""
    if not report:
        return False
    reasons = report.get("skip_reasons") or {}
    if reasons.get("strategy_holdout"):
        return True
    saved_picks = _json_key(normalize_selections(list(report.get("strategy") or [])))
    active_picks = _json_key(normalize_selections(list(selections or [])))
    if saved_picks != active_picks:
        return True
    if stop_mult is not None and target_mult is not None:
        saved_stop = float(report.get("stop_mult", STOP_MULT))
        saved_target = float(report.get("target_mult", TARGET_MULT))
        if saved_stop != float(stop_mult) or saved_target != float(target_mult):
            return True
    if "filters" not in report:
        return False
    saved_gates = _json_key(sanitize_gates(list(report.get("filters") or [])))
    active_gates = _json_key(sanitize_gates(list(filters or [])))
    return saved_gates != active_gates


CANDIDATES_NAME = "candidates.csv"
REPORT_NAME = "daily_report.json"
DEFAULT_CASH = 1_000_000.0
_DAILY_LOCK = threading.Lock()


def describe_freshness(plan: str, as_of: str | None, today: date | None = None) -> dict[str, Any]:
    current = today or date.today()
    _, window_end = plan_data_window(plan, current)
    lag_days = None
    if as_of:
        try:
            lag_days = (current - date.fromisoformat(as_of)).days
        except ValueError:
            lag_days = None
    live = plan != "free"
    if plan == "free":
        message = (
            f"無料プランは約12週間遅れです。この候補は {as_of or '未取得'} 時点です。"
            "当日候補には Light プランが必要です。"
        )
    elif live and lag_days is not None and lag_days <= 1:
        message = f"当日引け後の候補です（as_of={as_of}）。"
    else:
        message = f"最新足は {as_of or '未取得'} です。今日は {current.isoformat()}。プラン終端は {window_end}。"
    return {
        "plan": plan,
        "live": live,
        "as_of": as_of,
        "today": current.isoformat(),
        "plan_window_end": window_end,
        "lag_days": lag_days,
        "message": message,
    }


def candidates_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / CANDIDATES_NAME


def report_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / REPORT_NAME


def write_candidates_csv(path: Path, rows: list[dict[str, Any]], *, as_of: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    score_keys: list[str] = []
    for row in rows:
        for key in (row.get("scores") or {}):
            if key not in score_keys:
                score_keys.append(str(key))
    fieldnames = [
        "rank",
        "code",
        "co_name",
        "mkt_nm",
        "s33_nm",
        "close",
        "total",
        *score_keys,
        "atr",
        "stop",
        "target",
        "rr",
        "alloc",
        "qty",
        "risk_yen",
        "stop_pct",
        "buyable",
        "skip_reason",
        "skip_label",
        "reason",
        "as_of",
    ]
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            item = {
                "rank": row.get("rank"),
                "code": row.get("code"),
                "co_name": row.get("co_name"),
                "mkt_nm": row.get("mkt_nm"),
                "s33_nm": row.get("s33_nm"),
                "close": row.get("close"),
                "alloc": row.get("alloc"),
                "qty": row.get("qty"),
                "risk_yen": row.get("risk_yen"),
                "stop_pct": row.get("stop_pct"),
                "buyable": row.get("buyable"),
                "skip_reason": row.get("skip_reason"),
                "skip_label": row.get("skip_label"),
                "total": row.get("total"),
                "atr": row.get("atr"),
                "stop": row.get("stop"),
                "target": row.get("target"),
                "rr": row.get("rr"),
                "reason": row.get("reason"),
                "as_of": as_of,
            }
            scores = row.get("scores") or {}
            for key in score_keys:
                item[key] = scores.get(key)
            writer.writerow(item)
    tmp.replace(path)
    return path


def _atomic_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def latest_daily_payload(conn: Any) -> dict[str, Any]:
    last = latest_daily_run(conn)
    last_ok = latest_daily_run(conn, status="ok")
    return {"last": last, "last_ok": last_ok}


def attach_candidate_sizes(
    rows: list[dict[str, Any]],
    *,
    cash: float = DEFAULT_CASH,
    risk_pct: float | None = None,
    stop_mult: float = STOP_MULT,
) -> list[dict[str, Any]]:
    risk_frac = parse_risk_pct(risk_pct if risk_pct is not None else DEFAULT_RISK_POINTS)
    return size_rows(rows, cash=float(cash), risk_pct=risk_frac, stop_mult=stop_mult)


def run_daily(
    settings: Settings,
    *,
    skip_sync: bool = False,
    top: int = 20,
    market: str = "プライム",
    min_avg_volume: float = 50_000,
    min_bars: int = 60,
    max_days: int = 5,
    refresh: bool = False,
    auto_learn: bool = True,
    client: Any = None,
    sync_service: Any = None,
    cash: float = DEFAULT_CASH,
    risk_pct: float | None = None,
) -> dict[str, Any]:
    with _DAILY_LOCK:
        return _run_daily_body(
            settings,
            skip_sync=skip_sync,
            top=top,
            market=market,
            min_avg_volume=min_avg_volume,
            min_bars=min_bars,
            max_days=max_days,
            refresh=refresh,
            auto_learn=auto_learn,
            client=client,
            sync_service=sync_service,
            cash=cash,
            risk_pct=risk_pct,
        )


def _run_daily_body(
    settings: Settings,
    *,
    skip_sync: bool,
    top: int,
    market: str,
    min_avg_volume: float,
    min_bars: int,
    max_days: int,
    refresh: bool,
    auto_learn: bool,
    client: Any,
    sync_service: Any,
    cash: float,
    risk_pct: float | None,
) -> dict[str, Any]:
    log = get_logger()
    settings.ensure_dirs()
    picks = normalize_selections(active_rank_selections(settings))
    gates = active_rank_filters(settings)
    own_client = False
    run_id: int | None = None
    synced_days = 0
    error_days = 0
    with connect(settings.db_path) as conn:
        run_id = start_daily_run(conn, settings.jquants_plan)
    try:
        sync_info = None
        if not skip_sync:
            sync_info = _sync_latest(
                settings,
                client=client,
                sync_service=sync_service,
                max_days=max_days,
            )
            own_client = sync_info.pop("_own_client", False)
            client = sync_info.pop("_client", client)
            synced_days = int((sync_info.get("bars") or {}).get("synced_days") or 0)
            error_days = int((sync_info.get("bars") or {}).get("error_days") or 0)
            if error_days:
                raise SkabuError(
                    f"日足同期に失敗した日付が {error_days} 件あります。"
                    "壊れたデータでは候補を出しません。"
                )

        with connect(settings.db_path) as conn:
            as_of = latest_bar_date(conn)
            if not as_of:
                raise SkabuError("日足がありません。先に `python -m skabu sync all` を実行してください。")
            holdout_ok = active_holdout_ok(settings)
            stop_mult, target_mult = exit_pair(settings)
            sizing_cash = float(cash)
            held_codes: list[str] = []
            held_names: list[str] = []
            broker_note = None
            try:
                from skabu.broker.execute import held_sectors, snapshot_broker

                if settings.kabu_live:
                    snap = snapshot_broker(settings)
                    if snap.get("online") and snap.get("cash") is not None:
                        sizing_cash = float(snap["cash"])
                        held_codes = list(snap.get("codes") or [])
                        held_names = held_sectors(settings, held_codes)
                        broker_note = "カブコム余力"
                    else:
                        broker_note = snap.get("error") or "kabuステーション未接続。引数の資金で枚数だけ計算"
            except Exception:
                log.exception("broker snapshot skipped")
                broker_note = "ブローカー参照に失敗。引数の資金で枚数だけ計算"
            locked = lock_as_of(
                conn,
                as_of=as_of,
                selections=picks,
                filters=gates,
                settings=settings,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                top=top,
                holdout_ok=holdout_ok,
                stop_mult=stop_mult,
                target_mult=target_mult,
            )
            ranking = build_ranking(
                conn,
                selections=picks,
                filters=gates,
                settings=settings,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                top=top,
                as_of=as_of,
                refresh=refresh,
                stop_mult=stop_mult,
                target_mult=target_mult,
                holdout_ok=holdout_ok,
            )
            picked = select_buys(
                ranking.get("rows") or [],
                top=top,
                holdout_ok=holdout_ok,
                held_codes=held_codes,
                held_sectors=held_names,
                stop_mult=stop_mult,
                target_mult=target_mult,
            )
            risk_frac = parse_risk_pct(risk_pct if risk_pct is not None else DEFAULT_RISK_POINTS)
            rows = attach_candidate_sizes(
                picked["bought"],
                cash=sizing_cash,
                risk_pct=risk_pct,
                stop_mult=stop_mult,
            )
            csv_path = write_candidates_csv(candidates_path(settings), rows, as_of=as_of)
            freshness = describe_freshness(settings.jquants_plan, as_of)
            cash_only = not rows
            report = {
                "status": "ok",
                "as_of": as_of,
                "plan": settings.jquants_plan,
                "freshness": freshness,
                "market": market,
                "top": top,
                "min_avg_volume": min_avg_volume,
                "min_bars": min_bars,
                "skip_sync": skip_sync,
                "strategy": picks,
                "filters": gates,
                "stop_mult": stop_mult,
                "target_mult": target_mult,
                "synced_days": synced_days,
                "error_days": error_days,
                "universe": locked.get("universe"),
                "locked": True,
                "strategy_key": locked.get("strategy_key"),
                "locked_at": locked.get("locked_at"),
                "candidates_path": str(csv_path),
                "rows": rows,
                "scanned": picked["scanned"],
                "bought": picked["bought_n"],
                "skipped": picked["skipped_n"],
                "skip_reasons": picked["reasons"],
                "skip_labels": SKIP_LABELS,
                "holdout_ok": holdout_ok,
                "cash": cash_only,
                "cash_note": "条件を満たす銘柄がないため、今日は買わない（現金）。" if cash_only else None,
                "start_cash": float(sizing_cash),
                "cash_source": broker_note,
                "held_n": len(held_codes),
                "risk_pct": risk_frac,
                "risk_points": round(risk_frac * 100.0, 4),
                "max_per_sector": MAX_PER_SECTOR,
                "sync": sync_info,
                "grade": None,
                "learn": None,
            }
            report["grade"] = _safe_grade(settings, horizon=20)
            if auto_learn:
                report["learn"] = _safe_learn(
                    settings,
                    market=market,
                    min_avg_volume=min_avg_volume,
                    min_bars=min_bars,
                    top=top,
                )
            _atomic_json(report_path(settings), {k: v for k, v in report.items() if k != "rows"} | {"row_count": len(rows)})
            finished = finish_daily_run(
                conn,
                run_id,
                status="ok",
                as_of=as_of,
                synced_days=synced_days,
                error_days=error_days,
                locked=1,
                candidates_path=str(csv_path),
                report=report,
            )
        log.info(
            "daily ok as_of=%s bought=%s skipped=%s cash=%s risk_pct=%s synced=%s skip_sync=%s",
            as_of,
            len(rows),
            picked["skipped_n"],
            cash_only,
            risk_frac,
            synced_days,
            skip_sync,
        )
        return finished
    except Exception as exc:
        message = str(exc)
        log.exception("daily failed run_id=%s", run_id)
        dump_debug_event(
            settings.debug_dir,
            {"kind": "daily", "run_id": run_id, "error": message, "skip_sync": skip_sync},
        )
        if run_id is not None:
            with connect(settings.db_path) as conn:
                finish_daily_run(
                    conn,
                    run_id,
                    status="failed",
                    synced_days=synced_days,
                    error_days=error_days,
                    locked=0,
                    error=message,
                )
        raise
    finally:
        if own_client and client is not None:
            close = getattr(client, "close", None)
            if close:
                close()


def _safe_grade(settings: Settings, *, horizon: int) -> dict[str, Any]:
    from skabu.learn import grade_outcomes

    try:
        return grade_outcomes(settings, horizon=horizon)
    except Exception as exc:
        get_logger().exception("daily grade failed")
        dump_debug_event(settings.debug_dir, {"kind": "daily_grade", "error": str(exc)})
        return {"status": "failed", "error": str(exc), "graded": 0, "pending_runs": 0}


def _safe_learn(
    settings: Settings,
    *,
    market: str,
    min_avg_volume: float,
    min_bars: int,
    top: int,
) -> dict[str, Any]:
    from skabu.learn import maybe_learn

    try:
        return maybe_learn(
            settings,
            market=market,
            min_avg_volume=min_avg_volume,
            min_bars=min_bars,
            top=top,
        )
    except Exception as exc:
        get_logger().exception("daily learn failed")
        dump_debug_event(settings.debug_dir, {"kind": "daily_learn", "error": str(exc)})
        return {"status": "failed", "error": str(exc)}


def _sync_latest(
    settings: Settings,
    *,
    client: Any,
    sync_service: Any,
    max_days: int,
) -> dict[str, Any]:
    from skabu.jquants import JQuantsClient
    from skabu.sync import SyncService

    own_client = False
    if sync_service is None:
        if client is None:
            settings.require_api_key()
            client = JQuantsClient(settings)
            own_client = True
        sync_service = SyncService(settings, client)
    calendar = sync_service.sync_calendar()
    master = sync_service.sync_master()
    bars = sync_service.sync_bars(max_days=max_days)
    return {
        "calendar": calendar,
        "master": master,
        "bars": bars,
        "_own_client": own_client,
        "_client": client,
    }
