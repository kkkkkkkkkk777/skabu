"""当日の売買台帳。同一予測日・同一銘柄は再送しない。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skabu.broker.codes import to_internal, to_kabu
from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.logging_setup import dump_debug_event, get_logger

BOOK_NAME = "trade_book.json"
STATUSES = {
    "pending",
    "approved",
    "sent",
    "filled",
    "skipped",
    "rejected",
}


def book_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / BOOK_NAME


def empty_book(as_of: str | None = None) -> dict[str, Any]:
    return {"as_of": as_of, "updated_at": None, "tickets": []}


def load_book(settings: Settings) -> dict[str, Any]:
    path = book_path(settings)
    if not path.exists():
        return empty_book()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        get_logger().exception("trade book read failed")
        dump_debug_event(settings.debug_dir, {"kind": "trade_book_read", "error": str(exc)})
        return empty_book()
    if not isinstance(raw, dict):
        return empty_book()
    tickets = raw.get("tickets") if isinstance(raw.get("tickets"), list) else []
    return {"as_of": raw.get("as_of"), "updated_at": raw.get("updated_at"), "tickets": tickets}


def save_book(settings: Settings, book: dict[str, Any]) -> dict[str, Any]:
    settings.ensure_dirs()
    book["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = book_path(settings)
    try:
        path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        dump_debug_event(settings.debug_dir, {"kind": "trade_book_write", "error": str(exc)})
        raise SkabuError(f"trade_book.json の書き込みに失敗しました: {exc}") from exc
    return book


def ticket_key(code: str) -> str:
    return to_internal(code)


def find_ticket(book: dict[str, Any], code: str) -> dict[str, Any] | None:
    key = ticket_key(code)
    for item in book.get("tickets") or []:
        if ticket_key(str(item.get("code") or "")) == key:
            return item
    return None


def upsert_ticket(book: dict[str, Any], ticket: dict[str, Any]) -> dict[str, Any]:
    key = ticket_key(str(ticket.get("code") or ""))
    tickets = list(book.get("tickets") or [])
    out = []
    replaced = False
    for item in tickets:
        if ticket_key(str(item.get("code") or "")) == key:
            out.append(ticket)
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(ticket)
    book["tickets"] = out
    return ticket


def new_ticket(row: dict[str, Any], *, as_of: str) -> dict[str, Any]:
    code = to_internal(str(row.get("code") or ""))
    return {
        "as_of": as_of,
        "code": code,
        "kabu_code": to_kabu(code),
        "co_name": row.get("co_name"),
        "s33_nm": row.get("s33_nm"),
        "qty": row.get("qty"),
        "alloc": row.get("alloc"),
        "atr": row.get("atr"),
        "ref_price": row.get("close") or row.get("entry_ref"),
        "status": "pending",
        "skip_reason": None,
        "buy_order_id": None,
        "target_order_id": None,
        "stop_order_id": None,
        "fill_price": None,
        "fill_qty": None,
        "target": None,
        "stop": None,
        "front_order_type": None,
        "entry_date": None,
        "exit_date": None,
        "hold_days": None,
        "exit_order_id": None,
        "exit_note": None,
        "exit": None,
        "scheduled": False,
    }
