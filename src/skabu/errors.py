"""ドメインエラー。失敗時に原因を切り分けやすくする。"""

from __future__ import annotations


class SkabuError(Exception):
    """skabu 共通の例外。"""


class ConfigError(SkabuError):
    """設定不足・不正。"""


class AuthError(SkabuError):
    """APIキー不正、権限不足。"""


class RateLimitError(SkabuError):
    """J-Quants の分間リクエスト上限超過。"""

    def __init__(self, message: str, retry_after_sec: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


class ApiError(SkabuError):
    """API が 4xx/5xx を返した。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.body = body


class NetworkError(SkabuError):
    """タイムアウト・接続失敗などネットワーク層のエラー。"""
