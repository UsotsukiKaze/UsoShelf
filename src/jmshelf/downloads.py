from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


ProgressCallback = Callable[[dict[str, Any]], None]
DownloadHandler = Callable[..., dict[str, Any]]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class DownloadQueue:
    """A bounded worker queue for long-running jmcomic downloads."""

    ACTIVE_STATUSES = {"queued", "running"}

    def __init__(
        self,
        handler: DownloadHandler,
        retain: int = 40,
        workers: int = 2,
        state_path: Path | None = None,
    ) -> None:
        self.handler = handler
        self.retain = max(10, retain)
        self._lock = threading.RLock()
        self._pending: queue.Queue[str | None] = queue.Queue()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._closing = threading.Event()
        self._workers: list[threading.Thread] = []
        self._worker_count = max(1, min(4, workers))
        self._state_path = state_path.resolve() if state_path else None
        self._persist_interval = 0.75
        self._last_persist_at = 0.0
        self._persist_dirty = False
        self._load_state()

    @property
    def closing(self) -> bool:
        return self._closing.is_set()

    def _ensure_worker_locked(self) -> None:
        if self._workers:
            return
        for index in range(self._worker_count):
            worker = threading.Thread(target=self._run, name=f"jmshelf-downloads-{index}", daemon=True)
            self._workers.append(worker)
            worker.start()

    def enqueue(
        self,
        source_id: str,
        output_path: Path,
        title: str = "",
        context: dict[str, Any] | None = None,
        queue_priority: str = "normal",
    ) -> tuple[dict[str, Any], bool]:
        resolved_output = str(output_path.resolve())
        clean_context = deepcopy(context) if context else None
        clean_priority = "background" if queue_priority == "background" else "normal"
        with self._lock:
            if self.closing:
                raise ValueError("下载队列正在关闭")
            for task_id in reversed(self._order):
                existing = self._tasks[task_id]
                if (
                    existing["status"] in self.ACTIVE_STATUSES
                    and existing["sourceId"] == source_id
                    and existing["outputPath"] == resolved_output
                    and existing.get("context") == clean_context
                ):
                    return deepcopy(existing), False

            task_id = str(uuid.uuid4())
            task = {
                "id": task_id,
                "sourceId": source_id,
                "title": title.strip() or f"JM{source_id}",
                "outputPath": resolved_output,
                "status": "queued",
                "phase": "等待下载",
                "progress": 0,
                "completedItems": 0,
                "importedItems": 0,
                "totalItems": 0,
                "currentItem": None,
                "currentItemTitle": "",
                "streamReady": False,
                "streamingRoot": "",
                "activeSourceId": "",
                "availablePages": 0,
                "expectedPages": 0,
                "createdAt": _now_iso(),
                "startedAt": None,
                "completedAt": None,
                "error": None,
                "comicIds": [],
                "context": clean_context,
                "queuePriority": clean_priority,
            }
            self._tasks[task_id] = task
            self._order.append(task_id)
            self._trim_locked()
            self._persist_locked()
            self._ensure_worker_locked()
            self._put_pending_locked(task_id, clean_priority)
            return deepcopy(task), True

    def _put_pending_locked(self, task_id: str, queue_priority: str) -> None:
        """Insert normal work before queued background preloads.

        Running work is deliberately untouched. This gives the first chapter of
        a newly added book a turn before the trailing chapters of older books,
        without interrupting an image request that is already in flight.
        """
        with self._pending.mutex:
            if queue_priority == "background":
                self._pending.queue.append(task_id)
            else:
                insert_at = len(self._pending.queue)
                for index, pending_id in enumerate(self._pending.queue):
                    pending = self._tasks.get(str(pending_id))
                    if pending and pending.get("queuePriority") == "background":
                        insert_at = index
                        break
                self._pending.queue.insert(insert_at, task_id)
            self._pending.unfinished_tasks += 1
            self._pending.not_empty.notify()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            with self._pending.mutex:
                queued_ids = [
                    str(task_id)
                    for task_id in self._pending.queue
                    if task_id is not None
                    and self._tasks.get(str(task_id), {}).get("status") == "queued"
                ]
            # A worker can have claimed an ID and be waiting for this lock before
            # it switches the task to running. Keep that tiny transition visible.
            queued_ids.extend(
                task_id for task_id in self._order
                if self._tasks[task_id]["status"] == "queued" and task_id not in queued_ids
            )
            queue_positions = {task_id: index + 1 for index, task_id in enumerate(queued_ids)}
            tasks = []
            for task_id in reversed(self._order):
                task = deepcopy(self._tasks[task_id])
                task["queuePosition"] = queue_positions.get(task_id)
                tasks.append(task)
            return {
                "items": tasks,
                "activeCount": sum(task["status"] in self.ACTIVE_STATUSES for task in self._tasks.values()),
                "queuedCount": len(queued_ids),
            }

    def prioritize(self, task_id: str) -> bool:
        """Move a queued task to the front without duplicating queue work."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.get("status") != "queued":
                return False
        # queue.Queue has no public reordering API. Reordering its protected
        # deque under the queue mutex preserves unfinished-task accounting and
        # keeps workers from observing the container midway through the move.
        with self._pending.mutex:
            try:
                self._pending.queue.remove(task_id)
            except ValueError:
                return False
            self._pending.queue.appendleft(task_id)
            self._pending.not_empty.notify()
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.get("status") == "queued":
                task["queuePriority"] = "normal"
                self._persist_locked()
        return True

    def retry(self, task_id: str) -> tuple[dict[str, Any], bool]:
        with self._lock:
            failed = self._tasks.get(task_id)
            if not failed:
                raise ValueError("下载任务不存在")
            if failed["status"] != "failed":
                raise ValueError("只有失败的任务可以重试")
            source_id = failed["sourceId"]
            output_path = Path(failed["outputPath"])
            title = failed["title"]
            context = failed.get("context")
        return self.enqueue(
            source_id,
            output_path,
            title,
            context,
            queue_priority=str(failed.get("queuePriority") or "normal"),
        )

    def is_active(self, source_id: str, output_path: Path | None = None) -> bool:
        resolved_output = str(output_path.resolve()) if output_path else None
        with self._lock:
            return any(
                task["status"] in self.ACTIVE_STATUSES
                and task["sourceId"] == source_id
                and (resolved_output is None or task["outputPath"] == resolved_output)
                for task in self._tasks.values()
            )

    def close(self, timeout: float = 5.0) -> bool:
        self._closing.set()
        with self._lock:
            for task in self._tasks.values():
                if task["status"] == "queued":
                    task.update({
                        "status": "failed",
                        "phase": "应用已退出",
                        "completedAt": _now_iso(),
                        "error": "应用退出前任务尚未开始，请重试",
                    })
            self._persist_locked()
        for _worker in self._workers:
            self._pending.put(None)
        deadline = time.monotonic() + max(0.0, timeout)
        for worker in self._workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        return all(not worker.is_alive() for worker in self._workers)

    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.is_file():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            items = payload.get("items", []) if isinstance(payload, dict) else []
            for item in items[-self.retain:]:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                task = deepcopy(item)
                task.pop("queuePosition", None)
                if task.get("status") in self.ACTIVE_STATUSES:
                    task.update({
                        "status": "failed",
                        "phase": "上次运行已中断",
                        "completedAt": _now_iso(),
                        "error": "应用在任务完成前退出，请重试",
                    })
                task_id = str(task["id"])
                self._tasks[task_id] = task
                self._order.append(task_id)
            self._trim_locked()
            self._persist_locked()
        except (OSError, ValueError, TypeError):
            # A damaged history must not stop the application from starting.
            self._tasks.clear()
            self._order.clear()

    def _persist_locked(self, *, force: bool = True) -> None:
        if self._state_path is None:
            return
        now = time.monotonic()
        if not force and now - self._last_persist_at < self._persist_interval:
            self._persist_dirty = True
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(
                    {"schemaVersion": 1, "items": [self._tasks[item] for item in self._order]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            temporary.replace(self._state_path)
            self._last_persist_at = now
            self._persist_dirty = False
        except OSError:
            pass

    def _trim_locked(self) -> None:
        terminal = [
            task_id for task_id in self._order
            if self._tasks[task_id]["status"] not in self.ACTIVE_STATUSES
        ]
        while len(terminal) > self.retain:
            task_id = terminal.pop(0)
            self._order.remove(task_id)
            self._tasks.pop(task_id, None)

    def _update(self, task_id: str, patch: dict[str, Any]) -> None:
        allowed = {
            "title", "phase", "progress", "completedItems", "importedItems", "totalItems",
            "currentItem", "currentItemTitle", "streamReady", "streamingRoot",
            "activeSourceId", "availablePages", "expectedPages",
        }
        clean = {key: value for key, value in patch.items() if key in allowed}
        if "progress" in clean:
            clean["progress"] = max(0, min(100, int(clean["progress"])))
        if "importedItems" in clean:
            clean["importedItems"] = max(0, int(clean["importedItems"]))
        for key in ("availablePages", "expectedPages"):
            if key in clean:
                clean[key] = max(0, int(clean[key]))
        if "streamReady" in clean:
            clean["streamReady"] = bool(clean["streamReady"])
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task["status"] == "running":
                task.update(clean)
                # Image progress can arrive dozens of times per second. Keep it
                # live in memory, but avoid rewriting the whole queue file for
                # every page; terminal state changes still force an immediate
                # durable snapshot.
                self._persist_locked(force=False)

    def _run(self) -> None:
        while True:
            task_id = self._pending.get()
            if task_id is None or self.closing:
                return
            with self._lock:
                task = self._tasks.get(task_id)
                if not task or task["status"] != "queued":
                    continue
                task.update({
                    "status": "running",
                    "phase": "读取作品信息",
                    "progress": 1,
                    "startedAt": _now_iso(),
                })
                self._persist_locked()
                source_id = task["sourceId"]
                output_path = Path(task["outputPath"])
                context = deepcopy(task.get("context"))

            try:
                progress = lambda patch, current_id=task_id: self._update(current_id, patch)
                result = (
                    self.handler(source_id, output_path, progress, context)
                    if context is not None
                    else self.handler(source_id, output_path, progress)
                )
                if self._closing.is_set():
                    raise RuntimeError("JmShelf 已关闭，任务未执行入库")
                comics = result.get("comics", [])
                with self._lock:
                    task = self._tasks[task_id]
                    task.update({
                        "status": "completed",
                        "phase": "已下载并加入书架",
                        "progress": 100,
                        "completedItems": max(task["completedItems"], len(comics)),
                        "completedAt": _now_iso(),
                        "comicIds": [comic["id"] for comic in comics if comic.get("id")],
                    })
                    self._trim_locked()
                    self._persist_locked()
            except Exception as exc:
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task:
                        task.update({
                            "status": "failed",
                            "phase": "下载失败",
                            "completedAt": _now_iso(),
                            "error": str(exc) or type(exc).__name__,
                        })
                        self._trim_locked()
                        self._persist_locked()
