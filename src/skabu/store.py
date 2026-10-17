"""ローカル SQLite。取得したカレンダー・銘柄・日足・週足を保存する。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from skabu.logging_setup import get_logger

SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar (
    date TEXT PRIMARY KEY,
    hol_div TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS listed_master (
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    co_name TEXT,
    co_name_en TEXT,
    s17 TEXT,
    s17_nm TEXT,
    s33 TEXT,
    s33_nm TEXT,
    scale_cat TEXT,
    mkt TEXT,
    mkt_nm TEXT,
    mrgn TEXT,
    mrgn_nm TEXT,
    prod_cat TEXT,
    PRIMARY KEY (date, code)
);

CREATE TABLE IF NOT EXISTS daily_bars (
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    o REAL,
    h REAL,
    l REAL,
    c REAL,
    ul TEXT,
    ll TEXT,
    vo REAL,
    va REAL,
    adj_factor REAL,
    adj_o REAL,
    adj_h REAL,
    adj_l REAL,
    adj_c REAL,
    adj_vo REAL,
    mkt_cap REAL,
    ex_rt TEXT,
    PRIMARY KEY (date, code)
);

CREATE INDEX IF NOT EXISTS idx_daily_bars_code_date ON daily_bars(code, date);
CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(date);

CREATE TABLE IF NOT EXISTS equity_facts (
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    per REAL,
    pbr REAL,
    op_margin REAL,
    roe REAL,
    eq_ar REAL,
    div_ann REAL,
    sales_growth3 REAL,
    PRIMARY KEY (date, code)
);
CREATE INDEX IF NOT EXISTS idx_equity_facts_date ON equity_facts(date);
CREATE INDEX IF NOT EXISTS idx_equity_facts_code_date ON equity_facts(code, date);

CREATE TABLE IF NOT EXISTS fins_annual (
    code TEXT NOT NULL,
    fy_end TEXT NOT NULL,
    sales REAL,
    disc_date TEXT NOT NULL,
    PRIMARY KEY (code, fy_end)
);
CREATE INDEX IF NOT EXISTS idx_fins_annual_code ON fins_annual(code);

CREATE TABLE IF NOT EXISTS ranking_cache (
    cache_key TEXT PRIMARY KEY,
    as_of TEXT NOT NULL,
    config_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    elapsed_ms INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction_runs (
    strategy_key TEXT NOT NULL,
    as_of TEXT NOT NULL,
    config_json TEXT NOT NULL,
    rows_json TEXT NOT NULL,
    universe INTEGER,
    locked_at TEXT NOT NULL,
    PRIMARY KEY (strategy_key, as_of)
);

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    strategy_key TEXT NOT NULL,
    as_of TEXT NOT NULL,
    code TEXT NOT NULL,
    rank INTEGER,
    proba REAL,
    y_time INTEGER,
    y_touch INTEGER,
    excess REAL,
    time_excess REAL,
    ret REAL,
    bench_ret REAL,
    exit_reason TEXT,
    exit_date TEXT,
    stop REAL,
    target REAL,
    feature_json TEXT,
    model_fp TEXT,
    graded_at TEXT NOT NULL,
    PRIMARY KEY (strategy_key, as_of, code)
);

CREATE INDEX IF NOT EXISTS idx_outcomes_as_of ON prediction_outcomes(as_of);
CREATE INDEX IF NOT EXISTS idx_outcomes_graded ON prediction_outcomes(graded_at);

CREATE TABLE IF NOT EXISTS verify_cache (
    cache_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    horizon INTEGER,
    n_train INTEGER,
    n_valid INTEGER,
    accuracy REAL,
    auc REAL,
    model_path TEXT,
    meta_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS strategy_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    chosen_id TEXT,
    horizon INTEGER,
    holdout_ok INTEGER,
    report_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS daily_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    plan TEXT,
    as_of TEXT,
    synced_days INTEGER,
    error_days INTEGER,
    locked INTEGER,
    candidates_path TEXT,
    report_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS weekly_bars (
    week_end TEXT NOT NULL,
    code TEXT NOT NULL,
    adj_o REAL,
    adj_h REAL,
    adj_l REAL,
    adj_c REAL,
    adj_vo REAL,
    sessions INTEGER NOT NULL,
    PRIMARY KEY (week_end, code)
);

CREATE TABLE IF NOT EXISTS sync_state (
    dataset TEXT PRIMARY KEY,
    last_success_at TEXT,
    last_from TEXT,
    last_to TEXT,
    row_count INTEGER,
    detail TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_days (
    dataset TEXT NOT NULL,
    date TEXT NOT NULL,
    status TEXT NOT NULL,
    row_count INTEGER,
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (dataset, date)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    _migrate_equity_facts(conn)
    return conn


def _migrate_equity_facts(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(equity_facts)")}
    for name in ("roe", "eq_ar", "div_ann", "sales_growth3"):
        if name not in cols:
            conn.execute(f"ALTER TABLE equity_facts ADD COLUMN {name} REAL")
            get_logger().info("equity_facts add column %s", name)


def upsert_calendar(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    payload = []
    for row in rows:
        day = _as_date(row.get("Date"))
        hol = row.get("HolDiv")
        if not day or hol is None:
            continue
        payload.append((day, str(hol)))
    conn.executemany(
        "INSERT OR REPLACE INTO calendar(date, hol_div) VALUES (?, ?)",
        payload,
    )
    return len(payload)


def trading_dates(conn: sqlite3.Connection, from_date: str, to_date: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT date FROM calendar
        WHERE date BETWEEN ? AND ? AND hol_div = '1'
        ORDER BY date
        """,
        (from_date, to_date),
    ).fetchall()
    return [row["date"] for row in rows]


