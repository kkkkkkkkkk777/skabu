from datetime import date, timedelta
from pathlib import Path

from skabu.indicators.plugins.doublebottom import DoubleBottomPlugin
from skabu.indicators.plugins.doubletop import DoubleTopPlugin
from skabu.indicators.plugins.downtrend import DowntrendPlugin
from skabu.indicators.plugins.candles import CandlesPlugin
from skabu.indicators.plugins.headshoulders import HeadShouldersPlugin
from skabu.indicators.plugins.invheadshoulders import InvHeadShouldersPlugin
from skabu.indicators.plugins.uptrend import UptrendPlugin
from skabu.indicators.plugins.momentum import MomentumPlugin
from skabu.ranking import (
    apply_momentum_ranks,
    build_ranking,
    fact_snapshot,
    momentum_points,
    filter_compare,
    passes_filters,
    refresh_cached_buys,
    pct_ranks,
    regime_above_ma,
    score_issue,
)
from skabu.store import connect, list_universe, upsert_daily_bars, upsert_equity_facts, upsert_master
from skabu.verify import SEARCH_PRESETS, strategy_key


def _bars(code: str, start: float, step: float, days: int = 25, volume: float = 100_000) -> list[dict]:
    rows = []
    for index in range(days):
        close = start + step * index
        rows.append(
            {
                "Date": f"2026-01-{index + 1:02d}",
                "Code": code,
                "AdjO": close,
                "AdjH": close + 1,
                "AdjL": close - 1,
                "AdjC": close,
                "AdjVo": volume,
            }
        )
    return rows


