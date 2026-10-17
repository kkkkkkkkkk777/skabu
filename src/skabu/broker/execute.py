"""候補を台帳にし、承認または自動で買い、約定後は損切と期間の引け売りを付ける。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from skabu.broker.book import find_ticket, load_book, new_ticket, save_book, upsert_ticket
from skabu.broker.client import (
    FRONT_CLOSE,
    FRONT_MOW,
    FRONT_STOP,
    FRONT_ZARABA_MORNING,
    SIDE_BUY,
    SIDE_SELL,
    Broker,
    make_broker,
    order_dead,
    order_filled,
)
from skabu.broker.codes import to_internal
from skabu.broker.prefs import MODE_AUTO, MODE_APPROVE, load_prefs, save_prefs
from skabu.config import Settings
from skabu.daily import latest_daily_payload, report_path
from skabu.errors import SkabuError
from skabu.levels import exit_pair, levels_from_anchor
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.store import connect, get_issue, trading_dates

CALENDAR_SHORT = "カレンダーが足りません"
CLOSE_PASSED = "引けの受付を過ぎています"
KEEP_STATUSES = {"sent", "filled", "approved"}

def _tokyo_tz() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Tokyo")
    except Exception:
        return timezone(timedelta(hours=9))


TOKYO = _tokyo_tz()


def morning_open_passed(now: datetime | None = None) -> bool:
    current = now or datetime.now(TOKYO)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TOKYO)
    else:
        current = current.astimezone(TOKYO)
    return current.hour > 9 or (current.hour == 9 and current.minute >= 0)


def buy_front(now: datetime | None = None) -> int:
    return FRONT_MOW if morning_open_passed(now) else FRONT_ZARABA_MORNING


def tokyo_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(TOKYO)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TOKYO)
    else:
        current = current.astimezone(TOKYO)
    return current


def tokyo_day(now: datetime | None = None) -> str:
    return tokyo_now(now).date().isoformat()


def close_window_passed(now: datetime | None = None) -> bool:
    current = tokyo_now(now)
    return current.hour > 15 or (current.hour == 15 and current.minute >= 25)


def exit_session(conn: Any, entry_date: str, hold_days: int) -> str | None:
    """買う日を0日目とし、hold_days 営業日後を返す。足りなければ None。"""
    try:
        start = date.fromisoformat(entry_date)
        days = int(hold_days)
    except (TypeError, ValueError):
        get_logger().warning("exit session bad input entry=%s hold=%s", entry_date, hold_days)
        return None
    if days < 1:
        return None
    end = start + timedelta(days=days * 3 + 30)
    dates = trading_dates(conn, start.isoformat(), end.isoformat())
    if not dates:
        get_logger().warning("hold calendar empty entry=%s hold=%s", entry_date, days)
        return None
    if entry_date in dates:
        origin = dates.index(entry_date)
    else:
        origin = 0
        get_logger().info("hold entry not a session entry=%s using=%s", entry_date, dates[0])
    index = origin + days
    if index >= len(dates):
        get_logger().warning(
            "hold calendar short entry=%s hold=%s sessions=%s",
            entry_date,
            days,
            len(dates) - origin,
        )
        return None
    return dates[index]


def _parse_day(value: Any) -> str:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise SkabuError("売る日は YYYY-MM-DD の営業日にしてください。") from exc


def snapshot_broker(settings: Settings, broker: Broker | None = None) -> dict[str, Any]:
    client = broker or make_broker(settings)
    ping = client.ping()
    if not ping.get("online"):
        return {"online": False, "paper": bool(ping.get("paper")), "live": bool(settings.kabu_live), "error": ping.get("error"), "cash": None, "positions": [], "codes": []}
    try:
        cash = client.cash()
        positions = client.positions()
    except SkabuError as exc:
        get_logger().warning("broker snapshot failed: %s", exc)
        return {"online": False, "paper": bool(ping.get("paper")), "live": bool(settings.kabu_live), "error": str(exc), "cash": None, "positions": [], "codes": []}
    codes = [to_internal(str(item.get("code") or "")) for item in positions]
    return {
        "online": True,
        "paper": bool(ping.get("paper")),
        "live": bool(settings.kabu_live),
        "error": None,
        "cash": cash,
        "positions": positions,
        "codes": [code for code in codes if code],
    }


def held_sectors(settings: Settings, codes: list[str]) -> list[str]:
    names: list[str] = []
    if not codes:
        return names
    try:
        with connect(settings.db_path) as conn:
            for code in codes:
                issue = get_issue(conn, code)
                if issue and issue.get("s33_nm"):
                    names.append(str(issue["s33_nm"]))
    except Exception:
        get_logger().exception("held sector lookup failed")
    return names


def _daily_rows(settings: Settings) -> tuple[str | None, list[dict[str, Any]]]:
    import json

    path = report_path(settings)
    as_of = None
    rows: list[dict[str, Any]] = []
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            as_of = raw.get("as_of")
            if raw.get("rows"):
                rows = list(raw["rows"])
        except (OSError, json.JSONDecodeError):
            get_logger().exception("daily report read failed")
    with connect(settings.db_path) as conn:
        payload = latest_daily_payload(conn)
    last = (payload.get("last_ok") or payload.get("last") or {}).get("report") or {}
    if last.get("rows"):
        rows = list(last["rows"])
        as_of = as_of or last.get("as_of")
    return as_of, rows


def sync_tickets_from_daily(settings: Settings, *, held_codes: list[str] | None = None) -> dict[str, Any]:
    as_of, rows = _daily_rows(settings)
    if not as_of:
        raise SkabuError("日次候補がありません。先に `python -m skabu daily` を実行してください。")
    book = load_book(settings)
    if book.get("as_of") != as_of:
        kept = [dict(item) for item in (book.get("tickets") or []) if item.get("status") in KEEP_STATUSES]
        get_logger().info(
            "trade book rolled as_of=%s from=%s kept=%s",
            as_of,
            book.get("as_of"),
            len(kept),
        )
        book = {"as_of": as_of, "tickets": kept, "updated_at": None}
    held = {to_internal(code) for code in (held_codes or [])}
    bought = [row for row in rows if row.get("qty") and int(row.get("qty") or 0) >= 1]
    if not bought and rows:
        bought = [row for row in rows if row.get("buyable") and row.get("qty")]
    for row in bought:
        code = to_internal(str(row.get("code") or ""))
        if not code:
            continue
        existing = find_ticket(book, code)
        if existing and existing.get("status") in {"sent", "filled", "approved"}:
            continue
        if existing and existing.get("status") == "skipped":
            continue
        ticket = existing or new_ticket(row, as_of=as_of)
        if code in held:
            ticket["status"] = "skipped"
            ticket["skip_reason"] = "already_held"
        else:
            ticket["qty"] = row.get("qty")
            ticket["alloc"] = row.get("alloc")
            ticket["atr"] = row.get("atr")
            ticket["ref_price"] = row.get("close") or row.get("entry_ref")
            if ticket.get("status") not in {"approved", "sent", "filled"}:
                ticket["status"] = "pending"
                ticket["skip_reason"] = None
        upsert_ticket(book, ticket)
    save_book(settings, book)
    return book


def prepare_buy(settings: Settings, *, broker: Broker | None = None, now: datetime | None = None) -> dict[str, Any]:
    prefs = load_prefs(settings)
    snap = snapshot_broker(settings, broker)
    book = sync_tickets_from_daily(settings, held_codes=snap.get("codes") or [])
    if prefs.get("halted"):
        get_logger().info("trade prepare halted")
        return {"ok": True, "action": "halted", "book": book, "prefs": prefs, "snapshot": snap}
    if not snap.get("online") and settings.kabu_live:
        _skip_open(book, reason="kabu_offline")
        save_book(settings, book)
        return {"ok": False, "action": "kabu_offline", "book": book, "prefs": prefs, "snapshot": snap}
    mode = prefs.get("mode") or MODE_APPROVE
    if mode == MODE_APPROVE:
        if morning_open_passed(now):
            _skip_open(book, reason="unapproved")
            save_book(settings, book)
            return {"ok": True, "action": "unapproved_skip", "book": book, "prefs": prefs, "snapshot": snap}
        return {"ok": True, "action": "pending", "book": book, "prefs": prefs, "snapshot": snap}
    sent = _send_tickets(settings, book, statuses={"pending", "approved"}, broker=broker or make_broker(settings), now=now)
    return {"ok": True, "action": "auto_sent", "sent": sent, "book": load_book(settings), "prefs": prefs, "snapshot": snap}


def approve_tickets(settings: Settings, codes: list[str], *, broker: Broker | None = None, now: datetime | None = None) -> dict[str, Any]:
    prefs = load_prefs(settings)
    if prefs.get("halted"):
        raise SkabuError("緊急停止中です")
    snap = snapshot_broker(settings, broker)
    if settings.kabu_live and not snap.get("online"):
        raise SkabuError(snap.get("error") or "kabuステーションに接続できません")
    book = load_book(settings)
    wanted = {to_internal(code) for code in codes}
    if not wanted:
        wanted = {to_internal(str(item.get("code") or "")) for item in book.get("tickets") or [] if item.get("status") == "pending"}
    for item in book.get("tickets") or []:
        if to_internal(str(item.get("code") or "")) in wanted and item.get("status") == "pending":
            item["status"] = "approved"
    save_book(settings, book)
    sent = _send_tickets(settings, book, statuses={"approved"}, broker=broker or make_broker(settings), now=now)
    return {"ok": True, "action": "approved", "sent": sent, "book": load_book(settings), "prefs": prefs, "snapshot": snap}


def halt_trading(settings: Settings, *, broker: Broker | None = None) -> dict[str, Any]:
    prefs = save_prefs(settings, mode=MODE_APPROVE, halted=True)
    client = broker or make_broker(settings)
    book = load_book(settings)
    canceled = []
    for item in book.get("tickets") or []:
        for key in ("buy_order_id", "target_order_id", "stop_order_id", "exit_order_id"):
            oid = item.get(key)
            if not oid:
                continue
            if key == "buy_order_id" and item.get("status") == "filled":
                continue
            try:
                client.cancel_order(str(oid))
                canceled.append(oid)
            except SkabuError as exc:
                get_logger().warning("halt cancel failed id=%s error=%s", oid, exc)
    get_logger().info("trade halted canceled=%s", canceled)
    return {"ok": True, "prefs": prefs, "canceled": canceled, "book": book}


def resume_trading(settings: Settings) -> dict[str, Any]:
    return save_prefs(settings, halted=False)


def watch_exits(settings: Settings, *, broker: Broker | None = None) -> dict[str, Any]:
    client = broker or make_broker(settings)
    book = load_book(settings)
    changed = 0
    for item in book.get("tickets") or []:
        if item.get("status") == "sent" and item.get("buy_order_id"):
            order = client.get_order(str(item["buy_order_id"]))
            if order_filled(order):
                item["status"] = "filled"
                item["fill_qty"] = order.get("cum_qty") or item.get("qty")
                item["fill_price"] = order.get("price") or item.get("ref_price")
                changed += 1
            elif order_dead(order):
                item["status"] = "rejected"
                item["skip_reason"] = "order_dead"
                changed += 1
        if item.get("status") == "filled" and item.get("fill_price") and not item.get("scheduled") and not item.get("exit"):
            _schedule_hold(settings, item)
            if not item.get("stop_order_id") and not item.get("target_order_id"):
                _place_stop(settings, client, item)
            item["scheduled"] = True
            changed += 1
        if item.get("stop_order_id") and not item.get("exit"):
            stop = client.get_order(str(item["stop_order_id"]))
            if order_filled(stop):
                item["exit"] = "stop"
                item["exit_note"] = None
                if item.get("exit_order_id"):
                    try:
                        client.cancel_order(str(item["exit_order_id"]))
                        item["exit_order_id"] = None
                    except SkabuError as exc:
                        get_logger().warning("cancel close after stop failed code=%s error=%s", item.get("code"), exc)
                changed += 1
                get_logger().info("exit stop code=%s", item.get("code"))
        if item.get("exit_order_id") and not item.get("exit"):
            close = client.get_order(str(item["exit_order_id"]))
            if order_filled(close):
                item["exit"] = "time"
                item["exit_note"] = None
                if item.get("stop_order_id"):
                    try:
                        client.cancel_order(str(item["stop_order_id"]))
                        item["stop_order_id"] = None
                    except SkabuError as exc:
                        get_logger().warning("cancel stop after close failed code=%s error=%s", item.get("code"), exc)
                changed += 1
                get_logger().info("exit time code=%s", item.get("code"))
            elif order_dead(close):
                get_logger().warning("close order dead code=%s id=%s", item.get("code"), item.get("exit_order_id"))
                item["exit_order_id"] = None
                item["exit_note"] = "引け売りが失効しました"
                _place_stop(settings, client, item)
                changed += 1
        if item.get("target_order_id") and item.get("stop_order_id") and not item.get("exit"):
            target = client.get_order(str(item["target_order_id"]))
            stop = client.get_order(str(item["stop_order_id"]))
            if order_filled(target):
                try:
                    client.cancel_order(str(item["stop_order_id"]))
                    item["stop_order_id"] = None
                    item["exit"] = "target"
                    changed += 1
                except SkabuError as exc:
                    get_logger().warning("cancel stop failed: %s", exc)
            elif order_filled(stop):
                try:
                    client.cancel_order(str(item["target_order_id"]))
                    item["target_order_id"] = None
                    item["exit"] = "stop"
                    changed += 1
                except SkabuError as exc:
                    get_logger().warning("cancel target failed: %s", exc)
    save_book(settings, book)
    return {"ok": True, "changed": changed, "book": book}


def trade_status(settings: Settings, *, broker: Broker | None = None) -> dict[str, Any]:
    prefs = load_prefs(settings)
    snap = snapshot_broker(settings, broker)
    book = load_book(settings)
    if not (book.get("tickets") or []):
        _as_of, rows = _daily_rows(settings)
        sized = [row for row in rows if row.get("qty") and int(row.get("qty") or 0) >= 1]
        if sized and _as_of:
            try:
                book = sync_tickets_from_daily(settings, held_codes=list(snap.get("codes") or []))
                get_logger().info(
                    "trade book filled from daily as_of=%s n=%s",
                    book.get("as_of"),
                    len(book.get("tickets") or []),
                )
            except SkabuError as exc:
                get_logger().warning("trade book fill skipped error=%s", exc)
                dump_debug_event(settings.debug_dir, {"kind": "trade_book_fill", "error": str(exc)})
    return {
        "prefs": prefs,
        "live": bool(settings.kabu_live),
        "snapshot": snap,
        "book": book,
        "today": tokyo_day(None),
        "max_order_yen": settings.kabu_max_order_yen,
    }


def _skip_open(book: dict[str, Any], *, reason: str) -> None:
    for item in book.get("tickets") or []:
        if item.get("status") == "pending":
            item["status"] = "skipped"
            item["skip_reason"] = reason


def _send_tickets(
    settings: Settings,
    book: dict[str, Any],
    *,
    statuses: set[str],
    broker: Broker,
    now: datetime | None,
) -> list[str]:
    sent: list[str] = []
    front = buy_front(now)
    for item in book.get("tickets") or []:
        if item.get("status") not in statuses:
            continue
        qty = int(item.get("qty") or 0)
        if qty < 1:
            item["status"] = "skipped"
            item["skip_reason"] = "size_too_small"
            continue
        notional = float(item.get("alloc") or 0)
        if notional > settings.kabu_max_order_yen:
            item["status"] = "skipped"
            item["skip_reason"] = "over_limit"
            continue
        if item.get("buy_order_id"):
            continue
        try:
            result = broker.send_order(
                {
                    "symbol": item.get("kabu_code") or item.get("code"),
                    "side": SIDE_BUY,
                    "qty": qty,
                    "front": front,
                    "price": 0,
                    "fill_price": item.get("ref_price"),
                    "notional": notional,
                }
            )
        except SkabuError as exc:
            item["status"] = "rejected"
            item["skip_reason"] = str(exc)
            dump_debug_event(settings.debug_dir, {"kind": "trade_send", "code": item.get("code"), "error": str(exc)})
            continue
        item["status"] = "sent"
        item["buy_order_id"] = result.get("id")
        item["front_order_type"] = front
        if not item.get("entry_date"):
            item["entry_date"] = tokyo_day(now)
        if not item.get("hold_days"):
            item["hold_days"] = int(load_prefs(settings).get("hold_days") or 20)
        get_logger().info(
            "buy sent code=%s entry=%s hold_days=%s front=%s",
            item.get("code"),
            item.get("entry_date"),
            item.get("hold_days"),
            front,
        )
        sent.append(str(item.get("code")))
    save_book(settings, book)
    watch_exits(settings, broker=broker)
    return sent


def _schedule_hold(settings: Settings, item: dict[str, Any]) -> None:
    if item.get("exit_date"):
        return
    if not item.get("entry_date"):
        item["entry_date"] = tokyo_day(None)
        get_logger().warning("hold entry_date missing code=%s using=%s", item.get("code"), item["entry_date"])
    if not item.get("hold_days"):
        item["hold_days"] = int(load_prefs(settings).get("hold_days") or 20)
    try:
        hold_days = int(item["hold_days"])
    except (TypeError, ValueError):
        hold_days = 20
        item["hold_days"] = hold_days
    try:
        with connect(settings.db_path) as conn:
            exit_date = exit_session(conn, str(item["entry_date"]), hold_days)
    except Exception:
        get_logger().exception("hold calendar lookup failed code=%s", item.get("code"))
        exit_date = None
    if exit_date:
        item["exit_date"] = exit_date
        item["exit_note"] = None
        get_logger().info(
            "hold scheduled code=%s entry=%s hold=%s exit=%s",
            item.get("code"),
            item.get("entry_date"),
            hold_days,
            exit_date,
        )
        return
    item["exit_date"] = None
    item["exit_note"] = CALENDAR_SHORT
    get_logger().warning(
        "hold calendar short code=%s entry=%s hold=%s",
        item.get("code"),
        item.get("entry_date"),
        hold_days,
    )


def _place_stop(settings: Settings, broker: Broker, item: dict[str, Any]) -> None:
    if item.get("stop_order_id") or item.get("exit"):
        return
    fill = float(item.get("fill_price") or 0)
    atr = item.get("atr")
    try:
        atr_value = float(atr) if atr not in (None, "") else None
    except (TypeError, ValueError):
        atr_value = None
    stop_mult, target_mult = exit_pair(settings)
    levels = levels_from_anchor(
        fill,
        atr_value,
        provisional=False,
        stop_mult=stop_mult,
        target_mult=target_mult,
    )
    item["stop"] = levels.get("stop")
    qty = int(item.get("fill_qty") or item.get("qty") or 0)
    if qty < 1 or not item.get("stop"):
        get_logger().warning("skip stop code=%s fill=%s atr=%s", item.get("code"), fill, atr)
        return
    symbol = item.get("kabu_code") or item.get("code")
    try:
        stop = broker.send_order(
            {
                "symbol": symbol,
                "side": SIDE_SELL,
                "qty": qty,
                "front": FRONT_STOP,
                "trigger_price": item["stop"],
                "price": 0,
                "notional": qty * float(item["stop"]),
            }
        )
    except SkabuError as exc:
        dump_debug_event(settings.debug_dir, {"kind": "trade_stop", "code": item.get("code"), "error": str(exc)})
        get_logger().warning("place stop failed code=%s error=%s", item.get("code"), exc)
        return
    item["stop_order_id"] = stop.get("id")
    get_logger().info("stop placed code=%s stop=%s hold_exit=%s", item.get("code"), item.get("stop"), item.get("exit_date"))


def set_mode(settings: Settings, mode: str) -> dict[str, Any]:
    return save_prefs(settings, mode=mode)


def set_hold_days(settings: Settings, hold_days: Any) -> dict[str, Any]:
    return save_prefs(settings, hold_days=hold_days)


def set_exit_date(settings: Settings, code: str, exit_date: Any) -> dict[str, Any]:
    day = _parse_day(exit_date)
    book = load_book(settings)
    item = find_ticket(book, code)
    if not item or item.get("status") != "filled":
        raise SkabuError("約定済みの行だけ売る日を変えられます。")
    if item.get("exit") or item.get("exit_order_id"):
        raise SkabuError("すでに売りに出している行の日付は変えられません。")
    entry = str(item.get("entry_date") or "")
    if entry and day < entry:
        raise SkabuError("売る日は買った日より前にはできません。")
    try:
        with connect(settings.db_path) as conn:
            sessions = trading_dates(conn, day, day)
    except Exception as exc:
        get_logger().exception("exit date calendar failed code=%s", code)
        raise SkabuError("営業日カレンダーを読めませんでした。") from exc
    if day not in sessions:
        raise SkabuError("売る日は営業日にしてください。")
    item["exit_date"] = day
    item["exit_note"] = None
    item["scheduled"] = True
    save_book(settings, book)
    get_logger().info("exit date set code=%s exit=%s", item.get("code"), day)
    return book


def approve_close_sells(
    settings: Settings,
    codes: list[str],
    *,
    broker: Broker | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    prefs = load_prefs(settings)
    if prefs.get("halted"):
        raise SkabuError("緊急停止中です")
    if close_window_passed(now):
        raise SkabuError(CLOSE_PASSED)
    snap = snapshot_broker(settings, broker)
    if settings.kabu_live and not snap.get("online"):
        raise SkabuError(snap.get("error") or "kabuステーションに接続できません")
    wanted = {to_internal(code) for code in codes if str(code).strip()}
    if not wanted:
        raise SkabuError("今日が売る日の行を選んでください。")
    today = tokyo_day(now)
    book = load_book(settings)
    client = broker or make_broker(settings)
    sent: list[str] = []
    detail: str | None = None
    for item in book.get("tickets") or []:
        code = to_internal(str(item.get("code") or ""))
        if code not in wanted:
            continue
        if item.get("status") != "filled" or item.get("exit") or item.get("exit_order_id"):
            continue
        if item.get("exit_date") != today:
            detail = "売る日は今日ではありません"
            item["exit_note"] = detail
            get_logger().info("close skipped not today code=%s exit=%s today=%s", code, item.get("exit_date"), today)
            continue
        if item.get("stop_order_id"):
            stop = client.get_order(str(item["stop_order_id"]))
            if order_filled(stop):
                item["exit"] = "stop"
                item["exit_note"] = None
                detail = "逆指値が先に約定しています"
                get_logger().info("close skipped stop already filled code=%s", code)
                continue
            try:
                client.cancel_order(str(item["stop_order_id"]))
                item["stop_order_id"] = None
            except SkabuError as exc:
                detail = "損切を取り消せなかったので引け売りは出していません"
                item["exit_note"] = detail
                dump_debug_event(settings.debug_dir, {"kind": "trade_close_cancel", "code": code, "error": str(exc)})
                get_logger().warning("close cancel stop failed code=%s error=%s", code, exc)
                continue
        qty = int(item.get("fill_qty") or item.get("qty") or 0)
        price = float(item.get("fill_price") or item.get("ref_price") or 0)
        if qty < 1:
            item["exit_note"] = "株数がありません"
            get_logger().warning("close skipped no qty code=%s", code)
            continue
        try:
            result = client.send_order(
                {
                    "symbol": item.get("kabu_code") or item.get("code"),
                    "side": SIDE_SELL,
                    "qty": qty,
                    "front": FRONT_CLOSE,
                    "price": 0,
                    "notional": qty * price,
                }
            )
        except SkabuError as exc:
            item["exit_note"] = str(exc)
            dump_debug_event(settings.debug_dir, {"kind": "trade_close", "code": code, "error": str(exc)})
            get_logger().warning("close send failed code=%s error=%s", code, exc)
            _place_stop(settings, client, item)
            continue
        item["exit_order_id"] = result.get("id")
        item["exit_note"] = None
        sent.append(code)
        get_logger().info("close sent code=%s exit=%s id=%s", code, today, result.get("id"))
    save_book(settings, book)
    if not sent:
        raise SkabuError(detail or "今日が売る日の約定済みを選んでください。")
    watched = watch_exits(settings, broker=client)
    return {
        "ok": True,
        "action": "close_sent",
        "sent": sent,
        "book": watched.get("book") or load_book(settings),
        "prefs": prefs,
        "snapshot": snap,
    }
