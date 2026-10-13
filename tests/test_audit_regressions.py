from __future__ import annotations

import base64
import json
import os
import threading
import time
import tomllib
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from Crypto.PublicKey import ECC
from fastapi.testclient import TestClient

from jmshelf.app_updates import AppUpdateManager
from jmshelf.db import LibraryDatabase
from jmshelf.main import create_app
from jmshelf.preferences import UiPreferences
from jmshelf.provider import JmcomicProvider
from jmshelf.release_signing import sign_release, verify_release
from jmshelf.updates import SeriesUpdateChecker


def test_progress_updates_preserve_downloads_and_read_counts() -> None:
    db = LibraryDatabase()
    comic = db.upsert_comic({"sourceId": "701", "storageKind": "remote"})
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(db.update_comic, comic["id"], {"progressPage": i}) for i in range(30)]
        futures += [pool.submit(db.record_comic_open, comic["id"]) for _ in range(30)]
        futures += [pool.submit(db.upsert_comic, {"id": comic["id"], "rootPath": "D:/saved", "storageKind": "download", "pageCount": 80})]
        for future in futures:
            future.result()
    final = db.get_comic(comic["id"])
    assert final["rootPath"] == "D:/saved"
    assert final["storageKind"] == "download"
    assert final["readCount"] == 30
    db.close()


def test_progress_snapshot_cannot_overwrite_a_concurrent_download() -> None:
    db = LibraryDatabase()
    comic = db.upsert_comic({"sourceId": "701", "rootPath": "", "storageKind": "remote"})
    original_get = db.get_comic
    captured, release, downloaded = threading.Event(), threading.Event(), threading.Event()
    paused = []
    def get(comic_id):
        result = original_get(comic_id)
        if threading.current_thread().name == "progress" and not paused:
            paused.append(True)
            captured.set()
            assert release.wait(3)
        return result
    db.get_comic = get
    progress = threading.Thread(name="progress", target=lambda: db.update_comic(comic["id"], {"progressPage": 2}))
    def download():
        db.upsert_comic({"sourceId": "701", "rootPath": "D:/saved", "pageCount": 80, "storageKind": "download"})
        downloaded.set()
    progress.start()
    assert captured.wait(3)
    worker = threading.Thread(target=download)
    worker.start()
    downloaded.wait(.1)
    release.set()
    progress.join(3); worker.join(3)
    assert db.get_comic(comic["id"])["rootPath"] == "D:/saved"
    assert db.get_comic(comic["id"])["progressPage"] == 2
    db.close()


def test_storage_priority_preserves_saved_files_and_remote_metadata() -> None:
    db = LibraryDatabase()
    original = db.upsert_comic({"sourceId": "701", "rootPath": "D:/saved", "storageKind": "download", "pageCount": 80})
    cached = db.upsert_comic({"sourceId": "701", "rootPath": "D:/cache", "storageKind": "cache", "pageCount": 4}, preserve_storage=True)
    refreshed = db.upsert_comic({"sourceId": "701", "storageKind": "remote", "tags": ["new"]}, preserve_storage=True)
    assert cached["rootPath"] == original["rootPath"]
    assert refreshed["storageKind"] == "download" and refreshed["tags"] == ["new"]
    db.close()


