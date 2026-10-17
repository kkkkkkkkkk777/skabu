from pathlib import Path

import pytest

from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.indicators.mathutil import atr
from skabu.levels import (
    LABEL_SPEC,
    LEVELS_SPEC,
    compute_levels,
    levels_from_anchor,
    load_exit_levels,
    save_exit_levels,
    simulate_exit,
    simulate_trade,
)
from skabu.verify import PRESETS, strategy_key


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
            }
        )
    return rows


def test_atr_no_lookahead() -> None:
    closes = [100.0 + index * 0.3 for index in range(30)]
    highs = [item + 1 for item in closes]
    lows = [item - 1 for item in closes]
    left = atr(highs, lows, closes, 14)
    changed_closes = closes[:-1] + [closes[-1] + 20]
    changed_highs = highs[:-1] + [changed_closes[-1] + 1]
    changed_lows = lows[:-1] + [changed_closes[-1] - 1]
    right = atr(changed_highs, changed_lows, changed_closes, 14)
    assert left[:-1] == right[:-1]


def test_compute_levels_rr() -> None:
    bars = _bars([100.0 + index * 0.2 for index in range(30)])
    levels = compute_levels(bars)
    assert levels["atr"] is not None
    assert levels["stop"] < levels["entry_ref"] < levels["target"]
    assert abs(levels["rr"] - 2.0) < 1e-6
    assert levels["provisional"] is True
    entered = compute_levels(bars, anchor=120.0)
    assert entered["entry_ref"] == 120.0
    assert entered["provisional"] is False
    assert abs((entered["target"] - 120.0) / (120.0 - entered["stop"]) - 2.0) < 1e-6
    assert LEVELS_SPEC["anchor"] == "entry"
    assert LEVELS_SPEC["label"] == LABEL_SPEC == "target_first"


def test_simulate_exit_reasons() -> None:
    time_path = [
        {"date": "2026-01-02", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5},
        {"date": "2026-01-03", "open": 100.5, "high": 101.0, "low": 99.5, "close": 100.8},
    ]
    timed = simulate_exit(100.0, 90.0, 120.0, time_path)
    assert timed["reason"] == "time"
    assert timed["exit"] == 100.8

    stop_path = [
        {"date": "2026-01-02", "open": 100.0, "high": 101.0, "low": 94.0, "close": 95.0},
    ]
    stopped = simulate_exit(100.0, 95.0, 110.0, stop_path)
    assert stopped["reason"] == "stop"
    assert stopped["exit"] == 95.0

    target_path = [
        {"date": "2026-01-02", "open": 100.0, "high": 111.0, "low": 99.0, "close": 110.0},
    ]
    taken = simulate_exit(100.0, 90.0, 110.0, target_path)
    assert taken["reason"] == "target"
    assert taken["exit"] == 110.0
    held = simulate_exit(100.0, 90.0, 110.0, target_path, take_profit=False)
    assert held["reason"] == "time"
    assert held["exit"] == 110.0
    still_stopped = simulate_exit(100.0, 95.0, 110.0, stop_path, take_profit=False)
    assert still_stopped["reason"] == "stop"
    assert still_stopped["exit"] == 95.0

    both = [
        {"date": "2026-01-02", "open": 100.0, "high": 120.0, "low": 80.0, "close": 110.0},
    ]
    pessimistic = simulate_exit(100.0, 90.0, 110.0, both)
    assert pessimistic["reason"] == "stop"
    assert pessimistic["exit"] == 90.0

    gap = [
        {"date": "2026-01-02", "open": 85.0, "high": 88.0, "low": 80.0, "close": 84.0},
    ]
    gapped = simulate_exit(100.0, 90.0, 110.0, gap)
    assert gapped["reason"] == "stop"
    assert gapped["exit"] == 85.0


def test_simulate_trade_target_within_window() -> None:
    path = [
        {"date": "2026-01-02", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5},
        {"date": "2026-01-03", "open": 101.0, "high": 102.0, "low": 100.0, "close": 101.5},
        {"date": "2026-01-04", "open": 102.0, "high": 116.0, "low": 101.0, "close": 115.0},
        {"date": "2026-01-05", "open": 115.0, "high": 116.0, "low": 114.0, "close": 115.5},
    ]
    hit = simulate_trade(100.0, 5.0, path, horizon=20)
    assert hit["reason"] == "target"
    assert hit["y_hit"] == 1
    assert hit["stop"] == 92.5
    assert hit["target"] == 115.0
    assert hit["exit_date"] == "2026-01-04"

    stopped = simulate_trade(100.0, 5.0, [
        {"date": "2026-01-02", "open": 100.0, "high": 101.0, "low": 90.0, "close": 91.0},
    ], horizon=20)
    assert stopped["reason"] == "stop"
    assert stopped["y_hit"] == 0

    timed = simulate_trade(100.0, 5.0, [
        {"date": "2026-01-02", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.4},
        {"date": "2026-01-03", "open": 100.4, "high": 101.0, "low": 99.5, "close": 100.2},
    ], horizon=1)
    assert timed["reason"] == "time"
    assert timed["y_hit"] == 0
    from skabu.model import sample_y

    assert sample_y(label="target_first", y_hit=timed["y_hit"], ret=timed["ret"], excess=0.01) == 0
    assert timed["ret"] > 0
    assert sample_y(label="ret_pos", y_hit=timed["y_hit"], ret=timed["ret"], excess=0.01) == 1
    assert sample_y(label="excess_pos", y_hit=timed["y_hit"], ret=timed["ret"], excess=0.01) == 1
    assert sample_y(label="excess_pos", y_hit=timed["y_hit"], ret=timed["ret"], excess=None) is None


def test_search_presets() -> None:
    assert len(PRESETS) == 8
    assert PRESETS["filters"]["selections"] == []
    assert PRESETS["filters"]["filters"][0]["feature"] == "above_cloud"
    assert "sma_rsi" not in PRESETS
    assert "gaussian_rsi_boost" not in PRESETS
    assert "boost" not in PRESETS
    assert "boost_cloud" not in PRESETS
    assert "boost_cool" not in PRESETS
    assert "boost_trend" not in PRESETS
    assert [item["id"] for item in PRESETS["ema_rsi"]["selections"]] == ["ema", "rsi"]


def test_custom_exit_mults_move_prices() -> None:
    levels = levels_from_anchor(100.0, 10.0, stop_mult=2, target_mult=4)
    assert levels["stop"] == 80.0
    assert levels["target"] == 140.0
    assert levels["rr"] == 2.0
    same = strategy_key([{"id": "sma", "params": {"length": 5}}], "プライム", 1, 10, 1)
    other = strategy_key(
        [{"id": "sma", "params": {"length": 5}}],
        "プライム",
        1,
        10,
        1,
        stop_mult=2,
        target_mult=4,
    )
    assert same != other


def test_exit_levels_round_trip_and_reject(tmp_path: Path) -> None:
    settings = Settings(
        jquants_api_key="test",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    missing = load_exit_levels(settings)
    assert missing["saved"] is False
    assert missing["stop_mult"] == 1.5
    assert missing["target_mult"] == 3.0
    saved = save_exit_levels(settings, 1.55, 4)
    assert saved["stop_mult"] == 1.6
    assert saved["target_mult"] == 4.0
    assert saved["saved"] is True
    again = load_exit_levels(settings)
    assert again["stop_mult"] == 1.6
    with pytest.raises(SkabuError):
        save_exit_levels(settings, 0, 3)
    with pytest.raises(SkabuError):
        save_exit_levels(settings, "wide", 3)
    kept = load_exit_levels(settings)
    assert kept["stop_mult"] == 1.6
