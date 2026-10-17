"""ウォークフォワードで組み合わせを選び、日次・ランキングの既定にする。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.ranking import default_rank_selections, default_strategy_label, normalize_selections
from skabu.store import connect, finish_strategy_run, latest_strategy_run, start_strategy_run
from skabu.verify import PRESETS, run_compare

ACTIVE_NAME = "active_strategy.json"
SELECT_HORIZON = "20"


def active_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / ACTIVE_NAME


def load_active_strategy(settings: Settings) -> dict[str, Any] | None:
    path = active_path(settings)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        get_logger().exception("active strategy read failed")
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("selections") is None and not payload.get("id"):
        return None
    payload["selections"] = normalize_selections(payload.get("selections") or [])
    return payload


def active_rank_selections(settings: Settings | None = None) -> list[dict[str, Any]]:
    if settings is not None:
        active = load_active_strategy(settings)
        if active is not None:
            return list(active.get("selections") or [])
    return default_rank_selections()


def active_rank_filters(settings: Settings | None = None) -> list[dict[str, Any]]:
    if settings is None:
        return []
    active = load_active_strategy(settings)
    if not active:
        return []
    return list(active.get("filters") or [])


def latest_strategy_payload(settings: Settings) -> dict[str, Any]:
    active = load_active_strategy(settings)
    with connect(settings.db_path) as conn:
        last = latest_strategy_run(conn)
    payload = {
        "active": active,
        "last": last,
        "selections": list((active or {}).get("selections") or []) if active is not None else default_rank_selections(),
        "filters": (active or {}).get("filters") or [],
        "presets": [
            {
                "id": preset_id,
                "title": spec["title"],
                "selections": spec["selections"],
                "filters": spec.get("filters") or [],
            }
            for preset_id, spec in PRESETS.items()
        ],
    }
    try:
        from skabu.experiments import experiments_payload

        payload["experiments"] = experiments_payload(settings)
    except Exception:
        get_logger().exception("experiments payload failed")
        payload["experiments"] = {"items": [], "active_id": (active or {}).get("id")}
    try:
        from skabu.search import load_last_search

        payload["search"] = load_last_search(settings)
    except Exception:
        get_logger().exception("search last payload failed")
        payload["search"] = None
    return payload


def adopt_strategy(
    settings: Settings,
    *,
    presets: dict[str, dict[str, Any]] | None = None,
    market: str = "プライム",
    min_avg_volume: float = 50_000,
    min_bars: int = 60,
    top: int = 20,
    stride: int = 20,
    min_history: int = 80,
    refresh: bool = False,
    horizon: str = SELECT_HORIZON,
) -> dict[str, Any]:
    settings.ensure_dirs()
    log = get_logger()
    with connect(settings.db_path) as conn:
        run_id = start_strategy_run(conn, horizon=int(horizon))
    try:
        with connect(settings.db_path) as conn:
            compared = run_compare(
                conn,
                presets=presets,
                include_periods=True,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                top=top,
                stride=stride,
                min_history=min_history,
                refresh=refresh,
            )
        ranked = []
        for item in compared.get("items") or []:
            body = (item.get("horizons") or {}).get(str(horizon)) or {}
            periods = body.get("periods") or []
            train_periods, holdout_periods = _split_periods(periods)
            train = _side_metrics(train_periods)
            holdout = _side_metrics(holdout_periods)
            ranked.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "selections": normalize_selections(item.get("selections") or []),
                    "filters": item.get("filters") or [],
                    "train": train,
                    "holdout": holdout,
                    "summary": body.get("summary"),
                }
            )
        usable = [item for item in ranked if item["train"]["avg_excess"] is not None]
        if not usable:
            raise SkabuError("選定できる期間リターンがありません。日足か検証期間を確認してください。")
        usable.sort(key=_rank_tuple, reverse=True)
        winner = usable[0]
        holdout_excess = winner["holdout"]["avg_excess"]
        holdout_measured = holdout_excess is not None
        holdout_ok = holdout_measured and holdout_excess > 0
        previous = load_active_strategy(settings)
        if holdout_ok or not holdout_measured:
            warning = None
            if winner["train"]["avg_excess"] is not None and winner["train"]["avg_excess"] <= 0:
                warning = "選定期間でも超過がプラスではありません。"
            elif not holdout_measured:
                warning = "確認期間が足りないため、選定期間の勝者を採用した。"
            active = {
                "id": winner["id"],
                "title": winner["title"],
                "selections": winner["selections"],
                "filters": winner.get("filters") or [],
                "horizon": int(horizon),
                "adopted_at": datetime.now(timezone.utc).isoformat(),
                "train": winner["train"],
                "holdout": winner["holdout"],
                "holdout_ok": holdout_ok,
                "warning": warning,
            }
            _atomic_json(active_path(settings), active)
            chosen_id = str(winner["id"])
        elif previous:
            warning = "確認期間の超過がプラスではないため、採用中の戦略を維持した。"
            active = previous
            active["warning"] = warning
            active["holdout_ok"] = False
            chosen_id = str(previous.get("id") or "")
            log.info("strategy keep previous id=%s holdout=%s", chosen_id, holdout_excess)
        else:
            strategy_id, strategy_title, strategy_short = default_strategy_label()
            warning = f"確認期間の超過がプラスではないため、既定の {strategy_short} にした。"
            active = {
                "id": strategy_id,
                "title": strategy_title,
                "selections": default_rank_selections(),
                "filters": [],
                "horizon": int(horizon),
                "adopted_at": datetime.now(timezone.utc).isoformat(),
                "train": winner["train"],
                "holdout": winner["holdout"],
                "holdout_ok": False,
                "warning": warning,
            }
            _atomic_json(active_path(settings), active)
            chosen_id = strategy_id
            log.info("strategy fallback default holdout=%s", holdout_excess)
        report = {
            "active": active,
            "candidates": ranked,
            "rules": compared.get("rules"),
            "horizon": int(horizon),
            "winner_id": winner["id"],
            "adopted": chosen_id == str(winner["id"]),
        }
        with connect(settings.db_path) as conn:
            finished = finish_strategy_run(
                conn,
                run_id,
                status="ok",
                chosen_id=chosen_id,
                horizon=int(horizon),
                holdout_ok=holdout_ok,
                report=report,
            )
        log.info(
            "strategy adopt result chosen=%s winner=%s train_excess=%s holdout_ok=%s",
            chosen_id,
            winner["id"],
            winner["train"]["avg_excess"],
            holdout_ok,
        )
        return finished
    except Exception as exc:
        log.exception("strategy adopt failed")
        dump_debug_event(settings.debug_dir, {"kind": "strategy_adopt", "error": str(exc)})
        with connect(settings.db_path) as conn:
            finish_strategy_run(conn, run_id, status="failed", horizon=int(horizon), error=str(exc))
        raise


def _split_periods(periods: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(periods, key=lambda item: str(item.get("as_of") or ""))
    if len(ordered) < 4:
        return ordered, []
    cut = max(2, int(len(ordered) * 0.7))
    return ordered[:cut], ordered[cut:]


def _period_excess(period: dict[str, Any]) -> float | None:
    left = period.get("period_return")
    right = period.get("bench_return")
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _side_metrics(periods: list[dict[str, Any]]) -> dict[str, Any]:
    excesses = [item for item in (_period_excess(period) for period in periods) if item is not None]
    rets = [float(period["period_return"]) for period in periods if period.get("period_return") is not None]
    wins = [1 for item in excesses if item > 0]
    return {
        "periods": len(periods),
        "avg_excess": None if not excesses else round(sum(excesses) / len(excesses), 6),
        "avg_return": None if not rets else round(sum(rets) / len(rets), 6),
        "win_rate_vs_bench": None if not excesses else round(sum(wins) / len(excesses), 4),
    }


def _rank_tuple(item: dict[str, Any]) -> tuple[float, float, float]:
    train = item.get("train") or {}
    excess = train.get("avg_excess")
    win = train.get("win_rate_vs_bench") or 0.0
    ret = train.get("avg_return") or 0.0
    if excess is None:
        return (float("-inf"), float("-inf"), float("-inf"))
    return (float(excess), float(win), float(ret))


def _atomic_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path
