import sqlite3
from datetime import date
from pathlib import Path

import pytest

from skabu.store import (
    attach_sales_growth3,
    compute_sales_growth3,
    connect,
    get_chart_bars,
    get_issue,
    load_facts_as_of,
    normalize_code,
    rebuild_weekly_bars,
    sales_growth3_as_of,
    search_issues,
    trading_dates,
    upsert_calendar,
    upsert_daily_bars,
    upsert_equity_facts,
    upsert_fins_annual,
    upsert_master,
    week_end_friday,
)


def test_week_end_friday() -> None:
    assert week_end_friday(date(2026, 6, 22)) == date(2026, 6, 26)
    assert week_end_friday(date(2026, 6, 26)) == date(2026, 6, 26)
    assert week_end_friday(date(2026, 6, 15)) == date(2026, 6, 19)


def test_trading_dates_only_hol_div_1(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    upsert_calendar(
        conn,
        [
            {"Date": "2026-06-22", "HolDiv": "1"},
            {"Date": "2026-06-20", "HolDiv": "0"},
            {"Date": "2026-06-23", "HolDiv": "1"},
        ],
    )
    conn.commit()
    assert trading_dates(conn, "2026-06-20", "2026-06-23") == ["2026-06-22", "2026-06-23"]


def test_weekly_aggregation(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    upsert_daily_bars(
        conn,
        [
            {
                "Date": "2026-06-22",
                "Code": "86970",
                "AdjO": 100,
                "AdjH": 110,
                "AdjL": 99,
                "AdjC": 105,
                "AdjVo": 10,
            },
            {
                "Date": "2026-06-23",
                "Code": "86970",
                "AdjO": 106,
                "AdjH": 120,
                "AdjL": 90,
                "AdjC": 101,
                "AdjVo": 7,
            },
        ],
    )
    written = rebuild_weekly_bars(conn, ["2026-06-22", "2026-06-23"])
    conn.commit()
    assert written == 1
    row = conn.execute("SELECT * FROM weekly_bars WHERE code='86970'").fetchone()
    assert row["week_end"] == "2026-06-26"
    assert row["adj_o"] == 100
    assert row["adj_h"] == 120
    assert row["adj_l"] == 90
    assert row["adj_c"] == 101
    assert row["adj_vo"] == 17
    assert row["sessions"] == 2


def test_normalize_code() -> None:
    assert normalize_code("8697") == "86970"
    assert normalize_code("86970") == "86970"


def test_search_and_chart_query(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    upsert_master(
        conn,
        [
            {
                "Date": "2026-06-23",
                "Code": "86970",
                "CoName": "日本取引所グループ",
                "CoNameEn": "Japan Exchange Group",
                "MktNm": "プライム",
            },
            {
                "Date": "2026-06-23",
                "Code": "72030",
                "CoName": "トヨタ自動車",
                "CoNameEn": "TOYOTA MOTOR",
                "MktNm": "プライム",
            },
            {
                "Date": "2026-06-23",
                "Code": "31160",
                "CoName": "トヨタ紡織",
                "CoNameEn": "TOYOTA BOSHOKU",
                "MktNm": "プライム",
            },
        ],
    )
    upsert_daily_bars(
        conn,
        [
            {
                "Date": "2026-06-22",
                "Code": "86970",
                "AdjO": 100,
                "AdjH": 110,
                "AdjL": 99,
                "AdjC": 105,
                "AdjVo": 10,
            },
            {
                "Date": "2026-06-23",
                "Code": "86970",
                "AdjO": 106,
                "AdjH": 120,
                "AdjL": 90,
                "AdjC": 101,
                "AdjVo": 7,
            },
            {
                "Date": "2026-06-23",
                "Code": "72030",
                "AdjO": 2000,
                "AdjH": 2010,
                "AdjL": 1990,
                "AdjC": 2005,
                "AdjVo": 1000,
            },
            {
                "Date": "2026-06-23",
                "Code": "31160",
                "AdjO": 20,
                "AdjH": 21,
                "AdjL": 19,
                "AdjC": 20,
                "AdjVo": 10,
            },
        ],
    )
    conn.commit()

    hits = search_issues(conn, "8697")
    assert hits[0]["code"] == "86970"
    assert get_issue(conn, "8697")["co_name"] == "日本取引所グループ"
    daily = get_chart_bars(conn, "8697", "daily")
    assert len(daily) == 2
    weekly = get_chart_bars(conn, "86970", "weekly")
    assert len(weekly) == 1
    assert weekly[0]["open"] == 100
    assert weekly[0]["close"] == 101
    toyota = search_issues(conn, "トヨタ")
    assert toyota[0]["code"] == "72030"
    assert toyota[0]["co_name"] == "トヨタ自動車"


def test_equity_facts_as_of_and_coalesce(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    upsert_equity_facts(
        conn,
        [
            {"date": "2026-01-10", "code": "11110", "per": 15, "pbr": None, "op_margin": None},
            {"date": "2026-02-01", "code": "11110", "per": None, "pbr": 1.2, "op_margin": 0.08},
            {"date": "2026-03-01", "code": "11110", "per": 5, "pbr": None, "op_margin": None},
        ],
    )
    upsert_equity_facts(conn, [{"date": "2026-01-10", "code": "11110", "pbr": 0.9}])
    conn.commit()
    mid = load_facts_as_of(conn, "2026-02-15", ["11110"])
    assert mid["11110"]["per"] == 15.0
    assert mid["11110"]["pbr"] == 1.2
    assert mid["11110"]["op_margin"] == 0.08
    future = load_facts_as_of(conn, "2026-01-20", ["11110"])
    assert future["11110"]["per"] == 15.0
    assert future["11110"]["pbr"] == 0.9
    assert "op_margin" not in future["11110"] or future["11110"].get("op_margin") is None
    after = load_facts_as_of(conn, "2026-03-01", ["11110"])
    assert after["11110"]["per"] == 5.0
    assert after["11110"]["pbr"] == 1.2


def test_equity_facts_new_columns_coalesce(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    upsert_equity_facts(conn, [{"date": "2026-01-10", "code": "11110", "roe": 0.11, "div_ann": 35}])
    upsert_equity_facts(conn, [{"date": "2026-01-10", "code": "11110", "eq_ar": 0.42, "sales_growth3": 0.05}])
    conn.commit()
    loaded = load_facts_as_of(conn, "2026-01-10", ["11110"])
    assert loaded["11110"]["roe"] == 0.11
    assert loaded["11110"]["eq_ar"] == 0.42
    assert loaded["11110"]["div_ann"] == 35.0
    assert loaded["11110"]["sales_growth3"] == 0.05


def test_sales_growth3_four_periods_and_short() -> None:
    assert compute_sales_growth3([100, 110, 121, 133.1]) == pytest.approx(0.1)
    assert compute_sales_growth3([100, 110, 121]) is None
    assert compute_sales_growth3([100, 0, 121, 133]) is None


def test_fins_annual_skips_non_fy_and_needs_four(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    upsert_fins_annual(
        conn,
        [
            {"code": "11110", "cur_per_type": "1Q", "fy_end": "2023-03-31", "date": "2023-08-01", "sales": 999},
            {"code": "11110", "cur_per_type": "FY", "fy_end": "2022-03-31", "date": "2022-05-01", "sales": 100},
            {"code": "11110", "cur_per_type": "FY", "fy_end": "2023-03-31", "date": "2023-05-01", "sales": 110},
            {"code": "11110", "cur_per_type": "FY", "fy_end": "2024-03-31", "date": "2024-05-01", "sales": 121},
        ],
    )
    conn.commit()
    assert sales_growth3_as_of(conn, "11110", "2024-05-01") is None
    upsert_fins_annual(
        conn,
        [{"code": "11110", "cur_per_type": "FY", "fy_end": "2025-03-31", "date": "2025-05-01", "sales": 133.1}],
    )
    conn.commit()
    assert sales_growth3_as_of(conn, "11110", "2025-04-30") is None
    assert sales_growth3_as_of(conn, "11110", "2025-05-01") == pytest.approx(0.1)
    attached = attach_sales_growth3(
        conn,
        [{"date": "2025-05-01", "code": "11110", "op_margin": 0.08}],
        "2025-05-01",
    )
    assert attached[0]["sales_growth3"] == pytest.approx(0.1)


def test_equity_facts_migrate_adds_columns(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute(
        """
        CREATE TABLE equity_facts (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            per REAL,
            pbr REAL,
            op_margin REAL,
            PRIMARY KEY (date, code)
        )
        """
    )
    raw.commit()
    raw.close()
    conn = connect(path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(equity_facts)")}
    assert {"roe", "eq_ar", "div_ann", "sales_growth3"} <= cols
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "fins_annual" in tables
