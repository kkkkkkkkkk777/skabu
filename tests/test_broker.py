from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from skabu.broker.client import FRONT_CLOSE, FRONT_MOW, FRONT_STOP, FRONT_ZARABA_MORNING, PaperBroker
from skabu.broker.codes import public_chart_links, to_internal, to_kabu
from skabu.broker.execute import (
    approve_close_sells,
    approve_tickets,
    halt_trading,
    prepare_buy,
    set_exit_date,
    trade_status,
    watch_exits,
)
from skabu.broker.prefs import MODE_APPROVE, MODE_AUTO, load_prefs, save_prefs
from skabu.buys import select_buys
from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.store import connect, finish_daily_run, start_daily_run, trading_dates

TOKYO = timezone(timedelta(hours=9))
BEFORE_OPEN = datetime(2026, 9, 24, 8, 50, tzinfo=TOKYO)
AFTER_OPEN = datetime(2026, 9, 24, 9, 5, tzinfo=TOKYO)


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        jquants_api_key="test",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
        kabu_live=False,
    )
    settings.ensure_dirs()
    return settings


def _row(**kwargs):
    base = {
        "code": "74750",
        "co_name": "アルビス",
        "s33_nm": "小売業",
        "qty": 100,
        "alloc": 200_000,
        "atr": 20.0,
        "close": 2000.0,
        "buyable": True,
        "total": 2,
        "entry_ref": 2000.0,
        "proba": None,
    }
    base.update(kwargs)
    return base


