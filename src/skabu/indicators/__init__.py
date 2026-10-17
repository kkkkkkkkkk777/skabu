"""インジケータプラグイン。Pine は実行せず、Python に移植して登録する。"""

from skabu.indicators.registry import catalog, compute_indicators, get_plugin, parse_indicator_query

__all__ = ["catalog", "compute_indicators", "get_plugin", "parse_indicator_query"]