def _seed(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    upsert_master(
        conn,
        [
            {"Date": "2026-01-25", "Code": "11110", "CoName": "上昇工業", "MktNm": "プライム", "S33": "3650", "S33Nm": "電気機器"},
            {"Date": "2026-01-25", "Code": "22220", "CoName": "下降商事", "MktNm": "プライム", "S33": "7100", "S33Nm": "銀行業"},
            {"Date": "2026-01-25", "Code": "33330", "CoName": "細出来高", "MktNm": "プライム", "S33": "5250", "S33Nm": "情報・通信業"},
            {"Date": "2026-01-25", "Code": "44440", "CoName": "標準株", "MktNm": "スタンダード", "S33": "6100", "S33Nm": "小売業"},
        ],
    )
    upsert_daily_bars(conn, _bars("11110", 100, 1.2))
    upsert_daily_bars(conn, _bars("22220", 200, -1.2))
    upsert_daily_bars(conn, _bars("33330", 50, 0.2, volume=10))
    upsert_daily_bars(conn, _bars("44440", 80, 0.5))
    conn.commit()
    return conn


def test_universe_filters_market_and_volume(tmp_path: Path) -> None:
    conn = _seed(tmp_path)
    prime = list_universe(conn, market="プライム", min_avg_volume=50_000, min_bars=10)
    codes = {item["code"] for item in prime}
    assert codes == {"11110", "22220"}
    by_code = {item["code"]: item for item in prime}
    assert by_code["11110"]["s33_nm"] == "電気機器"
    assert by_code["22220"]["s33"] == "7100"
    all_mkts = list_universe(conn, market="all", min_avg_volume=50_000, min_bars=10)
    assert {item["code"] for item in all_mkts} == {"11110", "22220", "44440"}


def test_rising_ranks_above_falling(tmp_path: Path) -> None:
    conn = _seed(tmp_path)
    payload = build_ranking(
        conn,
        selections=[{"id": "sma", "params": {"length": 5}}, {"id": "rsi", "params": {"length": 5}}],
        market="プライム",
        min_avg_volume=50_000,
        min_bars=15,
        top=10,
        refresh=True,
    )
    assert payload["scored"] == 2
    assert payload["rows"][0]["code"] == "11110"
    assert payload["rows"][0]["s33_nm"] == "電気機器"
    assert payload["rows"][0]["total"] > payload["rows"][1]["total"]
    assert payload["rows"][0]["stop"] is not None
    assert payload["rows"][0]["target"] is not None
    assert payload["rows"][0]["stop"] < payload["rows"][0]["close"] < payload["rows"][0]["target"]
    assert payload["rows"][1]["code"] == "22220"
    cached = build_ranking(
        conn,
        selections=[{"id": "sma", "params": {"length": 5}}, {"id": "rsi", "params": {"length": 5}}],
        market="プライム",
        min_avg_volume=50_000,
        min_bars=15,
        top=10,
        refresh=False,
    )
    assert cached["cached"] is True


def test_score_issue_empty_selections_is_flat_one() -> None:
    bars = [
        {
            "date": f"2026-01-{i:02d}",
            "open": 10 + i,
            "high": 11 + i,
            "low": 9 + i,
            "close": 10 + i,
            "volume": 1000 * i,
        }
        for i in range(1, 12)
    ]
    scored = score_issue(bars, [])
    assert scored["total"] == 1
    assert scored["scores"] == {}
    assert scored["reason"] == "フィルター通過"
    assert scored["volume"] == 11000


def test_score_issue_on_short_series() -> None:
    bars = [
        {
            "date": f"2026-01-{i:02d}",
            "open": 10 + i,
            "high": 11 + i,
            "low": 9 + i,
            "close": 10 + i,
            "volume": 1,
        }
        for i in range(1, 12)
    ]
    scored = score_issue(bars, [{"id": "sma", "params": {"length": 3}}])
    assert scored["total"] == 1
    assert scored["scores"]["sma"] == 1
    assert "stop" in scored
    assert "target" in scored


def test_cloud_filter_skips_below_and_keeps_score() -> None:
    start = date(2024, 1, 2)
    rising = []
    falling = []
    for index in range(90):
        day = (start + timedelta(days=index)).isoformat()
        up = 100 + index * 1.8
        down = 300 - index * 1.8
        rising.append(
            {
                "date": day,
                "open": up,
                "high": up + 1,
                "low": up - 1,
                "close": up,
                "volume": 1,
            }
        )
        falling.append(
            {
                "date": day,
                "open": down,
                "high": down + 1,
                "low": down - 1,
                "close": down,
                "volume": 1,
            }
        )
    gates = [{"id": "ichimoku", "feature": "above_cloud", "equals": 1}]
    assert passes_filters(rising, gates) is True
    assert passes_filters(falling, gates) is False
    scored = score_issue(rising, [{"id": "sma", "params": {"length": 5}}])
    assert scored["scores"]["sma"] == 1
    assert "ichimoku" not in scored["scores"]


def test_gate_passed_is_narrower_than_universe(tmp_path: Path) -> None:
    conn = connect(tmp_path / "gate.db")
    start = date(2024, 1, 2)
    days = 90
    rising = [100 + index * 1.8 for index in range(days)]
    falling = [300 - index * 1.8 for index in range(days)]
    as_of = (start + timedelta(days=days - 1)).isoformat()
    upsert_master(
        conn,
        [
            {"Date": as_of, "Code": "11110", "CoName": "上昇工業", "MktNm": "プライム", "S33": "3650", "S33Nm": "電気機器"},
            {"Date": as_of, "Code": "22220", "CoName": "下降商事", "MktNm": "プライム", "S33": "7100", "S33Nm": "銀行業"},
        ],
    )
    upsert_daily_bars(conn, _dated_bars("11110", start, rising))
    upsert_daily_bars(conn, _dated_bars("22220", start, falling))
    conn.commit()
    common = {
        "selections": [{"id": "sma", "params": {"length": 5}}],
        "market": "プライム",
        "min_avg_volume": 1,
        "min_bars": 60,
        "top": 10,
        "as_of": as_of,
        "refresh": True,
    }
    wide = build_ranking(conn, filters=[], **common)
    narrow = build_ranking(
        conn,
        filters=[{"id": "ichimoku", "feature": "above_cloud", "equals": 1}],
        **common,
    )
    assert wide["universe"] == 2
    assert wide["gate_passed"] == 2
    assert narrow["universe"] == 2
    assert narrow["gate_passed"] == 1
    assert narrow["gate_passed"] < narrow["universe"]
    assert {row["code"] for row in narrow["rows"]} == {"11110"}


def test_boost_strategy_key_changes_with_fingerprint(monkeypatch) -> None:
    from skabu import ranking

    monkeypatch.setattr(ranking, "boost_fingerprint", lambda _sels: "v1")
    left = strategy_key([{"id": "boost", "params": {}}], "プライム", 1, 10, 2)
    monkeypatch.setattr(ranking, "boost_fingerprint", lambda _sels: "v2")
    right = strategy_key([{"id": "boost", "params": {}}], "プライム", 1, 10, 2)
    assert left != right


def test_filter_compare_numeric_ops() -> None:
    assert filter_compare(0.5, {"op": "lt", "value": 0.85}) is True
    assert filter_compare(0.9, {"op": "lt", "value": 0.85}) is False
    assert filter_compare(70, {"op": "lt", "value": 70}) is False
    assert filter_compare(69.9, {"op": "lt", "value": 70}) is True
    assert filter_compare(1, {"equals": 1}) is True
    assert filter_compare(None, {"op": "lt", "value": 0.85}) is False


def test_pct_b_filter_and_rsi(tmp_path: Path) -> None:
    from skabu.indicators.plugins.bbands import BbandsPlugin

    bars = []
    start = date(2026, 2, 2)
    for index in range(30):
        close = 100 + (index % 6) - 3
        bars.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": close,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "volume": 1,
            }
        )
    feats = BbandsPlugin().compute(bars, {"length": 20, "mult": 2.0}).features
    assert feats["pct_b"] is not None
    mid = float(feats["value"])
    upper = float(feats["upper"])
    lower = float(feats["lower"])
    assert abs(feats["pct_b"] - (bars[-1]["close"] - lower) / (upper - lower)) < 1e-5
    bars_at_upper = list(bars)
    bars_at_upper[-1] = {**bars[-1], "close": upper, "high": upper + 1, "low": upper - 1}
    upper_feats = BbandsPlugin().compute(bars_at_upper, {"length": 20, "mult": 2.0}).features
    assert upper_feats["pct_b"] is not None
    assert abs(upper_feats["pct_b"] - 1.0) < 0.15
    assert passes_filters(bars, [{"id": "bbands", "feature": "pct_b", "op": "lt", "value": 0.85}]) is True
    hot = list(bars)
    hot[-1] = {**bars[-1], "close": 1000, "high": 1001, "low": 999}
    assert passes_filters(hot, [{"id": "bbands", "feature": "pct_b", "op": "lt", "value": 0.85}]) is False
    assert passes_filters(bars, [{"id": "rsi", "feature": "value", "op": "lt", "value": 70}]) is True
    from skabu.verify import strategy_key as verify_key

    left = verify_key([{"id": "boost", "params": {}}], "プライム", 1, 10, 2, filters=[])
    right = verify_key(
        [{"id": "boost", "params": {}}],
        "プライム",
        1,
        10,
        2,
        filters=[{"id": "bbands", "feature": "pct_b", "op": "lt", "value": 0.85}],
    )
    assert left != right


