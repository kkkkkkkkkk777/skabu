import json
from datetime import date, timedelta
from pathlib import Path

from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.sim import default_as_of, default_long_range, run_long_sim, run_sim
from skabu.store import connect, upsert_daily_bars, upsert_master
from skabu.strategy import active_path


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        jquants_api_key="test",
        jquants_plan="free",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    return settings


def _days(count: int) -> list[str]:
    start = date(2025, 1, 6)
    return [(start + timedelta(days=i)).isoformat() for i in range(count)]


def _seed(db_path: Path, days: int = 80) -> list[str]:
    conn = connect(db_path)
    dates = _days(days)
    last = dates[-1]
    upsert_master(
        conn,
        [
            {"Date": last, "Code": "11110", "CoName": "上昇工業", "MktNm": "プライム"},
            {"Date": last, "Code": "22220", "CoName": "下降商事", "MktNm": "プライム"},
            {"Date": last, "Code": "13060", "CoName": "TOPIX ETF", "MktNm": "その他"},
        ],
    )
    bars = []
    for day_i, day in enumerate(dates):
        up = 100 + day_i * 1.5
        down = 200 - day_i * 2.0
        bars.append(
            {"Date": day, "Code": "11110", "AdjO": up, "AdjH": up + 1, "AdjL": up - 1, "AdjC": up, "AdjVo": 80_000}
        )
        bars.append(
            {
                "Date": day,
                "Code": "22220",
                "AdjO": down,
                "AdjH": down + 1,
                "AdjL": down - 3,
                "AdjC": down,
                "AdjVo": 80_000,
            }
        )
        bench = 1000 + day_i * 0.2
        bars.append(
            {
                "Date": day,
                "Code": "13060",
                "AdjO": bench,
                "AdjH": bench + 1,
                "AdjL": bench - 1,
                "AdjC": bench,
                "AdjVo": 10_000,
            }
        )
    upsert_daily_bars(conn, bars)
    conn.commit()
    conn.close()
    return dates


