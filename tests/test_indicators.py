from datetime import date, timedelta

from skabu.indicators.mathutil import (
    adx,
    atr,
    bollinger,
    ema,
    ichimoku,
    macd,
    sma,
    stochastic,
    supertrend,
)
from skabu.indicators.plugins.adx import AdxPlugin
from skabu.indicators.plugins.macd import MacdPlugin
from skabu.ind_groups import PLUGIN_GROUP, group_catalog
from skabu.indicators.registry import catalog, compute_indicators, parse_indicator_query


def test_sma_and_ema() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert sma(values, 3) == [None, None, 2.0, 3.0, 4.0]
    seeded = ema(values, 3)
    assert seeded[2] == 2.0
    assert seeded[3] is not None and seeded[3] > 2.0


def _bars(closes: list[float]) -> list[dict]:
    rows = []
    for index, close in enumerate(closes, start=1):
        rows.append(
            {
                "date": f"2026-01-{index:02d}" if index <= 31 else f"2026-02-{index - 31:02d}",
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 10,
            }
        )
    return rows


def test_macd_and_adx_plugins() -> None:
    closes = [100 + i * 0.6 for i in range(50)]
    bars = _bars(closes)
    macd_res = MacdPlugin().compute(bars, {})
    assert macd_res.error is None
    assert macd_res.features["score"] in {-1, 1}
    assert macd_res.features["hist"] is not None
    adx_res = AdxPlugin().compute(bars, {"length": 8, "trend": 10})
    assert adx_res.error is None
    assert adx_res.features["value"] is not None
    left = macd(closes, 5, 10, 4)
    changed = closes[:-1] + [999.0]
    right = macd(changed, 5, 10, 4)
    assert left[0][:-1] == right[0][:-1]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    adx_left = adx(highs, lows, closes, 5)
    highs2 = highs[:-1] + [highs[-1] + 50]
    adx_right = adx(highs2, lows, closes[:-1] + [closes[-1] + 50], 5)
    assert adx_left[2][:-1] == adx_right[2][:-1]


def test_registry_catalog_and_unknown() -> None:
    ids = {item["id"] for item in catalog()}
    assert {
        "sma",
        "ema",
        "rsi",
        "macd",
        "adx",
        "atr",
        "bbands",
        "stoch",
        "volma",
        "supertrend",
        "ichimoku",
        "momentum",
        "doubletop",
        "doublebottom",
        "downtrend",
        "uptrend",
        "headshoulders",
        "invheadshoulders",
        "candles",
        "elliott",
    } <= ids
    assert "boost" not in ids
    assert "relative" not in ids
    assert "gaussian" not in ids
    unknown = compute_indicators([], [{"id": "nope", "params": {}}])
    assert unknown[0]["error"] == "unknown_plugin"


def test_catalog_surfaces_split_chart_score_gate() -> None:
    items = {item["id"]: item for item in catalog()}
    assert set(items["sma"]["surface"]) == {"chart", "score"}
    assert set(items["ichimoku"]["surface"]) == {"chart", "score"}
    assert items["momentum"]["surface"] == ["score"]
    assert set(items["doubletop"]["surface"]) == {"chart", "gate"}
    assert set(items["doublebottom"]["surface"]) == {"chart", "score"}
    assert set(items["downtrend"]["surface"]) == {"chart", "gate"}
    assert items["uptrend"]["surface"] == ["chart"]
    assert set(items["headshoulders"]["surface"]) == {"chart", "gate"}
    assert set(items["invheadshoulders"]["surface"]) == {"chart", "score"}
    assert set(items["candles"]["surface"]) == {"chart", "score", "gate"}
    assert "score" not in items["headshoulders"]["surface"]
    assert "gate" not in items["invheadshoulders"]["surface"]
    assert set(items["elliott"]["surface"]) == {"chart", "gate"}
    assert "score" not in items["elliott"]["surface"]
    assert "score" not in items["doubletop"]["surface"]
    assert "score" not in items["downtrend"]["surface"]
    assert "score" not in items["uptrend"]["surface"]
    assert "gate" not in items["doublebottom"]["surface"]
    assert "gate" not in items["uptrend"]["surface"]
    assert "boost" not in items
    assert "relative" not in items
    assert "gaussian" not in items
    assert "例" in items["rsi"]["help"]
    assert "読み取り" in items["sma"]["help"]


