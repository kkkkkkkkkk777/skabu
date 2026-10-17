"""作戦の版を上書きせず残す。採用したときだけ active_strategy.json を書く。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.gates import sanitize_gates
from skabu.logging_setup import get_logger
from skabu.ranking import normalize_selections
from skabu.strategy import active_path, load_active_strategy

EXPERIMENTS_NAME = "experiments.json"

SEED_EXPERIMENTS: list[dict[str, Any]] = []


def experiments_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / EXPERIMENTS_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _blank_item(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": spec["id"],
        "title": spec.get("title") or spec["id"],
        "note": spec.get("note") or "",
        "created_at": spec.get("created_at") or _now(),
        "selections": normalize_selections(list(spec.get("selections") or [])),
        "filters": list(spec.get("filters") or []),
        "verify": spec.get("verify"),
        "sim": spec.get("sim"),
    }


def load_experiments(settings: Settings) -> dict[str, Any]:
    settings.ensure_dirs()
    path = experiments_path(settings)
    items: list[dict[str, Any]] = []
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            items = list((raw or {}).get("items") or [])
        except (OSError, json.JSONDecodeError):
            get_logger().exception("experiments read failed")
            items = []
    known = {str(item.get("id")) for item in items if item.get("id")}
    for spec in SEED_EXPERIMENTS:
        if spec["id"] not in known:
            items.append(_blank_item(spec))
    payload = {"items": [_blank_item(item) for item in items]}
    if not path.exists() or known != {item["id"] for item in payload["items"]}:
        _atomic_json(path, payload)
        get_logger().info("experiments seeded path=%s n=%s", path, len(payload["items"]))
    return payload


def list_experiments(settings: Settings) -> list[dict[str, Any]]:
    return list(load_experiments(settings).get("items") or [])


def get_experiment(settings: Settings, experiment_id: str) -> dict[str, Any]:
    for item in list_experiments(settings):
        if item.get("id") == experiment_id:
            return item
    raise SkabuError(f"版 {experiment_id} が見つかりません。")


def save_experiments(settings: Settings, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = {"items": [_blank_item(item) for item in items]}
    _atomic_json(experiments_path(settings), payload)
    return payload["items"]


def save_named_strategy(
    settings: Settings,
    title: str,
    selections: list[dict[str, Any]] | None,
    filters: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """画面の点と門を名前付きの版にする。同名の手作り版は入れ替え、確認結果は消す。"""
    name = str(title or "").strip()
    if not name:
        raise SkabuError("版の名前を入れてください。")
    if len(name) > 80:
        raise SkabuError("版の名前は80文字までです。")
    seed_ids = {spec["id"] for spec in SEED_EXPERIMENTS}
    seed_titles = {str(spec.get("title") or "") for spec in SEED_EXPERIMENTS}
    if name in seed_ids or name in seed_titles:
        raise SkabuError("種の版は上書きできません。別の名前にしてください。")
    if not isinstance(selections, list) or not isinstance(filters, list):
        raise SkabuError("点または門の形式が不正です。")
    picks = [
        item
        for item in selections
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    picks = normalize_selections(picks)
    gates = sanitize_gates(filters)
    items = list_experiments(settings)
    target: dict[str, Any] | None = None
    for item in items:
        if item.get("id") in seed_ids:
            continue
        if str(item.get("title") or "") == name:
            target = item
            break
    updated = target is not None
    if target is None:
        exp_id = f"saved_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]}"
        if exp_id in seed_ids or any(item.get("id") == exp_id for item in items):
            raise SkabuError("この名前の版は保存できません。別の名前にしてください。")
        target = {
            "id": exp_id,
            "title": name,
            "note": "画面の点と門から保存",
            "created_at": _now(),
            "selections": picks,
            "filters": gates,
            "verify": None,
            "sim": None,
        }
        items.append(target)
    else:
        target["selections"] = picks
        target["filters"] = gates
        target["verify"] = None
        target["sim"] = None
    save_experiments(settings, items)
    get_logger().info(
        "experiment saved id=%s title=%s selections=%s filters=%s updated=%s",
        target["id"],
        name,
        len(picks),
        len(gates),
        updated,
    )
    return get_experiment(settings, str(target["id"]))


def upsert_experiment(settings: Settings, spec: dict[str, Any]) -> dict[str, Any]:
    items = list_experiments(settings)
    exp_id = str(spec.get("id") or "")
    if not exp_id:
        raise SkabuError("版 id がありません。")
    for item in items:
        if item.get("id") == exp_id:
            return item
    items.append(_blank_item(spec))
    save_experiments(settings, items)
    get_logger().info("experiment upserted id=%s", exp_id)
    return get_experiment(settings, exp_id)


def record_result(
    settings: Settings,
    experiment_id: str,
    *,
    verify: dict[str, Any] | None = None,
    sim: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = list_experiments(settings)
    found = False
    for item in items:
        if item.get("id") != experiment_id:
            continue
        found = True
        if verify is not None:
            item["verify"] = verify
        if sim is not None:
            item["sim"] = sim
    if not found:
        raise SkabuError(f"版 {experiment_id} が見つかりません。")
    save_experiments(settings, items)
    get_logger().info("experiment recorded id=%s verify=%s sim=%s", experiment_id, bool(verify), bool(sim))
    return get_experiment(settings, experiment_id)


def activate(settings: Settings, experiment_id: str) -> dict[str, Any]:
    item = get_experiment(settings, experiment_id)
    verify = item.get("verify") or {}
    holdout = verify.get("holdout") or {}
    avg_return = holdout.get("avg_return")
    holdout_ok = None
    if avg_return is not None:
        try:
            holdout_ok = float(avg_return) > 0
        except (TypeError, ValueError):
            holdout_ok = None
    if holdout_ok is None and holdout.get("avg_excess") is not None:
        try:
            holdout_ok = float(holdout["avg_excess"]) > 0
        except (TypeError, ValueError):
            holdout_ok = None
    active = {
        "id": item["id"],
        "title": item["title"],
        "selections": item["selections"],
        "filters": item.get("filters") or [],
        "horizon": 20,
        "adopted_at": _now(),
        "train": verify.get("train"),
        "holdout": holdout or None,
        "holdout_ok": holdout_ok,
        "warning": None,
        "experiment_id": item["id"],
        "note": item.get("note") or "",
    }
    settings.ensure_dirs()
    _atomic_json(active_path(settings), active)
    get_logger().info("experiment activated id=%s title=%s", item["id"], item.get("title"))
    return active


def filter_summary(filters: list[dict[str, Any]] | None) -> str:
    if not filters:
        return "フィルターなし"
    parts = []
    for spec in filters:
        name = spec.get("id")
        feature = spec.get("feature")
        op = spec.get("op") or ("eq" if "equals" in spec else "eq")
        value = spec["value"] if "value" in spec else spec.get("equals", 1)
        symbol = {"eq": "=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}.get(str(op), str(op))
        parts.append(f"{name}.{feature} {symbol} {value}")
    return " かつ ".join(parts)


def experiments_payload(settings: Settings) -> dict[str, Any]:
    active = load_active_strategy(settings)
    items = []
    for item in list_experiments(settings):
        row = dict(item)
        row["filter_summary"] = filter_summary(item.get("filters"))
        row["active"] = bool(active and active.get("id") == item.get("id"))
        items.append(row)
    return {"items": items, "active_id": (active or {}).get("id")}
