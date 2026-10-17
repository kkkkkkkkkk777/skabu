"""ファイルとコンソールへ出すログ。失敗調査用のダンプもここ。"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CONFIGURED = False
_LOGGER_NAME = "skabu"


def setup_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    """同じプロセスで何度呼んでもハンドラを増やさない。"""
    global _CONFIGURED
    logger = logging.getLogger(_LOGGER_NAME)
    numeric = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(logging.DEBUG)

    if _CONFIGURED:
        logger.setLevel(min(logger.level, numeric) if logger.level else numeric)
        return logger

    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(numeric)
    console.setFormatter(formatter)

    file_handler = logging.FileHandler(log_dir / "skabu.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file_handler)
    logger.propagate = False
    _CONFIGURED = True
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def reset_logging_for_tests() -> None:
    """pytest 用。本番コードからは呼ばない。"""
    global _CONFIGURED
    logger = logging.getLogger(_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    _CONFIGURED = False


def dump_debug_event(debug_dir: Path, event: dict[str, Any]) -> Path:
    """API失敗などを JSON で残す。キーは絶対に書かない。"""
    debug_dir.mkdir(parents=True, exist_ok=True)
    stamped = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    kind = str(event.get("kind") or "event")
    path = debug_dir / f"{stamped}_{kind}.json"
    safe = _redact(event)
    path.write_text(json.dumps(safe, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    get_logger().debug("debug dump written: %s", path)
    return path


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lower = str(key).lower()
            if lower in {"x-api-key", "api_key", "apikey", "authorization"}:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
