from pathlib import Path

from fastapi.testclient import TestClient

from skabu.config import Settings
from skabu.store import connect, upsert_daily_bars, upsert_master
from skabu.web.app import create_app


def _seed(db_path: Path) -> None:
    conn = connect(db_path)
    upsert_master(
        conn,
        [
            {
                "Date": "2026-06-23",
                "Code": "86970",
                "CoName": "日本取引所グループ",
                "MktNm": "プライム",
            }
        ],
    )
    upsert_daily_bars(
        conn,
        [
            {
                "Date": "2026-06-22",
                "Code": "86970",
                "AdjO": 100,
                "AdjH": 110,
                "AdjL": 99,
                "AdjC": 105,
                "AdjVo": 10,
            },
            {
                "Date": "2026-06-23",
                "Code": "86970",
                "AdjO": 106,
                "AdjH": 120,
                "AdjL": 90,
                "AdjC": 101,
                "AdjVo": 7,
            },
        ],
    )
    conn.commit()
    conn.close()


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        jquants_api_key="test",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    _seed(settings.db_path)
    return TestClient(create_app(settings))


def test_chart_page_and_apis(tmp_path: Path) -> None:
    client = _client(tmp_path)
    page = client.get("/")
    assert page.status_code == 200
    assert "skabu" in page.text
    assert 'id="ind-bar"' in page.text
    assert 'id="ext-charts"' in page.text
    assert 'aria-label="公開チャート"' in page.text
    assert 'id="ref-levels"' not in page.text
    assert 'id="ref-pop"' in page.text
    assert 'app.js?v=gap2' in page.text
    assert 'id="draw-layer"' in page.text
    assert 'data-tool="box"' in page.text
    assert 'draw.js?v=draw5' in page.text
    assert 'id="draw-toggle"' in page.text
    assert ">描画</button>" in page.text
    assert 'id="draw-tools" role="toolbar" aria-label="描画" hidden' in page.text
    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "意識ポイント" in script.text
    assert "すべて" in script.text
    assert "線を表示" not in script.text
    assert "skabu.refLines" in script.text
    assert "function refItemOn" in script.text
    assert "function formatArrivals" in script.text
    assert "lineWidth: 1" in script.text
    assert "Gaussian" not in page.text

    search = client.get("/api/search", params={"q": "8697"})
    assert search.status_code == 200
    assert search.json()["items"][0]["code"] == "86970"
    assert "X-Request-Id" in search.headers

    daily = client.get("/api/chart/8697", params={"tf": "daily"})
    body = daily.json()
    assert daily.status_code == 200
    assert body["count"] == 2
    assert body["issue"]["code"] == "86970"
    assert body["candles"][0]["open"] == 100
    assert [item["url"] for item in body["links"]] == [
        "https://finance.yahoo.co.jp/quote/8697.T/chart",
        "https://kabutan.jp/stock/chart?code=8697",
        "https://minkabu.jp/stock/8697/chart",
        "https://jp.tradingview.com/chart/?symbol=TSE:8697",
    ]
    refs = {item["id"]: item for item in body["references"]}
    assert refs["prev_high"]["price"] == 110
    assert refs["prev_low"]["price"] == 99
    assert refs["prev_close"]["price"] == 105
    assert refs["round_below"]["price"] == 100
    assert refs["round_above"]["price"] == 200
    assert refs["high_20"]["price"] == 120
    assert refs["low_20"]["price"] == 90
    assert "2026-06-22" in refs["high_20"]["label"]
    assert refs["high_60"]["price"] == 120
    assert refs["low_60"]["price"] == 90
    assert "2026-06-22" in refs["high_60"]["label"]
    assert refs["high_52"]["price"] == 120
    assert refs["low_52"]["price"] == 90
    assert "2026-06-22" in refs["high_52"]["label"]

    weekly = client.get("/api/chart/86970", params={"tf": "weekly"})
    assert weekly.status_code == 200
    assert weekly.json()["count"] == 1

    missing = client.get("/api/chart/99999")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"

    bad_tf = client.get("/api/chart/86970", params={"tf": "minute"})
    assert bad_tf.status_code == 400


