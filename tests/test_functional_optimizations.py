from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jmshelf.db import LibraryDatabase
from jmshelf.downloads import DownloadQueue
from jmshelf.main import create_app
from jmshelf.provider import JmcomicProvider, QueryCancelled, _PriorityQueryGate, _load_image_window


def test_reader_starts_while_two_background_chapters_are_busy(tmp_path: Path):
    busy = threading.Barrier(3)
    release = threading.Event()
    reader_started = threading.Event()

    def load(source, _output, _progress, context):
        if context.get("readerForeground"):
            reader_started.set()
        else:
            busy.wait(2)
            release.wait(2)
        return {"comics": []}

    queue = DownloadQueue(load, workers=2, reserve_reader=True)
    try:
        for source in ("701", "702"):
            queue.enqueue(source, tmp_path, context={"selectedSourceIds": [source]})
        busy.wait(2)
        task, _ = queue.enqueue("703", tmp_path, context={"selectedSourceIds": ["703"]})
        queue.prioritize(task["id"])
        assert reader_started.wait(1), "Clicked chapter must not wait for either older chapter"
        assert not release.is_set()
    finally:
        release.set()
        queue.close(2)


def test_image_window_refills_workers_and_bounds_speculation():
    release = threading.Event()
    reached_end_of_window = threading.Event()
    started = []
    lock = threading.Lock()

    def load(index):
        with lock:
            started.append(index)
        if index == 0:
            assert release.wait(3)
        if index == 11:
            reached_end_of_window.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(_load_image_window, list(range(24)), load, workers=4, window=12)
        try:
            assert reached_end_of_window.wait(2), "Later groups should start before the slow first group finishes"
            time.sleep(.05)
            assert 12 not in started, "A slow page must bound speculative work"
        finally:
            release.set()
        result.result(3)
    assert sorted(started) == list(range(24))


def test_cancelled_query_does_not_wait_for_busy_query_slot():
    gate = _PriorityQueryGate(total_limit=1)
    cancel = threading.Event()
    def enter():
        with gate.slot(cancel_event=cancel):
            pytest.fail("Cancelled query must never start network work")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with gate.slot():
            queued = pool.submit(enter)
            cancel.set()
            with pytest.raises(QueryCancelled):
                queued.result(1)
    assert gate.snapshot()["activeTotal"] == gate.snapshot()["waitingInteractive"] == 0


def test_recommendation_expiry_and_behavior_changes_trigger_refresh(tmp_path: Path):
    db = LibraryDatabase()
    provider = JmcomicProvider(db, tmp_path)
    comic = db.upsert_comic({"sourceId": "701", "pageCount": 40})
    def build_snapshot(**_kwargs):
        provider._recommendations_cache = (time.monotonic(), (), {"items": []})
    provider.recommendations = build_snapshot
    snapshot = {"schemaVersion": 3, "sourceIds": ["701"], "result": {"items": []},
                "behaviorKey": provider._recommendation_behavior_key()}
    try:
        provider._recommendation_snapshot = {**snapshot, "updatedAt": datetime.now(UTC).isoformat()}
        assert not provider.schedule_recommendation_refresh()["refreshing"]
        provider._recommendation_snapshot["updatedAt"] = (datetime.now(UTC) - timedelta(days=1, seconds=1)).isoformat()
        assert provider.schedule_recommendation_refresh()["refreshing"]
        provider._background_threads[-1].join(timeout=2)
        db.record_comic_open(comic["id"])
        provider._recommendation_snapshot = {**snapshot, "updatedAt": (datetime.now(UTC) - timedelta(minutes=16)).isoformat()}
        assert provider.schedule_recommendation_refresh()["refreshing"]
    finally:
        provider.close()
        db.close()


