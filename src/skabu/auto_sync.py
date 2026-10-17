"""serve 起動時と定期で、足りない日だけ J-Quants から取る。全履歴は取らない。"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from skabu.config import Settings, plan_data_window
from skabu.errors import ConfigError
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.store import connect, latest_bar_date, synced_ok_dates, trading_dates

STATE_NAME = "auto_sync.json"
MAX_PENDING_DAYS = 20
FACTS_STALE_DAYS = 7
TICK_SECONDS = 30 * 60
AFTER_CLOSE_HOUR = 16
JST = timezone(timedelta(hours=9))


def state_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / STATE_NAME


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_jst(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(JST)


def after_close(moment: datetime) -> bool:
    local = to_jst(moment)
    return local.weekday() < 5 and local.hour >= AFTER_CLOSE_HOUR


def cap_pending(pending: list[str], limit: int = MAX_PENDING_DAYS) -> tuple[list[str], int]:
    extra = max(0, len(pending) - limit)
    if extra:
        return pending[-limit:], extra
    return list(pending), 0


def bars_end_for(reason: str, moment: datetime, current: date, window_to: str) -> str:
    """引け前の自動同期は当日を取りにいかない。手動だけ当日も含める。"""
    if reason == "manual" or after_close(moment):
        return window_to
    return (current - timedelta(days=1)).isoformat()


def pending_days(
    conn: Any,
    dataset: str,
    start: str,
    end: str,
    *,
    limit: int = MAX_PENDING_DAYS,
    min_rows: int = 0,
) -> tuple[list[str], int]:
    days = trading_dates(conn, start, end)
    already = synced_ok_dates(conn, dataset, min_rows=min_rows)
    pending = [day for day in days if day not in already]
    return cap_pending(pending, limit)


def parse_iso(raw: str | None) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def facts_is_fresh(last_facts_at: str | None, moment: datetime) -> bool:
    then = parse_iso(last_facts_at)
    if then is None:
        return False
    return (moment - then) < timedelta(days=FACTS_STALE_DAYS)


def load_state(settings: Settings) -> dict[str, Any]:
    path = state_path(settings)
    blank = {
        "last_at": None,
        "last_facts_at": None,
        "last_bars_to": None,
        "last_error": None,
        "last_action": None,
    }
    if not path.exists():
        return blank
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        get_logger().warning("auto sync state read failed path=%s error=%s", path, exc)
        dump_debug_event(settings.debug_dir, {"kind": "auto_sync_state_read", "path": str(path), "error": str(exc)})
        return blank
    if not isinstance(raw, dict):
        return blank
    return {**blank, **{key: raw.get(key) for key in blank}}


def save_state(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    settings.ensure_dirs()
    path = state_path(settings)
    body = {
        "last_at": payload.get("last_at"),
        "last_facts_at": payload.get("last_facts_at"),
        "last_bars_to": payload.get("last_bars_to"),
        "last_error": payload.get("last_error"),
        "last_action": payload.get("last_action"),
    }
    try:
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        dump_debug_event(settings.debug_dir, {"kind": "auto_sync_state_write", "path": str(path), "error": str(exc)})
        get_logger().warning("auto sync state write failed path=%s error=%s", path, exc)
    return body


def _own_service(settings: Settings, sync_service: Any | None) -> tuple[Any, bool]:
    if sync_service is not None:
        return sync_service, False
    from skabu.jquants import JQuantsClient
    from skabu.sync import SyncService

    settings.require_api_key()
    return SyncService(settings, JQuantsClient(settings)), True


def run_auto_sync(
    settings: Settings,
    *,
    reason: str = "start",
    now: datetime | None = None,
    today: date | None = None,
    sync_service: Any | None = None,
    lock: threading.Lock | None = None,
    running_flag: list[bool] | None = None,
) -> dict[str, Any]:
    log = get_logger()
    moment = now or now_utc()
    current = today or to_jst(moment).date()
    held = False
    if lock is not None:
        held = lock.acquire(blocking=False)
        if not held:
            log.info("auto sync skip busy reason=%s", reason)
            return {"action": "busy", "reason": reason}
    if running_flag is not None:
        running_flag[0] = True
    state = load_state(settings)
    try:
        window_from, window_to = plan_data_window(settings.jquants_plan, current)
        bar_end = bars_end_for(reason, moment, current, window_to)
        with connect(settings.db_path) as conn:
            bars_to = latest_bar_date(conn)
            early_pending, _early_extra = pending_days(conn, "daily_bars", window_from, bar_end, min_rows=1)
        facts_fresh = facts_is_fresh(state.get("last_facts_at"), moment)
        bars_current = bool(bars_to) and str(bars_to) >= window_to
        if reason == "tick":
            if not after_close(moment) and not early_pending:
                result = {
                    "action": "skipped",
                    "reason": reason,
                    "skip": "before_close",
                    "facts_fresh": facts_fresh,
                }
                state = save_state(settings, {**state, "last_action": "skipped", "last_error": None})
                log.info("auto sync skip before_close bars_to=%s", bars_to)
                return {**result, **state}
            if after_close(moment) and bars_current and facts_fresh:
                result = {
                    "action": "skipped",
                    "reason": reason,
                    "skip": "fresh",
                    "pending_bars": 0,
                    "facts_fresh": True,
                }
                state = save_state(settings, {**state, "last_action": "skipped", "last_error": None, "last_bars_to": bars_to})
                log.info("auto sync skip fresh bars_to=%s", bars_to)
                return {**result, **state}

        service, own = _own_service(settings, sync_service)
        try:
            calendar = service.sync_calendar()
            master = service.sync_master()
            with connect(settings.db_path) as conn:
                bar_pending, bar_extra = pending_days(conn, "daily_bars", window_from, bar_end, min_rows=1)
                fact_pending, fact_extra = pending_days(conn, "equity_facts", window_from, window_to)
            if bar_extra:
                log.warning("auto sync bars truncated extra=%s cap=%s", bar_extra, MAX_PENDING_DAYS)
            if fact_extra:
                log.warning("auto sync facts truncated extra=%s cap=%s", fact_extra, MAX_PENDING_DAYS)
            if bar_pending:
                bars = service.sync_bars(
                    from_date=bar_pending[0],
                    to_date=bar_pending[-1],
                    max_days=MAX_PENDING_DAYS,
                    full=False,
                )
            else:
                bars = {
                    "dataset": "daily_bars",
                    "pending_days": 0,
                    "synced_days": 0,
                    "skipped_days": 0,
                    "candidate_days": 0,
                    "empty_days": 0,
                    "error_days": 0,
                }
            facts = None
            if not facts_fresh:
                if fact_pending:
                    facts = service.sync_facts(
                        from_date=fact_pending[0],
                        to_date=fact_pending[-1],
                        max_days=MAX_PENDING_DAYS,
                        full=False,
                    )
                else:
                    facts = {
                        "dataset": "equity_facts",
                        "pending_days": 0,
                        "synced_days": 0,
                        "skipped_days": 0,
                        "candidate_days": 0,
                        "error_days": 0,
                    }
                state["last_facts_at"] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
            with connect(settings.db_path) as conn:
                bars_to = latest_bar_date(conn)
            synced_days = int((bars or {}).get("synced_days") or 0)
            empty_days = int((bars or {}).get("empty_days") or 0)
            error_days = int((bars or {}).get("error_days") or 0)
            action = "synced"
            if synced_days == 0 and empty_days > 0 and error_days == 0:
                action = "empty"
            state = save_state(
                settings,
                {
                    **state,
                    "last_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "last_bars_to": bars_to,
                    "last_error": None,
                    "last_action": action,
                },
            )
            log.info(
                "auto sync done reason=%s action=%s bars_pending=%s synced=%s empty=%s facts=%s bars_to=%s",
                reason,
                action,
                len(bar_pending),
                synced_days,
                empty_days,
                "yes" if facts is not None else "skip",
                bars_to,
            )
            return {
                "action": action,
                "reason": reason,
                "calendar": calendar,
                "master": master,
                "bars": bars,
                "facts": facts,
                "pending_bars": len(bar_pending),
                "truncated_bars": bar_extra,
                "full": False,
                **state,
            }
        finally:
            if own:
                client = getattr(service, "client", None)
                close = getattr(client, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        log.exception("auto sync client close failed")
    except ConfigError as exc:
        log.warning("auto sync no key reason=%s error=%s", reason, exc)
        dump_debug_event(settings.debug_dir, {"kind": "auto_sync", "reason": reason, "error": str(exc)})
        state = save_state(settings, {**state, "last_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"), "last_error": str(exc), "last_action": "failed"})
        return {"action": "failed", "reason": reason, "error": str(exc), **state}
    except Exception as exc:
        log.exception("auto sync failed reason=%s", reason)
        dump_debug_event(settings.debug_dir, {"kind": "auto_sync", "reason": reason, "error": str(exc)})
        state = save_state(settings, {**state, "last_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"), "last_error": str(exc), "last_action": "failed"})
        return {"action": "failed", "reason": reason, "error": str(exc), **state}
    finally:
        if running_flag is not None:
            running_flag[0] = False
        if lock is not None and held:
            lock.release()


class AutoSyncRunner:
    def __init__(self, settings: Settings, *, interval_sec: int = TICK_SECONDS) -> None:
        self.settings = settings
        self.interval_sec = interval_sec
        self._lock = threading.Lock()
        self._running = [False]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def enabled(self) -> bool:
        return bool(self.settings.skabu_auto_sync)

    def snapshot(self) -> dict[str, Any]:
        disk = load_state(self.settings)
        return {
            "enabled": self.enabled(),
            "running": bool(self._running[0]),
            "last_at": disk.get("last_at"),
            "last_bars_to": disk.get("last_bars_to"),
            "last_error": disk.get("last_error"),
            "last_action": disk.get("last_action"),
        }

    def start(self) -> None:
        if not self.enabled():
            get_logger().info("auto sync disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="skabu-auto-sync", daemon=True)
        self._thread.start()
        get_logger().info("auto sync thread started interval=%s", self.interval_sec)

    def stop(self) -> None:
        self._stop.set()

    def run(self, *, reason: str = "start", now: datetime | None = None, today: date | None = None, sync_service: Any | None = None) -> dict[str, Any]:
        return run_auto_sync(
            self.settings,
            reason=reason,
            now=now,
            today=today,
            sync_service=sync_service,
            lock=self._lock,
            running_flag=self._running,
        )

    def _loop(self) -> None:
        try:
            self.run(reason="start")
            while not self._stop.wait(self.interval_sec):
                self.run(reason="tick")
        except Exception:
            get_logger().exception("auto sync thread crashed")
            dump_debug_event(self.settings.debug_dir, {"kind": "auto_sync_thread", "error": "crashed"})
