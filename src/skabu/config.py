"""環境変数から設定を読む。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from skabu.errors import ConfigError

PlanName = Literal["free", "light", "standard", "premium"]

PLAN_REQUESTS_PER_MINUTE: dict[str, int] = {
    "free": 5,
    "light": 60,
    "standard": 120,
    "premium": 500,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jquants_api_key: str = ""
    jquants_plan: PlanName = "free"
    jquants_base_url: str = "https://api.jquants.com/v2"
    skabu_log_level: str = "INFO"
    skabu_data_dir: Path = Path("data")
    skabu_log_dir: Path = Path("logs")
    skabu_http_timeout_sec: float = 30.0
    skabu_http_max_retries: int = Field(default=4, ge=0, le=10)
    skabu_rate_limit_enabled: bool = True
    skabu_auto_sync: bool = True
    kabu_live: bool = False
    kabu_api_password: str = ""
    kabu_base_url: str = "http://127.0.0.1:18080/kabusapi"
    kabu_max_order_yen: float = Field(default=1_000_000, gt=0)

    @field_validator("jquants_plan", mode="before")
    @classmethod
    def _normalize_plan(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("skabu_log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @property
    def requests_per_minute(self) -> int:
        return PLAN_REQUESTS_PER_MINUTE[self.jquants_plan]

    @property
    def debug_dir(self) -> Path:
        return self.skabu_log_dir / "debug"

    @property
    def db_path(self) -> Path:
        return self.skabu_data_dir / "skabu.db"

    def require_api_key(self) -> str:
        key = self.jquants_api_key.strip()
        if not key:
            raise ConfigError(
                "JQUANTS_API_KEY が未設定です。"
                " .env.example をコピーして .env を作り、"
                " J-Quants ダッシュボードで発行した APIキーを入れてください。"
            )
        return key

    def ensure_dirs(self) -> None:
        self.skabu_data_dir.mkdir(parents=True, exist_ok=True)
        self.skabu_log_dir.mkdir(parents=True, exist_ok=True)
        self.debug_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def plan_safe_sample_window(plan: str, today: date | None = None) -> tuple[str, str]:
    """接続確認用の日付。無料プランは12週間遅延がある。"""
    current = today or date.today()
    if plan == "free":
        end = current - timedelta(weeks=13)
    else:
        end = current - timedelta(days=3)
    start = end - timedelta(days=6)
    return start.isoformat(), end.isoformat()


PLAN_HISTORY_DAYS: dict[str, int] = {
    "free": 730,
    "light": 365 * 5,
    "standard": 365 * 10,
    "premium": 365 * 20,
}


def plan_data_window(plan: str, today: date | None = None) -> tuple[str, str]:
    """プランで取れる日足の期間。無料は12週間遅れ・過去2年。"""
    current = today or date.today()
    if plan == "free":
        end = current - timedelta(weeks=12)
    else:
        end = current
    start = end - timedelta(days=PLAN_HISTORY_DAYS.get(plan, 730))
    return start.isoformat(), end.isoformat()
