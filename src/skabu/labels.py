"""Boost の当てる対象（正解）を、本番を触らずに2候補だけ測る。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.model import (
    COMPARE_LABELS,
    LABEL_MODEL_NAMES,
    LABEL_TARGET_FIRST,
    MODEL_NAME,
    meta_path,
    train_model,
)

COMPARE_NAME = "label_compare.json"
AUC_LIFT = 0.02
AUC_FLOOR = 0.55


def compare_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / COMPARE_NAME


def load_label_compare(settings: Settings) -> dict[str, Any] | None:
    path = compare_path(settings)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        get_logger().exception("label compare read failed")
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def consider_swap(base_auc: float | None, cand_auc: float | None) -> bool:
    if base_auc is None or cand_auc is None:
        return False
    return cand_auc >= AUC_FLOOR and cand_auc >= float(base_auc) + AUC_LIFT


def _row_from_meta(meta: dict[str, Any], *, name: str, source: str) -> dict[str, Any]:
    metrics = dict(meta.get("metrics") or {})
    return {
        "name": name,
        "label": meta.get("label") or LABEL_TARGET_FIRST,
        "source": source,
        "n_train": metrics.get("n_train"),
        "n_valid": metrics.get("n_valid"),
        "pos_rate": metrics.get("pos_rate") or metrics.get("pos_rate_train"),
        "valid_auc": metrics.get("valid_auc"),
        "valid_accuracy": metrics.get("valid_accuracy"),
        "train_as_of": metrics.get("train_as_of") or [],
        "valid_as_of": metrics.get("valid_as_of") or [],
        "cut": (metrics.get("train_as_of") or [None, None])[-1],
        "note": "本番維持",
    }


def read_production_row(settings: Settings) -> dict[str, Any]:
    path = meta_path(settings, MODEL_NAME)
    if not path.exists():
        raise SkabuError("本番 Boost の meta がありません。先に `python -m skabu train` してください。本番は再学習しません。")
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        get_logger().exception("production meta read failed")
        raise SkabuError("本番 Boost の meta が壊れています。本番は再学習しません。") from exc
    if not isinstance(meta, dict):
        raise SkabuError("本番 Boost の meta が壊れています。本番は再学習しません。")
    row = _row_from_meta(meta, name=MODEL_NAME, source="production")
    row["note"] = "本番（読むだけ）"
    return row


def _row_from_train(payload: dict[str, Any], *, label: str, name: str) -> dict[str, Any]:
    meta = dict(payload.get("meta") or {})
    row = _row_from_meta(meta, name=name, source="candidate")
    row["label"] = label
    row["n_train"] = payload.get("n_train") if payload.get("n_train") is not None else row["n_train"]
    row["n_valid"] = payload.get("n_valid") if payload.get("n_valid") is not None else row["n_valid"]
    row["valid_auc"] = payload.get("auc") if payload.get("auc") is not None else row["valid_auc"]
    return row


def _atomic_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def run_label_compare(
    settings: Settings,
    *,
    market: str = "プライム",
    min_avg_volume: float = 50_000,
    min_bars: int = 60,
    stride: int = 20,
    min_history: int = 80,
    horizon: int = 20,
    min_samples: int = 20,
) -> dict[str, Any]:
    log = get_logger()
    production = read_production_row(settings)
    base_auc = production.get("valid_auc")
    items = [production]
    for label in COMPARE_LABELS:
        name = LABEL_MODEL_NAMES[label]
        log.info("label compare train label=%s name=%s", label, name)
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
                name=name,
                label=label,
            )
        except Exception as exc:
            log.exception("label compare train failed label=%s name=%s", label, name)
            dump_debug_event(
                settings.debug_dir,
                {"kind": "label_compare_train", "label": label, "name": name, "error": str(exc)},
            )
            raise
        row = _row_from_train(trained, label=label, name=name)
        row["note"] = "入れ替えを検討" if consider_swap(base_auc, row.get("valid_auc")) else "本番維持"
        items.append(row)
        log.info(
            "label compare done label=%s auc=%s n_train=%s n_valid=%s cut=%s",
            label,
            row.get("valid_auc"),
            row.get("n_train"),
            row.get("n_valid"),
            row.get("cut"),
        )
    consider = [item["label"] for item in items if item.get("note") == "入れ替えを検討"]
    note = f"入れ替えを検討: {', '.join(consider)}" if consider else "本番維持"
    payload = {
        "ok": True,
        "compared_at": datetime.now(timezone.utc).isoformat(),
        "note": note,
        "swapped": False,
        "items": items,
        "path": str(compare_path(settings)),
    }
    _atomic_json(compare_path(settings), payload)
    dump_debug_event(settings.debug_dir, {"kind": "label_compare", **payload})
    log.info("label compare wrote path=%s note=%s", payload["path"], note)
    return payload
