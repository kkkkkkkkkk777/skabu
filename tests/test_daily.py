import json
from datetime import date
from pathlib import Path

import pytest

from skabu.config import Settings
from skabu.daily import daily_needs_rerun, describe_freshness, run_daily, write_candidates_csv
from skabu.errors import SkabuError
from skabu.store import connect, latest_daily_run, upsert_daily_bars, upsert_master
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


def _seed(db_path: Path, days: int = 25) -> None:
    conn = connect(db_path)
    last = f"2026-01-{days:02d}"
    upsert_master(
        conn,
        [
            {"Date": last, "Code": "11110", "CoName": "上昇工業", "MktNm": "プライム"},
            {"Date": last, "Code": "22220", "CoName": "下降商事", "MktNm": "プライム"},
        ],
    )
    rising = []
    falling = []
    for index in range(days):
        up = 100 + index * 1.5
        down = 200 - index * 1.2
        day = f"2026-01-{index + 1:02d}"
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
    upsert_daily_bars(conn, rising + falling)
    conn.commit()
    conn.close()


def test_daily_needs_rerun_only_for_stale_confirmation() -> None:
    active = [{"id": "sma", "params": {"length": 5}}]
    gates = [{"id": "rsi", "feature": "value", "op": "lt", "value": 70}]
    assert daily_needs_rerun(None, selections=active, filters=gates) is False
    assert daily_needs_rerun(
        {"strategy": active, "filters": gates, "skip_reasons": {"stop_wide": 1}, "bought": 0},
        selections=active,
        filters=gates,
    ) is False
    assert daily_needs_rerun(
        {"strategy": active, "filters": gates, "skip_reasons": {"strategy_holdout": 5}, "bought": 0},
        selections=active,
        filters=gates,
    ) is True
    assert daily_needs_rerun(
        {"strategy": [{"id": "boost", "params": {"high": 0.58, "low": 0.42}}], "skip_reasons": {}, "bought": 1},
        selections=active,
        filters=gates,
    ) is True


def test_describe_freshness_free_warns() -> None:
    info = describe_freshness("free", "2026-06-30", today=date(2026, 9, 23))
    assert info["live"] is False
    assert info["lag_days"] == 85
    assert "12週間" in info["message"]


def test_describe_freshness_light_same_day() -> None:
    info = describe_freshness("light", "2026-09-23", today=date(2026, 9, 23))
    assert info["live"] is True
    assert "当日" in info["message"]


def test_write_candidates_csv_atomic(tmp_path: Path) -> None:
    path = tmp_path / "candidates.csv"
    write_candidates_csv(
        path,
        [{"rank": 1, "code": "11110", "co_name": "上昇工業", "total": 3, "scores": {"rsi": 1}, "close": 10}],
        as_of="2026-01-25",
    )
    text = path.read_text(encoding="utf-8-sig")
    assert "11110" in text
    assert "rsi" in text
    assert "stop" in text
    assert "target" in text
    assert "s33_nm" in text
    assert "alloc" in text
    assert "qty" in text
    assert not path.with_suffix(".csv.tmp").exists()


def test_run_daily_skip_sync_writes_and_locks(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path)
    payload = run_daily(settings, skip_sync=True, top=2, min_avg_volume=1, min_bars=10)
    assert payload["status"] == "ok"
    assert payload["as_of"] == "2026-01-25"
    assert payload["locked"] == 1
    csv_file = settings.skabu_data_dir / "candidates.csv"
    assert csv_file.exists()
    csv_text = csv_file.read_text(encoding="utf-8-sig")
    assert "11110" in csv_text
    assert "reason" in csv_text
    report = settings.skabu_data_dir / "daily_report.json"
    assert report.exists()
    with connect(settings.db_path) as conn:
        last = latest_daily_run(conn, "ok")
    assert last is not None
    assert last["report"]["rows"][0]["code"] == "11110"
    assert last["report"]["rows"][0].get("reason")
    assert last["report"]["grade"]["status"] == "ok"


def test_run_daily_buys_when_holdout_fails(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path)
    active_path(settings).write_text(
        json.dumps(
            {
                "id": "boost",
                "title": "Boost",
                "selections": [{"id": "sma", "params": {"length": 5}}],
                "filters": [],
                "holdout_ok": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    payload = run_daily(settings, skip_sync=True, top=2, min_avg_volume=1, min_bars=10)
    assert payload["status"] == "ok"
    assert payload["report"]["cash"] is False
    assert payload["report"]["bought"] >= 1
    csv_file = settings.skabu_data_dir / "candidates.csv"
    assert csv_file.exists()
    text = csv_file.read_text(encoding="utf-8-sig")
    assert "11110" in text
    assert "strategy_holdout" not in (payload["report"].get("skip_reasons") or {})


def test_run_daily_empty_does_not_write_csv(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(SkabuError, match="日足がありません"):
        run_daily(settings, skip_sync=True, min_bars=10)
    assert not (settings.skabu_data_dir / "candidates.csv").exists()
    with connect(settings.db_path) as conn:
        last = latest_daily_run(conn)
    assert last is not None
    assert last["status"] == "failed"


def test_sync_error_keeps_previous_csv(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path)
    first = run_daily(settings, skip_sync=True, top=2, min_avg_volume=1, min_bars=10)
    csv_file = Path(first["candidates_path"])
    before = csv_file.read_text(encoding="utf-8-sig")

    class BrokenSync:
        def sync_calendar(self, **kwargs):
            return {"dataset": "calendar", "rows": 1}

        def sync_master(self, **kwargs):
            return {"dataset": "listed_master", "rows": 1}

        def sync_bars(self, **kwargs):
            return {"dataset": "daily_bars", "synced_days": 0, "error_days": 1}

    with pytest.raises(SkabuError, match="同期に失敗"):
        run_daily(settings, skip_sync=False, sync_service=BrokenSync(), top=2, min_avg_volume=1, min_bars=10)
    assert csv_file.read_text(encoding="utf-8-sig") == before
    with connect(settings.db_path) as conn:
        last = latest_daily_run(conn)
    assert last["status"] == "failed"
    assert last["error_days"] == 1