def test_favorite_first_page_survives_later_page_failure(tmp_path: Path):
    db = LibraryDatabase()
    provider = JmcomicProvider(db, tmp_path)
    provider.auth_status = lambda: {"authenticated": True, "username": "user"}
    calls = []
    def favorite_folder(page, **_kwargs):
        calls.append(page)
        if page == 2:
            raise RuntimeError("temporary failure")
        return SimpleNamespace(page_count=2, total=2, content=[("701", {"name": "First"})])
    provider._run_query = lambda operation: operation(SimpleNamespace(favorite_folder=favorite_folder))
    try:
        first = provider.favorites(page=1)
        assert first["hasMore"] and first["items"][0]["albumId"] == "701"
        assert calls == [1]
        with pytest.raises(RuntimeError, match="temporary"):
            provider.favorites(page=2)
        assert provider.favorites(page=1) == first
        assert calls == [1, 2]
    finally:
        db.close()


def test_adding_long_series_only_warms_first_two_chapters(tmp_path: Path):
    app = create_app(tmp_path)
    provider = app.state.provider
    provider.lookup = lambda *_args, **_kwargs: {"items": [
        {"sourceId": str(701 + i), "seriesId": "701", "seriesTitle": "Series", "chapterIndex": i + 1}
        for i in range(20)
    ]}
    queued = []
    def enqueue(source, _output, _title, context, **_kwargs):
        queued.extend(context["selectedSourceIds"])
        return {"id": source}, True
    app.state.caches.enqueue = enqueue
    app.state.caches.prioritize = lambda _task, **_kwargs: True
    with TestClient(app) as client:
        response = client.post("/api/provider/favorites/701/shelf", headers={"X-JmShelf-Request": "1"})
        assert response.status_code == 202
        assert response.json()["comicCount"] == 20
        assert len(client.get("/api/comics").json()) == 20
        assert queued == ["701", "702"]


def test_online_annotations_use_indexed_state_without_directory_scan(tmp_path: Path, monkeypatch):
    app = create_app(tmp_path)
    db = app.state.database
    db.upsert_comic({"sourceId": "701", "rootPath": str(tmp_path / "cache" / "701"),
                     "storageKind": "cache", "storageComplete": True, "pageCount": 80})
    app.state.provider.search = lambda *_args, **_kwargs: {"items": [{"albumId": "701"}]}
    app.state.provider.start_background_refreshes = lambda: None
    monkeypatch.setattr(db, "list_comics", lambda *_args, **_kwargs: pytest.fail("Must not read the entire library"))
    monkeypatch.setattr("jmshelf.main._comic_pages", lambda *_args: pytest.fail("Must not scan image directories"))
    with TestClient(app) as client:
        response = client.get("/api/online/search?q=test")
        assert response.status_code == 200
        assert response.json()["items"][0]["fullyAvailable"] is True


def test_cancel_before_query_arrives_never_executes_provider(tmp_path: Path):
    app = create_app(tmp_path)
    app.state.provider.search = lambda *_args, **_kwargs: pytest.fail("Cancelled work must not execute")
    with TestClient(app) as client:
        client.post("/api/online/cancel", headers={"X-JmShelf-Request": "1"}, json={"requestToken": "obsolete"})
        response = client.get("/api/online/search?q=test&requestToken=obsolete")
        assert response.status_code == 499


def test_search_cache_is_short_lived_isolated_and_invalidated(tmp_path: Path):
    db = LibraryDatabase()
    provider = JmcomicProvider(db, tmp_path)
    calls = []
    def search(query, **_kwargs):
        calls.append(query)
        return SimpleNamespace(content=[("701", {"name": "Cached"})], page_size=20, page_count=1, total=1)
    provider._run_query = lambda operation: operation(SimpleNamespace(search_site=search))
    try:
        first = provider.search("query")
        first["items"][0]["title"] = "Mutated by consumer"
        assert provider.search("query")["items"][0]["title"] == "Cached"
        assert calls == ["query"]
        provider.invalidate_cache()
        provider.search("query")
        assert calls == ["query", "query"]
        key = next(iter(provider._search_cache))
        _, cached = provider._search_cache[key]
        provider._search_cache[key] = (time.monotonic() - 61, cached)
        provider.search("query")
        assert calls == ["query", "query", "query"]
    finally:
        db.close()