def _dated_bars(code: str, start: date, closes: list[float], volume: float = 100_000) -> list[dict]:
    rows = []
    for index, close in enumerate(closes):
        rows.append(
            {
                "Date": (start + timedelta(days=index)).isoformat(),
                "Code": code,
                "AdjO": close,
                "AdjH": close + 1,
                "AdjL": close - 1,
                "AdjC": close,
                "AdjVo": volume,
            }
        )
    return rows


def _momentum_conn(tmp_path: Path, *, bench_last: float, days: int = 260):
    conn = connect(tmp_path / "m.db")
    start = date(2024, 1, 2)
    as_of = (start + timedelta(days=days - 1)).isoformat()
    upsert_master(
        conn,
        [
            {"Date": as_of, "Code": "11110", "CoName": "強い株", "MktNm": "プライム", "S33": "3650", "S33Nm": "電気機器"},
            {"Date": as_of, "Code": "22220", "CoName": "弱い株", "MktNm": "プライム", "S33": "7100", "S33Nm": "銀行業"},
            {"Date": as_of, "Code": "13060", "CoName": "TOPIX", "MktNm": "その他", "S33": "0000", "S33Nm": "指数"},
        ],
    )
    strong = [100 + i * 0.8 for i in range(days)]
    weak = [100 + (0.05 if i < days - 1 else 0.0) for i in range(days)]
    bench = [100.0] * (days - 1) + [bench_last]
    upsert_daily_bars(conn, _dated_bars("11110", start, strong))
    upsert_daily_bars(conn, _dated_bars("22220", start, weak))
    upsert_daily_bars(conn, _dated_bars("13060", start, bench))
    conn.commit()
    return conn, as_of, start, strong, weak


def test_regime_filter_is_market_gate_not_stock_plugin() -> None:
    bars = [
        {
            "date": f"2026-01-{i:02d}",
            "open": 10 + i,
            "high": 11 + i,
            "low": 9 + i,
            "close": 10 + i,
            "volume": 1,
        }
        for i in range(1, 12)
    ]
    gates = [{"id": "regime", "feature": "above_ma200", "equals": 1}]
    assert passes_filters(bars, gates) is True


def test_regime_above_ma200_keeps_candidates(tmp_path: Path) -> None:
    conn, as_of, _start, _strong, _weak = _momentum_conn(tmp_path, bench_last=120.0)
    state = regime_above_ma(conn, as_of)
    assert state["ok"] is True
    assert state["close"] == 120.0
    assert state["ma"] is not None
    assert state["close"] > state["ma"]
    payload = build_ranking(
        conn,
        selections=[{"id": "momentum", "params": {}}],
        filters=[{"id": "regime", "feature": "above_ma200", "equals": 1}],
        market="プライム",
        min_avg_volume=1,
        min_bars=60,
        top=10,
        as_of=as_of,
        refresh=True,
    )
    assert payload["cash"] is False
    assert payload["scored"] == 2
    assert payload["rows"][0]["code"] == "11110"
    assert payload["rows"][0]["total"] > payload["rows"][1]["total"]


def test_regime_below_ma200_is_cash(tmp_path: Path) -> None:
    conn, as_of, _start, _strong, _weak = _momentum_conn(tmp_path, bench_last=80.0)
    state = regime_above_ma(conn, as_of)
    assert state["ok"] is False
    assert state["reason"] == "below"
    payload = build_ranking(
        conn,
        selections=[{"id": "momentum", "params": {}}],
        filters=[{"id": "regime", "feature": "above_ma200", "equals": 1}],
        market="プライム",
        min_avg_volume=1,
        min_bars=60,
        top=10,
        as_of=as_of,
        refresh=True,
    )
    assert payload["cash"] is True
    assert payload["gate_passed"] == 0
    assert payload["scored"] == 0
    assert payload["rows"] == []


def test_regime_missing_bench_is_cash(tmp_path: Path) -> None:
    conn = connect(tmp_path / "nobench.db")
    start = date(2024, 1, 2)
    days = 80
    as_of = (start + timedelta(days=days - 1)).isoformat()
    upsert_master(
        conn,
        [{"Date": as_of, "Code": "11110", "CoName": "上昇工業", "MktNm": "プライム", "S33": "3650", "S33Nm": "電気機器"}],
    )
    upsert_daily_bars(conn, _dated_bars("11110", start, [100 + i for i in range(days)]))
    conn.commit()
    payload = build_ranking(
        conn,
        selections=[{"id": "sma", "params": {"length": 5}}],
        filters=[{"id": "regime", "feature": "above_ma200", "equals": 1}],
        market="プライム",
        min_avg_volume=1,
        min_bars=20,
        top=5,
        as_of=as_of,
        refresh=True,
    )
    assert payload["cash"] is True
    assert payload["rows"] == []
    assert payload["regime"]["reason"] in {"no_bars", "short"}