def test_catalog_and_groups_cover_plugins() -> None:
    items = catalog()
    assert items
    missing = [item["id"] for item in items if not item.get("group")]
    assert missing == []
    unmapped = [item["id"] for item in items if item["id"] not in PLUGIN_GROUP]
    assert unmapped == []
    for item in items:
        assert item["group"] == PLUGIN_GROUP[item["id"]]
        assert item["group"] != "other"


def test_group_help_has_reason_or_example() -> None:
    groups = group_catalog()
    assert {item["id"] for item in groups} >= {
        "trend",
        "power",
        "shape",
        "wave",
        "market",
        "facts",
        "model",
    }
    for item in groups:
        help_text = item["help"]
        assert "例" in help_text or "なぜ" in help_text, item["id"]


def _dated_bars(closes: list[float], volumes: list[float] | None = None) -> list[dict]:
    start = date(2024, 1, 2)
    rows = []
    for index, close in enumerate(closes):
        day = start + timedelta(days=index)
        volume = 10.0 if volumes is None else volumes[index]
        rows.append(
            {
                "date": day.isoformat(),
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": volume,
            }
        )
    return rows


def test_major_indicators_scores_and_no_lookahead() -> None:
    closes = [100.0 + (index % 9) * 0.35 + index * 0.08 for index in range(90)]
    volumes = [1000.0 + (index % 5) * 80 for index in range(90)]
    bars = _dated_bars(closes, volumes)
    highs = [close + 1 for close in closes]
    lows = [close - 1 for close in closes]
    changed_closes = closes[:-1] + [closes[-1] + 40]
    changed_highs = highs[:-1] + [changed_closes[-1] + 1]
    changed_lows = lows[:-1] + [changed_closes[-1] - 1]
    changed_volumes = volumes[:-1] + [volumes[-1] + 500]

    atr_left = atr(highs, lows, closes, 14)
    atr_right = atr(changed_highs, changed_lows, changed_closes, 14)
    assert atr_left[:-1] == atr_right[:-1]

    bb_left = bollinger(closes, 20, 2.0)
    bb_right = bollinger(changed_closes, 20, 2.0)
    assert bb_left[0][:-1] == bb_right[0][:-1]
    assert bb_left[1][:-1] == bb_right[1][:-1]

    stoch_left = stochastic(highs, lows, closes, 14, 3, 3)
    stoch_right = stochastic(changed_highs, changed_lows, changed_closes, 14, 3, 3)
    assert stoch_left[0][:-1] == stoch_right[0][:-1]

    st_left = supertrend(highs, lows, closes, 10, 3.0)
    st_right = supertrend(changed_highs, changed_lows, changed_closes, 10, 3.0)
    assert st_left[0][:-1] == st_right[0][:-1]

    ichi_left = ichimoku(highs, lows, closes)
    ichi_right = ichimoku(changed_highs, changed_lows, changed_closes)
    assert ichi_left["tenkan"][:-1] == ichi_right["tenkan"][:-1]
    assert ichi_left["kijun"][:-1] == ichi_right["kijun"][:-1]
    assert ichi_left["span_a_raw"][:-1] == ichi_right["span_a_raw"][:-1]

    changed_bars = _dated_bars(changed_closes, changed_volumes)
    for plugin_id in ("atr", "bbands", "stoch", "volma", "supertrend", "ichimoku"):
        result = compute_indicators(bars, [{"id": plugin_id, "params": {}}])[0]
        assert result["error"] is None, plugin_id
        assert "score" in result["features"], plugin_id
        assert result["features"]["score"] is not None, plugin_id
        assert any(overlay["points"] for overlay in result["overlays"]), plugin_id
        other = compute_indicators(changed_bars, [{"id": plugin_id, "params": {}}])[0]
        last_time = bars[-1]["date"]
        for left, right in zip(result["overlays"], other["overlays"], strict=True):
            if left["id"] == "ichi_chikou":
                continue
            left_past = [point for point in left["points"] if point["time"] < last_time]
            right_past = [point for point in right["points"] if point["time"] < last_time]
            assert left_past == right_past, plugin_id


def test_parse_indicator_query() -> None:
    parsed = parse_indicator_query({"ind": "ema,sma", "ema.length": "25", "sma.length": "20"})
    assert [item["id"] for item in parsed] == ["ema", "sma"]
    assert parsed[0]["params"]["length"] == "25"