def test_default_as_of_is_20_bars_before_latest(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    with connect(settings.db_path) as conn:
        assert default_as_of(conn, 20) == dates[-21]
        assert default_as_of(conn, 20) < dates[-1]


def test_run_sim_uses_as_of_only_and_marks_daily(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    pred = dates[50]
    payload = run_sim(
        settings,
        as_of=pred,
        cash=1_000_000,
        top=2,
        horizon=10,
        min_avg_volume=1,
        min_bars=20,
    )
    assert payload["status"] == "ok"
    assert payload["as_of"] == pred
    assert payload["entry_date"] == dates[51]
    assert payload["entry_date"] > payload["as_of"]
    assert payload["curve"]
    assert payload["curve"][0]["date"] == payload["entry_date"]
    assert payload["curve"][-1]["date"] == payload["end_date"]
    totals = [row["total"] for row in payload["curve"]]
    assert any(left != right for left, right in zip(totals, totals[1:]))
    codes = {row["code"] for row in payload["trades"]}
    assert "11110" in codes
    winner = next(item for item in payload["trades"] if item["code"] == "11110")
    assert winner["entry"] is not None
    assert winner["entry"] < 100 + 60 * 1.5
    loser = next((item for item in payload["trades"] if item["code"] == "22220"), None)
    if loser is not None:
        assert loser["exit_reason"] in {"stop", "time", "target"}
        if loser["exit_reason"] == "stop":
            assert loser["pnl"] is not None
            assert loser["pnl"] < 0
    assert payload["equity_end"] is not None
    assert payload["curve"][-1]["bench"] is not None


def test_run_sim_stop_closes_falling_name(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    conn = connect(settings.db_path)
    upsert_master(conn, [{"Date": dates[-1], "Code": "11110", "CoName": "削除", "MktNm": "スタンダード"}])
    conn.commit()
    conn.close()
    payload = run_sim(
        settings,
        as_of=dates[50],
        cash=500_000,
        top=1,
        horizon=10,
        min_avg_volume=1,
        min_bars=20,
    )
    assert payload["status"] == "ok"
    if payload["trades"]:
        trade = payload["trades"][0]
        assert trade["code"] == "22220"
        assert trade["exit_reason"] == "stop"
        last = payload["curve"][-1]
        assert last["positions"] == 0
        assert last["cash"] > 0
    else:
        assert payload["cash"] is True
        assert payload["equity_end"] == 500_000


def test_run_sim_cash_when_nothing_passes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    conn = connect(settings.db_path)
    upsert_master(conn, [{"Date": dates[-1], "Code": "11110", "CoName": "削除", "MktNm": "スタンダード"}])
    conn.commit()
    conn.close()
    active_path(settings).write_text(
        json.dumps(
            {
                "id": "sma",
                "title": "SMA",
                "selections": [{"id": "sma", "params": {"length": 5}}],
                "filters": [],
                "holdout_ok": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    payload = run_sim(
        settings,
        as_of=dates[50],
        cash=1_000_000,
        top=2,
        horizon=10,
        min_avg_volume=1,
        min_bars=20,
    )
    assert payload["status"] == "ok"
    assert payload["cash"] is True
    assert payload["trades"] == []
    assert payload["equity_end"] == 1_000_000
    assert payload["curve"]
    assert payload["curve"][-1]["total"] == 1_000_000
    assert payload["curve"][-1]["bench"] is not None


def test_run_sim_uses_risk_size_and_leaves_cash(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    payload = run_sim(
        settings,
        as_of=dates[50],
        cash=1_000_000,
        top=1,
        horizon=10,
        min_avg_volume=1,
        min_bars=20,
        risk_pct=1,
    )
    assert payload["status"] == "ok"
    assert payload["risk_pct"] == 0.01
    assert payload["trades"]
    trade = payload["trades"][0]
    assert trade["alloc"] < 1_000_000
    assert trade["qty"] >= 1
    assert trade["qty"] == int(trade["qty"])
    assert payload["curve"][0]["cash"] > 0
    assert abs(trade["alloc"] - 1_000_000) > 1


def test_run_sim_trial_ignores_active_filters(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    active_path(settings).write_text(
        json.dumps(
            {
                "id": "v1",
                "title": "v1 雲の上・過熱なし",
                "selections": [{"id": "boost", "params": {"high": 0.58, "low": 0.42}}],
                "filters": [{"id": "rsi", "feature": "value", "op": "lt", "value": 70}],
                "holdout_ok": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    adopted = run_sim(settings, as_of=dates[50], cash=1_000_000, top=1, horizon=10, min_avg_volume=1, min_bars=20)
    trial = run_sim(
        settings,
        as_of=dates[50],
        cash=1_000_000,
        top=1,
        horizon=10,
        min_avg_volume=1,
        min_bars=20,
        selections=[{"id": "boost", "params": {"high": 0.58, "low": 0.42}}],
        filters=[],
    )
    assert adopted["status"] == "ok"
    assert trial["status"] == "ok"
    assert adopted["filters"]
    assert adopted["filters"][0]["id"] == "rsi"
    assert trial["filters"] == []
    assert trial["strategy"][0]["id"] == "boost"


def test_run_sim_empty_selections_does_not_use_adopted_boost(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    active_path(settings).write_text(
        json.dumps(
            {
                "id": "v1",
                "title": "v1 雲の上・過熱なし",
                "selections": [{"id": "boost", "params": {"high": 0.58, "low": 0.42}}],
                "filters": [{"id": "rsi", "feature": "value", "op": "lt", "value": 70}],
                "holdout_ok": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    payload = run_sim(
        settings,
        as_of=dates[50],
        cash=1_000_000,
        top=2,
        horizon=10,
        min_avg_volume=1,
        min_bars=20,
        selections=[],
        filters=[
            {"id": "ichimoku", "feature": "above_cloud", "equals": 1},
            {"id": "bbands", "feature": "pct_b", "op": "lt", "value": 0.85},
            {"id": "rsi", "feature": "value", "op": "lt", "value": 70},
        ],
    )
    assert payload["status"] == "ok"
    assert payload["strategy"] == []
    assert payload["filters"][0]["id"] == "ichimoku"
    assert all(item.get("id") != "boost" for item in payload["strategy"])


def _long_kwargs(dates, **extra):
    payload = {
        "start": dates[40],
        "end": dates[70],
        "cash": 1_000_000,
        "top": 1,
        "min_avg_volume": 1,
        "min_bars": 20,
        "selections": [],
        "filters": [],
        "risk_pct": 1,
    }
    payload.update(extra)
    return payload


def test_long_sim_stop_refills_other_name(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    conn = connect(settings.db_path)
    bars = []
    for day_i, day in enumerate(dates):
        fall = 200 - day_i * 2.0
        rise = 80 + day_i * 1.2
        bars.append(
            {"Date": day, "Code": "11110", "AdjO": fall, "AdjH": fall + 1, "AdjL": fall - 3, "AdjC": fall, "AdjVo": 80_000}
        )
        bars.append(
            {"Date": day, "Code": "22220", "AdjO": rise, "AdjH": rise + 1, "AdjL": rise - 1, "AdjC": rise, "AdjVo": 80_000}
        )
    upsert_daily_bars(conn, bars)
    conn.commit()
    conn.close()
    payload = run_long_sim(settings, **_long_kwargs(dates))
    assert payload["status"] == "ok"
    assert payload["mode"] == "long"
    assert payload["as_of"] == dates[40]
    assert payload["entry_date"] == dates[41]
    assert payload["end_date"] == dates[70]
    codes = [row["code"] for row in payload["trades"]]
    assert "11110" in codes
    first = next(item for item in payload["trades"] if item["code"] == "11110")
    assert first["exit_reason"] == "stop"
    assert first["as_of"] < first["entry_date"]
    assert any(item["code"] == "22220" for item in payload["trades"])
    second = next(item for item in payload["trades"] if item["code"] == "22220")
    assert second["entry_date"] > first["exit_date"]
    assert second["as_of"] < second["entry_date"]


def test_long_sim_winner_holds_until_end(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    conn = connect(settings.db_path)
    upsert_master(conn, [{"Date": dates[-1], "Code": "22220", "CoName": "削除", "MktNm": "スタンダード"}])
    bars = []
    for day in dates:
        px = 120.0
        bars.append(
            {"Date": day, "Code": "11110", "AdjO": px, "AdjH": px + 0.4, "AdjL": px - 0.4, "AdjC": px, "AdjVo": 80_000}
        )
    upsert_daily_bars(conn, bars)
    conn.commit()
    conn.close()
    payload = run_long_sim(settings, **_long_kwargs(dates))
    assert payload["status"] == "ok"
    assert payload["trades"]
    trade = payload["trades"][0]
    assert trade["code"] == "11110"
    assert trade["exit_reason"] == "end"
    assert trade["exit_date"] == dates[70]
    assert trade["as_of"] < trade["entry_date"]


def test_long_sim_skips_held_and_buys_other(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    conn = connect(settings.db_path)
    upsert_master(conn, [{"Date": dates[-1], "Code": "33330", "CoName": "補充工業", "MktNm": "プライム"}])
    bars = []
    for day_i, day in enumerate(dates):
        held = 120.0
        fall = 200 - day_i * 2.0
        spare = 110 + day_i * 0.02
        bars.append(
            {"Date": day, "Code": "11110", "AdjO": held, "AdjH": held + 3, "AdjL": held - 3, "AdjC": held, "AdjVo": 80_000}
        )
        bars.append(
            {"Date": day, "Code": "22220", "AdjO": fall, "AdjH": fall + 1, "AdjL": fall - 3, "AdjC": fall, "AdjVo": 80_000}
        )
        bars.append(
            {"Date": day, "Code": "33330", "AdjO": spare, "AdjH": spare + 3, "AdjL": spare - 3, "AdjC": spare, "AdjVo": 80_000}
        )
    upsert_daily_bars(conn, bars)
    conn.commit()
    conn.close()
    payload = run_long_sim(settings, **_long_kwargs(dates, top=2))
    assert payload["status"] == "ok"
    held = [row for row in payload["trades"] if row["code"] == "11110"]
    assert len(held) == 1
    stopped = next(item for item in payload["trades"] if item["code"] == "22220")
    assert stopped["exit_reason"] == "stop"
    later = [row for row in payload["trades"] if row["entry_date"] > stopped["exit_date"]]
    assert later
    assert all(row["code"] != "11110" for row in later)


def test_long_sim_no_lookahead(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    payload = run_long_sim(settings, **_long_kwargs(dates, top=2))
    assert payload["trades"]
    for row in payload["trades"]:
        assert row["as_of"] < row["entry_date"]
        assert row["entry_date"] <= payload["end_date"]
        if row["exit_date"]:
            assert row["entry_date"] <= row["exit_date"]


def test_sim_no_take_profit_holds_through_target_until_day(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    conn = connect(settings.db_path)
    upsert_master(conn, [{"Date": dates[-1], "Code": "22220", "CoName": "対象外", "MktNm": "スタンダード"}])
    bars = []
    for day_i, day in enumerate(dates):
        px = 100.0
        high = px + 20 if day == dates[52] else px + 1
        bars.append(
            {"Date": day, "Code": "11110", "AdjO": px, "AdjH": high, "AdjL": px - 1, "AdjC": px, "AdjVo": 80_000}
        )
    upsert_daily_bars(conn, bars)
    conn.commit()
    conn.close()
    payload = run_sim(
        settings,
        as_of=dates[50],
        cash=1_000_000,
        top=1,
        horizon=5,
        min_avg_volume=1,
        min_bars=20,
        selections=[],
        filters=[],
        take_profit=False,
    )
    assert payload["take_profit"] is False
    assert payload["end_date"] == dates[56]
    trade = payload["trades"][0]
    assert trade["code"] == "11110"
    assert trade["exit_reason"] == "time"
    assert trade["exit_date"] == dates[56]
    assert trade["exit"] == 100.0


def test_sim_no_take_profit_stop_still_exits_first(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    conn = connect(settings.db_path)
    upsert_master(conn, [{"Date": dates[-1], "Code": "11110", "CoName": "対象外", "MktNm": "スタンダード"}])
    bars = []
    for day in dates:
        px = 100.0
        low = px - 30 if day == dates[52] else px - 1
        bars.append(
            {"Date": day, "Code": "22220", "AdjO": px, "AdjH": px + 1, "AdjL": low, "AdjC": px, "AdjVo": 80_000}
        )
    upsert_daily_bars(conn, bars)
    conn.commit()
    conn.close()
    payload = run_sim(
        settings,
        as_of=dates[50],
        cash=1_000_000,
        top=1,
        horizon=5,
        min_avg_volume=1,
        min_bars=20,
        selections=[],
        filters=[],
        take_profit=False,
    )
    trade = payload["trades"][0]
    assert trade["exit_reason"] == "stop"
    assert trade["exit_date"] == dates[52]
    assert trade["exit_date"] < dates[56]


def test_long_sim_no_take_profit_refills_after_hold(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    conn = connect(settings.db_path)
    bars = []
    for day in dates:
        px = 100.0
        if day <= dates[46]:
            bars.append(
                {"Date": day, "Code": "11110", "AdjO": px, "AdjH": px + 1, "AdjL": px - 1, "AdjC": px, "AdjVo": 90_000}
            )
        bars.append(
            {"Date": day, "Code": "22220", "AdjO": px, "AdjH": px + 1, "AdjL": px - 1, "AdjC": px, "AdjVo": 80_000}
        )
    upsert_daily_bars(conn, bars)
    conn.execute("DELETE FROM daily_bars WHERE code = ? AND date > ?", ("11110", dates[46]))
    conn.commit()
    conn.close()
    payload = run_long_sim(
        settings,
        **_long_kwargs(dates, top=1),
        take_profit=False,
        horizon=5,
    )
    assert payload["take_profit"] is False
    assert payload["horizon"] == 5
    first = payload["trades"][0]
    assert first["exit_reason"] == "time"
    assert first["entry_date"] == dates[41]
    assert first["exit_date"] == dates[46]
    later = [row for row in payload["trades"] if row["entry_date"] > first["exit_date"]]
    assert later
    assert later[0]["code"] != first["code"]


def test_long_sim_respects_start_end(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path)
    payload = run_long_sim(settings, **_long_kwargs(dates, start=dates[45], end=dates[55]))
    assert payload["as_of"] == dates[45]
    assert payload["entry_date"] == dates[46]
    assert payload["end_date"] == dates[55]
    assert payload["curve"][0]["date"] == dates[46]
    assert payload["curve"][-1]["date"] == dates[55]
    with connect(settings.db_path) as conn:
        start, end = default_long_range(conn)
    assert start < end
    try:
        run_long_sim(settings, **_long_kwargs(dates, start=dates[60], end=dates[50]))
        raise AssertionError("expected start/end error")
    except SkabuError as exc:
        assert "前" in str(exc)
