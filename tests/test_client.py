from pathlib import Path

import httpx
import pytest

from skabu.config import Settings
from skabu.errors import ApiError, AuthError, NetworkError, RateLimitError
from skabu.jquants import JQuantsClient, map_fins_summary_rows, map_valuation_rows


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        jquants_api_key="test-key",
        jquants_base_url="https://api.jquants.com/v2",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
        skabu_rate_limit_enabled=False,
        skabu_http_max_retries=2,
        skabu_http_timeout_sec=1.0,
    )


def _client(tmp_path: Path, handler) -> JQuantsClient:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return JQuantsClient(settings, http_client=http, limiter=None)


def test_get_calendar_success(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-key"
        assert request.url.path.endswith("/markets/calendar")
        return httpx.Response(200, json={"data": [{"Date": "2024-01-04", "HolidayDivision": "1"}]})

    with _client(tmp_path, handler) as client:
        rows = client.get_calendar(from_date="2024-01-04", to_date="2024-01-10")
    assert rows[0]["Date"] == "2024-01-04"


def test_pagination(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if "pagination_key" not in str(request.url):
            return httpx.Response(
                200,
                json={"data": [{"Code": "11110"}], "pagination_key": "NEXT"},
            )
        return httpx.Response(200, json={"data": [{"Code": "22220"}]})

    with _client(tmp_path, handler) as client:
        rows = client.get_listed_master()
    assert [row["Code"] for row in rows] == ["11110", "22220"]
    assert calls["n"] == 2


def test_auth_error_dumps_debug(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid key"})

    with _client(tmp_path, handler) as client:
        with pytest.raises(AuthError):
            client.get_calendar()
    dumps = list((tmp_path / "logs" / "debug").glob("*.json"))
    assert dumps


def test_rate_limit_then_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("skabu.jquants.time.sleep", lambda _sec: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"message": "slow down"})
        return httpx.Response(200, json={"data": [{"Date": "2024-01-04"}]})

    with _client(tmp_path, handler) as client:
        rows = client.get_calendar()
    assert rows[0]["Date"] == "2024-01-04"
    assert calls["n"] == 2


def test_rate_limit_exhausted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("skabu.jquants.time.sleep", lambda _sec: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "slow down"})

    with _client(tmp_path, handler) as client:
        with pytest.raises(RateLimitError):
            client.get_calendar()


def test_server_error_then_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("skabu.jquants.time.sleep", lambda _sec: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"data": [{"Code": "86970", "AdjC": 100.0}]})

    with _client(tmp_path, handler) as client:
        rows = client.get_daily_bars(code="86970", from_date="2024-01-04", to_date="2024-01-05")
    assert rows[0]["AdjC"] == 100.0


def test_daily_bars_require_code_or_date(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with _client(tmp_path, handler) as client:
        with pytest.raises(ApiError, match="code か date"):
            client.get_daily_bars()


def test_timeout_becomes_network_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("skabu.jquants.time.sleep", lambda _sec: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with _client(tmp_path, handler) as client:
        with pytest.raises(NetworkError, match="タイムアウト"):
            client.get_calendar()


def test_valuation_and_fins_endpoints(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/equities/valuation"):
            return httpx.Response(200, json={"data": [{"Date": "2026-01-05", "Code": "11110", "Per": 12, "Pbr": 1.4}]})
        if request.url.path.endswith("/fins/summary"):
            return httpx.Response(
                200,
                json={"data": [{"DiscDate": "2026-01-04", "Code": "11110", "Sales": 100, "OperatingProfit": 8}]},
            )
        return httpx.Response(404, json={"message": "nope"})

    with _client(tmp_path, handler) as client:
        vals = client.get_valuation(date="2026-01-05")
        fins = client.get_fins_summary(date="2026-01-04")
    assert map_valuation_rows(vals) == [{"date": "2026-01-05", "code": "11110", "per": 12.0, "pbr": 1.4}]
    assert map_fins_summary_rows(fins) == [
        {
            "date": "2026-01-04",
            "code": "11110",
            "op_margin": 0.08,
            "roe": None,
            "eq_ar": None,
            "div_ann": None,
            "sales": 100.0,
            "cur_per_type": None,
            "fy_end": None,
        }
    ]


def test_map_fins_prefers_forecast_div_and_extra_fields() -> None:
    rows = map_fins_summary_rows(
        [
            {
                "DiscDate": "2026-01-04",
                "Code": "11110",
                "Sales": 100,
                "OperatingProfit": 8,
                "ROE": 0.12,
                "EqAR": 0.45,
                "FDivAnn": 35,
                "DivAnn": 20,
                "CurPerType": "FY",
                "CurFYEn": "2025-03-31",
            }
        ]
    )
    assert rows[0]["op_margin"] == 0.08
    assert rows[0]["roe"] == 0.12
    assert rows[0]["eq_ar"] == 0.45
    assert rows[0]["div_ann"] == 35.0
    assert rows[0]["sales"] == 100.0
    assert rows[0]["cur_per_type"] == "FY"
    assert rows[0]["fy_end"] == "2025-03-31"
    fallback = map_fins_summary_rows(
        [{"DiscDate": "2026-01-04", "Code": "11110", "Sales": 50, "OP": 5, "DivAnn": 20}]
    )
    assert fallback[0]["div_ann"] == 20.0
    assert fallback[0]["op_margin"] == 0.1


def test_valuation_requires_code_or_date(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with _client(tmp_path, handler) as client:
        with pytest.raises(ApiError, match="code か date"):
            client.get_valuation()
        with pytest.raises(ApiError, match="code か date"):
            client.get_fins_summary()
