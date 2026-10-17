from datetime import date, timedelta
from pathlib import Path

from skabu.store import connect, lock_prediction_run, upsert_daily_bars, upsert_master
from skabu.buys import BUY_SPEC
from skabu.verify import SEARCH_PRESETS, evaluate_run, lock_as_of, pick_as_of_dates, run_verify, strategy_key


def _days(count: int) -> list[str]:
    start = date(2025, 1, 6)
    return [(start + timedelta(days=i)).isoformat() for i in range(count)]


def test_search_presets_keep_named_recipes() -> None:
    assert SEARCH_PRESETS["filters"]["selections"] == []
    assert "v1" not in SEARCH_PRESETS
    assert "boost" not in SEARCH_PRESETS
    assert "momentum" in SEARCH_PRESETS
    assert SEARCH_PRESETS["momentum"]["filters"][0]["id"] == "regime"
    assert "double_top" in SEARCH_PRESETS
    assert SEARCH_PRESETS["double_top"]["filters"][-1]["id"] == "doubletop"
    assert "lower_lows" in SEARCH_PRESETS
    assert SEARCH_PRESETS["lower_lows"]["filters"][-1]["id"] == "downtrend"
    assert len(SEARCH_PRESETS) == 19
    assert sum(1 for key in SEARCH_PRESETS if key not in {"momentum", "double_top", "lower_lows"}) == 16


def test_pick_as_of_dates() -> None:
    days = _days(40)
    picked = pick_as_of_dates(days, min_history=15, stride=10, horizon=20)
    assert picked[0] == days[15]
    assert picked[-1] == days[18]


def _seed(tmp_path: Path):
    conn = connect(tmp_path / "v.db")
    days = _days(40)
    upsert_master(
        conn,
        [
            {"Date": days[-1], "Code": "11110", "CoName": "上昇工業", "MktNm": "プライム"},
            {"Date": days[-1], "Code": "22220", "CoName": "下降商事", "MktNm": "プライム"},
            {"Date": days[-1], "Code": "13060", "CoName": "TOPIX ETF", "MktNm": "その他"},
        ],
    )
    rising = []
    falling = []
    bench = []
    for index, day in enumerate(days):
        up = 100 + index * 1.5
        down = 200 - index * 1.2
        px = 1000 + index * 0.2
        rising.append(
            {"Date": day, "Code": "11110", "AdjO": up, "AdjH": up + 1, "AdjL": up - 1, "AdjC": up, "AdjVo": 80_000}
        )
        falling.append(
            {
                "Date": day,
                "Code": "22220",
                "AdjO": down,
                "AdjH": down + 1,
                "AdjL": down - 1,
                "AdjC": down,
                "AdjVo": 80_000,
            }
        )
        bench.append(
            {"Date": day, "Code": "13060", "AdjO": px, "AdjH": px + 1, "AdjL": px - 1, "AdjC": px, "AdjVo": 10_000}
        )
    upsert_daily_bars(conn, rising + falling + bench)
    conn.commit()
    return conn, days


def test_lock_is_immutable(tmp_path: Path) -> None:
    conn, days = _seed(tmp_path)
    as_of = days[15]
    first = lock_as_of(
        conn,
        as_of=as_of,
        selections=[{"id": "sma", "params": {"length": 5}}],
        market="プライム",
        min_avg_volume=1,
        min_bars=10,
        top=2,
    )
    second = lock_prediction_run(
        conn,
        first["strategy_key"],
        as_of,
        {"changed": True},
        [{"rank": 1, "code": "99990", "co_name": "改ざん"}],
        0,
    )
    assert second["rows"][0]["code"] != "99990"
    assert second["locked_at"] == first["locked_at"]
    assert first["rows"][0]["code"] == "11110"
    assert isinstance(first["gate_passed"], int)
    assert first["config"]["gate_passed"] == first["gate_passed"]


def test_evaluate_and_verify_summary(tmp_path: Path) -> None:
    conn, days = _seed(tmp_path)
    payload = run_verify(
        conn,
        selections=[{"id": "sma", "params": {"length": 5}}],
        market="プライム",
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=10,
        min_history=15,
        refresh=True,
    )
    assert payload["locked_runs"] >= 1
    five = payload["horizons"]["5"]
    assert five["summary"]["trades"] >= 1
    assert five["summary"]["win_rate"] == 1.0
    assert five["summary"]["avg_return"] > 0
    assert payload["rules"]["exit"].startswith("損切")
    reasons = {
        trade.get("exit_reason")
        for period in five["periods"]
        for trade in period.get("trades") or []
    }
    assert reasons & {"stop", "target", "time"}
    cached = run_verify(
        conn,
        selections=[{"id": "sma", "params": {"length": 5}}],
        market="プライム",
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=10,
        min_history=15,
        refresh=False,
    )
    assert cached["cached"] is True


def test_string_params_share_verify_cache(tmp_path: Path) -> None:
    conn, _days_list = _seed(tmp_path)
    first = run_verify(
        conn,
        selections=[{"id": "sma", "params": {"length": "5"}}],
        market="プライム",
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=10,
        min_history=15,
        refresh=True,
    )
    second = run_verify(
        conn,
        selections=[{"id": "sma", "params": {"length": 5}}],
        market="プライム",
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=10,
        min_history=15,
        refresh=False,
    )
    assert first["cached"] is False
    assert second["cached"] is True
    assert first["strategy_key"] == second["strategy_key"]


def test_verify_cache_key_includes_buy_spec() -> None:
    left = strategy_key([{"id": "sma", "params": {"length": 5}}], "プライム", 1, 10, 1)
    right = strategy_key([{"id": "sma", "params": {"length": 5}}], "プライム", 1, 10, 1, holdout_ok=False)
    assert left != right
    assert "max_stop_pct" in BUY_SPEC


def test_evaluate_run_math() -> None:
    dates = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-08"]
    run = {"as_of": "2025-01-01", "rows": [{"rank": 1, "code": "11110", "co_name": "A", "total": 2}]}
    prices = {
        "11110": {
            "2025-01-02": {"open": 100.0, "close": 101.0},
            "2025-01-08": {"open": 110.0, "close": 110.0},
        },
        "13060": {
            "2025-01-02": {"open": 200.0, "close": 200.0},
            "2025-01-08": {"open": 202.0, "close": 202.0},
        },
    }
    index = {day: i for i, day in enumerate(dates)}
    result = evaluate_run(run, dates, index, prices, 2, "13060")
    assert result is not None
    assert result["entry_date"] == "2025-01-02"
    assert result["exit_date"] == "2025-01-08"
    assert abs(result["trades"][0]["return"] - 0.10) < 1e-9
    assert result["trades"][0]["win"] is True
    assert result["trades"][0]["win_vs_bench"] is True
    assert result["trades"][0]["exit_reason"] == "time"
