from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from skabu.auto_sync import cap_pending, facts_is_fresh, run_auto_sync
from skabu.config import Settings
from skabu.store import connect, mark_sync_day
from skabu.sync import SyncService


class FakeClient:
    def __init__(self) -> None:
        self.bar_calls: list[str] = []
        self.val_calls: list[str] = []
        self.fin_calls: list[str] = []

    def get_calendar(self, **kwargs):
        return [
            {"Date": "2026-06-20", "HolDiv": "0"},
            {"Date": "2026-06-22", "HolDiv": "1"},
            {"Date": "2026-06-23", "HolDiv": "1"},
        ]

    def get_listed_master(self, **kwargs):
        return [
            {
                "Date": kwargs.get("date") or "2026-06-23",
                "Code": "86970",
                "CoName": "日本取引所グループ",
                "CoNameEn": "JPX",
                "S17": "16",
                "S17Nm": "金融（除く銀行）",
                "S33": "7200",
                "S33Nm": "その他金融業",
                "ScaleCat": "TOPIX Large70",
                "Mkt": "0111",
                "MktNm": "プライム",
                "Mrgn": "1",
                "MrgnNm": "信用",
                "ProdCat": "011",
            }
        ]

    def get_valuation(self, **kwargs):
        day = kwargs["date"]
        self.val_calls.append(day)
        return [{"Date": day, "Code": "86970", "Per": 12.5, "Pbr": 1.1}]

    def get_fins_summary(self, **kwargs):
        day = kwargs["date"]
        self.fin_calls.append(day)
        return [{"DiscDate": day, "Code": "86970", "Sales": 200, "OperatingProfit": 20}]

    def get_daily_bars(self, **kwargs):
        day = kwargs["date"]
        self.bar_calls.append(day)
        return [
            {
                "Date": day,
                "Code": "86970",
                "O": 1,
                "H": 2,
                "L": 1,
                "C": 2,
                "UL": "0",
                "LL": "0",
                "Vo": 100,
                "Va": 200,
                "AdjFactor": 1,
                "AdjO": 1,
                "AdjH": 2,
                "AdjL": 1,
                "AdjC": 2,
                "AdjVo": 100,
                "MktCap": 10,
                "ExRT": None,
            }
        ]


def _service(tmp_path: Path) -> tuple[SyncService, FakeClient]:
    settings = Settings(
        jquants_api_key="test",
        jquants_plan="free",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
        skabu_rate_limit_enabled=False,
    )
    settings.ensure_dirs()
    fake = FakeClient()
    return SyncService(settings, fake), fake  # type: ignore[arg-type]


def test_sync_calendar_master_bars_and_skip(tmp_path: Path) -> None:
    service, fake = _service(tmp_path)
    calendar = service.sync_calendar(from_date="2026-06-20", to_date="2026-06-23")
    assert calendar["rows"] == 3
    master = service.sync_master(as_of="2026-06-23")
    assert master["rows"] == 1
    first = service.sync_bars(from_date="2026-06-20", to_date="2026-06-23", full=True)
    assert first["synced_days"] == 2
    assert fake.bar_calls == ["2026-06-22", "2026-06-23"]
    second = service.sync_bars(from_date="2026-06-20", to_date="2026-06-23", full=True)
    assert second["synced_days"] == 0
    assert second["skipped_days"] == 2
    assert fake.bar_calls == ["2026-06-22", "2026-06-23"]
    snap = service.status()
    assert snap["counts"]["daily_bars"] == 2
    assert snap["counts"]["weekly_bars"] == 1


def test_sync_facts_incremental_and_not_in_bars(tmp_path: Path) -> None:
    service, fake = _service(tmp_path)
    service.sync_calendar(from_date="2026-06-20", to_date="2026-06-23")
    first = service.sync_facts(from_date="2026-06-20", to_date="2026-06-23", full=True)
    assert first["synced_days"] == 2
    assert fake.val_calls == ["2026-06-22", "2026-06-23"]
    assert fake.fin_calls == ["2026-06-22", "2026-06-23"]
    second = service.sync_facts(from_date="2026-06-20", to_date="2026-06-23", full=True)
    assert second["synced_days"] == 0
    assert second["skipped_days"] == 2
    assert fake.val_calls == ["2026-06-22", "2026-06-23"]
    snap = service.status()
    assert snap["counts"]["equity_facts"] >= 1
    assert snap["counts"]["fins_annual"] == 0
    assert fake.bar_calls == []


class FyFakeClient(FakeClient):
    def get_fins_summary(self, **kwargs):
        day = kwargs["date"]
        self.fin_calls.append(day)
        return [
            {
                "DiscDate": day,
                "Code": "86970",
                "Sales": 200,
                "OperatingProfit": 20,
                "ROE": 0.11,
                "EqAR": 0.5,
                "FDivAnn": 40,
                "DivAnn": 10,
                "CurPerType": "FY",
                "CurFYEn": "2025-03-31",
            }
        ]


