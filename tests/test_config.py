from datetime import date
from pathlib import Path

import pytest

from skabu.config import Settings, load_settings, plan_data_window, plan_safe_sample_window
from skabu.errors import ConfigError


def test_plan_rate_limits() -> None:
    assert Settings(jquants_plan="free").requests_per_minute == 5
    assert Settings(jquants_plan="Light").requests_per_minute == 60
    assert Settings(jquants_plan="STANDARD").requests_per_minute == 120
    assert Settings(jquants_plan="premium").requests_per_minute == 500


def test_require_api_key_raises() -> None:
    settings = Settings(jquants_api_key="  ")
    with pytest.raises(ConfigError, match="JQUANTS_API_KEY"):
        settings.require_api_key()


def test_ensure_dirs(tmp_path: Path) -> None:
    settings = Settings(
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    assert settings.skabu_data_dir.is_dir()
    assert settings.debug_dir.is_dir()


def test_free_plan_sample_window_respects_delay() -> None:
    start, end = plan_safe_sample_window("free", today=date(2026, 9, 22))
    assert start == "2026-06-17"
    assert end == "2026-06-23"


def test_free_plan_data_window() -> None:
    start, end = plan_data_window("free", today=date(2026, 9, 22))
    assert end == "2026-06-30"
    assert start == "2024-06-30"


def test_load_settings_creates_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.skabu_data_dir.is_dir()
    assert settings.skabu_log_dir.is_dir()
