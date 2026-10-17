from datetime import date, timedelta
from pathlib import Path

from skabu.config import Settings
from skabu.store import connect, upsert_daily_bars, upsert_master
from skabu.ranking import default_rank_selections, default_strategy_label
from skabu.strategy import active_path, active_rank_selections, adopt_strategy, load_active_strategy


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        jquants_api_key="test",
        jquants_plan="free",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    return settings


def _days(count: int) -> list[str]:
    start = date(2025, 1, 6)
    return [(start + timedelta(days=i)).isoformat() for i in range(count)]


def _seed(db_path: Path) -> None:
    conn = connect(db_path)
    days = _days(40)
    upsert_master(
        conn,
        [
            {"Date": days[-1], "Code": "11110", "CoName": "上昇工業", "MktNm": "プライム"},
            {"Date": days[-1], "Code": "22220", "CoName": "下降商事", "MktNm": "プライム"},
            {"Date": days[-1], "Code": "13060", "CoName": "TOPIX ETF", "MktNm": "その他"},
        ],
    )
    rows = []
    for index, day in enumerate(days):
        up = 100 + index * 1.5
        down = 200 - index * 1.2
        px = 1000 + index * 0.2
        rows.append({"Date": day, "Code": "11110", "AdjO": up, "AdjH": up + 1, "AdjL": up - 1, "AdjC": up, "AdjVo": 80_000})
        rows.append(
            {"Date": day, "Code": "22220", "AdjO": down, "AdjH": down + 1, "AdjL": down - 1, "AdjC": down, "AdjVo": 80_000}
        )
        rows.append({"Date": day, "Code": "13060", "AdjO": px, "AdjH": px + 1, "AdjL": px - 1, "AdjC": px, "AdjVo": 10_000})
    upsert_daily_bars(conn, rows)
    conn.commit()
    conn.close()


def test_adopt_writes_active_strategy(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path)
    presets = {
        "sma": {"title": "SMA", "selections": [{"id": "sma", "params": {"length": 5}}]},
        "rsi": {"title": "RSI", "selections": [{"id": "rsi", "params": {"length": 5}}]},
    }
    payload = adopt_strategy(
        settings,
        presets=presets,
        market="プライム",
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=5,
        min_history=15,
        horizon="5",
    )
    assert payload["status"] == "ok"
    assert payload["chosen_id"] in {"sma", "rsi"}
    active = load_active_strategy(settings)
    assert active is not None
    assert active["id"] == payload["chosen_id"]
    assert active["selections"]
    picked = active_rank_selections(settings)
    assert picked[0]["id"] == active["selections"][0]["id"]
    candidates = (payload.get("report") or {}).get("candidates") or []
    assert len(candidates) == 2
    assert all("train" in item for item in candidates)


def test_default_selections_without_active(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    picked = active_rank_selections(settings)
    assert [item["id"] for item in picked] == [item["id"] for item in default_rank_selections()]


def test_default_selections_are_ema_and_rsi() -> None:
    picked = default_rank_selections()
    assert [item["id"] for item in picked] == ["ema", "rsi"]
    assert picked[0]["params"]["length"] == 25
    assert picked[1]["params"]["length"] == 14
    strategy_id, title, short = default_strategy_label()
    assert (strategy_id, title, short) == ("ema_rsi", "EMA + RSI", "ema+rsi")


def test_empty_active_selections_do_not_fall_back(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    active_path(settings).write_text(
        __import__("json").dumps(
            {
                "id": "filters",
                "title": "フィルターのみ",
                "selections": [],
                "filters": [{"id": "rsi", "feature": "value", "op": "lt", "value": 70}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    active = load_active_strategy(settings)
    assert active is not None
    assert active["id"] == "filters"
    assert active["selections"] == []
    assert active_rank_selections(settings) == []


def test_adopt_keeps_previous_when_holdout_loses(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path)
    previous = {
        "id": "keep_me",
        "title": "Keep",
        "selections": [{"id": "sma", "params": {"length": 5}}],
        "filters": [],
        "holdout_ok": True,
    }
    active_path(settings).write_text(__import__("json").dumps(previous, ensure_ascii=False), encoding="utf-8")

    def _fake_compare(*_args, **_kwargs):
        period = {"as_of": "2025-01-10", "period_return": 0.02, "bench_return": 0.01}
        hold = {"as_of": "2025-02-10", "period_return": -0.04, "bench_return": 0.01}
        return {
            "items": [
                {
                    "id": "loser",
                    "title": "Loser",
                    "selections": [{"id": "rsi", "params": {"length": 5}}],
                    "filters": [],
                    "horizons": {
                        "5": {
                            "summary": {},
                            "periods": [period, period, period, hold, hold],
                        }
                    },
                }
            ],
            "rules": {},
        }

    monkeypatch.setattr("skabu.strategy.run_compare", _fake_compare)
    payload = adopt_strategy(
        settings,
        presets={"loser": {"title": "Loser", "selections": [{"id": "rsi", "params": {"length": 5}}]}},
        market="プライム",
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=5,
        min_history=15,
        horizon="5",
    )
    assert payload["status"] == "ok"
    assert payload["holdout_ok"] is False
    assert (payload.get("report") or {}).get("adopted") is False
    assert ((payload.get("report") or {}).get("active") or {}).get("id") == "keep_me"
    active = load_active_strategy(settings)
    assert active is not None
    assert active["id"] == "keep_me"