def test_sync_facts_writes_fy_and_prefers_forecast_div(tmp_path: Path) -> None:
    settings = Settings(
        jquants_api_key="test",
        jquants_plan="free",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
        skabu_rate_limit_enabled=False,
    )
    settings.ensure_dirs()
    fake = FyFakeClient()
    service = SyncService(settings, fake)  # type: ignore[arg-type]
    service.sync_calendar(from_date="2026-06-20", to_date="2026-06-23")
    service.sync_facts(from_date="2026-06-20", to_date="2026-06-23", full=True)
    from skabu.store import connect, load_facts_as_of

    with connect(settings.db_path) as conn:
        facts = load_facts_as_of(conn, "2026-06-23", ["86970"])
        fy = conn.execute("SELECT fy_end, sales FROM fins_annual WHERE code='86970'").fetchall()
    assert facts["86970"]["roe"] == 0.11
    assert facts["86970"]["eq_ar"] == 0.5
    assert facts["86970"]["div_ann"] == 40.0
    assert "sales_growth3" not in facts["86970"]
    assert len(fy) == 1
    assert fy[0]["sales"] == 200.0


def _weekdays(end: date, count: int) -> list[str]:
    days: list[str] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return list(reversed(days))


class WideFakeClient(FakeClient):
    def __init__(self, days: list[str]) -> None:
        super().__init__()
        self.days = days
        self.calendar_calls = 0
        self.master_calls = 0

    def get_calendar(self, **kwargs):
        self.calendar_calls += 1
        return [{"Date": day, "HolDiv": "1"} for day in self.days]

    def get_listed_master(self, **kwargs):
        self.master_calls += 1
        return super().get_listed_master(**kwargs)


def _auto_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        jquants_api_key="test",
        jquants_plan="light",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
        skabu_rate_limit_enabled=False,
    )
    settings.ensure_dirs()
    return settings


def test_auto_sync_skips_bar_api_when_current(tmp_path: Path) -> None:
    days = ["2026-06-22", "2026-06-23"]
    settings = _auto_settings(tmp_path)
    fake = WideFakeClient(days)
    service = SyncService(settings, fake)  # type: ignore[arg-type]
    service.sync_calendar(from_date="2026-06-22", to_date="2026-06-23")
    with connect(settings.db_path) as conn:
        for day in days:
            mark_sync_day(conn, "daily_bars", day, "ok", row_count=1)
        conn.commit()
    fake.bar_calls.clear()
    result = run_auto_sync(
        settings,
        reason="start",
        now=datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc),
        today=date(2026, 6, 23),
        sync_service=service,
    )
    assert result["action"] == "synced"
    assert result["full"] is False
    assert fake.bar_calls == []
    assert fake.calendar_calls >= 1
    assert fake.master_calls >= 1


def test_auto_sync_skips_facts_when_fresh(tmp_path: Path) -> None:
    days = ["2026-06-22", "2026-06-23"]
    settings = _auto_settings(tmp_path)
    fake = WideFakeClient(days)
    service = SyncService(settings, fake)  # type: ignore[arg-type]
    now = datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)
    first = run_auto_sync(
        settings,
        reason="start",
        now=now,
        today=date(2026, 6, 23),
        sync_service=service,
    )
    assert first["facts"] is not None
    assert fake.val_calls == days
    second = run_auto_sync(
        settings,
        reason="start",
        now=now + timedelta(days=2),
        today=date(2026, 6, 23),
        sync_service=service,
    )
    assert second["facts"] is None
    assert fake.val_calls == days
    assert fake.fin_calls == days


def test_auto_sync_caps_pending_and_never_uses_full_window(tmp_path: Path) -> None:
    days = _weekdays(date(2026, 6, 23), 25)
    assert len(days) == 25
    capped, extra = cap_pending(days)
    assert extra == 5
    assert len(capped) == 20
    assert capped[0] == days[-20]
    assert days[0] not in capped

    settings = _auto_settings(tmp_path)
    fake = WideFakeClient(days)
    service = SyncService(settings, fake)  # type: ignore[arg-type]
    result = run_auto_sync(
        settings,
        reason="start",
        now=datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc),
        today=date(2026, 6, 23),
        sync_service=service,
    )
    assert result["full"] is False
    assert result["truncated_bars"] == 5
    assert result["pending_bars"] == 20
    assert fake.bar_calls == days[-20:]
    assert days[0] not in fake.bar_calls
    assert result["bars"]["candidate_days"] <= 20


