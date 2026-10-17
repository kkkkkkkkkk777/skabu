from skabu.config import Settings
from skabu.ui_prefs import catalog_defaults, clear_prefs, load_prefs, save_prefs


def _settings(tmp_path):
    settings = Settings(
        jquants_api_key="test",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    return settings


def test_catalog_defaults_omit_boost() -> None:
    defaults = catalog_defaults()
    assert "boost" not in defaults
    assert defaults["ema"]["enabled"] is False
    assert defaults["ema"]["params"]["length"] == 20


def test_save_load_and_clear(tmp_path) -> None:
    settings = _settings(tmp_path)
    empty = load_prefs(settings)
    assert empty["saved"] is False
    assert empty["items"] is None
    assert "boost" not in empty["defaults"]
    assert empty["defaults"]["ema"]["params"]["length"] == 20

    saved = save_prefs(
        settings,
        {"ema": {"enabled": True, "params": {"length": 30, "unknown": 1}}},
    )
    assert saved["saved"] is True
    assert saved["items"]["ema"]["enabled"] is True
    assert saved["items"]["ema"]["params"]["length"] == 30
    assert "unknown" not in saved["items"]["ema"]["params"]
    assert (settings.skabu_data_dir / "ui_prefs.json").exists()

    again = load_prefs(settings)
    assert again["items"]["ema"]["params"]["length"] == 30

    cleared = clear_prefs(settings)
    assert cleared["saved"] is False
    assert cleared["items"] is None
    assert cleared["gates"] is None
    assert not (settings.skabu_data_dir / "ui_prefs.json").exists()


def test_save_gates_without_wiping_items(tmp_path) -> None:
    settings = _settings(tmp_path)
    save_prefs(settings, {"ema": {"enabled": True, "params": {"length": 30}}})
    saved = save_prefs(settings, gates=[{"id": "rsi", "feature": "value", "op": "lt", "value": 65}])
    assert saved["items"]["ema"]["params"]["length"] == 30
    assert saved["gates"][0]["feature"] == "value"
    assert saved["gates"][0]["value"] == 65.0
    again = load_prefs(settings)
    assert again["items"]["ema"]["enabled"] is True
    assert again["gates"][0]["id"] == "rsi"


def test_save_partial_items_keeps_other_plugins(tmp_path) -> None:
    settings = _settings(tmp_path)
    save_prefs(settings, {"ema": {"enabled": True, "params": {"length": 30}}})
    saved = save_prefs(settings, {"sma": {"enabled": True, "params": {"length": 30}}})
    assert saved["items"]["ema"]["enabled"] is True
    assert saved["items"]["ema"]["params"]["length"] == 30
    assert saved["items"]["sma"]["enabled"] is True
    assert saved["items"]["sma"]["params"]["length"] == 30
