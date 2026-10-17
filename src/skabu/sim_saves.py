"""シミュ結果を名前付きで data/sim_saves.json に残す。採用JSONは触らない。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.logging_setup import dump_debug_event, get_logger

SAVES_NAME = "sim_saves.json"
MAX_SAVES = 40
TITLE_MAX = 80


def saves_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / SAVES_NAME


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _as_dict(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {}


def _as_list(raw: Any) -> list[Any]:
    return list(raw) if isinstance(raw, list) else []


def _number(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _risk_points(result: dict[str, Any]) -> float | None:
    points = _number(result.get("risk_points"))
    if points is not None:
        return points
    frac = _number(result.get("risk_pct"))
    if frac is None:
        return None
    return round(frac * 100.0 if frac <= 1 else frac, 4)


def extract_inputs(result: dict[str, Any], overlay: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = _as_dict(overlay)
    mode = str(extra.get("mode") or result.get("mode") or "month").strip().lower()
    if mode not in {"month", "long"}:
        mode = "month"
    source = str(extra.get("source") or result.get("source") or "active").strip().lower()
    if source not in {"active", "checks", "experiment"}:
        source = "active"
    as_of = str(extra.get("as_of") or result.get("as_of") or "").strip() or None
    start = str(extra.get("start") or (as_of if mode == "long" else "") or "").strip() or None
    end = str(extra.get("end") or result.get("end_date") or "").strip() or None
    cash = _number(extra.get("cash") if "cash" in extra else result.get("cash_start"))
    top = _number(extra.get("top") if "top" in extra else result.get("top"))
    risk = _number(extra.get("risk_pct") if "risk_pct" in extra else _risk_points(result))
    experiment_id = extra.get("experiment_id")
    if experiment_id is None:
        experiment_id = result.get("experiment_id")
    experiment_id = str(experiment_id).strip() if experiment_id else None
    strategy = extra.get("strategy") if extra.get("strategy") is not None else result.get("strategy")
    filters = extra.get("filters") if extra.get("filters") is not None else result.get("filters")
    return {
        "source": source,
        "experiment_id": experiment_id,
        "mode": mode,
        "as_of": as_of,
        "start": start,
        "end": end,
        "cash": cash,
        "top": int(top) if top is not None else None,
        "risk_pct": risk,
        "strategy": _as_list(strategy),
        "filters": _as_list(filters),
    }


def extract_summary(result: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    as_of = inputs.get("as_of") or result.get("as_of")
    end = inputs.get("end") or result.get("end_date")
    period = " ～ ".join(part for part in (as_of, end) if part) or "-"
    return {
        "return": result.get("return"),
        "pnl": result.get("pnl"),
        "equity_end": result.get("equity_end"),
        "max_drawdown": result.get("max_drawdown"),
        "wins": result.get("wins"),
        "n": result.get("n"),
        "skipped": result.get("skipped"),
        "cash": bool(result.get("cash")),
        "period": period,
        "source": inputs.get("source"),
        "mode": inputs.get("mode"),
    }


def _strip_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "created_at": item.get("created_at"),
        "inputs": _as_dict(item.get("inputs")),
        "summary": _as_dict(item.get("summary")),
    }


def load_saves(settings: Settings) -> list[dict[str, Any]]:
    settings.ensure_dirs()
    path = saves_path(settings)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        get_logger().exception("sim saves read failed path=%s", path)
        dump_debug_event(settings.debug_dir, {"kind": "sim_saves_read", "path": str(path), "error": str(exc)})
        raise SkabuError(f"sim_saves.json の読み込みに失敗しました: {exc}") from exc
    items = raw.get("items") if isinstance(raw, dict) else None
    return [item for item in _as_list(items) if isinstance(item, dict) and item.get("id")]


def _write_saves(settings: Settings, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = saves_path(settings)
    try:
        _atomic_json(path, {"items": items})
    except OSError as exc:
        dump_debug_event(settings.debug_dir, {"kind": "sim_saves_write", "path": str(path), "error": str(exc)})
        raise SkabuError(f"sim_saves.json の書き込みに失敗しました: {exc}") from exc
    get_logger().info("sim saves written n=%s path=%s", len(items), path)
    return items


def list_saves(settings: Settings) -> list[dict[str, Any]]:
    return [_strip_result(item) for item in load_saves(settings)]


def get_save(settings: Settings, save_id: str) -> dict[str, Any]:
    wanted = str(save_id or "").strip()
    if not wanted:
        raise SkabuError("保存 id がありません。")
    for item in load_saves(settings):
        if str(item.get("id")) == wanted:
            return item
    raise SkabuError(f"保存 {wanted} が見つかりません。")


def create_save(
    settings: Settings,
    title: str,
    result: Any,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_title = " ".join(str(title or "").split())
    if not clean_title:
        raise SkabuError("保存名を入れてください。")
    if len(clean_title) > TITLE_MAX:
        clean_title = clean_title[:TITLE_MAX]
    payload = _as_dict(result)
    if not payload or payload.get("ok") is False:
        raise SkabuError("保存する結果がありません。先に実行してください。")
    if payload.get("as_of") is None and payload.get("mode") is None and not payload.get("curve") and not payload.get("trades"):
        raise SkabuError("保存する結果がありません。先に実行してください。")
    extracted = extract_inputs(payload, inputs)
    item = {
        "id": _new_id(),
        "title": clean_title,
        "created_at": _now(),
        "inputs": extracted,
        "summary": extract_summary(payload, extracted),
        "result": payload,
    }
    items = [item, *load_saves(settings)]
    if len(items) > MAX_SAVES:
        dropped = items[MAX_SAVES:]
        get_logger().info("sim saves trimmed dropped=%s", [row.get("id") for row in dropped])
        items = items[:MAX_SAVES]
    _write_saves(settings, items)
    get_logger().info("sim save created id=%s title=%s", item["id"], item["title"])
    return item


def delete_save(settings: Settings, save_id: str) -> dict[str, Any]:
    wanted = str(save_id or "").strip()
    if not wanted:
        raise SkabuError("保存 id がありません。")
    items = load_saves(settings)
    kept = [item for item in items if str(item.get("id")) != wanted]
    if len(kept) == len(items):
        raise SkabuError(f"保存 {wanted} が見つかりません。")
    _write_saves(settings, kept)
    get_logger().info("sim save deleted id=%s", wanted)
    return {"id": wanted, "deleted": True}
