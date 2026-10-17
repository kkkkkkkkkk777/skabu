"""自動／承認と緊急停止。ペーパー／ライブとは混ぜない。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.logging_setup import dump_debug_event, get_logger

PREFS_NAME = "trade_prefs.json"
MODE_APPROVE = "approve"
MODE_AUTO = "auto"
MODES = {MODE_APPROVE, MODE_AUTO}
DEFAULT_HOLD_DAYS = 20
MIN_HOLD_DAYS = 1
MAX_HOLD_DAYS = 250


def prefs_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / PREFS_NAME


def default_prefs() -> dict[str, Any]:
    return {"mode": MODE_APPROVE, "halted": False, "hold_days": DEFAULT_HOLD_DAYS, "updated_at": None}


def parse_hold_days(value: Any, *, fallback: int = DEFAULT_HOLD_DAYS) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        get_logger().warning("hold_days parse failed value=%s", value)
        return fallback
    if days < MIN_HOLD_DAYS or days > MAX_HOLD_DAYS:
        get_logger().warning("hold_days out of range value=%s", value)
        return fallback
    return days


def load_prefs(settings: Settings) -> dict[str, Any]:
    path = prefs_path(settings)
    payload = default_prefs()
    if not path.exists():
        return payload
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        get_logger().exception("trade prefs read failed")
        dump_debug_event(settings.debug_dir, {"kind": "trade_prefs_read", "error": str(exc)})
        return payload
    if not isinstance(raw, dict):
        return payload
    mode = raw.get("mode") if raw.get("mode") in MODES else MODE_APPROVE
    payload["mode"] = mode
    payload["halted"] = bool(raw.get("halted"))
    payload["hold_days"] = parse_hold_days(raw.get("hold_days", DEFAULT_HOLD_DAYS))
    payload["updated_at"] = raw.get("updated_at")
    return payload


def save_prefs(settings: Settings, **changes: Any) -> dict[str, Any]:
    settings.ensure_dirs()
    current = load_prefs(settings)
    if "mode" in changes:
        mode = changes["mode"]
        if mode not in MODES:
            raise SkabuError("mode は approve か auto です")
        current["mode"] = mode
    if "halted" in changes:
        current["halted"] = bool(changes["halted"])
    if "hold_days" in changes:
        try:
            days = int(changes["hold_days"])
        except (TypeError, ValueError) as exc:
            raise SkabuError("保有日数は1から250の営業日にしてください。") from exc
        if days < MIN_HOLD_DAYS or days > MAX_HOLD_DAYS:
            raise SkabuError("保有日数は1から250の営業日にしてください。")
        current["hold_days"] = days
    current["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = prefs_path(settings)
    try:
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        dump_debug_event(settings.debug_dir, {"kind": "trade_prefs_write", "error": str(exc)})
        raise SkabuError(f"trade_prefs.json の書き込みに失敗しました: {exc}") from exc
    get_logger().info(
        "trade prefs saved mode=%s halted=%s hold_days=%s",
        current["mode"],
        current["halted"],
        current.get("hold_days"),
    )
    return current
