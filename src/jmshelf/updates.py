from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import LibraryDatabase, now_iso
from .provider import JmcomicProvider
from .utils import normalize_chapter_index


class SeriesUpdateChecker:
    """Startup and daily metadata-only checks for source-backed library series."""

    def __init__(
        self,
        database: LibraryDatabase,
        provider: JmcomicProvider,
        check_interval: timedelta = timedelta(days=1),
        poll_seconds: float = 15 * 60,
        initial_delay_seconds: float = 2,
    ) -> None:
        self.database = database
        self.provider = provider
        self.check_interval = check_interval
        self.poll_seconds = max(1.0, poll_seconds)
        self.initial_delay_seconds = max(0.0, initial_delay_seconds)
        self._closing = threading.Event()
        self._worker: threading.Thread | None = None
        self._lock = threading.RLock()
        self._checking: set[str] = set()
        self._startup_status: dict[str, Any] = {
            "state": "waiting",
            "startedAt": None,
            "completedAt": None,
            "eligibleCount": 0,
            "checkedCount": 0,
            "failedCount": 0,
            "updates": [],
        }

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None

    def is_due(self, series: dict[str, Any], current_time: datetime | None = None) -> bool:
        if not series.get("updateSupported") or not series.get("autoUpdateEnabled"):
            return False
        last_checked = self._parse_timestamp(series.get("lastCheckedAt"))
        if last_checked is None:
            return True
        now = current_time or datetime.now(UTC)
        return now - last_checked.astimezone(UTC) >= self.check_interval

    def check(self, series_id: str) -> dict[str, Any]:
        series = self.database.get_library_series(series_id)
        if not series:
            raise ValueError("未找到系列")
        source_series_id = series.get("sourceSeriesId")
        if not source_series_id:
            raise ValueError("这个系列不是同一 JM 作品，无法自动检查更新")
        with self._lock:
            if series_id in self._checking:
                raise ValueError("这个系列正在检查更新")
            self._checking.add(series_id)
        try:
            manifest = self.provider.inspect_series(source_series_id)
            known_source_ids = {
                str(member["sourceId"])
                for member in series.get("members", [])
                if member.get("sourceId")
            }
            new_items = [
                {
                    "sourceId": str(item["sourceId"]),
                    "title": str(item.get("title") or f"JM{item['sourceId']}"),
                    "chapterIndex": normalize_chapter_index(item.get("chapterIndex") or 1),
                }
                for item in manifest.get("items", [])
                if item.get("sourceId") and str(item["sourceId"]) not in known_source_ids
            ]
            new_items.sort(key=lambda item: (item["chapterIndex"], item["sourceId"]))
            updated = self.database.record_library_series_check(series_id, new_items, checked_at=now_iso())
            if not updated:
                raise ValueError("系列已被移除")
            return {
                "series": updated,
                "newItems": new_items,
                "newCount": len(new_items),
                "remoteItemCount": int(manifest.get("itemCount") or 0),
            }
        except Exception as exc:
            self.database.record_library_series_check(series_id, error=str(exc) or type(exc).__name__)
            raise
        finally:
            with self._lock:
                self._checking.discard(series_id)

    def check_due(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for series in self.database.list_library_series():
            if self._closing.is_set():
                break
            if not self.is_due(series):
                continue
            try:
                results.append(self.check(series["id"]))
            except Exception:
                # The error is persisted for the UI. One failing source must not
                # prevent the rest of the library from being checked.
                continue
        return results

    def check_all(self) -> list[dict[str, Any]]:
        """Force one check for every eligible series, used once per app startup."""
        eligible = [
            series for series in self.database.list_library_series()
            if series.get("updateSupported") and series.get("autoUpdateEnabled")
        ]
        results: list[dict[str, Any]] = []
        if not eligible or self._closing.is_set():
            return results
        workers = min(4, len(eligible))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="jmshelf-startup-updates") as executor:
            futures = {executor.submit(self.check, series["id"]): series for series in eligible}
            for future in as_completed(futures):
                if self._closing.is_set():
                    break
                try:
                    results.append(future.result())
                except Exception:
                    # Errors are persisted by check(); continue checking other series.
                    continue
        return results

    def startup_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._startup_status)

    def start(self) -> None:
        with self._lock:
            if self._worker is not None:
                return
            self._worker = threading.Thread(target=self._run, name="jmshelf-series-updates", daemon=True)
            self._worker.start()

    def close(self, timeout: float = 5.0) -> bool:
        self._closing.set()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=max(0.0, timeout))
        return worker is None or not worker.is_alive()

    def _run(self) -> None:
        if self._closing.wait(self.initial_delay_seconds):
            return
        eligible_count = sum(
            bool(series.get("updateSupported") and series.get("autoUpdateEnabled"))
            for series in self.database.list_library_series()
        )
        with self._lock:
            self._startup_status.update({
                "state": "checking",
                "startedAt": now_iso(),
                "eligibleCount": eligible_count,
            })
        results = self.check_all()
        updates = [
            {
                "seriesId": result["series"]["id"],
                "displayName": result["series"]["displayName"],
                "newCount": result["newCount"],
                "items": result["newItems"],
            }
            for result in results
            if result.get("newCount")
        ]
        with self._lock:
            self._startup_status.update({
                "state": "complete",
                "completedAt": now_iso(),
                "checkedCount": len(results),
                "failedCount": max(0, eligible_count - len(results)),
                "updates": updates,
            })
        while not self._closing.wait(self.poll_seconds):
            self.check_due()