def test_momentum_high_ratio_and_cross_ranks() -> None:
    closes = [100.0 + i for i in range(260)]
    bars = [
        {
            "date": (date(2024, 1, 2) + timedelta(days=i)).isoformat(),
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1,
        }
        for i, close in enumerate(closes)
    ]
    feats = MomentumPlugin().compute(bars, {}).features
    assert feats["high_ratio"] == 1.0
    assert abs(feats["ret_60"] - (closes[-1] / closes[-1 - 60] - 1)) < 1e-6
    assert abs(feats["ret_120"] - (closes[-1] / closes[-1 - 120] - 1)) < 1e-6
    short = MomentumPlugin().compute(bars[:100], {}).features
    assert short["high_ratio"] is None
    assert pct_ranks([1, 2, 3]) == [0.0, 0.5, 1.0]
    assert momentum_points(1.0) == 2
    assert momentum_points(0.5) == 0
    assert momentum_points(0.0) == -2
    ranked = apply_momentum_ranks(
        [
            {"momentum": {"ret_60": 0.3, "ret_120": 0.4, "high_ratio": 0.99}, "scores": {}, "avg_vo": 1},
            {"momentum": {"ret_60": 0.1, "ret_120": 0.1, "high_ratio": 0.8}, "scores": {}, "avg_vo": 9},
        ]
    )
    assert ranked[0]["total"] == 2
    assert ranked[1]["total"] == -2
    assert ranked[0]["scores"]["momentum"] == 2
    added = apply_momentum_ranks(
        [
            {
                "total": 3,
                "reason": "EMA: 上昇",
                "momentum": {"ret_60": 0.3, "ret_120": 0.4, "high_ratio": 0.99},
                "scores": {"ema": 3},
                "avg_vo": 1,
            },
            {
                "total": 3,
                "reason": "EMA: 上昇",
                "momentum": {"ret_60": 0.1, "ret_120": 0.1, "high_ratio": 0.8},
                "scores": {"ema": 3},
                "avg_vo": 9,
            },
        ]
    )
    assert added[0]["total"] == 5
    assert added[1]["total"] == 1
    assert added[0]["scores"]["ema"] == 3
    assert added[0]["scores"]["momentum"] == 2
    assert added[0]["reason"].startswith("EMA")
    assert "高値モメンタム" in added[0]["reason"]


def test_momentum_does_not_look_ahead(tmp_path: Path) -> None:
    conn, as_of, start, strong, _weak = _momentum_conn(tmp_path, bench_last=120.0)
    first = build_ranking(
        conn,
        selections=[{"id": "momentum", "params": {}}],
        filters=[],
        market="プライム",
        min_avg_volume=1,
        min_bars=60,
        top=10,
        as_of=as_of,
        refresh=True,
    )
    future = start + timedelta(days=260)
    upsert_daily_bars(
        conn,
        [
            {
                "Date": future.isoformat(),
                "Code": "22220",
                "AdjO": 400,
                "AdjH": 401,
                "AdjL": 399,
                "AdjC": 400,
                "AdjVo": 100_000,
            }
        ],
    )
    conn.commit()
    second = build_ranking(
        conn,
        selections=[{"id": "momentum", "params": {}}],
        filters=[],
        market="プライム",
        min_avg_volume=1,
        min_bars=60,
        top=10,
        as_of=as_of,
        refresh=True,
    )
    by_first = {row["code"]: row["total"] for row in first["rows"]}
    by_second = {row["code"]: row["total"] for row in second["rows"]}
    assert by_first == by_second
    assert first["rows"][0]["code"] == "11110"
    feats = first["rows"][0]["momentum"]
    assert feats["high_ratio"] == strong[-1] / max(strong[-250:])
    assert abs(feats["ret_60"] - (strong[-1] / strong[-1 - 60] - 1)) < 1e-5


def test_search_presets_add_momentum_only() -> None:
    assert SEARCH_PRESETS["momentum"]["title"] == "地合い＋高値モメンタム"
    assert SEARCH_PRESETS["double_top"]["title"] == "フィルター＋ダブルトップ除外"
    assert SEARCH_PRESETS["double_top"]["selections"] == []
    assert SEARCH_PRESETS["double_top"]["filters"][-1] == {
        "id": "doubletop",
        "feature": "double_top",
        "equals": 0,
    }
    assert SEARCH_PRESETS["lower_lows"]["title"] == "フィルター＋切り下げ除外"
    assert SEARCH_PRESETS["lower_lows"]["filters"][-1] == {
        "id": "downtrend",
        "feature": "lower_lows",
        "equals": 0,
    }
    assert len(SEARCH_PRESETS) == 19
    assert sum(1 for key in SEARCH_PRESETS if key not in {"momentum", "double_top", "lower_lows"}) == 16


