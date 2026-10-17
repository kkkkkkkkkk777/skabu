import json
from pathlib import Path

from skabu.logging_setup import dump_debug_event, reset_logging_for_tests, setup_logging


def test_dump_redacts_api_key(tmp_path: Path) -> None:
    path = dump_debug_event(
        tmp_path,
        {
            "kind": "auth",
            "headers": {"x-api-key": "secret-key", "Accept": "application/json"},
            "api_key": "also-secret",
        },
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["headers"]["x-api-key"] == "***REDACTED***"
    assert payload["api_key"] == "***REDACTED***"
    assert payload["headers"]["Accept"] == "application/json"


def test_setup_logging_writes_file(tmp_path: Path) -> None:
    reset_logging_for_tests()
    logger = setup_logging(tmp_path, "DEBUG")
    logger.info("hello-phase0")
    for handler in logger.handlers:
        if hasattr(handler, "flush"):
            handler.flush()
    text = (tmp_path / "skabu.log").read_text(encoding="utf-8")
    assert "hello-phase0" in text
