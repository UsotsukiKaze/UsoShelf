from pathlib import Path
import json
import threading
import time

import pytest

from jmshelf.downloads import DownloadQueue


def test_download_queue_runs_two_independent_tasks_in_parallel(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    started: list[str] = []
    lock = threading.Lock()

    def handler(source_id, output_path, _progress):
        with lock:
            started.append(source_id)
        barrier.wait(timeout=2)
        return {"items": [], "outputPath": str(output_path)}

    downloads = DownloadQueue(handler, workers=2)
    try:
        first, _ = downloads.enqueue("1019294", tmp_path / "first")
        second, _ = downloads.enqueue("1096733", tmp_path / "second")
        deadline = time.monotonic() + 3
        statuses: dict[str, str] = {}
        while time.monotonic() < deadline:
            statuses = {item["id"]: item["status"] for item in downloads.snapshot()["items"]}
            if statuses.get(first["id"]) == statuses.get(second["id"]) == "completed":
                break
            time.sleep(0.01)
        assert set(started) == {"1019294", "1096733"}
        assert statuses[first["id"]] == "completed"
        assert statuses[second["id"]] == "completed"
    finally:
        downloads.close()


def test_download_queue_passes_context_and_reuses_it_on_retry(tmp_path: Path) -> None:
    received: list[dict] = []
    attempts = {"count": 0}

    def handler(_source_id, _output_path, _progress, context):
        received.append(context)
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary")
        return {"items": []}

    downloads = DownloadQueue(handler, workers=1)
    try:
        first, _ = downloads.enqueue(
            "1019294",
            tmp_path / "cache",
            context={"selectedSourceIds": ["1096733"]},
        )
        deadline = time.monotonic() + 2
        failed = first
        while time.monotonic() < deadline:
            failed = next(item for item in downloads.snapshot()["items"] if item["id"] == first["id"])
            if failed["status"] == "failed":
                break
            time.sleep(0.01)
        retried, _ = downloads.retry(first["id"])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = next(item for item in downloads.snapshot()["items"] if item["id"] == retried["id"])
            if current["status"] == "completed":
                break
            time.sleep(0.01)
        assert current["status"] == "completed"
        assert received == [
            {"selectedSourceIds": ["1096733"]},
            {"selectedSourceIds": ["1096733"]},
        ]
    finally:
        downloads.close()


def test_download_queue_recovers_interrupted_history_as_retryable(tmp_path: Path) -> None:
    state_path = tmp_path / "queue.json"
    state_path.write_text(json.dumps({"schemaVersion": 1, "items": [{
        "id": "interrupted",
        "sourceId": "1019294",
        "title": "未完成任务",
        "outputPath": str(tmp_path / "downloads"),
        "status": "running",
        "phase": "下载图片",
        "progress": 50,
        "completedItems": 0,
        "totalItems": 1,
        "currentItem": None,
        "currentItemTitle": "",
        "createdAt": "2026-09-10T00:00:00+00:00",
        "startedAt": "2026-09-10T00:00:01+00:00",
        "completedAt": None,
        "error": None,
        "comicIds": [],
        "context": None,
    }]}), encoding="utf-8")

    downloads = DownloadQueue(lambda *_args: {"items": []}, state_path=state_path)
    try:
        recovered = downloads.snapshot()["items"][0]
        assert recovered["status"] == "failed"
        assert "重试" in recovered["error"]
    finally:
        assert downloads.close()


def test_download_queue_reports_worker_that_outlives_shutdown_timeout(tmp_path: Path) -> None:
    release = threading.Event()
    started = threading.Event()

    def handler(*_args):
        started.set()
        release.wait(2)
        return {"items": []}

    downloads = DownloadQueue(handler, workers=1, state_path=tmp_path / "queue.json")
    downloads.enqueue("1019294", tmp_path / "downloads")
    assert started.wait(1)
    assert downloads.close(timeout=0) is False
    release.set()
    assert downloads.close(timeout=2) is True


def test_normal_work_overtakes_queued_background_without_interrupting_running_task(tmp_path: Path) -> None:
    release = threading.Event()
    first_started = threading.Event()
    order: list[str] = []

    def handler(source_id, _output_path, _progress):
        order.append(source_id)
        if source_id == "book-a-p1":
            first_started.set()
            release.wait(2)
        return {"items": []}

    downloads = DownloadQueue(handler, workers=1)
    try:
        downloads.enqueue("book-a-p1", tmp_path / "cache")
        assert first_started.wait(1)
        downloads.enqueue("book-a-p2", tmp_path / "cache", queue_priority="background")
        downloads.enqueue("book-a-p3", tmp_path / "cache", queue_priority="background")
        downloads.enqueue("book-b-p1", tmp_path / "cache")
        assert order == ["book-a-p1"]
        positions = {
            item["sourceId"]: item["queuePosition"]
            for item in downloads.snapshot()["items"]
            if item["status"] == "queued"
        }
        assert positions == {"book-b-p1": 1, "book-a-p2": 2, "book-a-p3": 3}

        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and len(order) < 4:
            time.sleep(0.01)

        assert order == ["book-a-p1", "book-b-p1", "book-a-p2", "book-a-p3"]
    finally:
        release.set()
        downloads.close()


def test_image_progress_throttles_queue_state_file_rewrites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = (tmp_path / "queue.json").resolve()
    writes = 0
    original_replace = Path.replace

    def tracking_replace(path: Path, target: Path):
        nonlocal writes
        if Path(target).resolve() == state_path:
            writes += 1
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", tracking_replace)

    def handler(_source_id, _output_path, progress):
        for index in range(100):
            progress({"progress": index, "availablePages": index})
        return {"items": []}

    downloads = DownloadQueue(handler, workers=1, state_path=state_path)
    try:
        task, _ = downloads.enqueue("701", tmp_path / "cache")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = next(item for item in downloads.snapshot()["items"] if item["id"] == task["id"])
            if current["status"] == "completed":
                break
            time.sleep(0.01)
        assert current["status"] == "completed"
        assert writes <= 4
    finally:
        downloads.close()