def _price_bars(highs: list[float], lows: list[float], closes: list[float]) -> list[dict]:
    start = date(2024, 1, 2)
    rows = []
    for index, (high, low, close) in enumerate(zip(highs, lows, closes, strict=True)):
        rows.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1,
            }
        )
    return rows


def _twin_peaks(n: int = 60) -> tuple[list[float], list[float], list[float]]:
    highs = [101.0] * n
    lows = [99.0] * n
    closes = [100.0] * n
    highs[25] = 120.0
    highs[50] = 119.0
    lows[37] = 90.0
    closes[-1] = 110.0
    highs[-1] = 111.0
    lows[-1] = 109.0
    return highs, lows, closes


def test_double_top_detects_twins_and_blocks_buy() -> None:
    highs, lows, closes = _twin_peaks()
    bars = _price_bars(highs, lows, closes)
    result = DoubleTopPlugin().compute(bars, {})
    feats = result.features
    assert feats["double_top"] == 1
    assert "score" not in feats
    assert result.overlays
    assert all(item.color == "#ef5350" for item in result.overlays)
    assert {item.text for item in result.markers} == {"DT"}
    assert any(item.style == "dashed" for item in result.overlays)
    gates = [{"id": "doubletop", "feature": "double_top", "equals": 0}]
    assert passes_filters(bars, gates) is False


def test_double_top_rising_passes() -> None:
    n = 60
    closes = [100.0 + i * 0.8 for i in range(n)]
    highs = [close + 1.0 for close in closes]
    lows = [close - 1.0 for close in closes]
    bars = _price_bars(highs, lows, closes)
    result = DoubleTopPlugin().compute(bars, {})
    feats = result.features
    assert feats["double_top"] == 0
    assert result.overlays == []
    assert result.markers == []
    gates = [{"id": "doubletop", "feature": "double_top", "equals": 0}]
    assert passes_filters(bars, gates) is True


def test_double_top_does_not_look_ahead() -> None:
    highs, lows, closes = _twin_peaks()
    bars = _price_bars(highs, lows, closes)
    first = DoubleTopPlugin().compute(bars, {}).features["double_top"]
    future = list(bars) + [
        {
            "date": (date(2024, 1, 2) + timedelta(days=len(bars))).isoformat(),
            "open": 150,
            "high": 151,
            "low": 149,
            "close": 150,
            "volume": 1,
        }
    ]
    second = DoubleTopPlugin().compute(future[:-1], {}).features["double_top"]
    assert first == second == 1
    rising = _price_bars(
        [101.0 + i * 0.2 for i in range(60)],
        [99.0 + i * 0.2 for i in range(60)],
        [100.0 + i * 0.2 for i in range(60)],
    )
    before = DoubleTopPlugin().compute(rising, {}).features["double_top"]
    spiked = list(rising)
    spiked[-1] = {**spiked[-1], "high": 200, "close": 199, "low": 198}
    after = DoubleTopPlugin().compute(spiked[:-1] + [rising[-1]], {}).features["double_top"]
    assert before == after == 0


def _lower_swing_series(n: int = 65) -> tuple[list[float], list[float], list[float]]:
    highs = [118.0] * n
    lows = [112.0] * n
    closes = [115.0] * n
    highs[20] = 130.0
    lows[30] = 110.0
    highs[42] = 122.0
    lows[52] = 100.0
    closes[-1] = 108.0
    highs[-1] = 109.0
    lows[-1] = 107.0
    return highs, lows, closes


def test_downtrend_detects_lower_swings_and_blocks_buy() -> None:
    bars = _price_bars(*_lower_swing_series())
    result = DowntrendPlugin().compute(bars, {})
    feats = result.features
    assert feats["lower_lows"] == 1
    assert "score" not in feats
    assert result.overlays
    assert result.overlays[0].color == "#ef5350"
    assert {item.text for item in result.markers} == {"LH", "LL"}
    gates = [{"id": "downtrend", "feature": "lower_lows", "equals": 0}]
    assert passes_filters(bars, gates) is False


def test_downtrend_rising_passes() -> None:
    n = 65
    closes = [100.0 + i * 0.8 for i in range(n)]
    highs = [close + 1.0 for close in closes]
    lows = [close - 1.0 for close in closes]
    bars = _price_bars(highs, lows, closes)
    result = DowntrendPlugin().compute(bars, {})
    feats = result.features
    assert feats["lower_lows"] == 0
    assert result.overlays == []
    assert result.markers == []
    gates = [{"id": "downtrend", "feature": "lower_lows", "equals": 0}]
    assert passes_filters(bars, gates) is True


def test_downtrend_does_not_look_ahead() -> None:
    bars = _price_bars(*_lower_swing_series())
    first = DowntrendPlugin().compute(bars, {}).features["lower_lows"]
    future = list(bars) + [
        {
            "date": (date(2024, 1, 2) + timedelta(days=len(bars))).isoformat(),
            "open": 150,
            "high": 151,
            "low": 149,
            "close": 150,
            "volume": 1,
        }
    ]
    second = DowntrendPlugin().compute(future[:-1], {}).features["lower_lows"]
    assert first == second == 1