def test_cache_finishing_after_download_keeps_saved_storage(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    db = app.state.database
    db.upsert_comic({"sourceId": "701", "pageCount": 1})
    entered, finish = threading.Event(), threading.Event()
    def download(source_id, output_path, progress=None, selected_source_ids=None):
        if selected_source_ids:
            entered.set()
            assert finish.wait(3)
        root = Path(output_path) / source_id
        root.mkdir(parents=True, exist_ok=True)
        (root / "1.jpg").write_bytes(b"image")
        return {"items": [{"sourceId": source_id, "rootPath": str(root), "pageCount": 1}]}
    app.state.provider.download = download
    app.state.caches.enqueue("701", tmp_path / "data" / "cache", context={"selectedSourceIds": ["701"]})
    assert entered.wait(3)
    app.state.downloads.enqueue("701", tmp_path / "saved")
    deadline = time.monotonic() + 3
    while app.state.downloads.snapshot()["items"][0]["status"] != "completed" and time.monotonic() < deadline:
        time.sleep(.01)
    finish.set()
    while app.state.caches.snapshot()["items"][0]["status"] != "completed" and time.monotonic() < deadline:
        time.sleep(.01)
    final = db.get_comic_by_source("701")
    assert final["storageKind"] == "download"
    assert Path(final["rootPath"]).is_relative_to(tmp_path / "saved")
    app.state.caches.close(); app.state.downloads.close(); db.close()


def test_recent_unread_and_reader_next_cache_survive_pruning(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    db = app.state.database
    created = []
    for i in range(10):
        folder = tmp_path / "cache" / f"JM{800+i}"
        folder.mkdir(parents=True)
        (folder / "1.jpg").write_bytes(b"image")
        created.append(db.upsert_comic({"sourceId": str(800+i), "seriesId": "800", "chapterIndex": i+1,
            "rootPath": str(folder), "storageKind": "cache", "pageCount": 1,
            "lastReadAt": "2025-01-01T00:00:00+00:00" if i < 8 else None}))
    db.ensure_source_series("800")
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    endpoints["/api/activity/focus"]({"scope": "library", "comicId": created[0]["id"]})
    app.state.prune_library_cache()
    assert db.get_comic(created[0]["id"])["rootPath"]
    assert db.get_comic(created[1]["id"])["rootPath"]
    assert db.get_comic(created[-1]["id"])["rootPath"]
    assert sum(c["storageKind"] == "cache" for c in db.list_comics()) == 8
    db.close()


@pytest.mark.parametrize("headers", [
    {"host": "attacker.example"},
    {"origin": "https://attacker.example"},
    {"origin": "null"},
    {"sec-fetch-site": "cross-site"},
])
def test_local_service_rejects_foreign_requests(tmp_path: Path, headers: dict) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/settings", headers=headers).status_code == 403
        assert client.post("/api/activity/focus", headers={**headers, "x-jmshelf-request": "1"}, json={}).status_code == 403


@pytest.mark.parametrize("entry", ["JmShelf/C:/probe.txt", "JmShelf/dir/file.txt:stream", "JmShelf/NUL", "JmShelf/.. /probe.txt"])
def test_windows_zip_paths_cannot_escape_staging(tmp_path: Path, entry: str) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(entry, b"bad")
    with pytest.raises(ValueError, match="不安全路径"):
        AppUpdateManager("1.3.6", tmp_path)._extract_package(archive, tmp_path)


def test_complete_but_expired_metadata_is_refreshed(tmp_path: Path) -> None:
    db = LibraryDatabase()
    db.upsert_online_metadata({"albumId": "701", "detailsComplete": True,
        "fetchedAt": "2020-01-01T00:00:00Z", "episodeManifest": [{"sourceId": "701"}]})
    provider = JmcomicProvider(db, tmp_path)
    calls = []
    album = SimpleNamespace(id="701", title="new", episode_list=[("701", "1", "old"), ("702", "2", "new")],
                            pub_date="2020-01-01", update_date="2026-10-13")
    def fetch(source_id):
        calls.append(source_id)
        return album
    provider._new_html_client = lambda **_kwargs: SimpleNamespace(get_album_detail=fetch)
    result = provider.online_metadata(["701"])["items"][0]
    assert calls == ["701"]
    assert result["metadataFresh"] and len(result["episodeManifest"]) == 2
    db.close()


def test_new_search_update_date_invalidates_old_chapter_manifest() -> None:
    db = LibraryDatabase()
    db.upsert_online_metadata({"albumId": "701", "detailsComplete": True, "updatedAtSource": "2026-01-01",
                              "episodeManifest": [{"sourceId": "701"}]})
    updated = db.upsert_online_metadata({"albumId": "701", "updatedAtSource": "2026-01-02"})
    assert updated["detailsComplete"] is False
    assert updated["episodeManifest"] == []
    db.close()


def test_custom_date_filter_keeps_remote_pagination(tmp_path: Path) -> None:
    db = LibraryDatabase()
    provider = JmcomicProvider(db, tmp_path)
    provider._run_query = lambda *_args, **_kwargs: SimpleNamespace(
        content=[("701", {"pub_date": "2026-01-01"})], page_count=50, page_size=80, total=4000)
    result = provider.search("fixture", time_range="custom", date_from="2020-01-01", date_to="2020-12-31")
    assert result["items"] == [] and result["pageCount"] == 50 and result["matchedOnPage"] == 0
    db.close()


def test_custom_dates_use_html_when_api_search_omits_dates(tmp_path: Path) -> None:
    db = LibraryDatabase()
    provider = JmcomicProvider(db, tmp_path)
    provider._run_query = lambda *_a, **_kw: SimpleNamespace(content=[("701", {})], page_count=50, page_size=80, total=4000)
    album = SimpleNamespace(id="701", title="fixture", pub_date="2020-05-01", update_date="2020-05-01", episode_list=[("701", "1", "")])
    provider._new_html_client = lambda **_kw: SimpleNamespace(get_album_detail=lambda _source: album)
    result = provider.search("fixture", time_range="custom", date_from="2020-01-01", date_to="2020-12-31")
    assert result["items"][0]["publishedAt"] == "2020-05-01"
    assert result["pageCount"] == 50
    db.close()


def test_account_switch_discards_old_favorites_in_flight(tmp_path: Path) -> None:
    db = LibraryDatabase()
    provider = JmcomicProvider(db, tmp_path)
    provider.credentials.save({"username": "A", "password": "fixture", "cookies": {"AVS": "A"}})
    entered, release = threading.Event(), threading.Event()
    class Client(dict):
        def favorite_folder(self, **kwargs):
            if kwargs["username"] == "A":
                entered.set()
                assert release.wait(3)
            return SimpleNamespace(page_count=1, total=1, content=[("701", {"name": kwargs["username"]})])
        def login(self, username, _password):
            self["cookies"] = {"AVS": username}
            return SimpleNamespace(res_data={"username": username})
    provider._run_query = lambda operation, **_kwargs: operation(Client())
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(provider.favorites)
        assert entered.wait(3)
        provider.login("B", "fixture")
        release.set()
        with pytest.raises(ValueError, match="账号已变化"):
            old.result(timeout=3)
    assert provider.favorites()["username"] == "B"
    db.close()


def test_cancelled_preview_reopens_with_new_worker(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def download(source_id, output_path, progress=None, selected_source_ids=None, stream_first_pages=0,
                 lookup_hint=None, foreground=False, max_pages=0, cancel_event=None, transfer_priority=None):
        calls.append(source_id)
        entered.set()
        assert release.wait(3)
        if cancel_event.is_set():
            raise RuntimeError("cancelled")
        folder = Path(output_path) / "JM701"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "1.jpg").write_bytes(b"image")
        finished.set()
        return {"items": [{"sourceId": "701", "rootPath": str(folder), "pageCount": 1}]}
    app.state.provider.download = download
    post = next(r.endpoint for r in app.routes if r.path == "/api/online/{album_id}/preview" and "POST" in r.methods)
    get = next(r.endpoint for r in app.routes if r.path == "/api/online/{album_id}/preview" and "GET" in r.methods)
    cancel = next(r.endpoint for r in app.routes if r.path == "/api/online/{album_id}/preview/cancel")
    post("701", {})
    assert entered.wait(3)
    cancel("701")
    post("701", {})
    release.set()
    assert finished.wait(3)
    deadline = time.monotonic() + 3
    while get("701")["status"] == "loading" and time.monotonic() < deadline:
        time.sleep(.01)
    assert len(calls) == 2 and get("701")["status"] == "ready"
    app.state.database.close()


def test_preview_retention_counts_chapters_and_expires_idle_files(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    preview = tmp_path / "cache" / "previews" / "JM701"
    for chapter in range(7):
        root = preview / f"JM{701+chapter}"
        root.mkdir(parents=True)
        (root / "1.jpg").write_bytes(b"image")
    app.state.prune_preview_cache("")
    assert len(list(preview.rglob("1.jpg"))) == 4
    for page in preview.rglob("1.jpg"):
        os.utime(page.parent, (time.time()-3700, time.time()-3700))
    app.state.prune_preview_cache("")
    assert not list(preview.rglob("1.jpg"))
    app.state.database.close()


def test_ui_preferences_survive_reload_and_concurrent_changes(tmp_path: Path) -> None:
    path = tmp_path / "ui.json"
    prefs = UiPreferences(path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(prefs.update, {"jmshelf-theme": "light"})
        b = pool.submit(prefs.update, {"jmshelf-reader-mode": "book"})
        a.result(); b.result()
    assert UiPreferences(path).snapshot()["values"] == {"jmshelf-theme": "light", "jmshelf-reader-mode": "book"}


def test_enqueue_failure_persists_and_retries_before_a_day() -> None:
    db = LibraryDatabase()
    db.upsert_comic({"sourceId": "701", "seriesId": "701"})
    series = db.ensure_source_series("701")
    provider = SimpleNamespace(inspect_series=lambda *_args, **_kwargs: {"items": [{"sourceId": "701"}, {"sourceId": "702"}]})
    def failing(_result):
        raise RuntimeError("queue failure")
    checker = SeriesUpdateChecker(db, provider, enqueue_updates=failing)
    checker.check_all()
    stored = db.get_library_series(series["id"])
    assert stored["updateError"] == "queue failure"
    assert checker.is_due(stored, datetime.now(UTC) + timedelta(minutes=16))
    db.close()


def test_startup_update_timeout_reports_pending_and_keeps_scheduler_alive() -> None:
    db = LibraryDatabase()
    db.upsert_comic({"sourceId": "701", "seriesId": "701"})
    db.ensure_source_series("701")
    provider = SimpleNamespace(inspect_series=lambda *_args, **_kwargs: {"items": [{"sourceId": "701"}, {"sourceId": "702"}]})
    checker = SeriesUpdateChecker(db, provider, initial_delay_seconds=0, poll_seconds=60,
        enqueue_updates=lambda _r: [{"id": "queued"}], task_status=lambda _t: "queued", update_wait_seconds=.02)
    checker.start()
    deadline = time.monotonic() + 3
    while checker.startup_snapshot()["state"] != "complete" and time.monotonic() < deadline:
        time.sleep(.01)
    snapshot = checker.startup_snapshot()
    assert snapshot["state"] == "complete" and snapshot["updatePendingCount"] == 1
    assert snapshot["updateFailedCount"] == 0
    checker.close(); db.close()


def test_partial_enqueue_preserves_created_tasks_and_counts_failures(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    db = app.state.database
    db.upsert_comic({"sourceId": "701", "seriesId": "701"})
    db.ensure_source_series("701")
    app.state.provider.inspect_series = lambda *_a, **_kw: {"items": [
        {"sourceId": "701"}, {"sourceId": "702"}, {"sourceId": "703"},
    ]}
    def enqueue(source_id, *_args, **_kwargs):
        if source_id == "703":
            raise RuntimeError("queue failure")
        return {"id": "task-702", "sourceId": source_id}, True
    app.state.caches.enqueue = enqueue
    checker = app.state.series_updates
    checker.initial_delay_seconds = 0
    checker.update_wait_seconds = 0
    checker.task_status = lambda _task: "queued"
    checker.start()
    deadline = time.monotonic() + 3
    while checker.startup_snapshot()["state"] != "complete" and time.monotonic() < deadline:
        time.sleep(.01)
    summary = checker.startup_snapshot()
    assert summary["updates"][0]["tasks"][0]["id"] == "task-702"
    assert summary["updateFailedCount"] == 1
    assert summary["updatePendingCount"] == 1
    assert "703" in db.list_library_series()[0]["updateError"]
    checker.close(); db.close()


def test_series_lookup_and_existing_subscriptions_are_bounded() -> None:
    db = LibraryDatabase()
    for i in range(100):
        source = str(800+i)
        db.upsert_comic({"sourceId": source, "seriesId": source})
        series = db.ensure_source_series(source)
    selects = []
    db.connection.set_trace_callback(lambda sql: selects.append(sql) if sql.lstrip().upper().startswith("SELECT") else None)
    assert db.get_library_series(series["id"])
    assert len(selects) <= 3
    selects.clear()
    db.ensure_source_subscriptions()
    assert len(selects) == 1
    db.close()


def test_release_signature_rejects_changed_content_and_wrong_publisher() -> None:
    key = ECC.generate(curve="Ed25519")
    trusted = base64.b64encode(key.public_key().export_key(format="raw")).decode()
    package = {"file": "JmShelf-windows-x64-1.3.7.zip", "size": 100, "sha256": "A"*64}
    signed = sign_release({"schemaVersion": 1, "version": "1.3.7", "packages": [package]}, key.export_key(format="DER"))
    verify_release(signed, "1.3.7", package, public_key=trusted)
    other = base64.b64encode(ECC.generate(curve="Ed25519").public_key().export_key(format="raw")).decode()
    with pytest.raises(ValueError, match="签名"):
        verify_release(signed, "1.3.7", package, public_key=other)
    with pytest.raises(ValueError, match="签名"):
        verify_release(signed, "1.3.7", {**package, "sha256": "B"*64}, public_key=trusted)
    signed["payload"]["packages"][0]["size"] = 101
    with pytest.raises(ValueError, match="签名"):
        verify_release(signed, "1.3.7", package, public_key=trusted)


def test_concurrent_update_requests_create_only_one_download_worker(tmp_path: Path) -> None:
    manager = AppUpdateManager("1.3.6", tmp_path)
    manager.manifest = {"version": "1.3.7"}
    manager.status.update(state="available", available=True)
    entered, release = threading.Event(), threading.Event()
    workers = []
    def download(_manifest):
        workers.append(True)
        entered.set()
        assert release.wait(3)
    manager._download = download
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [pool.submit(manager.start_download) for _ in range(8)]
        for result in results:
            result.result()
    assert entered.wait(3)
    release.set()
    assert len(workers) == 1


def test_windows_fixed_and_string_versions_match_project() -> None:
    root = Path(__file__).parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    resource = (root / "packaging" / "version-info.txt").read_text(encoding="utf-8")
    numeric = tuple(map(int, version.split("."))) + (0,)
    assert f"filevers={numeric}" in resource and f"prodvers={numeric}" in resource
    assert f"u'FileVersion', u'{version}'" in resource
