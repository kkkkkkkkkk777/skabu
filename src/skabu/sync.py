"""J-Quants からローカルDBへ差分同期する。"""

from __future__ import annotations

from typing import Any, Callable

from skabu.config import Settings, plan_data_window
from skabu.errors import SkabuError
from skabu.jquants import JQuantsClient, map_fins_summary_rows, map_valuation_rows
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.store import (
    attach_sales_growth3,
    connect,
    mark_sync_day,
    rebuild_weekly_bars,
    set_sync_state,
    status_snapshot,
    synced_ok_dates,
    trading_dates,
    upsert_calendar,
    upsert_daily_bars,
    upsert_equity_facts,
    upsert_fins_annual,
    upsert_master,
)

ProgressFn = Callable[[str], None]


class SyncService:
    def __init__(self, settings: Settings, client: JQuantsClient) -> None:
        self.settings = settings
        self.client = client
        self._log = get_logger()

    def sync_calendar(self, *, from_date: str | None = None, to_date: str | None = None) -> dict[str, Any]:
        start, end = self._window(from_date, to_date)
        self._log.info("sync calendar start %s .. %s", start, end)
        rows = self.client.get_calendar(from_date=start, to_date=end)
        with connect(self.settings.db_path) as conn:
            written = upsert_calendar(conn, rows)
            unknown = {
                str(row.get("HolDiv"))
                for row in rows
                if str(row.get("HolDiv")) not in {"0", "1", "3"}
            }
            if unknown:
                self._log.warning("unexpected HolDiv values: %s", sorted(unknown))
            set_sync_state(
                conn,
                "calendar",
                last_from=start,
                last_to=end,
                row_count=written,
                detail=f"api_rows={len(rows)}",
            )
            conn.commit()
        self._log.info("sync calendar done rows=%s", written)
        return {"dataset": "calendar", "rows": written, "from": start, "to": end}

    def sync_master(self, *, as_of: str | None = None) -> dict[str, Any]:
        _, end = self._window(None, as_of)
        self._log.info("sync master as_of=%s", end)
        rows = self.client.get_listed_master(date=end)
        with connect(self.settings.db_path) as conn:
            written = upsert_master(conn, rows)
            set_sync_state(
                conn,
                "listed_master",
                last_from=end,
                last_to=end,
                row_count=written,
                detail=f"api_rows={len(rows)}",
            )
            conn.commit()
        self._log.info("sync master done rows=%s", written)
        return {"dataset": "listed_master", "rows": written, "as_of": end}

    def sync_bars(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        max_days: int | None = 5,
        full: bool = False,
        progress: ProgressFn | None = None,
    ) -> dict[str, Any]:
        start, end = self._window(from_date, to_date)
        with connect(self.settings.db_path) as conn:
            days = trading_dates(conn, start, end)
        if not days:
            raise SkabuError(
                "営業日カレンダーが空です。先に `python -m skabu sync calendar` を実行してください。"
            )
        if not full:
            limit = max_days if max_days is not None else 5
            days = days[-limit:]
        elif max_days is not None:
            days = days[-max_days:]

        with connect(self.settings.db_path) as conn:
            already = synced_ok_dates(conn, "daily_bars", min_rows=1)
        pending = [day for day in days if day not in already]
        self._log.info(
            "sync bars window=%s..%s candidates=%s pending=%s",
            days[0] if days else None,
            days[-1] if days else None,
            len(days),
            len(pending),
        )

        synced = 0
        rows_total = 0
        errors = 0
        empty = 0
        for index, day in enumerate(pending, start=1):
            message = f"[{index}/{len(pending)}] daily_bars {day}"
            if progress:
                progress(message)
            self._log.info("%s", message)
            try:
                rows = self.client.get_daily_bars(date=day)
                with connect(self.settings.db_path) as conn:
                    written = upsert_daily_bars(conn, rows)
                    if written <= 0:
                        mark_sync_day(conn, "daily_bars", day, "empty", row_count=0, error="no_rows")
                        conn.commit()
                    else:
                        rebuild_weekly_bars(conn, [day])
                        mark_sync_day(conn, "daily_bars", day, "ok", row_count=written)
                        conn.commit()
                if written <= 0:
                    empty += 1
                    self._log.warning("sync bars empty date=%s rows=0", day)
                    dump_debug_event(
                        self.settings.debug_dir,
                        {"kind": "sync_bars_empty", "date": day, "rows": 0},
                    )
                else:
                    synced += 1
                    rows_total += written
            except SkabuError as exc:
                errors += 1
                self._log.exception("sync bars failed date=%s", day)
                dump_debug_event(
                    self.settings.debug_dir,
                    {"kind": "sync_bars", "date": day, "error": str(exc)},
                )
                with connect(self.settings.db_path) as conn:
                    mark_sync_day(conn, "daily_bars", day, "error", error=str(exc))
                    conn.commit()

        with connect(self.settings.db_path) as conn:
            set_sync_state(
                conn,
                "daily_bars",
                last_from=days[0] if days else None,
                last_to=days[-1] if days else None,
                row_count=rows_total,
                detail=f"synced_days={synced} skipped={len(days)-len(pending)} empty={empty} errors={errors}",
            )
            conn.commit()
        return {
            "dataset": "daily_bars",
            "from": days[0] if days else None,
            "to": days[-1] if days else None,
            "candidate_days": len(days),
            "pending_days": len(pending),
            "synced_days": synced,
            "skipped_days": len(days) - len(pending),
            "empty_days": empty,
            "error_days": errors,
            "rows": rows_total,
        }

    def sync_facts(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        max_days: int | None = 5,
        full: bool = False,
        progress: ProgressFn | None = None,
    ) -> dict[str, Any]:
        start, end = self._window(from_date, to_date)
        with connect(self.settings.db_path) as conn:
            days = trading_dates(conn, start, end)
        if not days:
            raise SkabuError(
                "営業日カレンダーが空です。先に `python -m skabu sync calendar` を実行してください。"
            )
        if not full:
            limit = max_days if max_days is not None else 5
            days = days[-limit:]
        elif max_days is not None:
            days = days[-max_days:]

        with connect(self.settings.db_path) as conn:
            already = synced_ok_dates(conn, "equity_facts")
        pending = [day for day in days if day not in already]
        self._log.info(
            "sync facts window=%s..%s candidates=%s pending=%s",
            days[0] if days else None,
            days[-1] if days else None,
            len(days),
            len(pending),
        )

        synced = 0
        rows_total = 0
        errors = 0
        for index, day in enumerate(pending, start=1):
            message = f"[{index}/{len(pending)}] equity_facts {day}"
            if progress:
                progress(message)
            self._log.info("%s", message)
            try:
                valuation = map_valuation_rows(self.client.get_valuation(date=day))
                fins = map_fins_summary_rows(self.client.get_fins_summary(date=day))
                with connect(self.settings.db_path) as conn:
                    annual = upsert_fins_annual(conn, fins)
                    merged = attach_sales_growth3(conn, valuation + fins, day)
                    written = upsert_equity_facts(conn, merged)
                    mark_sync_day(conn, "equity_facts", day, "ok", row_count=written)
                    conn.commit()
                synced += 1
                rows_total += written
                self._log.info(
                    "sync facts date=%s valuation=%s fins=%s fy=%s upsert=%s",
                    day,
                    len(valuation),
                    len(fins),
                    annual,
                    written,
                )
            except SkabuError as exc:
                errors += 1
                self._log.exception("sync facts failed date=%s", day)
                dump_debug_event(
                    self.settings.debug_dir,
                    {"kind": "sync_facts", "date": day, "error": str(exc)},
                )
                with connect(self.settings.db_path) as conn:
                    mark_sync_day(conn, "equity_facts", day, "error", error=str(exc))
                    conn.commit()

        with connect(self.settings.db_path) as conn:
            set_sync_state(
                conn,
                "equity_facts",
                last_from=days[0] if days else None,
                last_to=days[-1] if days else None,
                row_count=rows_total,
                detail=f"synced_days={synced} skipped={len(days)-len(pending)} errors={errors}",
            )
            conn.commit()
        return {
            "dataset": "equity_facts",
            "from": days[0] if days else None,
            "to": days[-1] if days else None,
            "candidate_days": len(days),
            "pending_days": len(pending),
            "synced_days": synced,
            "skipped_days": len(days) - len(pending),
            "error_days": errors,
            "rows": rows_total,
        }

    def status(self) -> dict[str, Any]:
        with connect(self.settings.db_path) as conn:
            snap = status_snapshot(conn)
        snap["db_path"] = str(self.settings.db_path.resolve())
        snap["plan_window"] = plan_data_window(self.settings.jquants_plan)
        return snap

    def _window(self, from_date: str | None, to_date: str | None) -> tuple[str, str]:
        auto_from, auto_to = plan_data_window(self.settings.jquants_plan)
        return from_date or auto_from, to_date or auto_to