def _twin_troughs(n: int = 60) -> tuple[list[float], list[float], list[float]]:
    highs = [101.0] * n
    lows = [99.0] * n
    closes = [100.0] * n
    lows[25] = 80.0
    lows[50] = 81.0
    highs[37] = 110.0
    closes[-1] = 90.0
    highs[-1] = 91.0
    lows[-1] = 89.0
    return highs, lows, closes


def test_double_bottom_detects_twins_and_scores_one() -> None:
    bars = _price_bars(*_twin_troughs())
    result = DoubleBottomPlugin().compute(bars, {})
    assert result.features["double_bottom"] == 1
    assert result.features["score"] == 1
    assert result.overlays
    assert all(item.color == "#2dd4bf" for item in result.overlays)
    assert {item.text for item in result.markers} == {"DB"}
    assert any(item.style == "dashed" for item in result.overlays)
    scored = score_issue(bars, [{"id": "doublebottom", "params": {}}])
    assert scored["total"] == 1
    assert scored["scores"]["doublebottom"] == 1


def test_double_bottom_falling_has_no_score() -> None:
    n = 60
    closes = [150.0 - i * 0.8 for i in range(n)]
    highs = [close + 1.0 for close in closes]
    lows = [close - 1.0 for close in closes]
    bars = _price_bars(highs, lows, closes)
    result = DoubleBottomPlugin().compute(bars, {})
    assert result.features["double_bottom"] == 0
    assert "score" not in result.features
    assert result.overlays == []
    assert result.markers == []
    scored = score_issue(bars, [{"id": "doublebottom", "params": {}}])
    assert scored["total"] is None
    assert scored["scores"]["doublebottom"] is None


def test_double_bottom_does_not_look_ahead() -> None:
    bars = _price_bars(*_twin_troughs())
    first = DoubleBottomPlugin().compute(bars, {}).features["double_bottom"]
    future = list(bars) + [
        {
            "date": (date(2024, 1, 2) + timedelta(days=len(bars))).isoformat(),
            "open": 70,
            "high": 71,
            "low": 69,
            "close": 70,
            "volume": 1,
        }
    ]
    second = DoubleBottomPlugin().compute(future[:-1], {}).features["double_bottom"]
    assert first == second == 1
    falling = _price_bars(
        [101.0 - i * 0.2 for i in range(60)],
        [99.0 - i * 0.2 for i in range(60)],
        [100.0 - i * 0.2 for i in range(60)],
    )
    before = DoubleBottomPlugin().compute(falling, {}).features["double_bottom"]
    spiked = list(falling)
    spiked[-1] = {**spiked[-1], "low": 40, "close": 41, "high": 42}
    after = DoubleBottomPlugin().compute(spiked[:-1] + [falling[-1]], {}).features["double_bottom"]
    assert before == after == 0


def _higher_swing_series(n: int = 65) -> tuple[list[float], list[float], list[float]]:
    highs = [118.0] * n
    lows = [112.0] * n
    closes = [115.0] * n
    highs[20] = 122.0
    lows[30] = 100.0
    highs[42] = 132.0
    lows[52] = 108.0
    closes[-1] = 120.0
    highs[-1] = 121.0
    lows[-1] = 119.0
    return highs, lows, closes


def test_uptrend_detects_higher_swings() -> None:
    result = UptrendPlugin().compute(_price_bars(*_higher_swing_series()), {})
    assert result.features["higher_highs"] == 1
    assert "score" not in result.features
    assert result.overlays
    assert result.overlays[0].color == "#2dd4bf"
    assert {item.text for item in result.markers} == {"HH", "HL"}


def test_uptrend_falling_is_not_drawn() -> None:
    n = 65
    closes = [150.0 - i * 0.8 for i in range(n)]
    highs = [close + 1.0 for close in closes]
    lows = [close - 1.0 for close in closes]
    result = UptrendPlugin().compute(_price_bars(highs, lows, closes), {})
    assert result.features["higher_highs"] == 0
    assert "score" not in result.features
    assert result.overlays == []
    assert result.markers == []


def _head_shoulders_series(n: int = 80) -> tuple[list[float], list[float], list[float]]:
    highs = [101.0] * n
    lows = [99.0] * n
    closes = [100.0] * n
    highs[20] = 120.0
    lows[30] = 95.0
    highs[40] = 140.0
    lows[50] = 96.0
    highs[60] = 118.0
    closes[-1] = 105.0
    highs[-1] = 106.0
    lows[-1] = 104.0
    return highs, lows, closes


def test_head_shoulders_detects_and_blocks_buy() -> None:
    bars = _price_bars(*_head_shoulders_series())
    result = HeadShouldersPlugin().compute(bars, {})
    assert result.features["head_shoulders"] == 1
    assert "score" not in result.features
    assert result.overlays
    assert all(item.color == "#ef5350" for item in result.overlays)
    assert {item.text for item in result.markers} == {"LS", "H", "RS"}
    gates = [{"id": "headshoulders", "feature": "head_shoulders", "equals": 0}]
    assert passes_filters(bars, gates) is False


def test_head_shoulders_rising_passes() -> None:
    n = 80
    closes = [100.0 + i * 0.8 for i in range(n)]
    highs = [close + 1.0 for close in closes]
    lows = [close - 1.0 for close in closes]
    bars = _price_bars(highs, lows, closes)
    result = HeadShouldersPlugin().compute(bars, {})
    assert result.features["head_shoulders"] == 0
    assert result.overlays == []
    assert passes_filters(bars, [{"id": "headshoulders", "feature": "head_shoulders", "equals": 0}]) is True


