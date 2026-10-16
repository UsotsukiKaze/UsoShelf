from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

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
        enqueue_updates: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
        task_status: Callable[[dict[str, Any]], str] | None = None,
        update_wait_seconds: float = 600,
    ) -> None:
        self.database = database
        self.provider = provider
        self.check_interval = check_interval
        self.poll_seconds = max(1.0, poll_seconds)
        self.initial_delay_seconds = max(0.0, initial_delay_seconds)
        self.enqueue_updates = enqueue_updates
        self.task_status = task_status
        self.update_wait_seconds = max(0.0, update_wait_seconds)
        self._tracked_tasks: dict[str, list[dict[str, Any]]] = {}
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
            "updateCompletedCount": 0,
            "updateFailedCount": 0,
            "updatePendingCount": 0,
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
        interval = min(self.check_interval, timedelta(minutes=15)) if series.get("updateError") else self.check_interval
        return now - last_checked.astimezone(UTC) >= interval

    def check(self, series_id: str, *, background: bool = False) -> dict[str, Any]:
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
            # Update checks must query JM, not re-label an old card cache as a
            # successful check and postpone the next attempt for a full day.
            manifest = self.provider.inspect_series(source_series_id, fresh=True, background=background)
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
        self._poll_task_results()
        results: list[dict[str, Any]] = []
        for series in self.database.list_library_series():
            if self._closing.is_set():
                break
            if not self.is_due(series):
                continue
            try:
                result = self.check(series["id"], background=True)
                self._enqueue_new_items(result)
                results.append(result)
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
            futures = {executor.submit(self.check, series["id"], background=True): series for series in eligible}
            for future in as_completed(futures):
                if self._closing.is_set():
                    break
                try:
                    result = future.result()
                    self._enqueue_new_items(result)
                    results.append(result)
                except Exception:
                    # Errors are persisted by check(); continue checking other series.
                    continue
        return results

    def _enqueue_new_items(self, result: dict[str, Any]) -> None:
        if not result.get("newItems") or self.enqueue_updates is None:
            return
        try:
            result["updateTasks"] = self.enqueue_updates(result)
        except Exception as exc:
            result["updateTasks"] = list(getattr(exc, "update_tasks", []))
            result["updateError"] = str(exc) or type(exc).__name__
            self.database.record_library_series_check(result["series"]["id"], error=result["updateError"])
        if result.get("updateTasks"):
            with self._lock:
                self._tracked_tasks[result["series"]["id"]] = result["updateTasks"]

    def _poll_task_results(self) -> None:
        if self.task_status is None:
            return
        with self._lock:
            tracked = list(self._tracked_tasks.items())
        for series_id, tasks in tracked:
            statuses = []
            for task in tasks:
                try:
                    statuses.append(self.task_status(task))
                except Exception:
                    statuses.append("failed")
            if any(status in {"queued", "running"} for status in statuses):
                continue
            if any(status != "completed" for status in statuses):
                self.database.record_library_series_check(series_id, error="部分章节更新失败，将自动重试，也可手动重试")
            with self._lock:
                if self._tracked_tasks.get(series_id) == tasks:
                    self._tracked_tasks.pop(series_id, None)

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
                "tasks": result.get("updateTasks", []),
                "error": result.get("updateError", ""),
            }
            for result in results
            if result.get("newCount")
        ]
        tasks = [task for update in updates for task in update["tasks"]]
        statuses: list[str] = []
        if tasks and self.task_status is not None:
            with self._lock:
                self._startup_status["state"] = "updating"
            deadline = time.monotonic() + self.update_wait_seconds
            while not self._closing.is_set():
                statuses = []
                for task in tasks:
                    try:
                        statuses.append(self.task_status(task))
                    except Exception:
                        statuses.append("failed")
                if all(status not in {"queued", "running"} for status in statuses):
                    break
                if time.monotonic() >= deadline or self._closing.wait(min(0.5, max(0.0, deadline - time.monotonic()))):
                    break
        completed_count = sum(status == "completed" for status in statuses)
        failed_count = sum(status not in {"completed", "queued", "running"} for status in statuses)
        pending_count = sum(status in {"queued", "running"} for status in statuses)
        self._poll_task_results()
        with self._lock:
            self._startup_status.update({
                "state": "complete",
                "completedAt": now_iso(),
                "checkedCount": len(results),
                "failedCount": max(0, eligible_count - len(results)),
                "updateCompletedCount": completed_count,
                "updatePendingCount": pending_count,
                "updateFailedCount": failed_count + sum(max(0, update["newCount"] - len(update["tasks"])) for update in updates if update["error"]),
                "updates": updates,
            })
        while not self._closing.wait(self.poll_seconds):
            self.check_due()
