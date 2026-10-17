from datetime import date, timedelta
from pathlib import Path

from skabu.config import Settings
from skabu.indicators.plugins.boost import BoostPlugin
from skabu.levels import bars_from_prices, compute_levels, path_from_prices, simulate_trade
import pytest

from skabu.errors import SkabuError
from skabu.model import (
    ARRIVAL_REASONS,
    FEATURE_NAMES,
    FEATURE_SELECTIONS,
    active_feature_names,
    active_feature_selections,
    arrival_class,
    collect_samples,
    extract_features,
    predict_arrivals,
    predict_proba,
    sample_y,
    train_model,
)
from skabu.store import connect, upsert_daily_bars, upsert_master


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


def _seed(db_path: Path, days: int = 100) -> list[str]:
    conn = connect(db_path)
    dates = _days(days)
    master = [{"Date": dates[-1], "Code": "13060", "CoName": "TOPIX ETF", "MktNm": "その他"}]
    bars = []
    for index in range(12):
        code = f"{1110 + index}0"
        name = f"銘柄{index}"
        master.append({"Date": dates[-1], "Code": code, "CoName": name, "MktNm": "プライム"})
        step = 1.4 if index % 2 == 0 else -1.1
        start = 100 + index
        for day_i, day in enumerate(dates):
            px = start + day_i * step
            bars.append(
                {
                    "Date": day,
                    "Code": code,
                    "AdjO": px,
                    "AdjH": px + 1,
                    "AdjL": px - 1,
                    "AdjC": px,
                    "AdjVo": 80_000,
                }
            )
    for day_i, day in enumerate(dates):
        px = 1000 + day_i * 0.15
        bars.append(
            {
                "Date": day,
                "Code": "13060",
                "AdjO": px,
                "AdjH": px + 1,
                "AdjL": px - 1,
                "AdjC": px,
                "AdjVo": 10_000,
            }
        )
    upsert_master(conn, master)
    upsert_daily_bars(conn, bars)
    conn.commit()
    conn.close()
    return dates


def test_extract_features_from_history(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path)
    from skabu.store import get_chart_bars

    with connect(settings.db_path) as conn:
        bars = get_chart_bars(conn, "11100", "daily")
    feats = extract_features(bars)
    assert feats is not None
    assert "rsi.value" in feats
    assert "macd.score" in feats
    assert "supertrend.uptrend" in feats
    assert "ichimoku.above_cloud" in feats
    assert "bbands.price_above" in feats
    assert "supertrend.uptrend" in FEATURE_NAMES


def test_train_and_predict(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path)
    payload = train_model(
        settings,
        market="プライム",
        min_avg_volume=1,
        min_bars=20,
        stride=8,
        min_history=20,
        horizon=5,
        min_samples=12,
    )
    assert payload["status"] == "ok"
    assert payload["n_train"] >= 12
    assert (settings.skabu_data_dir / "models" / "boost.joblib").exists()
    from skabu.store import get_chart_bars

    with connect(settings.db_path) as conn:
        bars = get_chart_bars(conn, "11100", "daily")
    proba = predict_proba(bars, settings)
    assert proba is not None
    assert 0.0 <= proba <= 1.0
    arrivals = predict_arrivals(bars, settings)
    assert arrivals is not None
    assert abs(arrivals["target"] - proba) < 1e-9
    result = BoostPlugin().compute(bars, {})
    # プラグインは既定 data を見るので、ここでは直接 predict 済みであることだけ見る
    assert result.error is None
    meta = payload.get("meta") or {}
    assert meta.get("label") == "target_first"


def test_collect_samples_y_is_target_hit(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path, days=100)
    with connect(settings.db_path) as conn:
        samples = collect_samples(
            conn,
            market="プライム",
            min_avg_volume=1,
            min_bars=20,
            stride=20,
            min_history=20,
            horizon=5,
            bench_code="13060",
        )
        assert samples
        from skabu.store import all_bar_dates, load_price_map

        dates = all_bar_dates(conn)
        index = {day: i for i, day in enumerate(dates)}
        codes = {item["code"] for item in samples}
        prices = load_price_map(conn, codes, dates[0], dates[-1])
        checked = 0
        for item in samples[:12]:
            as_of = item["as_of"]
            pred_i = index[as_of]
            entry_i = pred_i + 1
            exit_i = entry_i + 5
            entry_date = dates[entry_i]
            exit_date = dates[exit_i]
            series = prices.get(item["code"], {})
            entry = (series.get(entry_date) or {}).get("open")
            atr_value = compute_levels(bars_from_prices(series, dates, as_of)).get("atr")
            trade = simulate_trade(entry, atr_value, path_from_prices(series, dates, entry_date, exit_date), horizon=5)
            assert item["y"] == arrival_class(trade["reason"])
            assert item["reason"] == trade["reason"]
            checked += 1
        assert checked >= 1