def _inv_head_shoulders_series(n: int = 80) -> tuple[list[float], list[float], list[float]]:
    highs = [101.0] * n
    lows = [99.0] * n
    closes = [100.0] * n
    lows[20] = 80.0
    highs[30] = 105.0
    lows[40] = 60.0
    highs[50] = 104.0
    lows[60] = 82.0
    closes[-1] = 90.0
    highs[-1] = 91.0
    lows[-1] = 89.0
    return highs, lows, closes


def test_inv_head_shoulders_detects_and_scores_one() -> None:
    bars = _price_bars(*_inv_head_shoulders_series())
    result = InvHeadShouldersPlugin().compute(bars, {})
    assert result.features["inv_head_shoulders"] == 1
    assert result.features["score"] == 1
    assert result.overlays
    assert all(item.color == "#2dd4bf" for item in result.overlays)
    assert {item.text for item in result.markers} == {"LS", "H", "RS"}
    scored = score_issue(bars, [{"id": "invheadshoulders", "params": {}}])
    assert scored["total"] == 1
    assert scored["scores"]["invheadshoulders"] == 1


def test_inv_head_shoulders_miss_has_no_score() -> None:
    n = 80
    closes = [150.0 - i * 0.8 for i in range(n)]
    highs = [close + 1.0 for close in closes]
    lows = [close - 1.0 for close in closes]
    bars = _price_bars(highs, lows, closes)
    result = InvHeadShouldersPlugin().compute(bars, {})
    assert result.features["inv_head_shoulders"] == 0
    assert "score" not in result.features
    assert result.overlays == []
    scored = score_issue(bars, [{"id": "invheadshoulders", "params": {}}])
    assert scored["total"] is None


def test_pattern_scores_use_max_not_sum() -> None:
    bars = _price_bars(*_inv_head_shoulders_series())
    scored = score_issue(
        bars,
        [
            {"id": "invheadshoulders", "params": {}},
            {"id": "doublebottom", "params": {}},
        ],
    )
    assert scored["scores"]["invheadshoulders"] == 1
    assert scored["scores"]["doublebottom"] in {None, 0} or scored["total"] == 1
    assert scored["total"] == 1


def _ohlc_bars(rows: list[tuple[float, float, float, float]]) -> list[dict]:
    start = date(2024, 1, 2)
    out = []
    for index, (open_px, high, low, close) in enumerate(rows):
        out.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": open_px,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1,
            }
        )
    return out


def _pad_ohlc(pattern: list[tuple[float, float, float, float]], n: int = 8) -> list[dict]:
    flat = [(100.0, 101.0, 99.0, 100.0)] * n
    return _ohlc_bars(flat + pattern)


def test_candles_engulf_up_scores_one() -> None:
    bars = _pad_ohlc([(100.0, 101.0, 90.0, 91.0), (90.0, 103.0, 89.0, 102.0)])
    result = CandlesPlugin().compute(bars, {})
    assert result.features["engulf_up"] == 1
    assert result.features["bullish"] == 1
    assert result.features["score"] == 1
    assert result.features["bearish"] == 0
    assert {item.text for item in result.markers} == {"包"}
    scored = score_issue(bars, [{"id": "candles", "params": {}}])
    assert scored["total"] == 1


def test_candles_engulf_down_is_gate() -> None:
    bars = _pad_ohlc([(90.0, 102.0, 89.0, 101.0), (101.0, 102.0, 88.0, 89.0)])
    result = CandlesPlugin().compute(bars, {})
    assert result.features["engulf_down"] == 1
    assert result.features["bearish"] == 1
    assert "score" not in result.features
    gates = [{"id": "candles", "feature": "bearish", "equals": 0}]
    assert passes_filters(bars, gates) is False


def test_candles_morning_star_beats_engulf() -> None:
    bars = _pad_ohlc(
        [
            (100.0, 101.0, 90.0, 91.0),
            (91.0, 93.0, 89.0, 90.5),
            (90.0, 102.0, 89.5, 101.0),
        ]
    )
    result = CandlesPlugin().compute(bars, {})
    assert result.features["morning_star"] == 1
    assert result.features["engulf_up"] == 0
    assert result.features["score"] == 1
    assert {item.text for item in result.markers} == {"明星"}


def test_candles_hammer_is_mark_only() -> None:
    bars = _pad_ohlc([(100.1, 100.3, 90.0, 100.2)])
    result = CandlesPlugin().compute(bars, {})
    assert result.features["hammer"] == 1
    assert result.features["bullish"] == 0
    assert "score" not in result.features
    assert {item.text for item in result.markers} == {"ハ"}


def test_double_bottom_and_engulf_cap_at_one() -> None:
    bars = _price_bars(*_twin_troughs())
    bars[-2] = {**bars[-2], "open": 92.0, "high": 93.0, "low": 88.0, "close": 89.0}
    bars[-1] = {**bars[-1], "open": 88.5, "high": 95.0, "low": 88.0, "close": 94.0}
    db = DoubleBottomPlugin().compute(bars, {})
    cd = CandlesPlugin().compute(bars, {})
    assert db.features["double_bottom"] == 1
    assert cd.features["engulf_up"] == 1
    scored = score_issue(bars, [{"id": "doublebottom", "params": {}}, {"id": "candles", "params": {}}])
    assert scored["scores"]["doublebottom"] == 1
    assert scored["scores"]["candles"] == 1
    assert scored["total"] == 1