def test_chart_drawings_roundtrip(tmp_path: Path) -> None:
    client = _client(tmp_path)
    trend = {
        "id": "line1",
        "kind": "trend",
        "a": {"time": "2026-06-22", "price": 100},
        "b": {"time": "2026-06-23", "price": 110.5},
        "color": "#5eead4",
        "extend": True,
    }
    box = {
        "id": "box1",
        "kind": "box",
        "a": {"time": "2026-06-22", "price": 90},
        "b": {"time": "2026-06-23", "price": 120},
        "color": "#fbbf24",
    }
    saved = client.put("/api/drawings/86970", params={"tf": "daily"}, json={"shapes": [trend, box]})
    assert saved.status_code == 200
    body = saved.json()
    assert body["shapes"][0]["extend"] is True
    assert body["shapes"][0]["b"]["price"] == 110.5
    assert [item["kind"] for item in body["shapes"]] == ["trend", "box"]

    again = client.get("/api/drawings/86970", params={"tf": "daily"})
    assert again.status_code == 200
    assert again.json()["shapes"][1]["id"] == "box1"
    weekly = client.get("/api/drawings/86970", params={"tf": "weekly"})
    assert [item["id"] for item in weekly.json()["shapes"]] == ["line1", "box1"]

    bad = client.put("/api/drawings/86970", params={"tf": "daily"}, json={"shapes": [{"kind": "fib"}]})
    assert bad.status_code == 400
    assert "種類" in bad.json()["message"]
    still = client.get("/api/drawings/86970", params={"tf": "daily"})
    assert [item["id"] for item in still.json()["shapes"]] == ["line1", "box1"]

    path = tmp_path / "data" / "chart_drawings.json"
    stored = path.read_text(encoding="utf-8")
    path.write_text(
        stored.replace(
            '"kind": "box"',
            '"kind": "nope"',
            1,
        ),
        encoding="utf-8",
    )
    dropped = client.get("/api/drawings/86970", params={"tf": "daily"})
    assert [item["kind"] for item in dropped.json()["shapes"]] == ["trend"]

    cleared = client.put("/api/drawings/86970", params={"tf": "daily"}, json={"shapes": []})
    assert cleared.status_code == 200
    assert cleared.json()["shapes"] == []
    assert client.get("/api/drawings/86970", params={"tf": "weekly"}).json()["shapes"] == []

    path.write_text(
        '{"version": 1, "items": {"86970:daily": [{"id": "line1", "kind": "trend", "a": {"time": "2026-06-22", "price": 100}, "b": {"time": "2026-06-23", "price": 110}, "color": "#5eead4", "extend": true}], "86970:weekly": [{"id": "line1", "kind": "trend", "a": {"time": "2026-06-22", "price": 100}, "b": {"time": "2026-06-23", "price": 110}, "color": "#5eead4", "extend": false}, {"id": "box1", "kind": "box", "a": {"time": "2026-06-22", "price": 90}, "b": {"time": "2026-06-23", "price": 120}, "color": "#fbbf24"}]}}',
        encoding="utf-8",
    )
    legacy = client.get("/api/drawings/86970", params={"tf": "daily"})
    assert [item["id"] for item in legacy.json()["shapes"]] == ["line1", "box1"]
    assert legacy.json()["shapes"][0]["extend"] is True


def test_indicators_api(tmp_path: Path) -> None:
    client = _client(tmp_path)
    listed = client.get("/api/indicators")
    assert listed.status_code == 200
    ids = {item["id"] for item in listed.json()["items"]}
    assert "gaussian" not in ids
    assert "ema" in ids
    assert "sma" in ids

    chart = client.get("/api/chart/86970", params={"tf": "daily", "ind": "sma", "sma.length": "2"})
    body = chart.json()
    assert chart.status_code == 200
    assert body["indicators"][0]["id"] == "sma"
    assert body["indicators"][0]["error"] is None
    assert body["indicators"][0]["overlays"][0]["points"]
    assert "levels" in body
    assert "note" in body["levels"]
    assert "arrivals" in body


