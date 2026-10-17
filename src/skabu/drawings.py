"""チャートの線と箱を、銘柄ごとに data/chart_drawings.json へ残す。日足と週足は同じ図を使う。"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.logging_setup import get_logger

DRAWINGS_NAME = "chart_drawings.json"
DRAWINGS_VERSION = 1
MAX_SHAPES = 200
TIMEFRAMES = {"daily", "weekly"}
COLORS = ("#fbbf24", "#5eead4", "#60a5fa", "#f472b6", "#fb923c", "#e5e7eb")
DEFAULT_COLOR = COLORS[0]

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CODE = re.compile(r"^[0-9A-Za-z]{4,16}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def drawings_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / DRAWINGS_NAME


def drawing_key(code: str, tf: str) -> str:
    _tf(tf)
    return _code(code)


def load_drawings(settings: Settings, code: str, tf: str) -> dict[str, Any]:
    key = drawing_key(code, tf)
    path = drawings_path(settings)
    payload, readable = _read(path)
    shapes: list[dict[str, Any]] = []
    if readable:
        items = payload["items"]
        if key in items:
            shapes = parse_shapes(items.get(key, []), strict=False)
        else:
            shapes = _legacy_shapes(items, key)
    else:
        get_logger().warning("drawings unreadable path=%s key=%s", path, key)
    return {"code": key, "tf": _tf(tf), "shapes": shapes}


def save_drawings(settings: Settings, code: str, tf: str, raw: Any) -> dict[str, Any]:
    key = drawing_key(code, tf)
    shapes = parse_shapes(raw, strict=True)
    path = drawings_path(settings)
    payload, readable = _read(path)
    if path.exists() and not readable:
        get_logger().warning("drawings save refused unreadable path=%s", path)
        raise SkabuError("描画ファイルを読めないので保存しません")
    items = payload["items"]
    items.pop(f"{key}:daily", None)
    items.pop(f"{key}:weekly", None)
    if shapes:
        items[key] = shapes
    else:
        items.pop(key, None)
    _atomic(path, payload)
    get_logger().info("drawings saved key=%s count=%s path=%s", key, len(shapes), path)
    return {"code": key, "tf": _tf(tf), "shapes": shapes}


def _legacy_shapes(items: dict[str, Any], code: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for suffix in ("daily", "weekly"):
        raw = items.get(f"{code}:{suffix}", [])
        for shape in parse_shapes(raw, strict=False):
            if shape["id"] in seen:
                continue
            seen.add(shape["id"])
            out.append(shape)
            if len(out) >= MAX_SHAPES:
                return out
    if out:
        get_logger().info("drawings merged legacy key=%s count=%s", code, len(out))
    return out


def parse_shapes(raw: Any, *, strict: bool) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        if strict:
            raise SkabuError("図形の一覧が配列ではありません")
        get_logger().warning("drawings list dropped type=%s", type(raw).__name__)
        return []
    if len(raw) > MAX_SHAPES:
        if strict:
            raise SkabuError("図形は200個までです")
        get_logger().warning("drawings truncated count=%s", len(raw))
        raw = raw[:MAX_SHAPES]
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in raw:
        try:
            out.append(_shape(item, seen))
        except SkabuError as exc:
            if strict:
                raise
            get_logger().warning("drawings dropped error=%s", exc)
    return out


def _shape(raw: Any, seen: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SkabuError("図形の形が違います")
    kind = raw.get("kind")
    color = _color(raw.get("color"))
    shape_id = _shape_id(raw.get("id"), seen)
    if kind == "trend":
        return {
            "id": shape_id,
            "kind": "trend",
            "a": _point(raw.get("a")),
            "b": _point(raw.get("b")),
            "color": color,
            "extend": bool(raw.get("extend")),
        }
    if kind == "hline":
        return {"id": shape_id, "kind": "hline", "price": _price(raw.get("price")), "color": color}
    if kind == "vline":
        return {"id": shape_id, "kind": "vline", "time": _time(raw.get("time")), "color": color}
    if kind == "box":
        return {
            "id": shape_id,
            "kind": "box",
            "a": _point(raw.get("a")),
            "b": _point(raw.get("b")),
            "color": color,
        }
    raise SkabuError("図形の種類が違います")


def _code(value: Any) -> str:
    if not isinstance(value, str) or not _CODE.fullmatch(value):
        raise SkabuError("銘柄コードが違います")
    return value


def _tf(value: Any) -> str:
    if value not in TIMEFRAMES:
        raise SkabuError("時間足が違います")
    return str(value)


def _price(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SkabuError("価格が数値ではありません")
    number = float(value)
    if number != number or abs(number) == float("inf") or abs(number) > 1_000_000_000:
        raise SkabuError("価格が数値ではありません")
    return round(number, 4)


def _time(value: Any) -> str:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise SkabuError("日付が違います")
    return value


def _point(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SkabuError("点の形が違います")
    return {"time": _time(value.get("time")), "price": _price(value.get("price"))}


def _color(value: Any) -> str:
    if value in (None, ""):
        return DEFAULT_COLOR
    if not isinstance(value, str):
        raise SkabuError("色が違います")
    allowed = {item.lower(): item for item in COLORS}
    found = allowed.get(value.strip().lower())
    if found is None:
        raise SkabuError("色が違います")
    return found


def _shape_id(value: Any, seen: set[str]) -> str:
    if isinstance(value, str) and _ID.fullmatch(value) and value not in seen:
        seen.add(value)
        return value
    fresh = uuid.uuid4().hex
    seen.add(fresh)
    return fresh


def _empty() -> dict[str, Any]:
    return {"version": DRAWINGS_VERSION, "items": {}}


def _read(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return _empty(), True
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        get_logger().warning("drawings read failed path=%s error=%s", path, exc)
        return _empty(), False
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), dict):
        get_logger().warning("drawings read failed path=%s error=items", path)
        return _empty(), False
    return {"version": DRAWINGS_VERSION, "items": dict(raw["items"])}, True


def _atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