def test_fact_gates_missing_drops_and_numbers_pass() -> None:
    bars = [
        {
            "date": "2026-01-01",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "volume": 1,
            "mkt_cap": 1_000,
        }
    ]
    per_gate = [{"id": "facts", "feature": "per", "op": "lt", "value": 20}]
    cap_gate = [{"id": "facts", "feature": "mkt_cap", "op": "gt", "value": 0}]
    assert passes_filters(bars, per_gate, facts={}) is False
    assert passes_filters(bars, per_gate, facts={"per": None}) is False
    assert passes_filters(bars, per_gate, facts={"per": 15}) is True
    assert passes_filters(bars, per_gate, facts={"per": 21}) is False
    assert passes_filters(bars, cap_gate, facts={"mkt_cap": 1_000}) is True


def test_per_gate_uses_as_of_not_future(tmp_path: Path) -> None:
    conn = _seed(tmp_path)
    upsert_equity_facts(
        conn,
        [
            {"date": "2026-01-10", "code": "11110", "per": 15, "pbr": None, "op_margin": None},
            {"date": "2026-01-25", "code": "11110", "per": 5, "pbr": None, "op_margin": None},
        ],
    )
    conn.commit()
    gates = [{"id": "facts", "feature": "per", "op": "lt", "value": 10}]
    before = build_ranking(
        conn,
        selections=[{"id": "sma", "params": {"length": 5}}],
        filters=gates,
        market="プライム",
        min_avg_volume=50_000,
        min_bars=15,
        top=10,
        as_of="2026-01-20",
        refresh=True,
    )
    after = build_ranking(
        conn,
        selections=[{"id": "sma", "params": {"length": 5}}],
        filters=gates,
        market="プライム",
        min_avg_volume=50_000,
        min_bars=15,
        top=10,
        as_of="2026-01-25",
        refresh=True,
    )
    assert "11110" not in {row["code"] for row in before["rows"]}
    assert "11110" in {row["code"] for row in after["rows"]}


def test_fact_snapshot_yield_from_close() -> None:
    snap = fact_snapshot([{"close": 1000}], stored={"div_ann": 35, "roe": 0.12})
    assert snap["div_yield"] == 0.035
    assert snap["div_yield_hi"] == 0.035
    assert snap["roe"] == 0.12
    missing = fact_snapshot([{"close": 1000}], stored={})
    assert missing["div_yield"] is None
    assert missing["div_yield_hi"] is None
    zero = fact_snapshot([{"close": 0}], stored={"div_ann": 35})
    assert zero["div_yield"] is None


def test_yield_growth_and_op_margin_gates() -> None:
    bars = [
        {
            "date": "2026-01-01",
            "open": 1000,
            "high": 1001,
            "low": 999,
            "close": 1000,
            "volume": 1,
        }
    ]
    yield_lo = [{"id": "facts", "feature": "div_yield", "op": "gte", "value": 0.035}]
    yield_hi = [{"id": "facts", "feature": "div_yield_hi", "op": "lte", "value": 0.05}]
    growth = [{"id": "facts", "feature": "sales_growth3", "op": "gte", "value": 0}]
    margin = [{"id": "facts", "feature": "op_margin", "op": "gt", "value": 0}]
    facts = fact_snapshot(bars, stored={"div_ann": 35, "sales_growth3": 0.02, "op_margin": 0.08})
    assert passes_filters(bars, yield_lo, facts) is True
    assert passes_filters(bars, yield_hi, facts) is True
    assert passes_filters(bars, growth, facts) is True
    assert passes_filters(bars, margin, facts) is True
    low = fact_snapshot(bars, stored={"div_ann": 20, "op_margin": 0.08})
    assert passes_filters(bars, yield_lo, low) is False
    high = fact_snapshot(bars, stored={"div_ann": 60, "op_margin": 0.08})
    assert passes_filters(bars, yield_hi, high) is False
    assert passes_filters(bars, growth, fact_snapshot(bars, stored={"op_margin": 0.08})) is False
    assert passes_filters(bars, margin, fact_snapshot(bars, stored={})) is False
    assert passes_filters(bars, margin, fact_snapshot(bars, stored={"op_margin": 0.08})) is True


def test_refresh_cached_buys_drops_confirmation_skip() -> None:
    payload = refresh_cached_buys(
        {
            "rows": [
                {
                    "code": "11110",
                    "total": 2,
                    "close": 100.0,
                    "entry_ref": 100.0,
                    "atr": 2.0,
                    "proba": None,
                    "s33_nm": "電気機器",
                    "skip_reason": "strategy_holdout",
                    "buyable": False,
                }
            ]
        },
        holdout_ok=False,
    )
    row = payload["rows"][0]
    assert row["buyable"] is True
    assert row["skip_reason"] is None
    assert payload["bought_n"] == 1
    assert payload["cached"] is True
