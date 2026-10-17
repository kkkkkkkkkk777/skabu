"""チャート／ランキングのインジケータ数値を data/ui_prefs.json に残す。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.gates import sanitize_gates
from skabu.indicators.registry import catalog
from skabu.logging_setup import dump_debug_event, get_logger

UNSET = object()

PREFS_NAME = "ui_prefs.json"
PREFS_VERSION = 1


def prefs_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / PREFS_NAME


def catalog_defaults() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in catalog():
        out[item["id"]] = {
            "enabled": bool(item.get("enabled_by_default")),
            "params": {spec["name"]: spec["default"] for spec in item.get("params") or []},
        }
    return out


def sanitize_items(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise SkabuError("items がオブジェクトではありません")
    defaults = catalog_defaults()
    out: dict[str, dict[str, Any]] = {}
    for plugin_id, spec in defaults.items():
        incoming = raw.get(plugin_id)
        if not isinstance(incoming, dict):
            continue
        src_params = incoming.get("params") if isinstance(incoming.get("params"), dict) else {}
        params: dict[str, Any] = {}
        for name, default in spec["params"].items():
            value = src_params.get(name, default)
            if value in ("", None):
                value = default
            params[name] = value
        out[plugin_id] = {
            "enabled": bool(incoming.get("enabled", spec["enabled"])),
            "params": params,
        }
    if not out:
        raise SkabuError("保存できるインジケータがありません")
    return out


def empty_payload() -> dict[str, Any]:
    return {
        "saved": False,
        "saved_at": None,
        "path": None,
        "items": None,
        "gates": None,
        "defaults": catalog_defaults(),
    }


def load_prefs(settings: Settings) -> dict[str, Any]:
    path = prefs_path(settings)
    payload = empty_payload()
    payload["path"] = str(path)
    if not path.exists():
        return payload
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        get_logger().exception("ui prefs read failed path=%s", path)
        dump_debug_event(settings.debug_dir, {"kind": "ui_prefs_read", "path": str(path), "error": str(exc)})
        return payload
    if not isinstance(raw, dict):
        return payload
    items = None
    if raw.get("items") is not None:
        try:
            items = sanitize_items(raw.get("items"))
        except SkabuError:
            get_logger().warning("ui prefs items invalid path=%s", path)
            items = None
    gates = sanitize_gates(raw.get("gates")) if "gates" in raw else None
    if items is None and gates is None:
        return payload
    payload["saved"] = True
    payload["saved_at"] = raw.get("saved_at")
    payload["items"] = items
    payload["gates"] = gates
    return payload


def save_prefs(settings: Settings, items: Any = None, gates: Any = UNSET) -> dict[str, Any]:
    settings.ensure_dirs()
    path = prefs_path(settings)
    current_items = None
    current_gates = None
    had_gates = False
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                if loaded.get("items") is not None:
                    current_items = sanitize_items(loaded.get("items"))
                if "gates" in loaded:
                    had_gates = True
                    current_gates = sanitize_gates(loaded.get("gates"))
        except (OSError, json.JSONDecodeError, SkabuError) as exc:
            get_logger().warning("ui prefs merge read failed path=%s error=%s", path, exc)
    if items is not None:
        incoming = sanitize_items(items)
        clean = {**(current_items or {}), **incoming}
    else:
        clean = current_items
    if clean is None and gates is UNSET:
        raise SkabuError("保存できるインジケータがありません")
    if gates is UNSET:
        clean_gates = current_gates
    else:
        clean_gates = sanitize_gates(gates)
        had_gates = True
    body: dict[str, Any] = {
        "v": PREFS_VERSION,
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": clean,
    }
    if had_gates or gates is not UNSET:
        body["gates"] = [] if clean_gates is None else clean_gates
    try:
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        dump_debug_event(settings.debug_dir, {"kind": "ui_prefs_write", "path": str(path), "error": str(exc)})
        raise SkabuError(f"ui_prefs.json の書き込みに失敗しました: {exc}") from exc
    get_logger().info(
        "ui prefs saved items=%s gates=%s path=%s",
        0 if clean is None else len(clean),
        0 if clean_gates is None else len(clean_gates),
        path,
    )
    return load_prefs(settings)


def clear_prefs(settings: Settings) -> dict[str, Any]:
    path = prefs_path(settings)
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        dump_debug_event(settings.debug_dir, {"kind": "ui_prefs_clear", "path": str(path), "error": str(exc)})
        raise SkabuError(f"ui_prefs.json の削除に失敗しました: {exc}") from exc
    get_logger().info("ui prefs cleared path=%s", path)
    return empty_payload() | {"path": str(path)}
