"""カブコム API とペーパー発注。パスワードはログに出さない。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

from skabu.broker.codes import to_internal, to_kabu
from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.logging_setup import dump_debug_event, get_logger

PAPER_LOG = "kabu_paper.jsonl"
EXCHANGE_TSE = 1
SIDE_SELL = "1"
SIDE_BUY = "2"
CASH_MARGIN_GENBUTSU = 1
FRONT_MOW = 10
FRONT_ZARABA_MORNING = 13
FRONT_CLOSE = 16
FRONT_LIMIT = 20
FRONT_STOP = 30


class Broker(Protocol):
    def ping(self) -> dict[str, Any]: ...
    def cash(self) -> float: ...
    def positions(self) -> list[dict[str, Any]]: ...
    def send_order(self, order: dict[str, Any]) -> dict[str, Any]: ...
    def get_order(self, order_id: str) -> dict[str, Any] | None: ...
    def cancel_order(self, order_id: str) -> dict[str, Any]: ...
    def list_orders(self) -> list[dict[str, Any]]: ...


def paper_log_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / PAPER_LOG


class PaperBroker:
    def __init__(self, settings: Settings, *, cash: float = 1_000_000.0, positions: list[dict[str, Any]] | None = None) -> None:
        self.settings = settings
        self._cash = float(cash)
        self._positions = list(positions or [])
        self.orders: dict[str, dict[str, Any]] = {}
        self.sent: list[dict[str, Any]] = []

    def ping(self) -> dict[str, Any]:
        return {"ok": True, "online": True, "paper": True, "live": False}

    def cash(self) -> float:
        return self._cash

    def positions(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._positions]

    def send_order(self, order: dict[str, Any]) -> dict[str, Any]:
        oid = f"P{len(self.sent) + 1:04d}"
        qty = int(order.get("qty") or 0)
        price = float(order.get("price") or order.get("fill_price") or 0)
        buy_open = order.get("side") == SIDE_BUY and order.get("front") in {FRONT_MOW, FRONT_ZARABA_MORNING, None}
        rec = {
            "id": oid,
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "qty": qty,
            "cum_qty": qty if buy_open else 0,
            "price": price,
            "front": order.get("front"),
            "state": "filled" if buy_open else "sent",
            "rec_type": 8 if buy_open else 4,
        }
        self.orders[oid] = rec
        self.sent.append({**order, "id": oid})
        self._append_log({"id": oid, **{k: v for k, v in order.items() if k != "password"}})
        get_logger().info("paper send_order id=%s symbol=%s side=%s qty=%s front=%s", oid, order.get("symbol"), order.get("side"), qty, order.get("front"))
        return {"id": oid, "result": 0}

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        return self.orders.get(order_id)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        rec = self.orders.get(order_id)
        if rec:
            rec["state"] = "canceled"
            rec["rec_type"] = 6
        get_logger().info("paper cancel_order id=%s", order_id)
        return {"id": order_id, "result": 0}

    def list_orders(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.orders.values()]

    def _append_log(self, event: dict[str, Any]) -> None:
        self.settings.ensure_dirs()
        path = paper_log_path(self.settings)
        line = json.dumps({"at": datetime.now(timezone.utc).isoformat(), **event}, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


class KabuBroker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._token: str | None = None
        self._last_send = 0.0

    def _base(self) -> str:
        return self.settings.kabu_base_url.rstrip("/")

    def _password(self) -> str:
        secret = self.settings.kabu_api_password.strip()
        if not secret:
            raise SkabuError("KABU_API_PASSWORD が未設定です。kabuステーションの API パスワードを .env に入れてください。")
        return secret

    def _headers(self) -> dict[str, str]:
        if not self._token:
            self._token = self._login()
        return {"X-API-KEY": self._token, "Content-Type": "application/json"}

    def _login(self) -> str:
        url = f"{self._base()}/token"
        try:
            res = httpx.post(url, json={"APIPassword": self._password()}, timeout=self.settings.skabu_http_timeout_sec)
        except httpx.HTTPError as exc:
            dump_debug_event(self.settings.debug_dir, {"kind": "kabu_token", "error": str(exc)})
            raise SkabuError(f"kabuステーションに接続できません: {exc}") from exc
        if res.status_code >= 400:
            dump_debug_event(self.settings.debug_dir, {"kind": "kabu_token", "status": res.status_code, "body": res.text[:500]})
            raise SkabuError(f"kabu トークン取得に失敗しました status={res.status_code}")
        body = res.json()
        token = body.get("Token") or body.get("token")
        if not token:
            raise SkabuError("kabu トークンが空です")
        get_logger().info("kabu token ok")
        return str(token)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            res = httpx.get(f"{self._base()}{path}", headers=self._headers(), params=params, timeout=self.settings.skabu_http_timeout_sec)
        except httpx.HTTPError as exc:
            dump_debug_event(self.settings.debug_dir, {"kind": "kabu_get", "path": path, "error": str(exc)})
            raise SkabuError(f"kabu GET {path} に失敗しました: {exc}") from exc
        if res.status_code >= 400:
            dump_debug_event(self.settings.debug_dir, {"kind": "kabu_get", "path": path, "status": res.status_code, "body": res.text[:800]})
            raise SkabuError(f"kabu GET {path} status={res.status_code}")
        return res.json()

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_send
        if gap < 0.25:
            time.sleep(0.25 - gap)
        self._last_send = time.monotonic()

    def ping(self) -> dict[str, Any]:
        try:
            self._token = self._login()
            wallet = self._get("/wallet/cash")
        except SkabuError as exc:
            get_logger().warning("kabu ping failed: %s", exc)
            return {"ok": False, "online": False, "paper": False, "live": True, "error": str(exc)}
        return {"ok": True, "online": True, "paper": False, "live": True, "wallet": wallet}

    def cash(self) -> float:
        body = self._get("/wallet/cash")
        for key in ("Cash", "StockAccountWallet", "AuKCStockAccountWallet"):
            raw = body.get(key) if isinstance(body, dict) else None
            if raw not in (None, ""):
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    continue
        raise SkabuError("余力が読めませんでした")

    def positions(self) -> list[dict[str, Any]]:
        body = self._get("/positions", params={"product": "1"})
        rows = body if isinstance(body, list) else []
        out: list[dict[str, Any]] = []
        for item in rows:
            qty = item.get("LeavesQty")
            if qty in (None, "", 0, 0.0):
                continue
            out.append(
                {
                    "code": to_internal(str(item.get("Symbol") or "")),
                    "kabu_code": str(item.get("Symbol") or ""),
                    "qty": float(qty),
                    "price": item.get("Price"),
                    "value": item.get("HoldingsValue"),
                    "pnl": item.get("ProfitLoss"),
                    "name": item.get("SymbolName"),
                }
            )
        return out

    def send_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self._throttle()
        qty = int(order.get("qty") or 0)
        if qty < 1:
            raise SkabuError("発注数量がありません")
        yen = float(order.get("notional") or 0)
        if yen > 0 and yen > self.settings.kabu_max_order_yen:
            raise SkabuError(f"1注文の上限 {self.settings.kabu_max_order_yen} 円を超えます")
        payload: dict[str, Any] = {
            "Password": self._password(),
            "Symbol": to_kabu(str(order.get("symbol") or "")),
            "Exchange": EXCHANGE_TSE,
            "SecurityType": 1,
            "Side": order.get("side"),
            "CashMargin": CASH_MARGIN_GENBUTSU,
            "DelivType": 2,
            "FundType": "AA",
            "AccountType": 4,
            "Qty": qty,
            "FrontOrderType": order.get("front"),
            "Price": order.get("price") or 0,
            "ExpireDay": 0,
        }
        if order.get("front") == FRONT_STOP:
            payload["ReverseLimitOrder"] = {
                "TriggerSec": 1,
                "TriggerPrice": order.get("trigger_price"),
                "UnderOver": 2,
                "AfterHitOrderType": 1,
                "AfterHitPrice": 0,
            }
        try:
            res = httpx.post(f"{self._base()}/sendorder", headers=self._headers(), json=payload, timeout=self.settings.skabu_http_timeout_sec)
        except httpx.HTTPError as exc:
            dump_debug_event(self.settings.debug_dir, {"kind": "kabu_send", "error": str(exc), "symbol": payload.get("Symbol")})
            raise SkabuError(f"kabu 発注に失敗しました: {exc}") from exc
        body = {}
        try:
            body = res.json()
        except Exception:
            body = {"raw": res.text[:500]}
        result_ok = body.get("Result") in (0, "0", None)
        has_id = bool(body.get("OrderId") or body.get("orderId"))
        if res.status_code >= 400 or not (result_ok or has_id):
            dump_debug_event(self.settings.debug_dir, {"kind": "kabu_send", "status": res.status_code, "result": body.get("Result"), "symbol": payload.get("Symbol")})
            raise SkabuError(body.get("Message") or f"kabu 発注エラー status={res.status_code}")
        oid = str(body.get("OrderId") or body.get("orderId") or "")
        if not oid:
            raise SkabuError("注文番号が返りませんでした")
        get_logger().info("kabu send_order id=%s symbol=%s side=%s qty=%s", oid, payload.get("Symbol"), payload.get("Side"), qty)
        return {"id": oid, "result": body.get("Result")}

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        body = self._get("/orders", params={"id": order_id, "details": "true"})
        rows = body if isinstance(body, list) else []
        if not rows:
            return None
        return _normalize_live_order(rows[0])

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        self._throttle()
        try:
            res = httpx.put(
                f"{self._base()}/cancelorder",
                headers=self._headers(),
                json={"OrderId": order_id, "Password": self._password()},
                timeout=self.settings.skabu_http_timeout_sec,
            )
        except httpx.HTTPError as exc:
            dump_debug_event(self.settings.debug_dir, {"kind": "kabu_cancel", "id": order_id, "error": str(exc)})
            raise SkabuError(f"kabu 取消に失敗しました: {exc}") from exc
        if res.status_code >= 400:
            dump_debug_event(self.settings.debug_dir, {"kind": "kabu_cancel", "id": order_id, "status": res.status_code})
            raise SkabuError(f"kabu 取消エラー status={res.status_code}")
        get_logger().info("kabu cancel_order id=%s", order_id)
        return {"id": order_id, "result": 0}

    def list_orders(self) -> list[dict[str, Any]]:
        body = self._get("/orders", params={"product": "1", "details": "true"})
        rows = body if isinstance(body, list) else []
        return [_normalize_live_order(item) for item in rows]


def _normalize_live_order(item: dict[str, Any]) -> dict[str, Any]:
    details = item.get("Details") or []
    latest = details[-1] if details else {}
    rec_type = latest.get("RecType")
    qty = item.get("OrderQty") or item.get("Qty")
    cum = item.get("CumQty")
    if cum is None:
        filled = [row for row in details if row.get("RecType") == 8]
        cum = sum(float(row.get("Qty") or 0) for row in filled)
    price = item.get("Price")
    if filled_px := _last_fill_price(details):
        price = filled_px
    return {
        "id": str(item.get("ID") or item.get("Id") or ""),
        "symbol": item.get("Symbol"),
        "side": str(item.get("Side") or ""),
        "qty": qty,
        "cum_qty": cum,
        "price": price,
        "state": item.get("State") or item.get("OrderState"),
        "rec_type": rec_type,
    }


def _last_fill_price(details: list[dict[str, Any]]) -> float | None:
    for row in reversed(details):
        if row.get("RecType") == 8 and row.get("Price") not in (None, ""):
            try:
                return float(row["Price"])
            except (TypeError, ValueError):
                return None
    return None


def order_filled(order: dict[str, Any] | None) -> bool:
    if not order:
        return False
    if order.get("rec_type") == 8:
        return True
    try:
        qty = float(order.get("qty") or 0)
        cum = float(order.get("cum_qty") or 0)
    except (TypeError, ValueError):
        return False
    return qty > 0 and cum >= qty


def order_dead(order: dict[str, Any] | None) -> bool:
    if not order:
        return False
    if order.get("state") in ("canceled", 5, "5"):
        return True
    return order.get("rec_type") in {3, 6, 7}


def make_broker(settings: Settings, *, paper: PaperBroker | None = None) -> Broker:
    if paper is not None:
        return paper
    if settings.kabu_live:
        return KabuBroker(settings)
    return PaperBroker(settings)