def test_auto_sync_tick_skips_before_close_and_when_fresh(tmp_path: Path) -> None:
    days = ["2026-06-22", "2026-06-23"]
    settings = _auto_settings(tmp_path)
    fake = WideFakeClient(days)
    service = SyncService(settings, fake)  # type: ignore[arg-type]
    run_auto_sync(
        settings,
        reason="start",
        now=datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc),
        today=date(2026, 6, 23),
        sync_service=service,
    )
    fake.bar_calls.clear()
    fake.val_calls.clear()
    before = run_auto_sync(
        settings,
        reason="tick",
        now=datetime(2026, 6, 23, 6, 0, tzinfo=timezone.utc),
        today=date(2026, 6, 23),
        sync_service=service,
    )
    assert before["skip"] == "before_close"
    assert fake.bar_calls == []
    fresh = run_auto_sync(
        settings,
        reason="tick",
        now=datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc),
        today=date(2026, 6, 23),
        sync_service=service,
    )
    assert fresh["skip"] == "fresh"
    assert fake.bar_calls == []
    assert fake.val_calls == []


class EmptyThenReadyClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.empty_once: set[str] = {"2026-06-23"}

    def get_daily_bars(self, **kwargs):
        day = kwargs["date"]
        self.bar_calls.append(day)
        if day in self.empty_once:
            self.empty_once.discard(day)
            return []
        return [
            {
                "Date": day,
                "Code": "86970",
                "O": 1,
                "H": 2,
                "L": 1,
                "C": 2,
                "Vo": 100,
                "AdjO": 1,
                "AdjH": 2,
                "AdjL": 1,
                "AdjC": 2,
                "AdjVo": 100,
            }
        ]


def test_sync_bars_empty_day_is_not_ok_and_retries(tmp_path: Path) -> None:
    settings = Settings(
        jquants_api_key="test",
        jquants_plan="light",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
        skabu_rate_limit_enabled=False,
    )
    settings.ensure_dirs()
    fake = EmptyThenReadyClient()
    service = SyncService(settings, fake)  # type: ignore[arg-type]
    service.sync_calendar(from_date="2026-06-20", to_date="2026-06-23")
    first = service.sync_bars(from_date="2026-06-20", to_date="2026-06-23", full=True)
    assert first["synced_days"] == 1
    assert first["empty_days"] == 1
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT status, row_count FROM sync_days WHERE dataset='daily_bars' AND date='2026-06-23'"
        ).fetchone()
    assert row["status"] == "empty"
    assert row["row_count"] == 0
    second = service.sync_bars(from_date="2026-06-20", to_date="2026-06-23", full=True)
    assert second["synced_days"] == 1
    assert second["empty_days"] == 0
    assert fake.bar_calls.count("2026-06-23") == 2
    assert fake.bar_calls.count("2026-06-22") == 1


def test_zero_row_ok_is_retried(tmp_path: Path) -> None:
    service, fake = _service(tmp_path)
    service.sync_calendar(from_date="2026-06-20", to_date="2026-06-23")
    with connect(service.settings.db_path) as conn:
        mark_sync_day(conn, "daily_bars", "2026-06-22", "ok", row_count=1)
        mark_sync_day(conn, "daily_bars", "2026-06-23", "ok", row_count=0)
        conn.commit()
    result = service.sync_bars(from_date="2026-06-20", to_date="2026-06-23", full=True)
    assert result["synced_days"] == 1
    assert fake.bar_calls == ["2026-06-23"]


def test_tick_before_close_catches_up_yesterday_not_today(tmp_path: Path) -> None:
    days = ["2026-06-22", "2026-06-23"]
    settings = _auto_settings(tmp_path)
    fake = WideFakeClient(days)
    service = SyncService(settings, fake)  # type: ignore[arg-type]
    service.sync_calendar(from_date="2026-06-22", to_date="2026-06-23")
    with connect(settings.db_path) as conn:
        mark_sync_day(conn, "daily_bars", "2026-06-23", "ok", row_count=0)
        conn.commit()
    morning = datetime(2026, 6, 24, 6, 0, tzinfo=timezone.utc)
    result = run_auto_sync(
        settings,
        reason="tick",
        now=morning,
        today=date(2026, 6, 24),
        sync_service=service,
    )
    assert result["action"] == "synced"
    assert "2026-06-23" in fake.bar_calls
    assert "2026-06-24" not in fake.bar_calls


def test_manual_sync_includes_today_before_close(tmp_path: Path) -> None:
    days = ["2026-06-22", "2026-06-23"]
    settings = _auto_settings(tmp_path)
    fake = WideFakeClient(days)
    service = SyncService(settings, fake)  # type: ignore[arg-type]
    result = run_auto_sync(
        settings,
        reason="manual",
        now=datetime(2026, 6, 23, 6, 0, tzinfo=timezone.utc),
        today=date(2026, 6, 23),
        sync_service=service,
    )
    assert result["reason"] == "manual"
    assert "2026-06-23" in fake.bar_calls


def test_facts_is_fresh_window() -> None:
    now = datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)
    assert facts_is_fresh("2026-06-20T08:00:00Z", now) is True
    assert facts_is_fresh("2026-06-16T08:00:00Z", now) is False
    assert facts_is_fresh(None, now) is False