def upsert_master(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    payload = []
    for row in rows:
        day = _as_date(row.get("Date"))
        code = row.get("Code")
        if not day or not code:
            continue
        payload.append(
            (
                day,
                str(code),
                row.get("CoName"),
                row.get("CoNameEn"),
                row.get("S17"),
                row.get("S17Nm"),
                row.get("S33"),
                row.get("S33Nm"),
                row.get("ScaleCat"),
                row.get("Mkt"),
                row.get("MktNm"),
                row.get("Mrgn"),
                row.get("MrgnNm"),
                row.get("ProdCat"),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO listed_master(
            date, code, co_name, co_name_en, s17, s17_nm, s33, s33_nm,
            scale_cat, mkt, mkt_nm, mrgn, mrgn_nm, prod_cat
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def upsert_daily_bars(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    payload = []
    for row in rows:
        day = _as_date(row.get("Date"))
        code = row.get("Code")
        if not day or not code:
            continue
        payload.append(
            (
                day,
                str(code),
                _as_float(row.get("O")),
                _as_float(row.get("H")),
                _as_float(row.get("L")),
                _as_float(row.get("C")),
                row.get("UL"),
                row.get("LL"),
                _as_float(row.get("Vo")),
                _as_float(row.get("Va")),
                _as_float(row.get("AdjFactor")),
                _as_float(row.get("AdjO")),
                _as_float(row.get("AdjH")),
                _as_float(row.get("AdjL")),
                _as_float(row.get("AdjC")),
                _as_float(row.get("AdjVo")),
                _as_float(row.get("MktCap")),
                row.get("ExRT"),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO daily_bars(
            date, code, o, h, l, c, ul, ll, vo, va, adj_factor,
            adj_o, adj_h, adj_l, adj_c, adj_vo, mkt_cap, ex_rt
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def rebuild_weekly_bars(conn: sqlite3.Connection, dates: Iterable[str]) -> int:
    week_ends = {week_end_friday(date.fromisoformat(day)).isoformat() for day in dates}
    written = 0
    for week_end in sorted(week_ends):
        friday = date.fromisoformat(week_end)
        monday = friday - timedelta(days=4)
        rows = conn.execute(
            """
            SELECT date, code, adj_o, adj_h, adj_l, adj_c, adj_vo
            FROM daily_bars
            WHERE date BETWEEN ? AND ?
            ORDER BY code, date
            """,
            (monday.isoformat(), friday.isoformat()),
        ).fetchall()
        conn.execute("DELETE FROM weekly_bars WHERE week_end = ?", (week_end,))
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["code"], []).append(row)
        payload = [
            _weekly_row(week_end, code, series) for code, series in grouped.items()
        ]
        conn.executemany(
            """
            INSERT INTO weekly_bars(
                week_end, code, adj_o, adj_h, adj_l, adj_c, adj_vo, sessions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        written += len(payload)
    return written


def mark_sync_day(
    conn: sqlite3.Connection,
    dataset: str,
    day: str,
    status: str,
    row_count: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO sync_days(dataset, date, status, row_count, error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (dataset, day, status, row_count, error, _now()),
    )


def synced_ok_dates(conn: sqlite3.Connection, dataset: str, *, min_rows: int = 0) -> set[str]:
    if min_rows > 0:
        rows = conn.execute(
            """
            SELECT date FROM sync_days
            WHERE dataset = ? AND status = 'ok' AND row_count >= ?
            """,
            (dataset, min_rows),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT date FROM sync_days WHERE dataset = ? AND status = 'ok'",
            (dataset,),
        ).fetchall()
    return {row["date"] for row in rows}


def set_sync_state(
    conn: sqlite3.Connection,
    dataset: str,
    *,
    last_from: str | None,
    last_to: str | None,
    row_count: int,
    detail: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO sync_state(
            dataset, last_success_at, last_from, last_to, row_count, detail, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (dataset, _now(), last_from, last_to, row_count, detail, _now()),
    )


def normalize_code(code: str) -> str:
    text = code.strip()
    if text.isdigit() and len(text) == 4:
        return text + "0"
    return text


def latest_bar_date(conn: sqlite3.Connection, as_of: str | None = None) -> str | None:
    if as_of:
        row = conn.execute("SELECT MAX(date) FROM daily_bars WHERE date <= ?", (as_of,)).fetchone()
    else:
        row = conn.execute("SELECT MAX(date) FROM daily_bars").fetchone()
    return row[0] if row else None


def recent_bar_dates(conn: sqlite3.Connection, limit: int, as_of: str | None = None) -> list[str]:
    if as_of:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_bars WHERE date <= ? ORDER BY date DESC LIMIT ?",
            (as_of, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_bars ORDER BY date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return sorted(row["date"] for row in rows)


def list_universe(
    conn: sqlite3.Connection,
    *,
    market: str = "プライム",
    min_avg_volume: float = 50_000,
    min_bars: int = 20,
    vol_days: int = 20,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    as_of_date = latest_bar_date(conn, as_of)
    master_date = latest_master_date(conn)
    if not as_of_date or not master_date:
        return []
    dates = recent_bar_dates(conn, vol_days, as_of_date)
    if not dates:
        return []
    placeholders = ",".join("?" * len(dates))
    market_sql = ""
    extra: list[Any] = []
    if market and market != "all":
        market_sql = "AND m.mkt_nm = ?"
        extra.append(market)
    rows = conn.execute(
        f"""
        SELECT m.code, m.co_name, m.mkt_nm, m.s33, m.s33_nm, v.avg_vo, v.bars
        FROM listed_master AS m
        JOIN (
            SELECT code, AVG(adj_vo) AS avg_vo, COUNT(*) AS bars
            FROM daily_bars
            WHERE date IN ({placeholders})
              AND adj_c IS NOT NULL
            GROUP BY code
            HAVING COUNT(*) >= ? AND AVG(IFNULL(adj_vo, 0)) >= ?
        ) AS v ON v.code = m.code
        WHERE m.date = ?
        {market_sql}
        ORDER BY v.avg_vo DESC, m.code
        """,
        tuple([*dates, min_bars, min_avg_volume, master_date, *extra]),
    ).fetchall()
    return [dict(row) for row in rows]


def load_daily_bars_since(
    conn: sqlite3.Connection,
    from_date: str,
    to_date: str,
    codes: Iterable[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    args: list[Any] = [from_date, to_date]
    code_sql = ""
    code_list = list(codes) if codes is not None else []
    if code_list:
        code_sql = f"AND code IN ({','.join('?' * len(code_list))})"
        args.extend(code_list)
    rows = conn.execute(
        f"""
        SELECT date, code, adj_o, adj_h, adj_l, adj_c, adj_vo, mkt_cap
        FROM daily_bars
        WHERE date BETWEEN ? AND ?
          AND adj_o IS NOT NULL AND adj_h IS NOT NULL
          AND adj_l IS NOT NULL AND adj_c IS NOT NULL
          {code_sql}
        ORDER BY code, date
        """,
        args,
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["code"], []).append(
            {
                "date": row["date"],
                "open": row["adj_o"],
                "high": row["adj_h"],
                "low": row["adj_l"],
                "close": row["adj_c"],
                "volume": row["adj_vo"],
                "mkt_cap": row["mkt_cap"],
            }
        )
    return grouped


def upsert_equity_facts(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    payload = []
    for row in rows:
        day = _as_date(row.get("date") or row.get("Date") or row.get("DiscDate"))
        code = row.get("code") or row.get("Code")
        if not day or not code:
            continue
        payload.append(
            (
                day,
                str(code),
                _as_float(row.get("per") if "per" in row else row.get("Per")),
                _as_float(row.get("pbr") if "pbr" in row else row.get("Pbr")),
                _as_float(row.get("op_margin")),
                _as_float(row.get("roe")),
                _as_float(row.get("eq_ar")),
                _as_float(row.get("div_ann")),
                _as_float(row.get("sales_growth3")),
            )
        )
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO equity_facts(date, code, per, pbr, op_margin, roe, eq_ar, div_ann, sales_growth3)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, code) DO UPDATE SET
            per = COALESCE(excluded.per, per),
            pbr = COALESCE(excluded.pbr, pbr),
            op_margin = COALESCE(excluded.op_margin, op_margin),
            roe = COALESCE(excluded.roe, roe),
            eq_ar = COALESCE(excluded.eq_ar, eq_ar),
            div_ann = COALESCE(excluded.div_ann, div_ann),
            sales_growth3 = COALESCE(excluded.sales_growth3, sales_growth3)
        """,
        payload,
    )
    return len(payload)


def compute_sales_growth3(sales: list[float | None]) -> float | None:
    if len(sales) < 4:
        return None
    window = sales[-4:]
    rates: list[float] = []
    for prev, cur in zip(window, window[1:]):
        if prev is None or cur is None or float(prev) == 0:
            get_logger().debug("sales_growth3 skip prev=%s cur=%s", prev, cur)
            return None
        rates.append((float(cur) - float(prev)) / float(prev))
    return sum(rates) / 3.0


def upsert_fins_annual(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    payload = []
    for row in rows:
        period = str(row.get("cur_per_type") or "").strip().upper()
        if period != "FY":
            continue
        code = row.get("code") or row.get("Code")
        fy_end = _as_date(row.get("fy_end") or row.get("CurFYEn"))
        disc = _as_date(row.get("date") or row.get("DiscDate"))
        sales = _as_float(row.get("sales"))
        if not code or not fy_end or not disc or sales is None:
            continue
        payload.append((str(code), fy_end, sales, disc))
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO fins_annual(code, fy_end, sales, disc_date)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(code, fy_end) DO UPDATE SET
            sales = excluded.sales,
            disc_date = excluded.disc_date
        WHERE excluded.disc_date >= fins_annual.disc_date
        """,
        payload,
    )
    return len(payload)


def sales_growth3_as_of(conn: sqlite3.Connection, code: str, as_of: str) -> float | None:
    rows = conn.execute(
        """
        SELECT fy_end, sales
        FROM fins_annual
        WHERE code = ? AND disc_date <= ?
        ORDER BY fy_end
        """,
        (code, as_of),
    ).fetchall()
    sales = [row["sales"] for row in rows]
    return compute_sales_growth3(sales)


def attach_sales_growth3(conn: sqlite3.Connection, rows: list[dict[str, Any]], as_of: str) -> list[dict[str, Any]]:
    codes = {str(row.get("code") or "") for row in rows if row.get("code")}
    growth: dict[str, float | None] = {}
    for code in codes:
        if not code:
            continue
        growth[code] = sales_growth3_as_of(conn, code, as_of)
    out = []
    for row in rows:
        item = dict(row)
        code = str(item.get("code") or "")
        if growth.get(code) is not None:
            item["sales_growth3"] = growth[code]
        out.append(item)
    return out


def load_facts_as_of(
    conn: sqlite3.Connection,
    as_of: str,
    codes: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    code_list = [str(item) for item in (codes or [])]
    args: list[Any] = [as_of]
    code_sql = ""
    if code_list:
        code_sql = f"AND code IN ({','.join('?' * len(code_list))})"
        args.extend(code_list)
    rows = conn.execute(
        f"""
        SELECT date, code, per, pbr, op_margin, roe, eq_ar, div_ann, sales_growth3
        FROM equity_facts
        WHERE date <= ?
          {code_sql}
        ORDER BY date
        """,
        args,
    ).fetchall()
    out: dict[str, dict[str, Any]] = {code: {} for code in code_list}
    for row in rows:
        item = out.setdefault(row["code"], {})
        for key in ("per", "pbr", "op_margin", "roe", "eq_ar", "div_ann", "sales_growth3"):
            if row[key] is not None:
                item[key] = float(row[key])
        item["date"] = row["date"]
    return out


def get_ranking_cache(conn: sqlite3.Connection, cache_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload_json, elapsed_ms, updated_at FROM ranking_cache WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    if not row:
        return None
    payload = json.loads(row["payload_json"])
    payload["cached"] = True
    payload["cache_elapsed_ms"] = row["elapsed_ms"]
    payload["cache_updated_at"] = row["updated_at"]
    return payload


def put_ranking_cache(
    conn: sqlite3.Connection,
    cache_key: str,
    as_of: str,
    config: dict[str, Any],
    payload: dict[str, Any],
    elapsed_ms: int,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO ranking_cache(
            cache_key, as_of, config_json, payload_json, elapsed_ms, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (cache_key, as_of, json.dumps(config, ensure_ascii=False), json.dumps(payload, ensure_ascii=False), elapsed_ms, _now()),
    )
    conn.commit()


def get_prediction_run(conn: sqlite3.Connection, strategy_key: str, as_of: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT strategy_key, as_of, config_json, rows_json, universe, locked_at
        FROM prediction_runs
        WHERE strategy_key = ? AND as_of = ?
        """,
        (strategy_key, as_of),
    ).fetchone()
    if not row:
        return None
    return {
        "strategy_key": row["strategy_key"],
        "as_of": row["as_of"],
        "config": json.loads(row["config_json"]),
        "rows": json.loads(row["rows_json"]),
        "universe": row["universe"],
        "locked_at": row["locked_at"],
    }


def lock_prediction_run(
    conn: sqlite3.Connection,
    strategy_key: str,
    as_of: str,
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    universe: int,
) -> dict[str, Any]:
    existing = get_prediction_run(conn, strategy_key, as_of)
    if existing:
        return existing
    conn.execute(
        """
        INSERT INTO prediction_runs(
            strategy_key, as_of, config_json, rows_json, universe, locked_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            strategy_key,
            as_of,
            json.dumps(config, ensure_ascii=False),
            json.dumps(rows, ensure_ascii=False),
            universe,
            _now(),
        ),
    )
    conn.commit()
    locked = get_prediction_run(conn, strategy_key, as_of)
    if locked is None:
        raise RuntimeError(f"failed to lock prediction {strategy_key} {as_of}")
    return locked


def list_prediction_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT strategy_key, as_of, config_json, rows_json, universe, locked_at
        FROM prediction_runs
        ORDER BY as_of, strategy_key
        """
    ).fetchall()
    return [
        {
            "strategy_key": row["strategy_key"],
            "as_of": row["as_of"],
            "config": json.loads(row["config_json"]),
            "rows": json.loads(row["rows_json"]),
            "universe": row["universe"],
            "locked_at": row["locked_at"],
        }
        for row in rows
    ]


def outcome_exists(conn: sqlite3.Connection, strategy_key: str, as_of: str, code: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM prediction_outcomes
        WHERE strategy_key = ? AND as_of = ? AND code = ?
        """,
        (strategy_key, as_of, code),
    ).fetchone()
    return row is not None


def delete_outcomes(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM prediction_outcomes")
    conn.commit()
    get_logger().info("prediction_outcomes deleted rows=%s", cur.rowcount)
    return int(cur.rowcount)


def insert_outcome(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO prediction_outcomes(
            strategy_key, as_of, code, rank, proba, y_time, y_touch, excess, time_excess,
            ret, bench_ret, exit_reason, exit_date, stop, target, feature_json, model_fp, graded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["strategy_key"],
            row["as_of"],
            row["code"],
            row.get("rank"),
            row.get("proba"),
            row.get("y_time"),
            row.get("y_touch"),
            row.get("excess"),
            row.get("time_excess"),
            row.get("ret"),
            row.get("bench_ret"),
            row.get("exit_reason"),
            row.get("exit_date"),
            row.get("stop"),
            row.get("target"),
            json.dumps(row.get("features"), ensure_ascii=False) if row.get("features") is not None else None,
            row.get("model_fp"),
            row.get("graded_at") or _now(),
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def count_outcomes(conn: sqlite3.Connection, strategy_key: str | None = None, as_of: str | None = None) -> int:
    if strategy_key is None:
        return _count(conn, "prediction_outcomes")
    row = conn.execute(
        "SELECT COUNT(*) FROM prediction_outcomes WHERE strategy_key = ? AND as_of = ?",
        (strategy_key, as_of),
    ).fetchone()
    return int(row[0])


def list_outcome_as_ofs(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT as_of FROM prediction_outcomes ORDER BY as_of"
    ).fetchall()
    return [row["as_of"] for row in rows]


def outcome_period_stats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT as_of,
               COUNT(*) AS n,
               AVG(excess) AS avg_excess,
               AVG(y_touch) AS touch_rate,
               AVG(y_time) AS time_rate,
               AVG(CASE WHEN exit_reason = 'target' THEN 1.0 ELSE 0.0 END) AS hit_rate,
               AVG(proba) AS avg_proba
        FROM prediction_outcomes
        GROUP BY as_of
        ORDER BY as_of
        """
    ).fetchall()
    return [
        {
            "as_of": row["as_of"],
            "n": int(row["n"]),
            "avg_excess": None if row["avg_excess"] is None else round(float(row["avg_excess"]), 6),
            "touch_rate": None if row["touch_rate"] is None else round(float(row["touch_rate"]), 4),
            "time_rate": None if row["time_rate"] is None else round(float(row["time_rate"]), 4),
            "hit_rate": None if row["hit_rate"] is None else round(float(row["hit_rate"]), 4),
            "avg_proba": None if row["avg_proba"] is None else round(float(row["avg_proba"]), 4),
        }
        for row in rows
    ]


def list_outcome_probas(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT proba, y_time, y_touch, excess, exit_reason FROM prediction_outcomes WHERE proba IS NOT NULL"
    ).fetchall()
    return [
        {
            "proba": None if row["proba"] is None else float(row["proba"]),
            "y_time": row["y_time"],
            "y_touch": row["y_touch"],
            "excess": row["excess"],
            "exit_reason": row["exit_reason"],
        }
        for row in rows
    ]


def list_model_runs(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM model_runs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_model_run_from_row(row) for row in rows]


def latest_learn_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM model_runs
        WHERE status IN ('ok', 'skipped')
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return _model_run_from_row(row)


def get_verify_cache(conn: sqlite3.Connection, cache_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload_json, updated_at FROM verify_cache WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    if not row:
        return None
    payload = json.loads(row["payload_json"])
    payload["cached"] = True
    payload["cache_updated_at"] = row["updated_at"]
    return payload


def _daily_run_from_row(row: sqlite3.Row) -> dict[str, Any]:
    report = json.loads(row["report_json"]) if row["report_json"] else None
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "plan": row["plan"],
        "as_of": row["as_of"],
        "synced_days": row["synced_days"],
        "error_days": row["error_days"],
        "locked": row["locked"],
        "candidates_path": row["candidates_path"],
        "report": report,
        "error": row["error"],
    }


def start_daily_run(conn: sqlite3.Connection, plan: str) -> int:
    cur = conn.execute(
        "INSERT INTO daily_runs(started_at, status, plan) VALUES (?, 'running', ?)",
        (_now(), plan),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_daily_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    as_of: str | None = None,
    synced_days: int | None = None,
    error_days: int | None = None,
    locked: int | None = None,
    candidates_path: str | None = None,
    report: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    conn.execute(
        """
        UPDATE daily_runs
        SET finished_at = ?, status = ?, as_of = ?, synced_days = ?, error_days = ?,
            locked = ?, candidates_path = ?, report_json = ?, error = ?
        WHERE id = ?
        """,
        (
            _now(),
            status,
            as_of,
            synced_days,
            error_days,
            locked,
            candidates_path,
            json.dumps(report, ensure_ascii=False) if report is not None else None,
            error,
            run_id,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM daily_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"daily run {run_id} not found after finish")
    return _daily_run_from_row(row)


def _model_run_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "horizon": row["horizon"],
        "n_train": row["n_train"],
        "n_valid": row["n_valid"],
        "accuracy": row["accuracy"],
        "auc": row["auc"],
        "model_path": row["model_path"],
        "meta": json.loads(row["meta_json"]) if row["meta_json"] else None,
        "error": row["error"],
    }


def start_model_run(conn: sqlite3.Connection, *, horizon: int) -> int:
    cur = conn.execute(
        "INSERT INTO model_runs(created_at, status, horizon) VALUES (?, 'running', ?)",
        (_now(), horizon),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_model_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    horizon: int | None = None,
    n_train: int | None = None,
    n_valid: int | None = None,
    accuracy: float | None = None,
    auc: float | None = None,
    model_path: str | None = None,
    meta: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    conn.execute(
        """
        UPDATE model_runs
        SET finished_at = ?, status = ?, horizon = COALESCE(?, horizon),
            n_train = COALESCE(?, n_train), n_valid = COALESCE(?, n_valid),
            accuracy = COALESCE(?, accuracy), auc = COALESCE(?, auc),
            model_path = COALESCE(?, model_path),
            meta_json = COALESCE(?, meta_json), error = COALESCE(?, error)
        WHERE id = ?
        """,
        (
            _now(),
            status,
            horizon,
            n_train,
            n_valid,
            accuracy,
            auc,
            model_path,
            json.dumps(meta, ensure_ascii=False) if meta is not None else None,
            error,
            run_id,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM model_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"model run {run_id} not found after finish")
    return _model_run_from_row(row)


def latest_model_run(conn: sqlite3.Connection, status: str | None = None) -> dict[str, Any] | None:
    if status:
        row = conn.execute(
            "SELECT * FROM model_runs WHERE status = ? ORDER BY id DESC LIMIT 1",
            (status,),
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM model_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    return _model_run_from_row(row)


def start_strategy_run(conn: sqlite3.Connection, *, horizon: int) -> int:
    cur = conn.execute(
        "INSERT INTO strategy_runs(created_at, status, horizon) VALUES (?, 'running', ?)",
        (_now(), horizon),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_strategy_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    chosen_id: str | None = None,
    horizon: int | None = None,
    holdout_ok: bool | None = None,
    report: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    conn.execute(
        """
        UPDATE strategy_runs
        SET finished_at = ?, status = ?, chosen_id = ?, horizon = COALESCE(?, horizon),
            holdout_ok = ?, report_json = ?, error = ?
        WHERE id = ?
        """,
        (
            _now(),
            status,
            chosen_id,
            horizon,
            None if holdout_ok is None else int(holdout_ok),
            json.dumps(report, ensure_ascii=False) if report is not None else None,
            error,
            run_id,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM strategy_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"strategy run {run_id} not found after finish")
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "chosen_id": row["chosen_id"],
        "horizon": row["horizon"],
        "holdout_ok": None if row["holdout_ok"] is None else bool(row["holdout_ok"]),
        "report": json.loads(row["report_json"]) if row["report_json"] else None,
        "error": row["error"],
    }


def latest_strategy_run(conn: sqlite3.Connection, status: str | None = None) -> dict[str, Any] | None:
    if status:
        row = conn.execute(
            "SELECT * FROM strategy_runs WHERE status = ? ORDER BY id DESC LIMIT 1",
            (status,),
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM strategy_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "chosen_id": row["chosen_id"],
        "horizon": row["horizon"],
        "holdout_ok": None if row["holdout_ok"] is None else bool(row["holdout_ok"]),
        "report": json.loads(row["report_json"]) if row["report_json"] else None,
        "error": row["error"],
    }


def latest_daily_run(conn: sqlite3.Connection, status: str | None = None) -> dict[str, Any] | None:
    if status:
        row = conn.execute(
            "SELECT * FROM daily_runs WHERE status = ? ORDER BY id DESC LIMIT 1",
            (status,),
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM daily_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    return _daily_run_from_row(row)


def put_verify_cache(conn: sqlite3.Connection, cache_key: str, payload: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO verify_cache(cache_key, payload_json, updated_at)
        VALUES (?, ?, ?)
        """,
        (cache_key, json.dumps(payload, ensure_ascii=False), _now()),
    )
    conn.commit()


def all_bar_dates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT date FROM daily_bars ORDER BY date").fetchall()
    return [row["date"] for row in rows]


def load_price_map(
    conn: sqlite3.Connection,
    codes: Iterable[str],
    from_date: str,
    to_date: str,
) -> dict[str, dict[str, dict[str, float | None]]]:
    code_list = list(codes)
    if not code_list:
        return {}
    placeholders = ",".join("?" * len(code_list))
    rows = conn.execute(
        f"""
        SELECT date, code, adj_o, adj_h, adj_l, adj_c
        FROM daily_bars
        WHERE code IN ({placeholders})
          AND date BETWEEN ? AND ?
        """,
        (*code_list, from_date, to_date),
    ).fetchall()
    out: dict[str, dict[str, dict[str, float | None]]] = {}
    for row in rows:
        out.setdefault(row["code"], {})[row["date"]] = {
            "open": row["adj_o"],
            "high": row["adj_h"],
            "low": row["adj_l"],
            "close": row["adj_c"],
        }
    return out


def latest_master_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM listed_master").fetchone()
    return row[0] if row else None


def search_issues(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict[str, Any]]:
    text = query.strip()
    if not text:
        return []
    as_of = latest_master_date(conn)
    if not as_of:
        return []
    like = f"%{text}%"
    prefix = f"{text}%"
    exact = normalize_code(text)
    rows = conn.execute(
        """
        SELECT m.code, m.co_name, m.co_name_en, m.mkt_nm
        FROM listed_master AS m
        LEFT JOIN daily_bars AS b
          ON b.code = m.code
         AND b.date = (SELECT MAX(date) FROM daily_bars)
        WHERE m.date = ?
          AND (
            m.code LIKE ?
            OR IFNULL(m.co_name, '') LIKE ?
            OR IFNULL(m.co_name_en, '') LIKE ?
          )
        ORDER BY
          CASE
            WHEN m.code = ? OR m.code = ? THEN 0
            WHEN IFNULL(m.co_name, '') = ? THEN 1
            WHEN IFNULL(m.co_name, '') LIKE ? THEN 2
            ELSE 3
          END,
          IFNULL(b.adj_vo, 0) DESC,
          m.code
        LIMIT ?
        """,
        (as_of, like, like, like, text, exact, text, prefix, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def get_issue(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    as_of = latest_master_date(conn)
    if not as_of:
        return None
    candidates = [code.strip(), normalize_code(code)]
    for item in candidates:
        row = conn.execute(
            """
            SELECT code, co_name, co_name_en, mkt_nm, s33_nm
            FROM listed_master
            WHERE date = ? AND code = ?
            """,
            (as_of, item),
        ).fetchone()
        if row:
            return dict(row)
    return None


def get_chart_bars(conn: sqlite3.Connection, code: str, timeframe: str) -> list[dict[str, Any]]:
    resolved = get_issue(conn, code)
    actual = resolved["code"] if resolved else normalize_code(code)
    if timeframe == "weekly":
        _ensure_weekly_for_code(conn, actual)
        table = "weekly_bars"
        date_col = "week_end"
    elif timeframe == "daily":
        table = "daily_bars"
        date_col = "date"
    else:
        raise ValueError(f"unknown timeframe: {timeframe}")
    rows = conn.execute(
        f"""
        SELECT {date_col} AS date, adj_o, adj_h, adj_l, adj_c, adj_vo
        FROM {table}
        WHERE code = ?
          AND adj_o IS NOT NULL AND adj_h IS NOT NULL
          AND adj_l IS NOT NULL AND adj_c IS NOT NULL
        ORDER BY {date_col}
        """,
        (actual,),
    ).fetchall()
    return [
        {
            "date": row["date"],
            "open": row["adj_o"],
            "high": row["adj_h"],
            "low": row["adj_l"],
            "close": row["adj_c"],
            "volume": row["adj_vo"],
        }
        for row in rows
    ]


def rebuild_weekly_for_code(conn: sqlite3.Connection, code: str) -> int:
    rows = conn.execute(
        """
        SELECT date, code, adj_o, adj_h, adj_l, adj_c, adj_vo
        FROM daily_bars
        WHERE code = ?
        ORDER BY date
        """,
        (code,),
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        week_end = week_end_friday(date.fromisoformat(row["date"])).isoformat()
        grouped.setdefault(week_end, []).append(row)
    conn.execute("DELETE FROM weekly_bars WHERE code = ?", (code,))
    payload = [_weekly_row(week_end, code, series) for week_end, series in grouped.items()]
    conn.executemany(
        """
        INSERT INTO weekly_bars(
            week_end, code, adj_o, adj_h, adj_l, adj_c, adj_vo, sessions
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _ensure_weekly_for_code(conn: sqlite3.Connection, code: str) -> None:
    existing = conn.execute(
        "SELECT COUNT(*) FROM weekly_bars WHERE code = ?",
        (code,),
    ).fetchone()[0]
    if existing:
        return
    if rebuild_weekly_for_code(conn, code):
        conn.commit()


def _weekly_row(week_end: str, code: str, series: list[sqlite3.Row]) -> tuple[Any, ...]:
    opens = [item["adj_o"] for item in series if item["adj_o"] is not None]
    highs = [item["adj_h"] for item in series if item["adj_h"] is not None]
    lows = [item["adj_l"] for item in series if item["adj_l"] is not None]
    closes = [item["adj_c"] for item in series if item["adj_c"] is not None]
    volumes = [item["adj_vo"] for item in series if item["adj_vo"] is not None]
    return (
        week_end,
        code,
        opens[0] if opens else None,
        max(highs) if highs else None,
        min(lows) if lows else None,
        closes[-1] if closes else None,
        sum(volumes) if volumes else None,
        len(series),
    )


def status_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = {
        "calendar": _count(conn, "calendar"),
        "listed_master": _count(conn, "listed_master"),
        "daily_bars": _count(conn, "daily_bars"),
        "weekly_bars": _count(conn, "weekly_bars"),
        "prediction_runs": _count(conn, "prediction_runs"),
        "prediction_outcomes": _count(conn, "prediction_outcomes"),
        "daily_runs": _count(conn, "daily_runs"),
        "model_runs": _count(conn, "model_runs"),
        "strategy_runs": _count(conn, "strategy_runs"),
        "equity_facts": _count(conn, "equity_facts"),
        "fins_annual": _count(conn, "fins_annual"),
    }
    bar_range = conn.execute("SELECT MIN(date), MAX(date) FROM daily_bars").fetchone()
    states = {
        row["dataset"]: dict(row)
        for row in conn.execute("SELECT * FROM sync_state ORDER BY dataset")
    }
    return {
        "counts": counts,
        "daily_bar_from": bar_range[0],
        "daily_bar_to": bar_range[1],
        "sync_state": states,
    }


def week_end_friday(day: date) -> date:
    return day + timedelta(days=(5 - day.isoweekday()))


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _as_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        get_logger().debug("skip non-float value: %r", value)
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