def test_rank_page_and_api(tmp_path: Path) -> None:
    from skabu.store import upsert_daily_bars

    client = _client(tmp_path)
    settings = client.app.state.settings
    conn = connect(settings.db_path)
    extra = []
    for index in range(3, 22):
        close = 100 + index
        extra.append(
            {
                "Date": f"2026-06-{index:02d}",
                "Code": "86970",
                "AdjO": close,
                "AdjH": close + 1,
                "AdjL": close - 1,
                "AdjC": close,
                "AdjVo": 80_000,
            }
        )
    upsert_daily_bars(conn, extra)
    conn.commit()
    conn.close()

    page = client.get("/rank")
    assert page.status_code == 200
    assert "ランキング" in page.text
    assert "採用で見る" in page.text
    assert 'id="gate-bar"' in page.text

    res = client.get(
        "/api/rank",
        params={"market": "プライム", "min_avg_volume": 1, "min_bars": 10, "top": 5, "ind": "sma", "sma.length": 5},
    )
    body = res.json()
    assert res.status_code == 200
    assert body["ok"] is True
    assert body["rows"][0]["code"] == "86970"
    assert body["rows"][0]["total"] is not None
    assert "stop" in body["rows"][0]
    assert "target" in body["rows"][0]
    assert "buyable" in body["rows"][0]
    assert body.get("levels_note")


