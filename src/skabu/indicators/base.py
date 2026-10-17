"""プラグインが守る入出力。チャート描画と将来のランキングで同じ結果を使う。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    kind: str
    default: Any
    minimum: Any = None
    maximum: Any = None
    step: Any = None


@dataclass
class LineSeries:
    id: str
    label: str
    color: str
    points: list[dict[str, Any]]
    width: int = 2
    style: str = "solid"
    pane: str = "price"


@dataclass
class Marker:
    time: str
    position: str
    shape: str
    color: str
    text: str = ""


@dataclass
class IndicatorResult:
    plugin_id: str
    title: str
    pane: str
    overlays: list[LineSeries] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.plugin_id,
            "title": self.title,
            "pane": self.pane,
            "overlays": [
                {
                    "id": item.id,
                    "label": item.label,
                    "color": item.color,
                    "width": item.width,
                    "style": item.style,
                    "pane": item.pane,
                    "points": item.points,
                }
                for item in self.overlays
            ],
            "markers": [
                {
                    "time": item.time,
                    "position": item.position,
                    "shape": item.shape,
                    "color": item.color,
                    "text": item.text,
                }
                for item in self.markers
            ],
            "features": self.features,
            "debug": self.debug,
            "error": self.error,
        }


class IndicatorPlugin(Protocol):
    id: str
    title: str
    description: str
    pane: str
    group: str
    enabled_by_default: bool
    params: list[ParamSpec]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        ...


def coerce_params(specs: list[ParamSpec], raw: dict[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for spec in specs:
        value = raw.get(spec.name, spec.default)
        if spec.kind == "int":
            try:
                value = int(float(value))
            except (TypeError, ValueError):
                value = spec.default
            if spec.minimum is not None:
                value = max(int(spec.minimum), value)
            if spec.maximum is not None:
                value = min(int(spec.maximum), value)
        elif spec.kind == "float":
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = spec.default
            if spec.minimum is not None:
                value = max(float(spec.minimum), value)
            if spec.maximum is not None:
                value = min(float(spec.maximum), value)
        elif spec.kind == "bool":
            if isinstance(value, str):
                value = value.strip().lower() in {"1", "true", "yes", "on"}
            else:
                value = bool(value)
        resolved[spec.name] = value
    return resolved


def source_series(bars: list[dict[str, Any]], source: str = "close") -> list[float | None]:
    out: list[float | None] = []
    for bar in bars:
        open_ = bar.get("open")
        high = bar.get("high")
        low = bar.get("low")
        close = bar.get("close")
        if source == "hlc3" and None not in (high, low, close):
            out.append((high + low + close) / 3)
        elif source == "hl2" and None not in (high, low):
            out.append((high + low) / 2)
        elif source == "ohlc4" and None not in (open_, high, low, close):
            out.append((open_ + high + low + close) / 4)
        else:
            out.append(close if close is not None else None)
    return out


def line_points(times: list[str], values: list[float | None]) -> list[dict[str, Any]]:
    points = []
    for time, value in zip(times, values, strict=True):
        if value is None:
            continue
        points.append({"time": time, "value": round(float(value), 6)})
    return points


def last_number(values: list[float | None]) -> float | None:
    for value in reversed(values):
        if value is not None:
            return float(value)
    return None
