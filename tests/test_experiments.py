import json
from pathlib import Path

import pytest

from skabu.buys import holdout_return_ok
from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.experiments import (
    activate,
    get_experiment,
    list_experiments,
    record_result,
    save_named_strategy,
)
from skabu.strategy import load_active_strategy


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        jquants_api_key="test",
        jquants_plan="free",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    return settings


def test_seed_list_starts_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    items = list_experiments(settings)
    assert items == []
    assert (settings.skabu_data_dir / "experiments.json").exists()


def test_activate_does_not_drop_other_versions(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = save_named_strategy(
        settings,
        "雲の上",
        [{"id": "ema", "params": {"length": 25}}],
        [{"id": "ichimoku", "feature": "above_cloud", "equals": 1}],
    )
    second = save_named_strategy(settings, "RSIだけ", [{"id": "rsi", "params": {"length": 14}}], [])
    activate(settings, first["id"])
    active = load_active_strategy(settings)
    assert active is not None
    assert active["id"] == first["id"]
    assert active["filters"][0]["feature"] == "above_cloud"
    ids = [item["id"] for item in list_experiments(settings)]
    assert ids == [first["id"], second["id"]]
    activate(settings, second["id"])
    assert load_active_strategy(settings)["id"] == second["id"]
    assert get_experiment(settings, first["id"])["filters"]


def test_save_named_strategy_and_replace_clears_verify(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    saved = save_named_strategy(
        settings,
        "手元の門",
        [{"id": "ema", "params": {"length": 25}}, {"id": "rsi", "params": {"length": 14}}],
        [
            {"id": "ichimoku", "feature": "above_cloud", "op": "eq", "value": 1},
            {"id": "rsi", "feature": "value", "op": "lt", "value": 70},
            {"id": "not_a_gate", "feature": "nope", "op": "eq", "value": 1},
        ],
    )
    assert saved["title"] == "手元の門"
    assert saved["id"].startswith("saved_")
    assert [item["id"] for item in saved["selections"]] == ["ema", "rsi"]
    assert saved["selections"][0]["params"]["length"] == 25
    assert [item["feature"] for item in saved["filters"]] == ["above_cloud", "value"]
    assert saved["verify"] is None
    record_result(settings, saved["id"], verify={"holdout": {"avg_return": 0.02}}, sim={"n": 1})
    again = save_named_strategy(
        settings,
        "手元の門",
        [],
        [{"id": "regime", "feature": "above_ma200", "op": "eq", "value": 1}],
    )
    assert again["id"] == saved["id"]
    assert again["selections"] == []
    assert again["filters"][0]["feature"] == "above_ma200"
    assert again["verify"] is None
    assert again["sim"] is None
    ids = [item["id"] for item in list_experiments(settings)]
    assert ids == [saved["id"]]


def test_save_named_strategy_rejects_blank_name(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(SkabuError):
        save_named_strategy(settings, "  ", [], [])
    assert list_experiments(settings) == []


def test_activate_without_verify_does_not_mark_holdout_ok(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    saved = save_named_strategy(
        settings,
        "未確認",
        [{"id": "rsi", "params": {"length": 14}}],
        [{"id": "ichimoku", "feature": "above_cloud", "equals": 1}],
    )
    active = activate(settings, saved["id"])
    assert active["holdout_ok"] is not True
    assert holdout_return_ok(active) is False
    assert get_experiment(settings, saved["id"])["filters"]


def test_record_result_keeps_sibling(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = save_named_strategy(settings, "一つ目", [{"id": "rsi", "params": {}}], [])
    second = save_named_strategy(settings, "二つ目", [{"id": "ema", "params": {}}], [])
    record_result(settings, first["id"], verify={"holdout": {"avg_return": 0.01}}, sim={"return": -0.02, "n": 3})
    stored = get_experiment(settings, first["id"])
    assert stored["verify"]["holdout"]["avg_return"] == 0.01
    assert stored["sim"]["n"] == 3
    assert get_experiment(settings, second["id"])["verify"] is None