def test_predict_arrivals_three_classes(tmp_path: Path) -> None:
    import json

    import joblib
    from sklearn.ensemble import HistGradientBoostingClassifier

    from skabu.model import _clear_cache
    from skabu.store import get_chart_bars

    settings = _settings(tmp_path)
    _seed(settings.db_path)
    with connect(settings.db_path) as conn:
        bars = get_chart_bars(conn, "11100", "daily")
    feats = extract_features(bars)
    assert feats is not None
    names = active_feature_names()
    base = [feats[name] for name in names]
    rows = []
    labels = []
    for label in (0, 1, 2):
        for step in range(8):
            rows.append([value + label * 0.2 + step * 0.01 for value in base])
            labels.append(label)
    clf = HistGradientBoostingClassifier(max_depth=3, max_iter=30, random_state=1)
    clf.fit(rows, labels)
    models = settings.skabu_data_dir / "models"
    models.mkdir(parents=True, exist_ok=True)
    joblib.dump(clf, models / "boost.joblib")
    (models / "boost.meta.json").write_text(
        json.dumps({"feature_names": names}),
        encoding="utf-8",
    )
    _clear_cache()
    arrivals = predict_arrivals(bars, settings)
    assert arrivals is not None
    assert arrivals["split"] is True
    assert abs((arrivals["target"] + arrivals["stop"] + arrivals["time"]) - 1.0) < 1e-6
    assert abs(predict_proba(bars, settings) - arrivals["target"]) < 1e-9


def test_sample_y_three_labels() -> None:
    assert sample_y(label="target_first", y_hit=1, ret=-0.01, excess=-0.02, reason="target") == ARRIVAL_REASONS["target"]
    assert sample_y(label="target_first", y_hit=0, ret=-0.01, excess=-0.02, reason="stop") == ARRIVAL_REASONS["stop"]
    assert sample_y(label="target_first", y_hit=0, ret=0.01, excess=0.01, reason="time") == ARRIVAL_REASONS["time"]
    assert sample_y(label="target_first", y_hit=1, ret=-0.01, excess=-0.02) == 1
    assert sample_y(label="ret_pos", y_hit=1, ret=-0.01, excess=-0.02) == 0
    assert sample_y(label="excess_pos", y_hit=0, ret=0.01, excess=0.02) == 1
    assert sample_y(label="excess_pos", y_hit=0, ret=0.01, excess=-0.02) == 0


def test_collect_samples_three_labels(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path, days=100)
    with connect(settings.db_path) as conn:
        kwargs = dict(
            market="プライム",
            min_avg_volume=1,
            min_bars=20,
            stride=20,
            min_history=20,
            horizon=5,
            bench_code="13060",
        )
        hit = collect_samples(conn, label="target_first", **kwargs)
        ret = collect_samples(conn, label="ret_pos", **kwargs)
        excess = collect_samples(conn, label="excess_pos", **kwargs)
    assert hit and ret
    by_key = {(item["as_of"], item["code"]): item for item in hit}
    for item in ret:
        base = by_key.get((item["as_of"], item["code"]))
        assert base is not None
        assert item["y"] == int(float(item["ret"]) > 0)
        assert item["y"] == sample_y(label="ret_pos", y_hit=base["y_hit"], ret=item["ret"], excess=item["excess"])
    for item in excess:
        assert item["y"] == int(float(item["excess"]) > 0)


def test_train_named_does_not_clobber_production(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path)
    models = settings.skabu_data_dir / "models"
    models.mkdir(parents=True, exist_ok=True)
    prod = models / "boost.joblib"
    prod.write_bytes(b"keep-production")
    (models / "boost.meta.json").write_text('{"label":"target_first","metrics":{"valid_auc":0.5}}', encoding="utf-8")
    payload = train_model(
        settings,
        market="プライム",
        min_avg_volume=1,
        min_bars=20,
        stride=8,
        min_history=20,
        horizon=5,
        min_samples=12,
        name="boost.ret_pos",
        label="ret_pos",
    )
    assert payload["status"] == "candidate"
    assert (payload.get("meta") or {}).get("label") == "ret_pos"
    assert prod.read_bytes() == b"keep-production"
    assert (models / "boost.ret_pos.joblib").exists()
    with pytest.raises(SkabuError, match="上書きしません"):
        train_model(
            settings,
            market="プライム",
            min_avg_volume=1,
            min_bars=20,
            stride=8,
            min_history=20,
            horizon=5,
            min_samples=12,
            name="boost",
            label="ret_pos",
        )
    assert prod.read_bytes() == b"keep-production"


def test_active_features_exclude_gaussian() -> None:
    names = active_feature_names()
    selections = active_feature_selections()
    assert names == list(FEATURE_NAMES)
    assert [item["id"] for item in selections] == [item["id"] for item in FEATURE_SELECTIONS]
    assert all(not name.startswith("gaussian.") for name in names)
    assert all(item["id"] != "gaussian" for item in selections)
