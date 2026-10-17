"""J-Quants API v2 クライアント。認証・再試行・ページング・失敗ダンプ付き。"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any
from urllib.parse import urljoin

import httpx

from skabu.config import Settings, plan_safe_sample_window
from skabu.errors import ApiError, AuthError, NetworkError, RateLimitError
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.rate_limit import SlidingWindowRateLimiter

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class JQuantsClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
        limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = http_client is None
        timeout = httpx.Timeout(settings.skabu_http_timeout_sec)
        self._client = http_client or httpx.Client(
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        self._limiter = limiter
        if limiter is None and settings.skabu_rate_limit_enabled:
            self._limiter = SlidingWindowRateLimiter(settings.requests_per_minute)
        self._log = get_logger()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> JQuantsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def ping(self) -> dict[str, Any]:
        """接続確認。無料プランでも取れる日付でカレンダーを叩く。"""
        from_date, to_date = plan_safe_sample_window(self.settings.jquants_plan)
        rows = self.get_calendar(from_date=from_date, to_date=to_date)
        return {
            "ok": True,
            "calendar_rows": len(rows),
            "from": from_date,
            "to": to_date,
        }

    def get_calendar(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        holiday_division: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        if holiday_division:
            params["holidaydivision"] = holiday_division
        return self.get_all("/markets/calendar", params)

    def get_listed_master(
        self,
        *,
        code: str | None = None,
        date: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if code:
            params["code"] = code
        if date:
            params["date"] = date
        return self.get_all("/equities/master", params)

    def get_daily_bars(
        self,
        *,
        code: str | None = None,
        date: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]]:
        if not code and not date:
            raise ApiError("get_daily_bars には code か date のいずれかが必須です")
        params: dict[str, str] = {}
        if code:
            params["code"] = code
        if date:
            params["date"] = date
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        return self.get_all("/equities/bars/daily", params)

    def get_valuation(
        self,
        *,
        code: str | None = None,
        date: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]]:
        if not code and not date and not from_date:
            raise ApiError("get_valuation には code か date のいずれかが必須です")
        params: dict[str, str] = {}
        if code:
            params["code"] = code
        if date:
            params["date"] = date
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        return self.get_all("/equities/valuation", params)

    def get_fins_summary(
        self,
        *,
        code: str | None = None,
        date: str | None = None,
    ) -> list[dict[str, Any]]:
        if not code and not date:
            raise ApiError("get_fins_summary には code か date のいずれかが必須です")
        params: dict[str, str] = {}
        if code:
            params["code"] = code
        if date:
            params["date"] = date
        return self.get_all("/fins/summary", params)

    def get_all(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        query = dict(params or {})
        page = 0
        while True:
            page += 1
            payload = self.request_json("GET", path, query)
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise ApiError(
                    f"{path} のレスポンスに data 配列がありません",
                    body=_clip(payload),
                )
            collected.extend(rows)
            next_key = payload.get("pagination_key")
            if not next_key:
                self._log.debug("pagination done path=%s pages=%s rows=%s", path, page, len(collected))
                return collected
            query["pagination_key"] = str(next_key)
            self._log.debug("pagination continue path=%s page=%s rows_so_far=%s", path, page, len(collected))

    def request_json(self, method: str, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        api_key = self.settings.require_api_key()
        url = _join_url(self.settings.jquants_base_url, path)
        request_id = uuid.uuid4().hex[:12]
        last_error: Exception | None = None
        attempts = self.settings.skabu_http_max_retries + 1

        for attempt in range(1, attempts + 1):
            if self._limiter is not None:
                self._limiter.acquire()
            self._log.debug(
                "http start id=%s method=%s path=%s attempt=%s/%s params=%s",
                request_id,
                method,
                path,
                attempt,
                attempts,
                params,
            )
            started = time.monotonic()
            try:
                response = self._client.request(
                    method,
                    url,
                    params=params,
                    headers={"x-api-key": api_key},
                )
            except httpx.TimeoutException as exc:
                last_error = NetworkError(f"タイムアウト id={request_id} path={path}: {exc}")
                self._log.warning("%s", last_error)
                if attempt >= attempts:
                    dump_debug_event(
                        self.settings.debug_dir,
                        {
                            "kind": "timeout",
                            "request_id": request_id,
                            "method": method,
                            "path": path,
                            "params": params,
                            "error": str(exc),
                        },
                    )
                    raise last_error from exc
                _sleep_backoff(attempt)
                continue
            except httpx.HTTPError as exc:
                last_error = NetworkError(f"接続失敗 id={request_id} path={path}: {exc}")
                self._log.warning("%s", last_error)
                if attempt >= attempts:
                    dump_debug_event(
                        self.settings.debug_dir,
                        {
                            "kind": "network",
                            "request_id": request_id,
                            "method": method,
                            "path": path,
                            "params": params,
                            "error": str(exc),
                        },
                    )
                    raise last_error from exc
                _sleep_backoff(attempt)
                continue

            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._log.debug(
                "http done id=%s status=%s elapsed_ms=%s",
                request_id,
                response.status_code,
                elapsed_ms,
            )

            if response.status_code in {401, 403}:
                dump_debug_event(
                    self.settings.debug_dir,
                    {
                        "kind": "auth",
                        "request_id": request_id,
                        "status": response.status_code,
                        "path": path,
                        "body": _clip(response.text),
                    },
                )
                raise AuthError(
                    f"認証に失敗しました (HTTP {response.status_code})。"
                    " APIキーとプランを確認してください。"
                )

            if response.status_code == 429:
                retry_after = _retry_after_sec(response) or _backoff_sec(attempt)
                last_error = RateLimitError(
                    f"レート制限 (HTTP 429) id={request_id} retry_after={retry_after:.1f}s",
                    retry_after_sec=retry_after,
                )
                self._log.warning("%s", last_error)
                if attempt >= attempts:
                    dump_debug_event(
                        self.settings.debug_dir,
                        {
                            "kind": "rate_limit",
                            "request_id": request_id,
                            "path": path,
                            "params": params,
                            "headers": dict(response.headers),
                            "body": _clip(response.text),
                        },
                    )
                    raise last_error
                time.sleep(retry_after)
                continue

            if response.status_code >= 400:
                body = _clip(response.text)
                if response.status_code in RETRYABLE_STATUS and attempt < attempts:
                    last_error = ApiError(
                        f"HTTP {response.status_code} id={request_id} path={path}",
                        status_code=response.status_code,
                        request_id=request_id,
                        body=body,
                    )
                    self._log.warning("%s retrying", last_error)
                    _sleep_backoff(attempt)
                    continue
                dump_debug_event(
                    self.settings.debug_dir,
                    {
                        "kind": "http_error",
                        "request_id": request_id,
                        "status": response.status_code,
                        "path": path,
                        "params": params,
                        "body": body,
                    },
                )
                raise ApiError(
                    f"HTTP {response.status_code} id={request_id} path={path} body={body}",
                    status_code=response.status_code,
                    request_id=request_id,
                    body=body,
                )

            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                dump_debug_event(
                    self.settings.debug_dir,
                    {
                        "kind": "invalid_json",
                        "request_id": request_id,
                        "path": path,
                        "body": _clip(response.text),
                    },
                )
                raise ApiError(
                    f"JSON を解釈できません id={request_id} path={path}",
                    status_code=response.status_code,
                    request_id=request_id,
                    body=_clip(response.text),
                ) from exc

            if not isinstance(payload, dict):
                raise ApiError(
                    f"JSON オブジェクトではありません id={request_id}",
                    request_id=request_id,
                    body=_clip(payload),
                )
            return payload

        raise last_error or NetworkError("unknown HTTP failure")


def _join_url(base: str, path: str) -> str:
    if not base.endswith("/"):
        base = base + "/"
    return urljoin(base, path.lstrip("/"))


def _clip(value: Any, limit: int = 4000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > limit:
        return text[:limit] + "...(truncated)"
    return text


def _retry_after_sec(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return None


def _backoff_sec(attempt: int) -> float:
    return min(2 ** attempt, 20)


def _sleep_backoff(attempt: int) -> None:
    delay = _backoff_sec(attempt)
    get_logger().info("retry backoff: %.1fs (attempt %s)", delay, attempt)
    time.sleep(delay)


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _as_ratio_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def map_valuation_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        day = _first(row, "Date", "date")
        code = _first(row, "Code", "code")
        if not day or not code:
            continue
        out.append(
            {
                "date": day,
                "code": str(code),
                "per": _as_ratio_number(_first(row, "Per", "PER", "per")),
                "pbr": _as_ratio_number(_first(row, "Pbr", "PBR", "pbr")),
            }
        )
    return out


def map_fins_summary_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        day = _first(row, "DiscDate", "Date", "date")
        code = _first(row, "Code", "code")
        if not day or not code:
            continue
        sales = _as_ratio_number(_first(row, "Sales", "NetSales", "sales"))
        operating = _as_ratio_number(
            _first(row, "OperatingProfit", "OperatingIncome", "OP", "operating_profit")
        )
        margin = None
        if sales is not None and sales != 0 and operating is not None:
            margin = operating / sales
        elif sales == 0:
            get_logger().debug("fins op_margin skip code=%s date=%s sales=0", code, day)
        fdiv = _as_ratio_number(_first(row, "FDivAnn", "fdiv_ann"))
        tdiv = _as_ratio_number(_first(row, "DivAnn", "div_ann"))
        if fdiv is None and tdiv is not None:
            get_logger().debug("fins div_ann fallback DivAnn code=%s date=%s", code, day)
        period = str(_first(row, "CurPerType", "cur_per_type") or "").strip().upper()
        out.append(
            {
                "date": day,
                "code": str(code),
                "op_margin": margin,
                "roe": _as_ratio_number(_first(row, "ROE", "roe")),
                "eq_ar": _as_ratio_number(_first(row, "EqAR", "eq_ar")),
                "div_ann": fdiv if fdiv is not None else tdiv,
                "sales": sales,
                "cur_per_type": period or None,
                "fy_end": _first(row, "CurFYEn", "fy_end"),
            }
        )
    return out
