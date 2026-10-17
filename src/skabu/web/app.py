"""ローカルDBを読むチャート用 HTTP API。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from skabu import __version__
from skabu.config import Settings, load_settings, plan_data_window
from skabu.errors import SkabuError
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.gates import gate_catalog, parse_filter_query
from skabu.ind_groups import group_catalog
from skabu.indicators.registry import catalog, compute_indicators, parse_indicator_query
from skabu.levels import LEVELS_NOTE, compute_levels, exit_pair, load_exit_levels, save_exit_levels
from skabu.ranking import build_ranking, default_rank_selections
from skabu.reference import reference_levels
from skabu.drawings import load_drawings, save_drawings
from skabu.daily import (
    DEFAULT_CASH,
    attach_candidate_sizes,
    candidates_path,
    daily_needs_rerun,
    describe_freshness,
    latest_daily_payload,
    run_daily,
    write_candidates_csv,
)
from skabu.learn import learn_status
from skabu.model import latest_model_payload, predict_arrivals
from skabu.sim import default_long_range, run_long_sim, run_sim
from skabu.sim_saves import create_save, delete_save, get_save, list_saves
from skabu.broker.codes import public_chart_links
from skabu.buys import DEFAULT_RISK_POINTS, SKIP_LABELS, parse_risk_pct, ranking_holdout_ok
from skabu.experiments import activate as activate_experiment
from skabu.experiments import get_experiment, record_result, save_named_strategy
from skabu.strategy import active_rank_filters, active_rank_selections, latest_strategy_payload
from skabu.auto_sync import AutoSyncRunner
from skabu.store import connect, get_chart_bars, get_issue, latest_bar_date, search_issues, status_snapshot
from skabu.ui_prefs import UNSET, clear_prefs, load_prefs, save_prefs
from skabu.verify import run_compare, run_verify

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_CODE = "86970"
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _verify_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def public_sync_result(result: dict[str, Any]) -> dict[str, Any]:
    bars = result.get("bars") if isinstance(result.get("bars"), dict) else {}
    return {
        "action": result.get("action"),
        "reason": result.get("reason"),
        "skip": result.get("skip"),
        "error": result.get("error"),
        "pending_bars": result.get("pending_bars"),
        "last_bars_to": result.get("last_bars_to"),
        "synced_days": bars.get("synced_days"),
        "empty_days": bars.get("empty_days"),
        "error_days": bars.get("error_days"),
    }


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            get_logger().exception("unhandled request request_id=%s path=%s", request_id, request.url.path)
            response = JSONResponse(
                {"ok": False, "error": "internal_error", "request_id": request_id},
                status_code=500,
            )
        response.headers["X-Request-Id"] = request_id
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="skabu", version=__version__)
    app.state.settings = settings
    app.state.auto_sync = AutoSyncRunner(settings)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/home")
    def home_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "home.html")

    @app.get("/rank")
    def rank_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "rank.html")

    @app.get("/verify")
    def verify_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "verify.html")

    @app.get("/daily")
    def daily_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "daily.html")

    @app.get("/model")
    def model_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "model.html")

    @app.get("/strategy")
    def strategy_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "strategy.html")

    @app.get("/sim")
    def sim_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "sim.html")

    @app.get("/trade")
    def trade_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "trade.html")

    @app.get("/help")
    def help_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "help.html")

    @app.get("/api/status")
    def api_status(request: Request) -> dict[str, Any]:
        with connect(settings.db_path) as conn:
            snap = status_snapshot(conn)
            as_of = snap["daily_bar_to"] or latest_bar_date(conn)
            daily = latest_daily_payload(conn)
            long_start, long_end = default_long_range(conn)
        strategy = latest_strategy_payload(settings)
        active = strategy.get("active") or {}
        return {
            "ok": True,
            "request_id": _rid(request),
            "plan": settings.jquants_plan,
            "plan_window": plan_data_window(settings.jquants_plan),
            "freshness": describe_freshness(settings.jquants_plan, as_of),
            "counts": snap["counts"],
            "daily_bar_from": snap["daily_bar_from"],
            "daily_bar_to": snap["daily_bar_to"],
            "long_sim_start": long_start,
            "long_sim_end": long_end,
            "last_daily": daily.get("last"),
            "active_strategy": {
                "id": active.get("id"),
                "title": active.get("title"),
                "holdout_ok": active.get("holdout_ok"),
                "selections": strategy.get("selections"),
                "filters": strategy.get("filters") or [],
            },
            "default_code": DEFAULT_CODE,
            "indicators": catalog(),
            "gates": gate_catalog(),
            "groups": group_catalog(),
            "auto_sync": getattr(request.app.state, "auto_sync", None).snapshot()
            if getattr(request.app.state, "auto_sync", None)
            else {
                "enabled": False,
                "running": False,
                "last_at": None,
                "last_bars_to": None,
                "last_error": None,
                "last_action": None,
            },
        }

    @app.post("/api/sync", response_model=None)
    def api_manual_sync(request: Request) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        runner = getattr(request.app.state, "auto_sync", None)
        if runner is None:
            get_logger().error("manual sync missing runner request_id=%s", request_id)
            return JSONResponse(
                {"ok": False, "error": "sync_unavailable", "message": "同期機能が初期化されていません", "request_id": request_id},
                status_code=500,
            )
        get_logger().info("manual sync start request_id=%s", request_id)
        try:
            result = runner.run(reason="manual")
        except Exception as exc:
            get_logger().exception("manual sync crashed request_id=%s", request_id)
            dump_debug_event(settings.debug_dir, {"kind": "manual_sync", "request_id": request_id, "error": str(exc)})
            return JSONResponse(
                {"ok": False, "error": "sync_failed", "message": str(exc), "request_id": request_id},
                status_code=500,
            )
        public = public_sync_result(result if isinstance(result, dict) else {})
        action = str(public.get("action") or "")
        get_logger().info(
            "manual sync done request_id=%s action=%s synced=%s empty=%s bars_to=%s",
            request_id,
            action,
            public.get("synced_days"),
            public.get("empty_days"),
            public.get("last_bars_to"),
        )
        if action == "failed":
            dump_debug_event(
                settings.debug_dir,
                {"kind": "manual_sync", "request_id": request_id, "error": public.get("error"), "action": action},
            )
            return JSONResponse(
                {
                    "ok": False,
                    "error": "sync_failed",
                    "message": public.get("error") or "株価の同期に失敗しました",
                    "request_id": request_id,
                    "result": public,
                    "auto_sync": runner.snapshot(),
                },
                status_code=502,
            )
        return {
            "ok": True,
            "request_id": request_id,
            "action": action,
            "result": public,
            "auto_sync": runner.snapshot(),
        }

    @app.get("/api/indicators")
    def api_indicators(request: Request) -> dict[str, Any]:
        return {"ok": True, "request_id": _rid(request), "items": catalog()}

    @app.get("/api/ui-prefs")
    def api_ui_prefs_get(request: Request) -> dict[str, Any]:
        payload = load_prefs(settings)
        return {"ok": True, "request_id": _rid(request), **payload}

    @app.put("/api/ui-prefs", response_model=None)
    async def api_ui_prefs_put(request: Request) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            body = await request.json()
        except Exception as exc:
            get_logger().warning("ui prefs json failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "invalid_json", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        try:
            incoming = body or {}
            if "items" not in incoming and "gates" not in incoming:
                raise SkabuError("items または gates を送ってください")
            payload = save_prefs(
                settings,
                incoming.get("items") if "items" in incoming else None,
                incoming["gates"] if "gates" in incoming else UNSET,
            )
        except SkabuError as exc:
            get_logger().warning("ui prefs save failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "save_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, **payload}

    @app.delete("/api/ui-prefs", response_model=None)
    def api_ui_prefs_delete(request: Request) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            payload = clear_prefs(settings)
        except SkabuError as exc:
            get_logger().warning("ui prefs clear failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "clear_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, **payload}

    @app.get("/api/drawings/{code}", response_model=None)
    def api_drawings_get(code: str, request: Request, tf: str = Query(default="daily")) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            payload = load_drawings(settings, code, tf)
        except SkabuError as exc:
            get_logger().warning("drawings load failed code=%s tf=%s request_id=%s error=%s", code, tf, request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "drawings_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, **payload}

    @app.put("/api/drawings/{code}", response_model=None)
    async def api_drawings_put(code: str, request: Request, tf: str = Query(default="daily")) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            body = await request.json()
        except Exception as exc:
            get_logger().warning("drawings json failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "invalid_json", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        try:
            incoming = body or {}
            if "shapes" not in incoming:
                raise SkabuError("shapes を送ってください")
            payload = save_drawings(settings, code, tf, incoming.get("shapes"))
        except SkabuError as exc:
            get_logger().warning("drawings save failed code=%s tf=%s request_id=%s error=%s", code, tf, request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "drawings_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, **payload}

    @app.get("/api/exit-levels")
    def api_exit_levels_get(request: Request) -> dict[str, Any]:
        payload = load_exit_levels(settings)
        return {"ok": True, "request_id": _rid(request), **payload}

    @app.put("/api/exit-levels", response_model=None)
    async def api_exit_levels_put(request: Request) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            body = await request.json()
        except Exception as exc:
            get_logger().warning("exit levels json failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "invalid_json", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        try:
            incoming = body or {}
            payload = save_exit_levels(settings, incoming.get("stop_mult"), incoming.get("target_mult"))
        except SkabuError as exc:
            get_logger().warning("exit levels save failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "save_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, **payload}

    @app.get("/api/search")
    def api_search(
        request: Request,
        q: str = Query(default="", max_length=80),
        limit: int = Query(default=20, ge=1, le=50),
    ) -> dict[str, Any]:
        with connect(settings.db_path) as conn:
            items = search_issues(conn, q, limit)
        get_logger().info(
            "search q=%r hits=%s request_id=%s",
            q,
            len(items),
            _rid(request),
        )
        return {"ok": True, "request_id": _rid(request), "query": q, "items": items}

    @app.get("/api/rank")
    def api_rank(
        request: Request,
        market: str = Query(default="プライム"),
        min_avg_volume: float = Query(default=50_000, ge=0),
        min_bars: int = Query(default=60, ge=10, le=400),
        top: int = Query(default=50, ge=1, le=500),
        as_of: str | None = Query(default=None),
        refresh: bool = Query(default=False),
    ) -> dict[str, Any]:
        request_id = _rid(request)
        parsed_ind = parse_indicator_query(request.query_params)
        parsed_flt = parse_filter_query(request.query_params)
        selections = parsed_ind or active_rank_selections(settings)
        filters = active_rank_filters(settings) if parsed_flt is None else parsed_flt
        holdout_ok = ranking_holdout_ok(settings, parsed_ind or None)
        get_logger().info(
            "rank query ind=%s flt=%s adopted_filters=%s request_id=%s",
            bool(parsed_ind),
            parsed_flt is not None,
            parsed_flt is None,
            request_id,
        )
        stop_mult, target_mult = exit_pair(settings)
        with connect(settings.db_path) as conn:
            payload = build_ranking(
                conn,
                selections=selections,
                filters=filters,
                settings=settings,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                top=top,
                as_of=as_of,
                refresh=refresh,
                holdout_ok=holdout_ok,
                stop_mult=stop_mult,
                target_mult=target_mult,
            )
        payload["stop_mult"] = stop_mult
        payload["target_mult"] = target_mult
        payload["ok"] = True
        payload["request_id"] = request_id
        payload["skip_labels"] = SKIP_LABELS
        get_logger().info(
            "rank as_of=%s market=%s rows=%s cached=%s request_id=%s",
            payload.get("as_of"),
            market,
            len(payload.get("rows") or []),
            payload.get("cached"),
            request_id,
        )
        return payload

    @app.get("/api/verify", response_model=None)
    def api_verify(
        request: Request,
        market: str = Query(default="プライム"),
        min_avg_volume: float = Query(default=50_000, ge=0),
        min_bars: int = Query(default=60, ge=10, le=400),
        top: int = Query(default=20, ge=1, le=100),
        stride: int = Query(default=20, ge=5, le=60),
        refresh: bool = Query(default=False),
        compare: str | None = Query(default=None),
        experiment: str | None = Query(default=None),
    ) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        compare_mode = (compare or "").strip().lower()
        experiment_id = (experiment or "").strip()
        selections = None
        filters = None
        experiment_meta = None
        if experiment_id and not compare_mode:
            try:
                item = get_experiment(settings, experiment_id)
            except SkabuError as exc:
                get_logger().warning(
                    "verify experiment missing id=%s error=%s request_id=%s",
                    experiment_id,
                    exc,
                    request_id,
                )
                return JSONResponse(
                    {"ok": False, "error": "not_found", "message": str(exc), "request_id": request_id},
                    status_code=404,
                )
            selections = list(item.get("selections") or [])
            filters = list(item.get("filters") or [])
            experiment_meta = {
                "id": item.get("id"),
                "title": item.get("title") or item.get("id"),
                "filter_count": len(filters),
            }
        else:
            parsed = parse_indicator_query(request.query_params)
            selections = parsed or default_rank_selections()
        stop_mult, target_mult = exit_pair(settings)
        with connect(settings.db_path) as conn:
            if compare_mode in {"true", "1", "yes", "all"}:
                payload = run_compare(
                    conn,
                    market=market,
                    min_avg_volume=min_avg_volume,
                    min_bars=min_bars,
                    top=top,
                    stride=stride,
                    refresh=refresh,
                    stop_mult=stop_mult,
                    target_mult=target_mult,
                )
            elif compare_mode == "core":
                get_logger().info("verify core compare removed request_id=%s", request_id)
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "gone",
                        "message": "3作戦の比較は削除しました",
                        "request_id": request_id,
                    },
                    status_code=410,
                )
            else:
                payload = run_verify(
                    conn,
                    selections=selections,
                    filters=filters,
                    settings=settings,
                    market=market,
                    min_avg_volume=min_avg_volume,
                    min_bars=min_bars,
                    top=top,
                    stride=stride,
                    refresh=refresh,
                    stop_mult=stop_mult,
                    target_mult=target_mult,
                )
        payload["ok"] = True
        payload["request_id"] = request_id
        payload["compare"] = compare_mode or None
        if experiment_meta:
            payload["experiment"] = experiment_meta
        get_logger().info(
            "verify compare=%s experiment=%s filters=%s locked=%s cached=%s request_id=%s",
            compare_mode or False,
            (experiment_meta or {}).get("id") or "-",
            (experiment_meta or {}).get("filter_count") if experiment_meta else "-",
            payload.get("locked_runs") or payload.get("items"),
            payload.get("cached"),
            request_id,
        )
        return payload

    @app.post("/api/verify/save", response_model=None)
    async def api_verify_save(request: Request) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            body = await request.json()
        except Exception as exc:
            get_logger().warning("verify save json failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "invalid_json", "message": "JSONを読めませんでした", "request_id": request_id},
                status_code=400,
            )
        incoming = body if isinstance(body, dict) else {}
        experiment_id = str(incoming.get("experiment_id") or "").strip()
        if not experiment_id:
            get_logger().warning("verify save missing experiment request_id=%s", request_id)
            return JSONResponse(
                {"ok": False, "error": "no_experiment", "message": "版を選んでください", "request_id": request_id},
                status_code=400,
            )
        try:
            item = get_experiment(settings, experiment_id)
        except SkabuError as exc:
            get_logger().warning("verify save missing id=%s error=%s request_id=%s", experiment_id, exc, request_id)
            return JSONResponse(
                {"ok": False, "error": "not_found", "message": str(exc), "request_id": request_id},
                status_code=404,
            )
        summary = incoming.get("summary") if isinstance(incoming.get("summary"), dict) else {}
        holdout = {
            "win_rate": _verify_number(summary.get("win_rate")),
            "win_rate_vs_bench": _verify_number(summary.get("win_rate_vs_bench")),
            "avg_return": _verify_number(summary.get("avg_return")),
            "avg_excess": _verify_number(summary.get("avg_excess")),
            "max_drawdown": _verify_number(summary.get("max_drawdown")),
            "trades": summary.get("trades"),
            "periods": summary.get("periods"),
        }
        current = dict(item.get("verify") or {})
        current["holdout"] = holdout
        current["screen"] = {
            "horizon": 20,
            "saved_at": _now_iso(),
            "top": incoming.get("top"),
            "stride": incoming.get("stride"),
            "as_of_from": incoming.get("as_of_from"),
            "as_of_to": incoming.get("as_of_to"),
            "source": "verify-page",
        }
        try:
            saved = record_result(settings, experiment_id, verify=current)
        except SkabuError as exc:
            get_logger().warning("verify save failed id=%s error=%s request_id=%s", experiment_id, exc, request_id)
            return JSONResponse(
                {"ok": False, "error": "save_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        get_logger().info(
            "verify saved id=%s return=%s excess=%s request_id=%s",
            experiment_id,
            holdout.get("avg_return"),
            holdout.get("avg_excess"),
            request_id,
        )
        return {"ok": True, "request_id": request_id, "experiment": saved}

    @app.get("/api/daily")
    def api_daily(
        request: Request,
        cash: float = Query(default=DEFAULT_CASH, gt=0, le=1_000_000_000),
        risk_pct: float = Query(default=DEFAULT_RISK_POINTS, gt=0, le=100),
    ) -> dict[str, Any]:
        request_id = _rid(request)
        with connect(settings.db_path) as conn:
            payload = latest_daily_payload(conn)
            as_of = ((payload.get("last_ok") or {}).get("as_of")) or latest_bar_date(conn)
        last_ok = payload.get("last_ok") or {}
        report = last_ok.get("report") or {}
        stop_mult, target_mult = exit_pair(settings)
        rows = attach_candidate_sizes(
            report.get("rows") or [],
            cash=cash,
            risk_pct=risk_pct,
            stop_mult=stop_mult,
        )
        risk_frac = parse_risk_pct(risk_pct)
        learning = learn_status(settings)
        rerun = daily_needs_rerun(
            report,
            selections=active_rank_selections(settings),
            filters=active_rank_filters(settings),
            stop_mult=stop_mult,
            target_mult=target_mult,
        )
        get_logger().info(
            "daily status last=%s last_ok=%s pending=%s graded=%s rerun=%s request_id=%s",
            (payload.get("last") or {}).get("status"),
            last_ok.get("status"),
            learning.get("pending_runs"),
            learning.get("graded"),
            rerun,
            request_id,
        )
        return {
            "ok": True,
            "request_id": request_id,
            "plan": settings.jquants_plan,
            "freshness": describe_freshness(settings.jquants_plan, as_of),
            "last": payload.get("last"),
            "last_ok": last_ok,
            "rows": rows,
            "csv_ready": candidates_path(settings).exists(),
            "learn": learning,
            "cash": bool(report.get("cash")),
            "cash_note": report.get("cash_note"),
            "bought": report.get("bought"),
            "skipped": report.get("skipped"),
            "skip_reasons": report.get("skip_reasons") or {},
            "skip_labels": report.get("skip_labels") or SKIP_LABELS,
            "rerun": rerun,
            "start_cash": cash,
            "risk_pct": risk_frac,
            "risk_points": risk_pct,
        }

    @app.post("/api/daily", response_model=None)
    def api_daily_run(
        request: Request,
        skip_sync: bool = Query(default=True),
        top: int = Query(default=20, ge=1, le=100),
        market: str = Query(default="プライム"),
        min_avg_volume: float = Query(default=50_000, ge=0),
        min_bars: int = Query(default=60, ge=10, le=400),
        cash: float = Query(default=DEFAULT_CASH, gt=0, le=1_000_000_000),
        risk_pct: float = Query(default=DEFAULT_RISK_POINTS, gt=0, le=100),
    ) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        if not skip_sync:
            get_logger().warning("daily web sync refused request_id=%s", request_id)
            return JSONResponse(
                {
                    "ok": False,
                    "error": "web_sync_disabled",
                    "message": "画面からは J-Quants 同期しません。同期込みは `python -m skabu daily` です。",
                    "request_id": request_id,
                },
                status_code=400,
            )
        try:
            finished = run_daily(
                settings,
                skip_sync=True,
                top=top,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                auto_learn=False,
                cash=cash,
                risk_pct=risk_pct,
            )
        except Exception as exc:
            get_logger().exception("daily web run failed request_id=%s", request_id)
            return JSONResponse(
                {"ok": False, "error": "daily_failed", "message": str(exc), "request_id": request_id},
                status_code=500,
            )
        report = finished.get("report") or {}
        get_logger().info(
            "daily web run as_of=%s names=%s request_id=%s",
            finished.get("as_of"),
            len(report.get("rows") or []),
            request_id,
        )
        return {
            "ok": True,
            "request_id": request_id,
            "plan": settings.jquants_plan,
            "freshness": report.get("freshness") or describe_freshness(settings.jquants_plan, finished.get("as_of")),
            "last": finished,
            "last_ok": finished,
            "rows": report.get("rows") or [],
            "csv_ready": candidates_path(settings).exists(),
            "learn": learn_status(settings),
            "cash": bool(report.get("cash")),
            "cash_note": report.get("cash_note"),
            "bought": report.get("bought"),
            "skipped": report.get("skipped"),
            "skip_reasons": report.get("skip_reasons") or {},
            "skip_labels": report.get("skip_labels") or SKIP_LABELS,
            "start_cash": report.get("start_cash") or cash,
            "risk_pct": report.get("risk_pct") or parse_risk_pct(risk_pct),
            "risk_points": report.get("risk_points") or risk_pct,
        }

    @app.get("/api/daily/csv", response_model=None)
    def api_daily_csv(
        request: Request,
        cash: float = Query(default=DEFAULT_CASH, gt=0, le=1_000_000_000),
        risk_pct: float = Query(default=DEFAULT_RISK_POINTS, gt=0, le=100),
    ) -> FileResponse | JSONResponse:
        path = candidates_path(settings)
        with connect(settings.db_path) as conn:
            payload = latest_daily_payload(conn)
        last_ok = payload.get("last_ok") or {}
        report = last_ok.get("report") or {}
        rows = report.get("rows") or []
        if rows:
            stop_mult, _target_mult = exit_pair(settings)
            sized = attach_candidate_sizes(rows, cash=cash, risk_pct=risk_pct, stop_mult=stop_mult)
            view = settings.skabu_data_dir / "candidates_view.csv"
            write_candidates_csv(view, sized, as_of=str(last_ok.get("as_of") or report.get("as_of") or ""))
            return FileResponse(view, filename="candidates.csv", media_type="text/csv")
        if not path.exists():
            return JSONResponse(
                {"ok": False, "error": "not_found", "request_id": _rid(request)},
                status_code=404,
            )
        return FileResponse(path, filename="candidates.csv", media_type="text/csv")

    @app.get("/api/model")
    def api_model(request: Request) -> dict[str, Any]:
        request_id = _rid(request)
        payload = latest_model_payload(settings)
        learning = learn_status(settings)
        from skabu.labels import load_label_compare

        labels = load_label_compare(settings)
        get_logger().info(
            "model status ready=%s last=%s pending=%s labels=%s request_id=%s",
            payload.get("ready"),
            (payload.get("last") or {}).get("status"),
            learning.get("pending_runs"),
            bool(labels),
            request_id,
        )
        return {
            "ok": True,
            "request_id": request_id,
            "ready": payload.get("ready"),
            "last": payload.get("last"),
            "meta": payload.get("meta"),
            "path": payload.get("path"),
            "learn": learning,
            "labels": labels,
        }

    @app.get("/api/strategy")
    def api_strategy(request: Request) -> dict[str, Any]:
        request_id = _rid(request)
        payload = latest_strategy_payload(settings)
        active = payload.get("active") or {}
        get_logger().info(
            "strategy active=%s holdout_ok=%s request_id=%s",
            active.get("id"),
            active.get("holdout_ok"),
            request_id,
        )
        return {
            "ok": True,
            "request_id": request_id,
            "active": active or None,
            "last": payload.get("last"),
            "selections": payload.get("selections"),
            "filters": payload.get("filters") or [],
            "presets": payload.get("presets"),
            "experiments": payload.get("experiments") or {"items": [], "active_id": active.get("id")},
            "search": payload.get("search"),
        }

    @app.post("/api/strategy/use", response_model=None)
    def api_strategy_use(
        request: Request,
        experiment_id: str = Query(alias="id"),
    ) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            active = activate_experiment(settings, experiment_id)
        except SkabuError as exc:
            get_logger().warning("strategy use failed id=%s error=%s", experiment_id, exc)
            return JSONResponse(
                {"ok": False, "error": "use_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        payload = latest_strategy_payload(settings)
        get_logger().info("strategy use id=%s request_id=%s", experiment_id, request_id)
        return {
            "ok": True,
            "request_id": request_id,
            "active": active,
            "experiments": payload.get("experiments"),
        }

    @app.post("/api/strategy/save", response_model=None)
    async def api_strategy_save(request: Request) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            body = await request.json()
        except Exception as exc:
            get_logger().warning("strategy save json failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "invalid_json", "message": "JSONを読めませんでした", "request_id": request_id},
                status_code=400,
            )
        incoming = body if isinstance(body, dict) else {}
        try:
            saved = save_named_strategy(
                settings,
                str(incoming.get("title") or ""),
                incoming.get("selections") if "selections" in incoming else [],
                incoming.get("filters") if "filters" in incoming else [],
            )
        except SkabuError as exc:
            get_logger().warning("strategy save failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "save_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        except Exception as exc:
            get_logger().exception("strategy save crashed request_id=%s", request_id)
            dump_debug_event(
                settings.debug_dir,
                {"kind": "strategy_save", "request_id": request_id, "error": str(exc)},
            )
            return JSONResponse(
                {"ok": False, "error": "save_failed", "message": str(exc), "request_id": request_id},
                status_code=500,
            )
        payload = latest_strategy_payload(settings)
        get_logger().info("strategy save id=%s title=%s request_id=%s", saved.get("id"), saved.get("title"), request_id)
        return {
            "ok": True,
            "request_id": request_id,
            "experiment": saved,
            "experiments": payload.get("experiments"),
        }

    @app.post("/api/strategy/search", response_model=None)
    def api_strategy_search(
        request: Request,
        market: str = Query(default="プライム"),
        min_avg_volume: float = Query(default=50_000, ge=0),
        min_bars: int = Query(default=60, ge=10, le=400),
        top: int = Query(default=20, ge=1, le=100),
        stride: int = Query(default=20, ge=5, le=60),
        refresh: bool = Query(default=False),
    ) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            from skabu.search import run_search

            report = run_search(
                settings,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                top=top,
                stride=stride,
                refresh=refresh,
            )
        except SkabuError as exc:
            get_logger().warning("strategy search failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "search_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        except Exception as exc:
            get_logger().exception("strategy search crashed request_id=%s", request_id)
            return JSONResponse(
                {"ok": False, "error": "search_failed", "message": str(exc), "request_id": request_id},
                status_code=500,
            )
        payload = latest_strategy_payload(settings)
        get_logger().info(
            "strategy search winner=%s adopted=%s request_id=%s",
            report.get("winner_id"),
            report.get("adopted"),
            request_id,
        )
        return {
            "ok": True,
            "request_id": request_id,
            "search": report,
            "active": payload.get("active"),
            "experiments": payload.get("experiments"),
        }

    @app.get("/api/sim", response_model=None)
    def api_sim(
        request: Request,
        as_of: str | None = Query(default=None),
        mode: str = Query(default="month"),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        cash: float = Query(default=1_000_000, gt=0, le=1_000_000_000),
        top: int = Query(default=5, ge=1, le=20),
        horizon: int = Query(default=20, ge=1, le=250),
        take_profit: bool = Query(default=True),
        market: str = Query(default="プライム"),
        min_avg_volume: float = Query(default=50_000, ge=0),
        min_bars: int = Query(default=60, ge=10, le=400),
        risk_pct: float = Query(default=DEFAULT_RISK_POINTS, gt=0, le=100),
        source: str = Query(default="active"),
        experiment_id: str | None = Query(default=None),
    ) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        source_norm = (source or "active").strip().lower()
        if source_norm not in {"active", "checks", "experiment"}:
            return JSONResponse(
                {"ok": False, "error": "invalid_source", "message": "source は active / checks / experiment です", "request_id": request_id},
                status_code=400,
            )
        selections = None
        filters = None
        used_experiment = None
        if source_norm == "experiment":
            exp_id = (experiment_id or "").strip()
            if not exp_id:
                return JSONResponse(
                    {"ok": False, "error": "no_experiment", "message": "experiment_id を指定してください", "request_id": request_id},
                    status_code=400,
                )
            try:
                item = get_experiment(settings, exp_id)
            except SkabuError as exc:
                return JSONResponse(
                    {"ok": False, "error": "trade_failed", "message": str(exc), "request_id": request_id},
                    status_code=400,
                )
            selections = list(item.get("selections") or [])
            filters = list(item.get("filters") or [])
            used_experiment = exp_id
        if source_norm == "checks":
            parsed = parse_indicator_query(request.query_params)
            parsed_flt = parse_filter_query(request.query_params)
            if not parsed:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "no_indicators",
                        "message": "実験はインジケータを1つ以上選んでください",
                        "request_id": request_id,
                    },
                    status_code=400,
                )
            selections = parsed
            filters = [] if parsed_flt is None else parsed_flt
            get_logger().info(
                "sim checks ind=%s flt=%s request_id=%s",
                [item.get("id") for item in selections],
                parsed_flt is not None,
                request_id,
            )
        mode_norm = (mode or "month").strip().lower()
        if mode_norm not in {"month", "long"}:
            return JSONResponse(
                {"ok": False, "error": "invalid_mode", "message": "mode は month / long です", "request_id": request_id},
                status_code=400,
            )
        try:
            if mode_norm == "long":
                if not start or not end:
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": "missing_range",
                            "message": "長期モードは開始日と終了日を指定してください",
                            "request_id": request_id,
                        },
                        status_code=400,
                    )
                payload = run_long_sim(
                    settings,
                    start=start,
                    end=end,
                    cash=cash,
                    top=top,
                    market=market,
                    min_avg_volume=min_avg_volume,
                    min_bars=min_bars,
                    risk_pct=risk_pct,
                    selections=selections,
                    filters=filters,
                    take_profit=take_profit,
                    horizon=horizon,
                )
            else:
                payload = run_sim(
                    settings,
                    as_of=as_of,
                    cash=cash,
                    top=top,
                    horizon=horizon,
                    market=market,
                    min_avg_volume=min_avg_volume,
                    min_bars=min_bars,
                    risk_pct=risk_pct,
                    selections=selections,
                    filters=filters,
                    take_profit=take_profit,
                )
        except SkabuError as exc:
            get_logger().warning("sim failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "sim_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        except Exception as exc:
            get_logger().exception("sim crashed request_id=%s", request_id)
            return JSONResponse(
                {"ok": False, "error": "sim_failed", "message": str(exc), "request_id": request_id},
                status_code=500,
            )
        get_logger().info(
            "sim source=%s as_of=%s names=%s pnl=%s request_id=%s",
            source_norm,
            payload.get("as_of"),
            payload.get("n"),
            payload.get("pnl"),
            request_id,
        )
        return {
            "ok": True,
            "request_id": request_id,
            "source": source_norm,
            "experiment_id": used_experiment,
            **payload,
        }

    @app.get("/api/sim-saves", response_model=None)
    def api_sim_saves_list(request: Request) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            items = list_saves(settings)
        except SkabuError as exc:
            get_logger().warning("sim saves list failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "saves_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, "items": items}

    @app.get("/api/sim-saves/{save_id}", response_model=None)
    def api_sim_saves_get(save_id: str, request: Request) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            item = get_save(settings, save_id)
        except SkabuError as exc:
            get_logger().warning("sim save get failed id=%s request_id=%s error=%s", save_id, request_id, exc)
            missing = "見つかりません" in str(exc)
            return JSONResponse(
                {"ok": False, "error": "not_found" if missing else "saves_failed", "message": str(exc), "request_id": request_id},
                status_code=404 if missing else 400,
            )
        return {"ok": True, "request_id": request_id, "item": item}

    @app.post("/api/sim-saves", response_model=None)
    async def api_sim_saves_create(request: Request) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            body = await request.json()
        except Exception as exc:
            get_logger().warning("sim save json failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "invalid_json", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        incoming = body or {}
        try:
            item = create_save(
                settings,
                str(incoming.get("title") or ""),
                incoming.get("result"),
                incoming.get("inputs") if isinstance(incoming.get("inputs"), dict) else None,
            )
        except SkabuError as exc:
            get_logger().warning("sim save create failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "save_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, "item": item}

    @app.delete("/api/sim-saves/{save_id}", response_model=None)
    def api_sim_saves_delete(save_id: str, request: Request) -> dict[str, Any] | JSONResponse:
        request_id = _rid(request)
        try:
            payload = delete_save(settings, save_id)
        except SkabuError as exc:
            get_logger().warning("sim save delete failed id=%s request_id=%s error=%s", save_id, request_id, exc)
            missing = "見つかりません" in str(exc)
            return JSONResponse(
                {"ok": False, "error": "not_found" if missing else "saves_failed", "message": str(exc), "request_id": request_id},
                status_code=404 if missing else 400,
            )
        return {"ok": True, "request_id": request_id, **payload}

    @app.get("/api/chart/{code}", response_model=None)
    def api_chart(
        code: str,
        request: Request,
        tf: str = Query(default="daily"),
    ) -> JSONResponse | dict[str, Any]:
        request_id = _rid(request)
        if tf not in {"daily", "weekly"}:
            return JSONResponse(
                {"ok": False, "error": "invalid_timeframe", "request_id": request_id},
                status_code=400,
            )
        with connect(settings.db_path) as conn:
            issue = get_issue(conn, code)
            if issue is None:
                get_logger().warning("chart not found code=%s request_id=%s", code, request_id)
                return JSONResponse(
                    {"ok": False, "error": "not_found", "code": code, "request_id": request_id},
                    status_code=404,
                )
            bars = get_chart_bars(conn, issue["code"], tf)
            daily_bars = bars if tf == "daily" else get_chart_bars(conn, issue["code"], "daily")
        selections = parse_indicator_query(request.query_params)
        indicators = compute_indicators(bars, selections)
        stop_mult, target_mult = exit_pair(settings)
        levels = compute_levels(daily_bars, stop_mult=stop_mult, target_mult=target_mult)
        try:
            arrivals = predict_arrivals(daily_bars, settings)
        except Exception as exc:
            get_logger().exception("arrival predict failed code=%s request_id=%s", issue["code"], request_id)
            dump_debug_event(
                settings.debug_dir,
                {"kind": "arrival_predict", "code": issue["code"], "error": str(exc), "request_id": request_id},
            )
            arrivals = None
        if arrivals:
            get_logger().info(
                "chart arrivals code=%s target=%s stop=%s time=%s split=%s request_id=%s",
                issue["code"],
                arrivals.get("target"),
                arrivals.get("stop"),
                arrivals.get("time"),
                arrivals.get("split"),
                request_id,
            )
        try:
            references = reference_levels(daily_bars)
        except Exception as exc:
            get_logger().exception("reference levels failed code=%s request_id=%s", issue["code"], request_id)
            dump_debug_event(
                settings.debug_dir,
                {"kind": "reference_levels", "code": issue["code"], "error": str(exc), "request_id": request_id},
            )
            references = []
        get_logger().info(
            "chart references code=%s n=%s request_id=%s",
            issue["code"],
            len(references),
            request_id,
        )
        candles = []
        volumes = []
        for bar in bars:
            candles.append(
                {
                    "time": bar["date"],
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                }
            )
            up = (bar["close"] or 0) >= (bar["open"] or 0)
            volumes.append(
                {
                    "time": bar["date"],
                    "value": bar["volume"] or 0,
                    "color": "rgba(38, 166, 154, 0.55)" if up else "rgba(239, 83, 80, 0.55)",
                }
            )
        links = public_chart_links(issue["code"])
        if links:
            get_logger().info(
                "public chart links code=%s n=%s request_id=%s",
                issue["code"],
                len(links),
                request_id,
            )
        else:
            get_logger().warning(
                "public chart links unavailable code=%s request_id=%s",
                issue["code"],
                request_id,
            )
        get_logger().info(
            "chart code=%s tf=%s bars=%s inds=%s request_id=%s",
            issue["code"],
            tf,
            len(candles),
            [item["id"] for item in indicators],
            request_id,
        )
        return {
            "ok": True,
            "request_id": request_id,
            "issue": issue,
            "links": links,
            "timeframe": tf,
            "count": len(candles),
            "from": candles[0]["time"] if candles else None,
            "to": candles[-1]["time"] if candles else None,
            "candles": candles,
            "volumes": volumes,
            "indicators": indicators,
            "levels": {**levels, "note": LEVELS_NOTE},
            "arrivals": arrivals,
            "references": references,
        }

    @app.get("/api/trade")
    def api_trade(request: Request) -> dict[str, Any]:
        from skabu.broker.execute import trade_status

        payload = trade_status(settings)
        return {"ok": True, "request_id": _rid(request), "skip_labels": SKIP_LABELS, **payload}

    @app.put("/api/trade/mode", response_model=None)
    async def api_trade_mode(request: Request) -> dict[str, Any] | JSONResponse:
        from skabu.broker.execute import set_mode

        request_id = _rid(request)
        try:
            body = await request.json()
            prefs = set_mode(settings, str((body or {}).get("mode") or ""))
        except Exception as exc:
            get_logger().warning("trade mode failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "trade_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, "prefs": prefs}

    @app.put("/api/trade/hold", response_model=None)
    async def api_trade_hold(request: Request) -> dict[str, Any] | JSONResponse:
        from skabu.broker.execute import set_hold_days

        request_id = _rid(request)
        try:
            body = await request.json()
            prefs = set_hold_days(settings, (body or {}).get("hold_days"))
        except Exception as exc:
            get_logger().warning("trade hold failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "trade_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, "prefs": prefs}

    @app.put("/api/trade/exit-date", response_model=None)
    async def api_trade_exit_date(request: Request) -> dict[str, Any] | JSONResponse:
        from skabu.broker.execute import set_exit_date

        request_id = _rid(request)
        try:
            body = await request.json()
            book = set_exit_date(settings, str((body or {}).get("code") or ""), (body or {}).get("exit_date"))
        except Exception as exc:
            get_logger().warning("trade exit date failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "trade_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, "book": book}

    @app.post("/api/trade/close", response_model=None)
    async def api_trade_close(request: Request) -> dict[str, Any] | JSONResponse:
        from skabu.broker.execute import approve_close_sells

        request_id = _rid(request)
        try:
            body = await request.json()
            codes = list((body or {}).get("codes") or [])
            payload = approve_close_sells(settings, [str(code) for code in codes])
        except Exception as exc:
            get_logger().warning("trade close failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "trade_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, "skip_labels": SKIP_LABELS, **payload}

    @app.post("/api/trade/buy", response_model=None)
    def api_trade_buy(request: Request) -> dict[str, Any] | JSONResponse:
        from skabu.broker.execute import prepare_buy

        request_id = _rid(request)
        try:
            payload = prepare_buy(settings)
        except SkabuError as exc:
            get_logger().warning("trade buy failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "trade_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": bool(payload.get("ok", True)), "request_id": request_id, "skip_labels": SKIP_LABELS, **payload}

    @app.post("/api/trade/approve", response_model=None)
    async def api_trade_approve(request: Request) -> dict[str, Any] | JSONResponse:
        from skabu.broker.execute import approve_tickets

        request_id = _rid(request)
        try:
            body = await request.json()
            codes = list((body or {}).get("codes") or [])
            payload = approve_tickets(settings, [str(code) for code in codes])
        except Exception as exc:
            get_logger().warning("trade approve failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "trade_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, "skip_labels": SKIP_LABELS, **payload}

    @app.post("/api/trade/watch", response_model=None)
    def api_trade_watch(request: Request) -> dict[str, Any] | JSONResponse:
        from skabu.broker.execute import watch_exits

        request_id = _rid(request)
        try:
            payload = watch_exits(settings)
        except SkabuError as exc:
            get_logger().warning("trade watch failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "trade_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, "skip_labels": SKIP_LABELS, **payload}

    @app.post("/api/trade/halt", response_model=None)
    def api_trade_halt(request: Request) -> dict[str, Any] | JSONResponse:
        from skabu.broker.execute import halt_trading

        request_id = _rid(request)
        try:
            payload = halt_trading(settings)
        except SkabuError as exc:
            get_logger().warning("trade halt failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "trade_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, **payload}

    @app.post("/api/trade/resume", response_model=None)
    def api_trade_resume(request: Request) -> dict[str, Any] | JSONResponse:
        from skabu.broker.execute import resume_trading

        request_id = _rid(request)
        try:
            prefs = resume_trading(settings)
        except SkabuError as exc:
            get_logger().warning("trade resume failed request_id=%s error=%s", request_id, exc)
            return JSONResponse(
                {"ok": False, "error": "trade_failed", "message": str(exc), "request_id": request_id},
                status_code=400,
            )
        return {"ok": True, "request_id": request_id, "prefs": prefs}

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", "-")
