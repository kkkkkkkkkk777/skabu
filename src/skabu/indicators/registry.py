"""プラグインの登録と実行。1本の失敗でチャート全体を止めない。"""

from __future__ import annotations

from typing import Any

from skabu.indicators.base import IndicatorPlugin, IndicatorResult
from skabu.indicators.plugins.adx import AdxPlugin
from skabu.indicators.plugins.atr import AtrPlugin
from skabu.indicators.plugins.bbands import BbandsPlugin
from skabu.indicators.plugins.boost import BoostPlugin
from skabu.indicators.plugins.candles import CandlesPlugin
from skabu.indicators.plugins.doublebottom import DoubleBottomPlugin
from skabu.indicators.plugins.doubletop import DoubleTopPlugin
from skabu.indicators.plugins.downtrend import DowntrendPlugin
from skabu.indicators.plugins.elliott import ElliottPlugin
from skabu.indicators.plugins.headshoulders import HeadShouldersPlugin
from skabu.indicators.plugins.invheadshoulders import InvHeadShouldersPlugin
from skabu.indicators.plugins.ema import EmaPlugin
from skabu.indicators.plugins.ichimoku import IchimokuPlugin
from skabu.indicators.plugins.macd import MacdPlugin
from skabu.indicators.plugins.momentum import MomentumPlugin
from skabu.indicators.plugins.rsi import RsiPlugin
from skabu.indicators.plugins.sma import SmaPlugin
from skabu.indicators.plugins.stoch import StochPlugin
from skabu.indicators.plugins.supertrend import SupertrendPlugin
from skabu.indicators.plugins.uptrend import UptrendPlugin
from skabu.indicators.plugins.volma import VolmaPlugin
from skabu.ind_groups import group_id
from skabu.logging_setup import get_logger

_PLUGINS: dict[str, IndicatorPlugin] = {}


def _register(plugin: IndicatorPlugin) -> None:
    _PLUGINS[plugin.id] = plugin


def _ensure_loaded() -> None:
    if _PLUGINS:
        return
    plugins: list[IndicatorPlugin] = [
        SmaPlugin(),
        EmaPlugin(),
        RsiPlugin(),
        MacdPlugin(),
        AdxPlugin(),
        BoostPlugin(),
        AtrPlugin(),
        BbandsPlugin(),
        StochPlugin(),
        VolmaPlugin(),
        SupertrendPlugin(),
        IchimokuPlugin(),
        MomentumPlugin(),
        DoubleTopPlugin(),
        DoubleBottomPlugin(),
        DowntrendPlugin(),
        UptrendPlugin(),
        HeadShouldersPlugin(),
        InvHeadShouldersPlugin(),
        CandlesPlugin(),
        ElliottPlugin(),
    ]
    for plugin in plugins:
        _register(plugin)


def get_plugin(plugin_id: str) -> IndicatorPlugin | None:
    _ensure_loaded()
    return _PLUGINS.get(plugin_id)


def catalog() -> list[dict[str, Any]]:
    _ensure_loaded()
    items = []
    for plugin in _PLUGINS.values():
        if plugin.id == "boost":
            continue
        items.append(
            {
                "id": plugin.id,
                "title": plugin.title,
                "description": plugin.description,
                "pane": plugin.pane,
                "enabled_by_default": plugin.enabled_by_default,
                "surface": list(getattr(plugin, "surface", ("chart", "score"))),
                "group": getattr(plugin, "group", None) or group_id(plugin.id),
                "help": getattr(plugin, "help", None) or plugin.description,
                "params": [
                    {
                        "name": spec.name,
                        "label": spec.label,
                        "kind": spec.kind,
                        "default": spec.default,
                        "min": spec.minimum,
                        "max": spec.maximum,
                        "step": spec.step,
                    }
                    for spec in plugin.params
                ],
            }
        )
    return items


def parse_indicator_query(query: dict[str, str] | Any) -> list[dict[str, Any]]:
    raw_ids = str(query.get("ind") or "").strip()
    ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
    extras: dict[str, dict[str, str]] = {}
    for key, value in query.items():
        if "." not in key:
            continue
        plugin_id, name = key.split(".", 1)
        extras.setdefault(plugin_id, {})[name] = value
    return [{"id": plugin_id, "params": extras.get(plugin_id, {})} for plugin_id in ids]


def compute_indicators(
    bars: list[dict[str, Any]],
    selections: list[dict[str, Any]],
    *,
    features_only: bool = False,
) -> list[dict[str, Any]]:
    _ensure_loaded()
    results: list[dict[str, Any]] = []
    for item in selections:
        plugin_id = str(item.get("id") or "").strip()
        plugin = get_plugin(plugin_id)
        if plugin is None:
            results.append(
                IndicatorResult(
                    plugin_id=plugin_id,
                    title=plugin_id,
                    pane="price",
                    error="unknown_plugin",
                ).to_dict()
            )
            continue
        try:
            computed = plugin.compute(bars, dict(item.get("params") or {}))
            data = computed.to_dict()
            if features_only:
                data["overlays"] = []
                data["markers"] = []
            results.append(data)
        except Exception as exc:
            get_logger().exception("indicator failed id=%s", plugin_id)
            results.append(
                IndicatorResult(
                    plugin_id=plugin_id,
                    title=plugin.title,
                    pane=plugin.pane,
                    error=str(exc),
                    debug={"exception_type": type(exc).__name__},
                ).to_dict()
            )
    return results