def test_rank_ind_keeps_adopted_filters_and_flt_overrides(tmp_path: Path) -> None:
    import json

    from skabu.store import upsert_daily_bars

    client = _client(tmp_path)
    settings = client.app.state.settings
    conn = connect(settings.db_path)
    extra = []
    for index in range(3, 22):
        close = 100 + index
        extra.append(
            {
                "Date": f"2026-06-{index:02d}",
                "Code": "86970",
                "AdjO": close,
                "AdjH": close + 1,
                "AdjL": close - 1,
                "AdjC": close,
                "AdjVo": 80_000,
            }
        )
    upsert_daily_bars(conn, extra)
    conn.commit()
    conn.close()
    (settings.skabu_data_dir / "active_strategy.json").write_text(
        json.dumps(
            {
                "id": "filters",
                "title": "フィルターのみ",
                "selections": [],
                "filters": [
                    {"id": "ichimoku", "feature": "above_cloud", "equals": 1},
                    {"id": "rsi", "feature": "value", "op": "lt", "value": 70},
                ],
                "holdout_ok": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    kept = client.get(
        "/api/rank",
        params={"market": "プライム", "min_avg_volume": 1, "min_bars": 10, "top": 5, "ind": "sma", "sma.length": 5},
    )
    assert kept.status_code == 200
    kept_ids = [item["id"] for item in kept.json()["filters"]]
    assert kept_ids == ["ichimoku", "rsi"]
    assert kept.json()["selections"][0]["id"] == "sma"

    changed = client.get(
        "/api/rank",
        params={
            "market": "プライム",
            "min_avg_volume": 1,
            "min_bars": 10,
            "top": 5,
            "ind": "sma",
            "sma.length": 5,
            "flt": "rsi.value<60,facts.mkt_cap>0",
        },
    )
    assert changed.status_code == 200
    flt_ids = [
        (item["id"], item["feature"], item["value"] if "value" in item else item.get("equals"))
        for item in changed.json()["filters"]
    ]
    assert flt_ids == [("rsi", "value", 60.0), ("facts", "mkt_cap", 0.0)]

    empty = client.get(
        "/api/rank",
        params={"market": "プライム", "min_avg_volume": 1, "min_bars": 10, "top": 5, "ind": "sma", "flt": ""},
    )
    assert empty.json()["filters"] == []


def test_home_page_links_from_brand(tmp_path: Path) -> None:
    client = _client(tmp_path)
    page = client.get("/home")
    assert page.status_code == 200
    text = page.text
    assert "シミュでやり方を試し" in text
    titles = [part.split("<", 1)[0] for part in text.split('class="home-title">')[1:]]
    assert titles == ["シミュ", "戦略", "ランキング", "日次", "売買", "検証", "モデル", "チャート"]
    nav = text.split('<nav class="nav">', 1)[1].split("</nav>", 1)[0]
    assert [nav.index(label) for label in titles] == sorted(nav.index(label) for label in titles)
    assert text.count("home-step") == 8
    assert "売買の前に済ませる必要はありません" in text
    assert 'href="/rank"' in text
    assert 'class="brand" href="/home"' in text
    for path in ("/", "/home", "/rank", "/verify", "/daily", "/trade", "/model", "/strategy", "/sim", "/help"):
        other = client.get(path)
        assert other.status_code == 200
        assert 'class="brand" href="/home"' in other.text
        other_nav = other.text.split('<nav class="nav">', 1)[1].split("</nav>", 1)[0]
        assert [other_nav.index(label) for label in titles] == sorted(other_nav.index(label) for label in titles)
        assert other_nav.index("チャート") < other_nav.index("ヘルプ")
    for script in ("/static/rank.js", "/static/daily.js", "/static/verify.js", "/static/trade.js"):
        body = client.get(script)
        assert body.status_code == 200
        assert "skabu-chart" in body.text
        assert "location.href" not in body.text


def test_help_page(tmp_path: Path) -> None:
    client = _client(tmp_path)
    page = client.get("/help")
    assert page.status_code == 200
    text = page.text
    assert 'aria-current="page">ヘルプ' in text
    for heading in ("help-flow", "help-common", "help-score", "help-sim", "help-strategy", "help-rank", "help-daily", "help-trade", "help-verify", "help-model", "help-chart"):
        assert f'id="{heading}"' in text
    assert text.count('class="help-fig"') == 5
    assert "終値は、その日の最後の値段です。" in text
    assert "生の口座をオンにする操作は、この画面ではしません。" in text


def test_strategy_page(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path)
    page = client.get("/strategy")
    assert page.status_code == 200
    assert "戦略" in page.text
    assert "自動探索" in page.text
    assert "選定で一番でも、確認で有意になるまで採用しない" in page.text
    assert "上位を最大3つまで重ねます" in page.text
    res = client.get("/api/strategy")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["active"] is None
    from skabu.ranking import default_rank_selections

    assert body["selections"][0]["id"] == default_rank_selections()[0]["id"]
    ids = [item["id"] for item in (body.get("experiments") or {}).get("items") or []]
    assert ids == []
    saved = client.post(
        "/api/strategy/save",
        json={
            "title": "画面の実験",
            "selections": [{"id": "rsi", "params": {"length": 14}}],
            "filters": [{"id": "ichimoku", "feature": "above_cloud", "op": "eq", "value": 1}],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["experiment"]["title"] == "画面の実験"
    used = client.post("/api/strategy/use", params={"id": saved.json()["experiment"]["id"]})
    assert used.status_code == 200
    assert used.json()["active"]["id"] == saved.json()["experiment"]["id"]
    blocked = client.post("/api/strategy/save", json={"title": "  ", "selections": [], "filters": []})
    assert blocked.status_code == 400
    script = client.get("/static/strategy.js")
    assert "確認は買う判定に使いません" in script.text
    assert "確認リターンは未計測です。買う判定には使いません" in script.text
    again = client.get("/api/strategy")
    assert again.json()["active"]["id"] == saved.json()["experiment"]["id"]

    def fake_search(_settings, **_kwargs):
        return {
            "ok": True,
            "adopted": False,
            "winner_id": "v1",
            "gate": {"ok": False, "reason": "not_better_than_v1"},
            "candidates": [{"id": "v1", "title": "v1", "confirm": {"avg_return": 0.001, "periods": 4}, "selections": [{"id": "boost"}]}],
            "note": "この確認は v1 採用と重なる。次の版の公式判定は、採用日以降の新しい足が溜まってから。",
            "active": again.json()["active"],
        }

    monkeypatch.setattr("skabu.search.run_search", fake_search)
    searched = client.post("/api/strategy/search")
    assert searched.status_code == 200
    assert searched.json()["search"]["adopted"] is False
    assert client.get("/api/strategy").json()["active"]["id"] == saved.json()["experiment"]["id"]


def test_model_page(tmp_path: Path) -> None:
    client = _client(tmp_path)
    page = client.get("/model")
    assert page.status_code == 200
    assert "学習" in page.text
    assert "目標到達" in page.text
    assert "未比較" in page.text
    assert "当てる対象" in page.text
    res = client.get("/api/model")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["ready"] is False
    assert "learn" in body
    assert body["learn"]["pending_runs"] == 0
    assert body.get("labels") is None


def test_daily_page(tmp_path: Path) -> None:
    client = _client(tmp_path)
    page = client.get("/daily")
    assert page.status_code == 200
    assert "日次" in page.text
    assert "リスク%" in page.text
    res = client.get("/api/daily")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "freshness" in body
    assert "learn" in body
    assert "graded" in body["learn"]
    missing = client.get("/api/daily/csv")
    assert missing.status_code == 404
    refused = client.post("/api/daily", params={"skip_sync": "false"})
    assert refused.status_code == 400


def test_sim_page(tmp_path: Path) -> None:
    from skabu.strategy import active_path

    client = _client(tmp_path)
    page = client.get("/sim")
    assert page.status_code == 200
    assert "シミュ" in page.text
    assert "リスク%" in page.text
    assert "この結果を保存" in page.text
    assert 'id="save-list"' in page.text
    assert "本番" in page.text
    assert "実験" in page.text
    assert "版" in page.text
    assert 'value="boost"' not in page.text
    assert 'value="filters"' not in page.text
    assert 'value="v1"' not in page.text
    assert 'id="gate-bar"' in page.text
    res = client.get("/api/sim", params={"min_avg_volume": 1, "min_bars": 10, "top": 1})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body.get("cash") is True
    assert body.get("trades") == []
    assert body.get("risk_pct") == 0.01
    assert body.get("source") == "active"

    settings = client.app.state.settings
    active_path(settings).write_text(
        '{"id":"v1","title":"v1 雲の上・過熱なし","selections":[{"id":"boost","params":{"high":0.58,"low":0.42}}],"filters":[{"id":"rsi","feature":"value","op":"lt","value":70}],"holdout_ok":true}',
        encoding="utf-8",
    )
    ignored = client.get(
        "/api/sim",
        params={"min_avg_volume": 1, "min_bars": 10, "top": 1, "source": "active", "ind": "sma", "sma.length": 5},
    )
    assert ignored.status_code == 200
    assert ignored.json()["source"] == "active"
    assert ignored.json()["strategy"][0]["id"] == "boost"
    assert ignored.json()["filters"][0]["id"] == "rsi"

    empty = client.get("/api/sim", params={"source": "checks", "min_avg_volume": 1, "min_bars": 10, "top": 1})
    assert empty.status_code == 400

    trial = client.get(
        "/api/sim",
        params={"source": "checks", "ind": "boost", "boost.high": 0.58, "boost.low": 0.42, "min_avg_volume": 1, "min_bars": 10, "top": 1},
    )
    assert trial.status_code == 200
    assert trial.json()["source"] == "checks"
    assert trial.json()["strategy"][0]["id"] == "boost"
    assert trial.json()["filters"] == []

    gated = client.get(
        "/api/sim",
        params={
            "source": "checks",
            "ind": "boost",
            "boost.high": 0.58,
            "boost.low": 0.42,
            "flt": "rsi.value<70",
            "min_avg_volume": 1,
            "min_bars": 10,
            "top": 1,
        },
    )
    assert gated.status_code == 200
    assert gated.json()["filters"][0]["id"] == "rsi"
    assert gated.json()["filters"][0]["value"] == 70.0
    assert isinstance(gated.json()["gate_passed"], int)

    missing = client.get("/api/sim", params={"source": "experiment", "min_avg_volume": 1, "min_bars": 10, "top": 1})
    assert missing.status_code == 400

    saved = client.post(
        "/api/strategy/save",
        json={
            "title": "フィルターのみ",
            "selections": [],
            "filters": [{"id": "ichimoku", "feature": "above_cloud", "op": "eq", "value": 1}],
        },
    )
    assert saved.status_code == 200
    experiment_id = saved.json()["experiment"]["id"]
    exp = client.get(
        "/api/sim",
        params={"source": "experiment", "experiment_id": experiment_id, "min_avg_volume": 1, "min_bars": 10, "top": 1},
    )
    assert exp.status_code == 200
    exp_body = exp.json()
    assert exp_body["source"] == "experiment"
    assert exp_body["experiment_id"] == experiment_id
    assert exp_body["strategy"] == []
    assert exp_body["filters"]
    assert exp_body["filters"][0]["id"] == "ichimoku"


def test_sim_saves_crud(tmp_path: Path) -> None:
    client = _client(tmp_path)
    empty_title = client.post("/api/sim-saves", json={"title": "  ", "result": {"ok": True, "mode": "month", "as_of": "2026-06-22"}})
    assert empty_title.status_code == 400
    no_result = client.post("/api/sim-saves", json={"title": "試し"})
    assert no_result.status_code == 400

    sim = client.get("/api/sim", params={"min_avg_volume": 1, "min_bars": 10, "top": 1})
    assert sim.status_code == 200
    result = sim.json()
    created = client.post(
        "/api/sim-saves",
        json={
            "title": "1か月 本番",
            "result": result,
            "inputs": {
                "source": "active",
                "mode": "month",
                "as_of": result.get("as_of"),
                "cash": 1000000,
                "top": 1,
                "risk_pct": 1,
            },
        },
    )
    assert created.status_code == 200
    item = created.json()["item"]
    assert item["title"] == "1か月 本番"
    assert item["result"]["curve"] is not None
    assert item["inputs"]["top"] == 1
    assert item["summary"]["period"]

    listed = client.get("/api/sim-saves")
    assert listed.status_code == 200
    rows = listed.json()["items"]
    assert len(rows) == 1
    assert "result" not in rows[0]
    assert "curve" not in rows[0]
    assert rows[0]["id"] == item["id"]
    assert rows[0]["summary"]["n"] == result.get("n")

    fetched = client.get(f"/api/sim-saves/{item['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["item"]["result"]["as_of"] == result.get("as_of")
    assert fetched.json()["item"]["result"]["curve"] == result.get("curve")

    missing = client.get("/api/sim-saves/nope")
    assert missing.status_code == 404

    gone = client.delete(f"/api/sim-saves/{item['id']}")
    assert gone.status_code == 200
    assert gone.json()["deleted"] is True
    assert client.get("/api/sim-saves").json()["items"] == []
    assert client.delete(f"/api/sim-saves/{item['id']}").status_code == 404


def test_sim_saves_keep_latest_forty(tmp_path: Path) -> None:
    from skabu.config import Settings
    from skabu.sim_saves import MAX_SAVES, create_save, list_saves

    settings = Settings(
        jquants_api_key="test",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    first_id = None
    last_id = None
    for index in range(MAX_SAVES + 1):
        item = create_save(
            settings,
            f"run {index}",
            {"ok": True, "mode": "month", "as_of": "2026-06-22", "return": 0.01, "curve": [], "trades": []},
        )
        if index == 0:
            first_id = item["id"]
        last_id = item["id"]
    rows = list_saves(settings)
    assert len(rows) == MAX_SAVES
    assert rows[0]["id"] == last_id
    assert first_id not in {row["id"] for row in rows}


def test_verify_page(tmp_path: Path) -> None:
    client = _client(tmp_path)
    page = client.get("/verify")
    assert page.status_code == 200
    assert "検証" in page.text
    assert "3作戦を比較" not in page.text
    assert 'id="compare-core"' not in page.text
    assert "組み合わせ比較" not in page.text
    assert 'id="compare"' not in page.text
    assert "予測の間隔" in page.text
    assert "売る期限" in page.text
    assert ">検証</button>" in page.text
    assert "再検証" not in page.text
    assert "検証を押すと始まります" in page.text
    assert 'id="experiment"' in page.text
    assert "この結果を保存" in page.text
    assert "損切の倍率" in page.text
    assert "目標の倍率" in page.text
    assert "verify.js?v=charttab1" in page.text
    rank = client.get("/rank")
    assert rank.status_code == 200
    assert "損切の倍率" in rank.text
    assert "rank.js?v=charttab1" in rank.text
    strategy = client.get("/strategy")
    assert strategy.status_code == 200
    assert "検証で比較" not in strategy.text
    model = client.get("/model")
    assert model.status_code == 200
    assert "検証で比較" not in model.text
    res = client.get("/api/verify", params={"top": 5, "stride": 20, "min_avg_volume": 1, "min_bars": 10})
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_exit_levels_api(tmp_path: Path) -> None:
    client = _client(tmp_path)
    got = client.get("/api/exit-levels")
    assert got.status_code == 200
    body = got.json()
    assert body["ok"] is True
    assert body["stop_mult"] == 1.5
    assert body["target_mult"] == 3.0
    assert body["saved"] is False
    bad = client.put("/api/exit-levels", json={"stop_mult": 20, "target_mult": 3})
    assert bad.status_code == 400
    assert "0.1" in bad.json()["message"]
    saved = client.put("/api/exit-levels", json={"stop_mult": 2, "target_mult": 5})
    assert saved.status_code == 200
    assert saved.json()["stop_mult"] == 2
    assert saved.json()["target_mult"] == 5
    assert saved.json()["saved"] is True
    again = client.get("/api/exit-levels")
    assert again.json()["stop_mult"] == 2
    assert again.json()["target_mult"] == 5


def test_verify_uses_saved_strategy_gates(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path)
    saved = client.post(
        "/api/strategy/save",
        json={
            "title": "検証用",
            "selections": [{"id": "rsi", "params": {"length": 14}}],
            "filters": [{"id": "rsi", "feature": "value", "op": "lt", "value": 70}],
        },
    )
    assert saved.status_code == 200
    exp_id = saved.json()["experiment"]["id"]
    seen: dict = {}

    def fake_verify(_conn, **kwargs):
        seen["selections"] = kwargs.get("selections")
        seen["filters"] = kwargs.get("filters")
        return {
            "locked_runs": 1,
            "cached": False,
            "selections": kwargs.get("selections"),
            "filters": kwargs.get("filters") or [],
            "horizons": {"20": {"summary": {"avg_return": 0.02, "avg_excess": 0.01, "win_rate": 0.5, "max_drawdown": -0.03}}},
            "as_of_dates": ["2026-06-01", "2026-06-22"],
            "rules": {},
        }

    monkeypatch.setattr("skabu.web.app.run_verify", fake_verify)
    res = client.get("/api/verify", params={"experiment": exp_id, "top": 5, "stride": 20})
    assert res.status_code == 200
    body = res.json()
    assert body["experiment"]["id"] == exp_id
    assert body["experiment"]["filter_count"] == 1
    assert seen["filters"][0]["feature"] == "value"
    assert seen["selections"][0]["id"] == "rsi"

    missing = client.get("/api/verify", params={"experiment": "missing-id"})
    assert missing.status_code == 404

    stored = client.post(
        "/api/verify/save",
        json={
            "experiment_id": exp_id,
            "top": 5,
            "stride": 20,
            "as_of_from": "2026-06-01",
            "as_of_to": "2026-06-22",
            "summary": body["horizons"]["20"]["summary"],
        },
    )
    assert stored.status_code == 200
    holdout = stored.json()["experiment"]["verify"]["holdout"]
    assert holdout["avg_return"] == 0.02
    assert holdout["avg_excess"] == 0.01
    screen = stored.json()["experiment"]["verify"]["screen"]
    assert screen["source"] == "verify-page"
    assert screen["as_of_from"] == "2026-06-01"


def test_verify_core_compare_is_gone(tmp_path: Path) -> None:
    client = _client(tmp_path)
    res = client.get("/api/verify", params={"compare": "core", "top": 5, "stride": 20})
    assert res.status_code == 410
    assert res.json()["error"] == "gone"


def test_ui_prefs_api(tmp_path: Path) -> None:
    client = _client(tmp_path)
    empty = client.get("/api/ui-prefs")
    assert empty.status_code == 200
    body = empty.json()
    assert body["ok"] is True
    assert body["saved"] is False
    assert "boost" not in body["defaults"]
    assert body["defaults"]["ema"]["params"]["length"] == 20

    saved = client.put("/api/ui-prefs", json={"items": {"ema": {"enabled": True, "params": {"length": 30}}}})
    assert saved.status_code == 200
    assert saved.json()["saved"] is True
    assert saved.json()["items"]["ema"]["params"]["length"] == 30
    gates = client.put("/api/ui-prefs", json={"gates": [{"id": "rsi", "feature": "value", "op": "lt", "value": 65}]})
    assert gates.status_code == 200
    assert gates.json()["items"]["ema"]["params"]["length"] == 30
    assert gates.json()["gates"][0]["value"] == 65.0

    again = client.get("/api/ui-prefs")
    assert again.json()["items"]["ema"]["params"]["length"] == 30

    cleared = client.delete("/api/ui-prefs")
    assert cleared.status_code == 200
    assert cleared.json()["saved"] is False
    assert client.get("/api/ui-prefs").json()["items"] is None

    bad = client.put("/api/ui-prefs", json={"items": "nope"})
    assert bad.status_code == 400


def test_trade_page_and_api(tmp_path: Path) -> None:
    from skabu.broker.prefs import MODE_AUTO
    from skabu.store import finish_daily_run, start_daily_run

    client = _client(tmp_path)
    page = client.get("/trade")
    assert page.status_code == 200
    assert "売買" in page.text
    assert "緊急停止" in page.text
    assert "引けで売る" in page.text
    assert "保有日数" in page.text

    empty = client.get("/api/trade")
    assert empty.status_code == 200
    body = empty.json()
    assert body["ok"] is True
    assert body["live"] is False
    assert body["prefs"]["mode"] == "approve"
    assert body["prefs"]["halted"] is False
    assert body["prefs"]["hold_days"] == 20
    assert body["today"]
    assert "KABU_LIVE" not in page.text

    switched = client.put("/api/trade/mode", json={"mode": MODE_AUTO})
    assert switched.status_code == 200
    assert switched.json()["prefs"]["mode"] == "auto"
    back = client.put("/api/trade/mode", json={"mode": "approve"})
    assert back.json()["prefs"]["mode"] == "approve"

    settings = client.app.state.settings
    conn = connect(settings.db_path)
    run_id = start_daily_run(conn, "free")
    finish_daily_run(
        conn,
        run_id,
        status="ok",
        as_of="2026-09-23",
        locked=1,
        report={
            "as_of": "2026-09-23",
            "rows": [
                {
                    "code": "86970",
                    "co_name": "日本取引所グループ",
                    "qty": 10,
                    "alloc": 10000,
                    "atr": 2.0,
                    "close": 101,
                    "buyable": True,
                }
            ],
        },
    )
    conn.close()

    from skabu.broker.execute import morning_open_passed as real_open

    def before_open(now=None):
        return False

    import skabu.broker.execute as execute

    execute.morning_open_passed = before_open
    try:
        queued = client.post("/api/trade/buy")
    finally:
        execute.morning_open_passed = real_open
    assert queued.status_code == 200
    tickets = queued.json()["book"]["tickets"]
    assert tickets[0]["status"] == "pending"
    assert queued.json()["snapshot"]["paper"] is True

    approved = client.post("/api/trade/approve", json={"codes": ["86970"]})
    assert approved.status_code == 200
    filled = approved.json()["book"]["tickets"][0]
    assert filled["status"] == "filled"
    assert not filled.get("target_order_id")
    assert filled["stop_order_id"]

    halted = client.post("/api/trade/halt")
    assert halted.status_code == 200
    assert halted.json()["prefs"]["halted"] is True
    resumed = client.post("/api/trade/resume")
    assert resumed.json()["prefs"]["halted"] is False

    refused = client.put("/api/trade/mode", json={"mode": "live"})
    assert refused.status_code == 400


def test_manual_sync_button_and_api(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path)
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="manual-sync"' in page.text
    assert "株価を同期" in page.text
    assert "syncbtn.js?v=manualsync1" in page.text
    for path in ("/rank", "/daily", "/sim"):
        other = client.get(path)
        assert other.status_code == 200
        assert 'id="manual-sync"' in other.text
    seen: dict[str, str] = {}

    def fake_run(**kwargs):
        seen["reason"] = kwargs.get("reason") or ""
        return {
            "action": "synced",
            "reason": "manual",
            "pending_bars": 2,
            "last_bars_to": "2026-10-09",
            "bars": {"synced_days": 2, "empty_days": 0, "error_days": 0},
        }

    monkeypatch.setattr(client.app.state.auto_sync, "run", fake_run)
    res = client.post("/api/sync")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["action"] == "synced"
    assert body["result"]["synced_days"] == 2
    assert body["result"]["last_bars_to"] == "2026-10-09"
    assert seen["reason"] == "manual"
    assert "auto_sync" in body

    def fake_fail(**kwargs):
        return {"action": "failed", "reason": "manual", "error": "APIキーがありません", "bars": {}}

    monkeypatch.setattr(client.app.state.auto_sync, "run", fake_fail)
    failed = client.post("/api/sync")
    assert failed.status_code == 502
    assert failed.json()["ok"] is False
    assert "APIキー" in failed.json()["message"]


def test_status_has_counts(tmp_path: Path) -> None:
    client = _client(tmp_path)
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["counts"]["daily_bars"] == 2
    assert data["counts"]["equity_facts"] == 0
    assert data["default_code"] == "86970"
    assert any(item["feature"] == "per" for item in data["gates"])
    assert all(item["id"] != "boost" for item in data["indicators"])
    assert all(item["id"] != "relative" for item in data["indicators"])
    assert all(item.get("help") for item in data["gates"])
    assert all(item.get("group") for item in data["gates"])
    groups = {item["id"]: item for item in data["groups"]}
    assert {"trend", "power", "shape", "wave", "market", "facts", "model"} <= set(groups)
    assert all("例" in item["help"] or "なぜ" in item["help"] for item in groups.values())
    auto = data["auto_sync"]
    assert auto["enabled"] is True
    assert auto["running"] is False
    assert "last_at" in auto
    assert "last_bars_to" in auto
    assert "last_error" in auto
    assert "last_action" in auto
