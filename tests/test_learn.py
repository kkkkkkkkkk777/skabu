from datetime import date, timedelta
from pathlib import Path

from skabu.config import Settings
from skabu.learn import decide_promotion, grade_outcomes, maybe_learn, should_retrain
from skabu.store import (
    connect,
    count_outcomes,
    delete_outcomes,
    insert_outcome,
    list_outcome_as_ofs,
    upsert_daily_bars,
    upsert_master,
)
from skabu.verify import lock_as_of


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


def _seed(db_path: Path, days: int = 80) -> list[str]:
    conn = connect(db_path)
    dates = _days(days)
    master = [{"Date": dates[-1], "Code": "13060", "CoName": "TOPIX ETF", "MktNm": "その他"}]
    bars = []
    for index in range(8):
        code = f"{1110 + index}0"
        master.append({"Date": dates[-1], "Code": code, "CoName": f"銘柄{index}", "MktNm": "プライム"})
        step = 1.4 if index % 2 == 0 else -1.1
        start = 100 + index
        for day_i, day in enumerate(dates):
            px = start + day_i * step
            bars.append(
                {
                    "Date": day,
                    "Code": code,
                    "AdjO": px,
                    "AdjH": px + 2,
                    "AdjL": px - 2,
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


def test_decide_promotion_rejects_worse_or_negative() -> None:
    worse = decide_promotion(
        production={"avg_excess": 0.02, "auc": 0.54},
        candidate={"avg_excess": 0.01, "auc": 0.55},
    )
    assert worse["promote"] is False
    assert worse["reason"] == "candidate_not_better_excess"
    negative = decide_promotion(
        production={"avg_excess": -0.03, "auc": 0.52},
        candidate={"avg_excess": -0.01, "auc": 0.56},
    )
    assert negative["promote"] is False
    assert negative["reason"] == "candidate_holdout_not_positive"
    auc = decide_promotion(
        production={"avg_excess": 0.01, "auc": 0.56},
        candidate={"avg_excess": 0.02, "auc": 0.50},
    )
    assert auc["promote"] is False
    assert auc["reason"] == "candidate_auc_worse"
    better = decide_promotion(
        production={"avg_excess": 0.01, "auc": 0.54},
        candidate={"avg_excess": 0.03, "auc": 0.54},
    )
    assert better["promote"] is True


def test_grade_is_idempotent_and_skips_future(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dates = _seed(settings.db_path, days=80)
    with connect(settings.db_path) as conn:
        lock_as_of(
            conn,
            as_of=dates[50],
            selections=[{"id": "sma", "params": {"length": 5}}],
            market="プライム",
            min_avg_volume=1,
            min_bars=20,
            top=4,
        )
        lock_as_of(
            conn,
            as_of=dates[-1],
            selections=[{"id": "sma", "params": {"length": 5}}],
            market="プライム",
            min_avg_volume=1,
            min_bars=20,
            top=4,
        )
    first = grade_outcomes(settings, horizon=5)
    assert first["status"] == "ok"
    assert first["graded"] >= 1
    assert dates[-1] in first["pending_as_ofs"]
    assert dates[50] in first["as_ofs"]
    with connect(settings.db_path) as conn:
        total = count_outcomes(conn)
    second = grade_outcomes(settings, horizon=5)
    assert second["graded"] == 0
    with connect(settings.db_path) as conn:
        assert count_outcomes(conn) == total
    rebuilt = grade_outcomes(settings, horizon=5, rebuild=True)
    assert rebuilt["graded"] == total
    with connect(settings.db_path) as conn:
        assert count_outcomes(conn) == total
        assert delete_outcomes(conn) == total


def test_should_retrain_needs_three_new_days(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path, days=40)
    with connect(settings.db_path) as conn:
        for day in ("2025-02-01", "2025-02-02"):
            insert_outcome(
                conn,
                {
                    "strategy_key": "t",
                    "as_of": day,
                    "code": "11100",
                    "y_time": 1,
                    "y_touch": 1,
                    "excess": 0.01,
                },
            )
        held = should_retrain(
            conn,
            last_learn=None,
            dates=["2025-01-06", "2025-02-03"],
            graded_as_ofs=list_outcome_as_ofs(conn),
        )
        assert held["ready"] is False
        insert_outcome(
            conn,
            {
                "strategy_key": "t",
                "as_of": "2025-02-03",
                "code": "11100",
                "y_time": 0,
                "y_touch": 0,
                "excess": -0.01,
            },
        )
        ready = should_retrain(
            conn,
            last_learn=None,
            dates=["2025-01-06", "2025-02-03"],
            graded_as_ofs=list_outcome_as_ofs(conn),
        )
        assert ready["ready"] is True
        assert ready["reason"] == "new_as_ofs"


def test_maybe_learn_holds_without_enough_outcomes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings.db_path, days=40)
    payload = maybe_learn(
        settings,
        market="プライム",
        min_avg_volume=1,
        min_bars=20,
        stride=8,
        min_history=20,
        horizon=5,
        min_samples=12,
        top=4,
    )
    assert payload["status"] == "held"
    assert payload["reason"] == "held"
    assert not (settings.skabu_data_dir / "models" / "boost.candidate.joblib").exists()