def _seed_calendar(settings: Settings, start: str = "2026-09-01", sessions: int = 80) -> None:
    conn = connect(settings.db_path)
    day = date.fromisoformat(start)
    found = 0
    rows: list[tuple[str, str]] = []
    while found < sessions:
        hol = "1" if day.weekday() < 5 else "2"
        rows.append((day.isoformat(), hol))
        if hol == "1":
            found += 1
        day += timedelta(days=1)
    conn.executemany("INSERT OR REPLACE INTO calendar(date, hol_div) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


def _seed_daily(settings: Settings, rows: list[dict], as_of: str = "2026-09-23") -> None:
    conn = connect(settings.db_path)
    run_id = start_daily_run(conn, "free")
    finish_daily_run(
        conn,
        run_id,
        status="ok",
        as_of=as_of,
        locked=1,
        report={"as_of": as_of, "rows": rows},
    )
    conn.close()


def test_codes_roundtrip() -> None:
    assert to_kabu("74750") == "7475"
    assert to_internal("7475") == "74750"
    assert to_kabu("8697") == "8697"
    assert to_internal("86970") == "86970"


def test_public_chart_links_from_internal_code() -> None:
    links = public_chart_links("72030")
    urls = {item["id"]: item["url"] for item in links}
    assert urls == {
        "yahoo": "https://finance.yahoo.co.jp/quote/7203.T/chart",
        "kabutan": "https://kabutan.jp/stock/chart?code=7203",
        "minkabu": "https://minkabu.jp/stock/7203/chart",
        "tradingview": "https://jp.tradingview.com/chart/?symbol=TSE:7203",
    }
    assert all(item["label"] for item in links)
    assert public_chart_links("12345") == []
    assert public_chart_links("") == []


def test_select_buys_skips_held_and_held_sectors() -> None:
    rows = [
        _row(code="11110", s33_nm="電気機器", atr=2.0, close=100.0, entry_ref=100.0),
        _row(code="22220", s33_nm="電気機器", atr=2.0, close=100.0, entry_ref=100.0),
        _row(code="33330", s33_nm="情報・通信業", atr=2.0, close=100.0, entry_ref=100.0),
    ]
    picked = select_buys(rows, top=5, holdout_ok=True, held_codes=["1111"], held_sectors=["電気機器", "電気機器"])
    assert [row["code"] for row in picked["bought"]] == ["33330"]
    reasons = {row["code"]: row["skip_reason"] for row in picked["skipped"]}
    assert reasons["11110"] == "already_held"
    assert reasons["22220"] == "sector_cap"


def test_trade_status_fills_empty_book_without_orders(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_daily(settings, [_row()])
    broker = PaperBroker(settings)
    payload = trade_status(settings, broker=broker)
    tickets = payload["book"]["tickets"]
    assert tickets[0]["status"] == "pending"
    assert tickets[0]["code"] == "74750"
    assert broker.sent == []


def test_approve_mode_does_not_send(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_daily(settings, [_row()])
    save_prefs(settings, mode=MODE_APPROVE)
    broker = PaperBroker(settings)
    payload = prepare_buy(settings, broker=broker, now=BEFORE_OPEN)
    assert payload["action"] == "pending"
    tickets = payload["book"]["tickets"]
    assert tickets[0]["status"] == "pending"
    assert tickets[0]["kabu_code"] == "7475"
    assert broker.sent == []


def test_approve_after_open_sends_market(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_daily(settings, [_row()])
    save_prefs(settings, mode=MODE_APPROVE)
    broker = PaperBroker(settings)
    prepare_buy(settings, broker=broker, now=BEFORE_OPEN)
    payload = approve_tickets(settings, ["74750"], broker=broker, now=AFTER_OPEN)
    assert payload["sent"] == ["74750"]
    assert broker.sent[0]["front"] == FRONT_MOW
    ticket = payload["book"]["tickets"][0]
    assert ticket["status"] == "filled"
    assert not ticket.get("target_order_id")
    assert ticket["stop_order_id"]
    assert ticket["entry_date"] == "2026-09-24"
    assert ticket["exit_note"] == "カレンダーが足りません"
    assert ticket["stop"] == 1970.0
    assert all(order.get("front") != 20 for order in broker.sent)


def test_unapproved_after_open_skips(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_daily(settings, [_row()])
    save_prefs(settings, mode=MODE_APPROVE)
    broker = PaperBroker(settings)
    payload = prepare_buy(settings, broker=broker, now=AFTER_OPEN)
    assert payload["action"] == "unapproved_skip"
    assert payload["book"]["tickets"][0]["status"] == "skipped"
    assert payload["book"]["tickets"][0]["skip_reason"] == "unapproved"
    assert broker.sent == []


def test_stop_fill_marks_stop(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_daily(settings, [_row()])
    save_prefs(settings, mode=MODE_AUTO)
    broker = PaperBroker(settings)
    payload = prepare_buy(settings, broker=broker, now=BEFORE_OPEN)
    assert payload["action"] == "auto_sent"
    assert broker.sent[0]["front"] == FRONT_ZARABA_MORNING
    ticket = payload["book"]["tickets"][0]
    assert ticket["status"] == "filled"
    assert not ticket.get("target_order_id")
    stop_id = ticket["stop_order_id"]
    assert stop_id
    broker.orders[stop_id]["state"] = "filled"
    broker.orders[stop_id]["rec_type"] = 8
    broker.orders[stop_id]["cum_qty"] = ticket["qty"]
    watched = watch_exits(settings, broker=broker)
    after = watched["book"]["tickets"][0]
    assert after["exit"] == "stop"


def test_halt_skips_filled_buy_cancel(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_daily(settings, [_row()])
    save_prefs(settings, mode=MODE_AUTO)
    broker = PaperBroker(settings)
    prepare_buy(settings, broker=broker, now=BEFORE_OPEN)
    payload = halt_trading(settings, broker=broker)
    assert payload["prefs"]["halted"] is True
    assert payload["prefs"]["mode"] == MODE_APPROVE
    ticket = payload["book"]["tickets"][0]
    assert ticket["buy_order_id"] not in payload["canceled"]
    assert ticket.get("target_order_id") not in payload["canceled"]
    assert ticket["stop_order_id"] in payload["canceled"]


def _fill(settings: Settings, *, hold_days: int = 20) -> tuple[PaperBroker, dict]:
    _seed_calendar(settings)
    _seed_daily(settings, [_row()])
    save_prefs(settings, mode=MODE_APPROVE, hold_days=hold_days)
    broker = PaperBroker(settings)
    prepare_buy(settings, broker=broker, now=BEFORE_OPEN)
    payload = approve_tickets(settings, ["74750"], broker=broker, now=AFTER_OPEN)
    return broker, payload["book"]["tickets"][0]


def test_fill_schedules_twenty_sessions_later(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _broker, ticket = _fill(settings, hold_days=20)
    conn = connect(settings.db_path)
    dates = trading_dates(conn, "2026-09-24", "2027-03-01")
    conn.close()
    assert ticket["entry_date"] == "2026-09-24"
    assert ticket["hold_days"] == 20
    assert ticket["exit_date"] == dates[20]
    assert not ticket.get("target_order_id")
    assert ticket["stop_order_id"]
    save_prefs(settings, hold_days=30)
    again = trade_status(settings, broker=PaperBroker(settings))
    assert again["book"]["tickets"][0]["exit_date"] == dates[20]
    assert load_prefs(settings)["hold_days"] == 30


def test_new_daily_keeps_filled_ticket(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _fill(settings)
    _seed_daily(settings, [_row(code="11110", co_name="別銘柄")], as_of="2026-09-24")
    broker = PaperBroker(settings)
    payload = prepare_buy(settings, broker=broker, now=BEFORE_OPEN)
    by_code = {item["code"]: item for item in payload["book"]["tickets"]}
    assert by_code["74750"]["status"] == "filled"
    assert by_code["74750"]["exit_date"]
    assert by_code["11110"]["status"] == "pending"


def test_close_sell_is_afternoon_close_and_cancels_stop(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    broker, ticket = _fill(settings, hold_days=1)
    assert ticket["exit_date"] == "2026-09-25"
    stop_id = ticket["stop_order_id"]
    afternoon = datetime(2026, 9, 25, 14, 0, tzinfo=TOKYO)
    payload = approve_close_sells(settings, ["74750"], broker=broker, now=afternoon)
    assert payload["sent"] == ["74750"]
    close = [order for order in broker.sent if order.get("front") == FRONT_CLOSE][-1]
    assert close["side"] == "1"
    assert close["price"] == 0
    assert broker.orders[stop_id]["state"] == "canceled"
    after = payload["book"]["tickets"][0]
    assert after["stop_order_id"] is None
    assert after["exit_order_id"]
    assert not after.get("exit")
    close_id = after["exit_order_id"]
    broker.orders[close_id]["state"] = "filled"
    broker.orders[close_id]["rec_type"] = 8
    broker.orders[close_id]["cum_qty"] = after["qty"]
    watched = watch_exits(settings, broker=broker)
    assert watched["book"]["tickets"][0]["exit"] == "time"


def test_close_sell_refuses_after_cutoff(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    broker, ticket = _fill(settings, hold_days=1)
    late = datetime(2026, 9, 25, 15, 25, tzinfo=TOKYO)
    with pytest.raises(SkabuError, match="引けの受付を過ぎています"):
        approve_close_sells(settings, ["74750"], broker=broker, now=late)
    assert ticket["stop_order_id"]
    assert all(order.get("front") != FRONT_CLOSE for order in broker.sent)
    assert all(order.get("front") != FRONT_STOP or order["id"] == ticket["stop_order_id"] for order in broker.sent)


def test_close_sell_skips_when_stop_cancel_fails(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    broker, _ticket = _fill(settings, hold_days=1)

    class BoomCancel(PaperBroker):
        def cancel_order(self, order_id: str) -> dict:
            raise SkabuError("cancel failed")

    boom = BoomCancel(settings)
    boom.orders = broker.orders
    boom.sent = broker.sent
    afternoon = datetime(2026, 9, 25, 14, 0, tzinfo=TOKYO)
    with pytest.raises(SkabuError, match="損切を取り消せなかった"):
        approve_close_sells(settings, ["74750"], broker=boom, now=afternoon)
    assert all(order.get("front") != FRONT_CLOSE for order in boom.sent)


def test_exit_date_must_be_a_session(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _fill(settings, hold_days=20)
    book = set_exit_date(settings, "74750", "2026-09-25")
    assert book["tickets"][0]["exit_date"] == "2026-09-25"
    with pytest.raises(SkabuError, match="営業日"):
        set_exit_date(settings, "74750", "2026-09-26")


def test_hold_days_rejects_out_of_range(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(SkabuError, match="1から250"):
        save_prefs(settings, hold_days=0)
    with pytest.raises(SkabuError, match="1から250"):
        save_prefs(settings, hold_days=251)
