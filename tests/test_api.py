from pathlib import Path
import threading
import time

import pytest
from fastapi.testclient import TestClient

from jmshelf import __version__
import jmshelf.main as main_module
from jmshelf.main import _series_update_output, create_app


HEADERS = {"X-JmShelf-Request": "1"}


def test_download_concurrency_is_fully_application_managed(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        settings = client.get("/api/settings").json()
    assert "chapterConcurrency" not in settings
    assert "imageConcurrency" not in settings


def test_online_results_persist_incrementally_and_reuse_cached_tags(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    app.state.database.upsert_online_metadata({
        "albumId": "701",
        "title": "缓存作品",
        "authors": ["缓存作者"],
        "tags": ["缓存标签"],
        "publishedAt": "2026-09-20",
        "detailsComplete": True,
        "fetchedAt": "2026-09-20T00:00:00+00:00",
    })
    app.state.provider.search = lambda *_args, **_kwargs: {
        "items": [
            {"albumId": "701", "title": "缓存作品", "authors": [], "tags": []},
            {"albumId": "702", "title": "新作品", "authors": ["新作者"], "tags": ["新标签"]},
        ],
        "page": 1,
        "pageCount": 1,
        "total": 2,
    }

    with TestClient(app) as client:
        response = client.get("/api/online/search?q=test")
        assert response.status_code == 200
        items = response.json()["items"]
        assert items[0]["tags"] == ["缓存标签"]
        assert items[0]["authors"] == ["缓存作者"]
        cached = app.state.database.get_online_metadata(["701", "702", "999"])
        assert [item["albumId"] for item in cached] == ["701", "702"]
        assert cached[1]["tags"] == ["新标签"]


def test_online_temp_cache_clear_keeps_full_covers_and_shelf_cache(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    app.state.database.upsert_online_metadata({
        "albumId": "701", "title": "临时索引", "tags": ["标签"], "detailsComplete": True,
    })
    thumbnail = app.state.data_root / "covers" / "thumbnails" / "701.jpg"
    full_cover = app.state.data_root / "covers" / "chapters" / "701.jpg"
    shelf_page = app.state.data_root / "cache" / "JM701" / "1.jpg"
    for path in (thumbnail, full_cover, shelf_page):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")

    with TestClient(app) as client:
        response = client.delete("/api/online/cache", headers=HEADERS)
        assert app.state.database.get_online_metadata(["701"]) == []

    assert response.status_code == 200
    assert response.json()["metadataRows"] == 1
    assert not thumbnail.exists()
    assert full_cover.exists()
    assert shelf_page.exists()


def test_recommendation_api_forwards_repeated_excluded_tags(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    received: dict[str, object] = {}

    def recommendation_feed(**kwargs):
        received.update(kwargs)
        return {"items": [], "batch": 0, "batchCount": 1, "profile": None}

    app.state.provider.recommendation_feed = recommendation_feed
    with TestClient(app) as client:
        response = client.get(
            "/api/online/recommendations?excludeTag=TagA&excludeTag=taga&excludeTag=%E4%B8%AD%E6%96%87"
        )

    assert response.status_code == 200
    assert received["excluded_tags"] == ["TagA", "taga", "中文"]


def test_online_preferences_are_persisted_to_local_file(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    with TestClient(create_app(data_root)) as client:
        initial = client.get("/api/online/preferences").json()
        assert initial["persisted"] is False
        updated = client.patch(
            "/api/online/preferences",
            headers=HEADERS,
            json={"excludedRecommendationTags": ["NTR", "ntr", "剧情"]},
        )
        assert updated.status_code == 200
        assert updated.json()["excludedRecommendationTags"] == ["NTR", "剧情"]
    assert (data_root / "jmonline-preferences.json").is_file()
    with TestClient(create_app(data_root)) as client:
        restored = client.get("/api/online/preferences").json()
        assert restored["persisted"] is True
        assert restored["excludedRecommendationTags"] == ["NTR", "剧情"]


def test_source_lookup_marks_existing_chapters_before_adding(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    app.state.database.upsert_comic({"sourceId": "701", "title": "已有章节"})
    app.state.provider.lookup = lambda *_args, **_kwargs: {
        "queryId": "700",
        "seriesId": "700",
        "seriesTitle": "查询去重",
        "items": [
            {"sourceId": "701", "title": "第一话", "chapterIndex": 1},
            {"sourceId": "702", "title": "第二话", "chapterIndex": 2},
        ],
    }

    with TestClient(app) as client:
        response = client.post("/api/source/lookup", headers=HEADERS, json={"plate": "700"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["existingCount"] == 1
        assert payload["inShelf"] is False
        assert payload["items"][0]["inShelf"] is True
        assert payload["items"][0]["existingComicIds"]
        assert payload["items"][1]["inShelf"] is False


def test_python_health_and_mutation_guard(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    assert "/api/window/to-tray" in app.openapi()["paths"]
    assert "/api/app/update" in app.openapi()["paths"]
    assert "/api/app/update/check" in app.openapi()["paths"]
    assert "/api/app/update/download" in app.openapi()["paths"]
    assert "/api/app/update/apply" in app.openapi()["paths"]
    assert "/api/app/about" in app.openapi()["paths"]
    assert "/api/provider/favorites" in app.openapi()["paths"]
    assert "/api/provider/favorites/{album_id}/shelf" in app.openapi()["paths"]
    assert "/api/online/search" in app.openapi()["paths"]
    assert "/api/online/recommendations" in app.openapi()["paths"]
    assert "/api/online/preferences" in app.openapi()["paths"]
    assert "/api/activity/focus" in app.openapi()["paths"]
    assert "/api/online/cache" in app.openapi()["paths"]
    assert "/api/online/{album_id}/preview/preload" not in app.openapi()["paths"]
    assert "/api/online/{album_id}/preview/cancel" in app.openapi()["paths"]
    assert "/media/source-thumbnail/{source_id}" in app.openapi()["paths"]
    assert "/api/online/{album_id}/shelf" in app.openapi()["paths"]
    assert "/api/comics/{comic_id}/read" in app.openapi()["paths"]
    assert "/api/library-series/startup-check" in app.openapi()["paths"]
    assert "/api/caches" in app.openapi()["paths"]
    assert "/api/caches/clear" in app.openapi()["paths"]
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.json()["backend"] == "python"
        update = client.get("/api/app/update")
        assert update.json()["currentVersion"] == __version__
        assert client.get("/api/app/about").json()["version"] == __version__
        assert client.post("/api/collections", json={"name": "blocked"}).status_code == 403


def test_unicode_round_trip_has_no_mojibake(tmp_path: Path) -> None:
    payload = {
        "sourceId": "350234",
        "title": "测试标题・作者名",
        "authors": ["作者甲", "サークル乙"],
        "tags": ["中文", "全彩"],
        "sourceStatus": "matched",
    }
    with TestClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/comics", headers=HEADERS, json=payload)
        assert created.status_code == 201
        result = client.get("/api/comics").json()[0]
        assert result["title"] == payload["title"]
        assert result["authors"] == payload["authors"]
        assert result["tags"] == payload["tags"]
        assert "�" not in created.text


def test_reader_open_count_is_persisted_once_per_session_request(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        comic = client.post("/api/comics", headers=HEADERS, json={"title": "常读作品"}).json()
        first = client.post(f"/api/comics/{comic['id']}/read", headers=HEADERS)
        second = client.post(f"/api/comics/{comic['id']}/read", headers=HEADERS)
        assert first.json()["readCount"] == 1
        assert second.json()["readCount"] == 2
        assert second.json()["lastReadAt"]


def test_collection_create_rename_and_delete(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/collections", headers=HEADERS, json={"name": "旧名称"}).json()
        renamed = client.patch(
            f"/api/collections/{created['id']}", headers=HEADERS, json={"name": "新名称", "parentId": None}
        )
        assert renamed.json()["name"] == "新名称"
        deleted = client.delete(f"/api/collections/{created['id']}", headers=HEADERS)
        assert deleted.status_code == 200
        assert client.get("/api/collections").json() == []


def test_settings_persist_default_download_path_and_proxy(tmp_path: Path) -> None:
    download_path = tmp_path / "downloads"
    download_path.mkdir()
    with TestClient(create_app(tmp_path / "data")) as client:
        updated = client.patch(
            "/api/settings",
            headers=HEADERS,
            json={
                "downloadPath": str(download_path),
                "proxy": "127.0.0.1:7890",
                "sanityMode": True,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["downloadPath"] == str(download_path)
        assert updated.json()["proxy"] == "http://127.0.0.1:7890"
        saved = client.get("/api/settings").json()
        assert saved["proxy"] == "http://127.0.0.1:7890"
        assert "chapterConcurrency" not in saved
        assert "imageConcurrency" not in saved
        assert saved["sanityMode"] is True


def test_local_cover_can_be_replaced_with_a_valid_image(tmp_path: Path) -> None:
    cover = tmp_path / "custom-cover.png"
    cover.write_bytes(b"image")
    invalid = tmp_path / "not-an-image.txt"
    invalid.write_text("no", encoding="utf-8")
    with TestClient(create_app(tmp_path / "data")) as client:
        comic = client.post("/api/comics", headers=HEADERS, json={"title": "自导入本子"}).json()
        updated = client.patch(
            f"/api/comics/{comic['id']}", headers=HEADERS, json={"coverPath": str(cover)},
        )
        assert updated.status_code == 200
        assert updated.json()["coverPath"] == str(cover.resolve())
        rejected = client.patch(
            f"/api/comics/{comic['id']}", headers=HEADERS, json={"coverPath": str(invalid)},
        )
        assert rejected.status_code == 400


def test_settings_reject_invalid_proxy(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        response = client.patch(
            "/api/settings",
            headers=HEADERS,
            json={"proxy": "not a proxy"},
        )
        assert response.status_code == 400


def test_legacy_image_concurrency_payload_is_ignored(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        response = client.patch(
            "/api/settings",
            headers=HEADERS,
            json={"imageConcurrency": 33},
        )
        assert response.status_code == 200
        assert "imageConcurrency" not in response.json()
        assert app.state.database.get_setting("download.imageConcurrency", "") == ""


def test_download_uses_saved_default_path_when_request_omits_one(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    captured: dict[str, str] = {}

    def fake_download(source_id, output_path):
        captured["sourceId"] = source_id
        captured["outputPath"] = str(output_path)
        root = Path(output_path) / f"JM{source_id}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "1.jpg").write_bytes(b"image")
        return {"ok": True, "items": [{"sourceId": source_id, "rootPath": str(root)}], "outputPath": str(output_path)}

    app.state.provider.download = fake_download
    default_path = tmp_path / "default-downloads"
    with TestClient(app) as client:
        client.patch(
            "/api/settings",
            headers=HEADERS,
            json={"downloadPath": str(default_path)},
        )
        response = client.post(
            "/api/source/download",
            headers=HEADERS,
            json={"plate": "JM1019294"},
        )
        assert response.status_code == 200
        assert captured == {"sourceId": "1019294", "outputPath": str(default_path.resolve())}


def test_library_series_api_create_list_and_dissolve(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        first = client.post("/api/comics", headers=HEADERS, json={"title": "第一本"}).json()
        second = client.post("/api/comics", headers=HEADERS, json={"title": "第二本"}).json()
        created = client.post(
            "/api/library-series",
            headers=HEADERS,
            json={"comicIds": [second["id"], first["id"]]},
        )
        assert created.status_code == 201
        assert created.json()["displayName"] == "第二本"
        assert created.json()["memberIds"] == [second["id"], first["id"]]
        listed = client.get("/api/library-series").json()
        assert len(listed) == 1
        dissolved = client.delete(f"/api/library-series/{listed[0]['id']}", headers=HEADERS)
        assert dissolved.status_code == 200
        assert len(client.get("/api/comics").json()) == 2


def test_adding_one_source_comic_immediately_creates_hidden_update_subscription(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        comic = client.post(
            "/api/comics",
            headers=HEADERS,
            json={
                "sourceId": "1019294",
                "seriesId": "1019294",
                "chapterIndex": 1,
                "title": "当前只有一话",
                "sourceStatus": "matched",
            },
        ).json()

        series = client.get("/api/library-series").json()
        assert len(series) == 1
        assert series[0]["memberIds"] == [comic["id"]]
        assert series[0]["displayAsSeries"] is False
        assert series[0]["updateSupported"] is True


def test_delete_comic_removes_record_and_local_storage(tmp_path: Path) -> None:
    comic_root = tmp_path / "downloads" / "JM123456" / "测试本子"
    comic_root.mkdir(parents=True)
    (comic_root / "001.jpg").write_bytes(b"image")
    with TestClient(create_app(tmp_path / "data")) as client:
        comic = client.post(
            "/api/comics",
            headers=HEADERS,
            json={"title": "测试本子", "rootPath": str(comic_root), "coverPath": str(comic_root / "001.jpg")},
        ).json()
        deleted = client.delete(f"/api/comics/{comic['id']}?deleteFiles=true", headers=HEADERS)
        assert deleted.status_code == 200
        assert deleted.json()["deletedCount"] == 1
        assert not comic_root.exists()
        assert client.get(f"/api/comics/{comic['id']}").status_code == 404


def test_remove_comic_from_shelf_preserves_local_storage(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    root = tmp_path / "library" / "book"
    root.mkdir(parents=True)
    (root / "1.jpg").write_bytes(b"image")
    comic = app.state.database.upsert_comic({"title": "保留文件", "rootPath": str(root), "pageCount": 1})

    with TestClient(app) as client:
        removed = client.delete(f"/api/comics/{comic['id']}", headers=HEADERS)
        assert removed.status_code == 200
        assert removed.json()["deletedPaths"] == []
        assert client.get(f"/api/comics/{comic['id']}").status_code == 404
        assert root.is_dir()
        assert (root / "1.jpg").is_file()


def test_dissolve_series_can_delete_all_comics_and_local_storage(tmp_path: Path) -> None:
    roots = [tmp_path / "downloads" / f"JM{source}" / f"第 {index} 本" for index, source in enumerate((111111, 222222), 1)]
    for root in roots:
        root.mkdir(parents=True)
        (root / "001.jpg").write_bytes(b"image")
    with TestClient(create_app(tmp_path / "data")) as client:
        comics = [
            client.post(
                "/api/comics",
                headers=HEADERS,
                json={"title": root.name, "rootPath": str(root), "coverPath": str(root / "001.jpg")},
            ).json()
            for root in roots
        ]
        series = client.post(
            "/api/library-series",
            headers=HEADERS,
            json={"comicIds": [comic["id"] for comic in comics]},
        ).json()

        deleted = client.delete(
            f"/api/library-series/{series['id']}?deleteComics=true",
            headers=HEADERS,
        )

        assert deleted.status_code == 200
        assert deleted.json()["deletedCount"] == 2
        assert all(not root.exists() for root in roots)
        assert client.get("/api/comics").json() == []
        assert client.get("/api/library-series").json() == []


def test_delete_comic_refuses_directory_shared_with_another_record(tmp_path: Path) -> None:
    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    (shared_root / "001.jpg").write_bytes(b"image")
    with TestClient(create_app(tmp_path / "data")) as client:
        first = client.post("/api/comics", headers=HEADERS, json={"title": "第一本", "rootPath": str(shared_root)}).json()
        client.post("/api/comics", headers=HEADERS, json={"title": "第二本", "rootPath": str(shared_root)})

        deleted = client.delete(f"/api/comics/{first['id']}?deleteFiles=true", headers=HEADERS)

        assert deleted.status_code == 400
        assert shared_root.exists()
        assert len(client.get("/api/comics").json()) == 2


def test_reader_reuses_one_page_manifest_for_all_image_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comic_root = tmp_path / "downloads" / "JM123456"
    comic_root.mkdir(parents=True)
    for index in range(3):
        comic_root.joinpath(f"{index:03}.jpg").write_bytes(b"image")
    scans = 0
    original_find_images = main_module.find_images

    def counted_find_images(root: Path):
        nonlocal scans
        scans += 1
        return original_find_images(root)

    monkeypatch.setattr(main_module, "find_images", counted_find_images)
    with TestClient(create_app(tmp_path / "data")) as client:
        comic = client.post(
            "/api/comics",
            headers=HEADERS,
            json={"title": "缓存索引测试", "rootPath": str(comic_root)},
        ).json()
        pages = client.get(f"/api/comics/{comic['id']}/pages")
        assert pages.status_code == 200
        assert len(pages.json()) == 3
        for index in range(3):
            assert client.get(f"/media/page/{comic['id']}/{index}").status_code == 200
    assert scans == 1


def test_batch_delete_restores_all_roots_when_staging_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = [tmp_path / "downloads" / f"JM{source}" for source in (111111, 222222)]
    for root in roots:
        root.mkdir(parents=True)
        root.joinpath("001.jpg").write_bytes(b"image")
    with TestClient(create_app(tmp_path / "data"), raise_server_exceptions=False) as client:
        comics = [
            client.post(
                "/api/comics",
                headers=HEADERS,
                json={"title": root.name, "rootPath": str(root)},
            ).json()
            for root in roots
        ]
        series = client.post(
            "/api/library-series",
            headers=HEADERS,
            json={"comicIds": [comic["id"] for comic in comics]},
        ).json()
        original_replace = Path.replace
        staged_count = 0

        def fail_second_stage(path: Path, target: Path):
            nonlocal staged_count
            if path in roots and ".jmshelf-delete-" in Path(target).name:
                staged_count += 1
                if staged_count == 2:
                    raise OSError("simulated staging failure")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", fail_second_stage)
        deleted = client.delete(
            f"/api/library-series/{series['id']}?deleteComics=true",
            headers=HEADERS,
        )
        assert deleted.status_code == 500
        assert all(root.is_dir() for root in roots)
        assert len(client.get("/api/comics").json()) == 2


def test_delete_refuses_comic_while_its_cache_task_is_active(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    started = threading.Event()
    release = threading.Event()

    def slow_download(source_id, output_path, progress=None, selected_source_ids=None):
        started.set()
        release.wait(2)
        root = Path(output_path) / f"JM{source_id}"
        root.mkdir(parents=True, exist_ok=True)
        root.joinpath("001.jpg").write_bytes(b"image")
        return {
            "items": [{
                "sourceId": str((selected_source_ids or [source_id])[0]),
                "seriesId": str(source_id),
                "rootPath": str(root),
            }],
        }

    app.state.provider.download = slow_download
    with TestClient(app) as client:
        comic = client.post(
            "/api/comics",
            headers=HEADERS,
            json={
                "sourceId": "1019294",
                "seriesId": "1019294",
                "title": "正在缓存",
                "storageKind": "remote",
            },
        ).json()
        queued = client.post(
            "/api/caches",
            headers=HEADERS,
            json={"comicIds": [comic["id"]]},
        )
        assert queued.status_code == 202
        assert started.wait(1)
        deleted = client.delete(f"/api/comics/{comic['id']}?deleteFiles=true", headers=HEADERS)
        assert deleted.status_code == 400
        assert "正在进行" in deleted.json()["error"]
        assert client.get(f"/api/comics/{comic['id']}").status_code == 200
        release.set()


def test_collection_drop_endpoint_adds_comics_without_removing_other_memberships(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        first = client.post("/api/comics", headers=HEADERS, json={"title": "第一本"}).json()
        second = client.post("/api/comics", headers=HEADERS, json={"title": "第二本"}).json()
        old_collection = client.post("/api/collections", headers=HEADERS, json={"name": "旧目录"}).json()
        target = client.post("/api/collections", headers=HEADERS, json={"name": "目标目录"}).json()
        client.put(
            f"/api/comics/{first['id']}/collections",
            headers=HEADERS,
            json={"collectionIds": [old_collection["id"]]},
        )

        response = client.post(
            f"/api/collections/{target['id']}/items",
            headers=HEADERS,
            json={"comicIds": [first["id"], second["id"]]},
        )

        assert response.status_code == 200
        assert response.json()["addedCount"] == 2
        comics = {comic["id"]: comic for comic in client.get("/api/comics").json()}
        assert set(comics[first["id"]]["collections"]) == {old_collection["id"], target["id"]}
        assert comics[second["id"]]["collections"] == [target["id"]]


def test_background_download_queue_accepts_work_and_reports_completion(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")

    def fake_download(source_id, output_path, progress=None):
        if progress:
            progress({"title": "测试下载", "phase": "下载图片", "progress": 55, "totalItems": 1})
        root = Path(output_path) / f"JM{source_id}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "1.jpg").write_bytes(b"image")
        return {"ok": True, "items": [{"sourceId": source_id, "rootPath": str(root)}], "outputPath": str(output_path), "queryId": source_id}

    app.state.provider.download = fake_download
    with TestClient(app) as client:
        response = client.post(
            "/api/downloads",
            headers=HEADERS,
            json={"plate": "JM1019294", "outputPath": str(tmp_path / "downloads")},
        )
        assert response.status_code == 202
        assert response.json()["created"] is True

        deadline = time.monotonic() + 2
        task = response.json()["task"]
        while time.monotonic() < deadline:
            task = client.get("/api/downloads").json()["items"][0]
            if task["status"] == "completed":
                break
            time.sleep(0.01)
        assert task["status"] == "completed"
        assert task["progress"] == 100
        assert task["title"] == "测试下载"


def test_downloaded_chapter_enters_library_before_whole_series_finishes(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    first_ready = threading.Event()
    release_second = threading.Event()

    def fake_download(source_id, output_path, progress=None, on_chapter_complete=None):
        items = []
        for index, photo_id in enumerate((source_id, "1096733"), start=1):
            if index == 2:
                release_second.wait(timeout=4)
            root = Path(output_path) / f"JM{source_id}" / f"JM{photo_id}"
            root.mkdir(parents=True, exist_ok=True)
            (root / "1.jpg").write_bytes(b"image")
            item = {
                "sourceId": photo_id,
                "seriesId": source_id,
                "chapterIndex": index,
                "title": f"第 {index} 话",
                "rootPath": str(root),
            }
            items.append(item)
            on_chapter_complete(item)
            if index == 1:
                first_ready.set()
        return {"ok": True, "items": items, "outputPath": str(output_path)}

    app.state.provider.download = fake_download
    try:
        with TestClient(app) as client:
            response = client.post("/api/downloads", headers=HEADERS, json={
                "plate": "JM1019294", "outputPath": str(tmp_path / "downloads"),
            })
            assert response.status_code == 202
            assert first_ready.wait(timeout=2)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                task = client.get("/api/downloads").json()["items"][0]
                if task.get("importedItems") == 1:
                    break
                time.sleep(0.01)
            assert task["status"] == "running"
            assert task["importedItems"] == 1
            assert {comic["sourceId"] for comic in client.get("/api/comics").json()} == {"1019294"}

            release_second.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                task = client.get("/api/downloads").json()["items"][0]
                if task["status"] == "completed":
                    break
                time.sleep(0.01)
            assert task["status"] == "completed"
            assert {comic["sourceId"] for comic in client.get("/api/comics").json()} == {
                "1019294", "1096733",
            }
    finally:
        release_second.set()


def test_shelf_items_cache_to_app_storage_and_can_be_cleared(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    captured: dict[str, object] = {}

    def fake_download(source_id, output_path, progress=None, selected_source_ids=None):
        captured["sourceId"] = source_id
        captured["outputPath"] = str(output_path)
        captured["selectedSourceIds"] = selected_source_ids
        items = []
        for index, photo_id in enumerate(selected_source_ids or [source_id], start=1):
            root = Path(output_path) / f"JM{source_id}" / f"JM{photo_id}"
            root.mkdir(parents=True, exist_ok=True)
            (root / "1.jpg").write_bytes(b"image")
            items.append({
                "sourceId": photo_id,
                "title": f"第 {index} 话",
                "seriesId": source_id,
                "seriesTitle": "缓存测试",
                "chapterIndex": index,
                "rootPath": str(root),
            })
        return {"ok": True, "items": items, "outputPath": str(output_path), "queryId": source_id}

    app.state.provider.download = fake_download
    with TestClient(app) as client:
        comics = [
            client.post("/api/comics", headers=HEADERS, json={
                "sourceId": source_id,
                "seriesId": "1019294",
                "seriesTitle": "缓存测试",
                "chapterIndex": index,
                "title": f"第 {index} 话",
                "sourceStatus": "matched",
            }).json()
            for index, source_id in enumerate(("1019294", "1096733"), start=1)
        ]
        queued = client.post(
            "/api/caches",
            headers=HEADERS,
            json={"comicIds": [comic["id"] for comic in comics]},
        )
        assert queued.status_code == 202
        assert queued.json()["createdCount"] == 1

        deadline = time.monotonic() + 2
        task = queued.json()["tasks"][0]
        while time.monotonic() < deadline:
            task = client.get("/api/caches").json()["items"][0]
            if task["status"] == "completed":
                break
            time.sleep(0.01)
        assert task["status"] == "completed"
        assert captured == {
            "sourceId": "1019294",
            "outputPath": str((tmp_path / "data" / "cache").resolve()),
            "selectedSourceIds": ["1019294", "1096733"],
        }

        cached = client.get("/api/comics").json()
        assert all(comic["storageKind"] == "cache" for comic in cached)
        assert all(Path(comic["rootPath"]).is_relative_to(tmp_path / "data" / "cache") for comic in cached)
        assert client.get(f"/api/comics/{cached[0]['id']}/pages").json()[0]["name"] == "1.jpg"

        cleared = client.post(
            "/api/caches/clear",
            headers=HEADERS,
            json={"comicIds": [comic["id"] for comic in cached]},
        )
        assert cleared.status_code == 200
        assert cleared.json()["clearedCount"] == 2
        remaining = client.get("/api/comics").json()
        assert all(comic["storageKind"] == "remote" and not comic["rootPath"] for comic in remaining)
        assert all(not Path(path).exists() for path in cleared.json()["deletedPaths"])


def test_reading_source_series_starts_next_chapter_cache(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    started = threading.Event()
    captured: dict[str, object] = {}

    def fake_download(
        source_id, output_path, progress=None, selected_source_ids=None,
        stream_first_pages=0, lookup_hint=None, transfer_priority="background",
    ):
        captured.update({
            "sourceId": source_id,
            "selectedSourceIds": selected_source_ids,
            "streamFirstPages": stream_first_pages,
            "lookupHint": lookup_hint,
            "transferPriority": transfer_priority,
        })
        next_source_id = str((selected_source_ids or [source_id])[0])
        root = Path(output_path) / f"JM{source_id}" / f"JM{next_source_id}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "1.jpg").write_bytes(b"image")
        started.set()
        return {"items": [{
            "sourceId": next_source_id,
            "seriesId": str(source_id),
            "chapterIndex": 2,
            "title": "第二话",
            "rootPath": str(root),
        }]}

    app.state.provider.download = fake_download
    with TestClient(app) as client:
        first = client.post("/api/comics", headers=HEADERS, json={
            "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一话",
        }).json()
        second = client.post("/api/comics", headers=HEADERS, json={
            "sourceId": "1096733", "seriesId": "1019294", "chapterIndex": 2,
            "title": "第二话", "pageCount": 1,
        }).json()

        response = client.post(f"/api/comics/{first['id']}/read", headers=HEADERS)
        assert response.status_code == 200
        assert started.wait(1)
        assert {key: captured[key] for key in (
            "sourceId", "selectedSourceIds", "streamFirstPages", "transferPriority",
        )} == {
            "sourceId": "1019294",
            "selectedSourceIds": ["1096733"],
            "streamFirstPages": 4,
            "transferPriority": "reader_next",
        }
        assert captured["lookupHint"]["items"][0]["sourceId"] == "1096733"
        assert captured["lookupHint"]["items"][0]["title"] == "第二话"

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            tasks = client.get("/api/caches").json()["items"]
            if tasks and tasks[0]["status"] == "completed":
                break
            time.sleep(0.01)
        assert tasks[0]["status"] == "completed"
        assert client.get(f"/api/comics/{second['id']}").json()["storageKind"] == "cache"

        client.post(f"/api/comics/{second['id']}/read", headers=HEADERS)
        assert len(client.get("/api/caches").json()["items"]) == 1


def test_reading_source_series_reuses_existing_next_chapter_task(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    started = threading.Event()
    release = threading.Event()

    def slow_download(source_id, output_path, progress=None, selected_source_ids=None, stream_first_pages=0):
        started.set()
        release.wait(2)
        next_source_id = str((selected_source_ids or [source_id])[0])
        root = Path(output_path) / f"JM{source_id}" / f"JM{next_source_id}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "1.jpg").write_bytes(b"image")
        return {"items": [{
            "sourceId": next_source_id,
            "seriesId": str(source_id),
            "chapterIndex": 2,
            "rootPath": str(root),
        }]}

    app.state.provider.download = slow_download
    with TestClient(app) as client:
        first = client.post("/api/comics", headers=HEADERS, json={
            "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一话",
        }).json()
        second = client.post("/api/comics", headers=HEADERS, json={
            "sourceId": "1096733", "seriesId": "1019294", "chapterIndex": 2, "title": "第二话",
        }).json()
        queued = client.post(
            "/api/caches",
            headers=HEADERS,
            json={"comicIds": [second["id"]], "progressive": True},
        ).json()
        assert started.wait(1)

        response = client.post(f"/api/comics/{first['id']}/read", headers=HEADERS)
        tasks = client.get("/api/caches").json()["items"]
        assert response.status_code == 200
        assert len(tasks) == 1
        assert tasks[0]["id"] == queued["tasks"][0]["id"]
        reopened = client.post(
            "/api/caches",
            headers=HEADERS,
            json={"comicIds": [second["id"]], "progressive": True, "priorityComicId": second["id"]},
        ).json()
        assert reopened["createdCount"] == 0
        assert reopened["tasks"][0]["id"] == queued["tasks"][0]["id"]
        release.set()


def test_cached_comic_can_be_deleted_without_exposing_the_cache_root(tmp_path: Path) -> None:
    cache_root = tmp_path / "data" / "cache"
    comic_root = cache_root / "JM1019294" / "JM1019294" / "测试本子"
    comic_root.mkdir(parents=True)
    (comic_root / "1.jpg").write_bytes(b"image")
    with TestClient(create_app(tmp_path / "data")) as client:
        comic = client.post("/api/comics", headers=HEADERS, json={
            "sourceId": "1019294",
            "title": "测试本子",
            "rootPath": str(comic_root),
            "coverPath": str(comic_root / "1.jpg"),
            "storageKind": "cache",
        }).json()
        deleted = client.delete(f"/api/comics/{comic['id']}?deleteFiles=true", headers=HEADERS)
        assert deleted.status_code == 200
        assert not comic_root.exists()
        assert cache_root.exists()


def test_library_cache_keeps_eight_recent_chapters_across_source_series(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    app = create_app(data_root)
    cache_root = data_root / "cache"
    with TestClient(app) as client:
        created = []
        for index in range(9):
            source_id = str(920000 + index)
            root = cache_root / "JM920000" / f"JM{source_id}" / f"第 {index + 1} 话"
            root.mkdir(parents=True)
            (root / "1.jpg").write_bytes(b"image")
            created.append(client.post("/api/comics", headers=HEADERS, json={
                "sourceId": source_id,
                "seriesId": "920000" if index < 5 else source_id,
                "seriesTitle": "混合缓存池",
                "chapterIndex": index + 1,
                "title": f"第 {index + 1} 话",
                "rootPath": str(root),
                "coverPath": str(root / "1.jpg"),
                "storageKind": "cache",
                "sourceStatus": "matched",
            }).json())

        result = app.state.prune_library_cache()
        assert result["clearedCount"] == 1
        remaining = client.get("/api/comics").json()
        assert sum(comic["storageKind"] == "cache" for comic in remaining) == 8
        assert sum(comic["storageKind"] == "remote" for comic in remaining) == 1


def test_failed_download_can_be_retried(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    calls = {"count": 0}

    def flaky_download(source_id, output_path, progress=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("临时网络错误")
        root = Path(output_path) / f"JM{source_id}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "1.jpg").write_bytes(b"image")
        return {
            "ok": True,
            "items": [{"sourceId": source_id, "rootPath": str(root)}],
            "outputPath": str(output_path),
            "queryId": source_id,
        }

    app.state.provider.download = flaky_download
    with TestClient(app) as client:
        created = client.post("/api/downloads", headers=HEADERS, json={"plate": "JM1019294"}).json()["task"]
        deadline = time.monotonic() + 2
        failed = created
        while time.monotonic() < deadline:
            failed = next(item for item in client.get("/api/downloads").json()["items"] if item["id"] == created["id"])
            if failed["status"] == "failed":
                break
            time.sleep(0.01)
        assert failed["status"] == "failed"

        retried = client.post(f"/api/downloads/{created['id']}/retry", headers=HEADERS)
        assert retried.status_code == 202
        retry_id = retried.json()["task"]["id"]
        deadline = time.monotonic() + 2
        retry_task = retried.json()["task"]
        while time.monotonic() < deadline:
            retry_task = next(item for item in client.get("/api/downloads").json()["items"] if item["id"] == retry_id)
            if retry_task["status"] == "completed":
                break
            time.sleep(0.01)
        assert retry_task["status"] == "completed"
        assert calls["count"] == 2


def test_multi_chapter_download_is_automatically_grouped_as_library_series(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")

    def fake_download(_source_id, output_path):
        items = []
        for index, source_id in enumerate(("1019294", "1096733"), start=1):
            root = Path(output_path) / "JM1019294" / f"JM{source_id}"
            root.mkdir(parents=True, exist_ok=True)
            (root / "1.jpg").write_bytes(b"image")
            items.append({
                "sourceId": source_id,
                "title": f"第 {index} 话",
                "seriesId": "1019294",
                "seriesTitle": "测试系列",
                "chapterIndex": index,
                "rootPath": str(root),
            })
        return {"ok": True, "items": items, "outputPath": str(output_path)}

    app.state.provider.download = fake_download
    with TestClient(app) as client:
        response = client.post(
            "/api/source/download",
            headers=HEADERS,
            json={"plate": "JM1019294", "outputPath": str(tmp_path / "downloads")},
        )
        assert response.status_code == 200
        assert len(response.json()["comics"]) == 2
        assert response.json()["librarySeries"] is not None
        series = client.get("/api/library-series").json()
        assert len(series) == 1
        assert len(series[0]["memberIds"]) == 2
        assert series[0]["kind"] == "source"


def test_series_refresh_detects_and_queues_only_missing_chapters(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    app.state.provider.inspect_series = lambda _source_id: {
        "seriesId": "1019294",
        "seriesTitle": "测试系列",
        "itemCount": 3,
        "items": [
            {"sourceId": "1019294", "title": "第一话", "chapterIndex": 1},
            {"sourceId": "1096733", "title": "第二话", "chapterIndex": 2},
            {"sourceId": "1200000", "title": "第三话", "chapterIndex": 3},
        ],
    }
    def fake_update_download(source_id, output_path, progress=None):
        root = Path(output_path) / f"JM{source_id}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "1.jpg").write_bytes(b"image")
        return {
            "ok": True,
            "queryId": source_id,
            "items": [{"sourceId": source_id, "seriesId": "1019294", "chapterIndex": 3, "rootPath": str(root)}],
            "outputPath": str(output_path),
        }

    app.state.provider.download = fake_update_download
    with TestClient(app) as client:
        first = client.post("/api/comics", headers=HEADERS, json={
            "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一话",
        }).json()
        second = client.post("/api/comics", headers=HEADERS, json={
            "sourceId": "1096733", "seriesId": "1019294", "chapterIndex": 2, "title": "第二话",
        }).json()
        series = client.get("/api/library-series").json()[0]

        response = client.post(f"/api/library-series/{series['id']}/refresh", headers=HEADERS)
        assert response.status_code == 202
        assert response.json()["newCount"] == 1
        assert response.json()["queuedCount"] == 1
        assert response.json()["tasks"][0]["sourceId"] == "1200000"
        refreshed = client.get("/api/library-series").json()[0]
        assert refreshed["updateAvailableCount"] == 1
        assert refreshed["lastCheckedAt"] is not None


def test_series_update_output_uses_outer_series_folder(tmp_path: Path) -> None:
    outer = tmp_path / "JM1055467"
    member_root = outer / "JM1055467" / "第一话"
    expected = _series_update_output(
        {"sourceSeriesId": "1055467", "members": [{"rootPath": str(member_root)}]},
        "",
        tmp_path / "fallback",
    )
    assert expected == outer.resolve()


def test_jm_favorites_merge_downloaded_items_and_leave_remote_items_downloadable(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    local_root = tmp_path / "downloads" / "JM701"
    local_root.mkdir(parents=True)
    app.state.provider.favorites = lambda fresh=False: {
        "authenticated": True,
        "username": "tester",
        "total": 2,
        "fetchedAt": "2026-09-08T00:00:00+00:00",
        "items": [
            {"albumId": "701", "title": "已下载", "authors": [], "tags": [], "coverUrl": "/media/source-cover/701"},
            {"albumId": "702", "title": "未下载", "authors": ["作者"], "tags": ["标签"], "coverUrl": "/media/source-cover/702"},
        ],
    }
    with TestClient(app) as client:
        local = client.post("/api/comics", headers=HEADERS, json={
            "sourceId": "701", "title": "已下载", "rootPath": str(local_root),
        }).json()
        response = client.get("/api/provider/favorites")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["visibleTotal"] == 2
    assert [comic["id"] for comic in body["localComics"]] == [local["id"]]
    assert [item["albumId"] for item in body["remoteItems"]] == ["702"]


def test_jm_favorite_can_be_added_to_shelf_and_cached_for_reading(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")

    def fake_lookup(source_id, defer_covers=False):
        assert source_id == "702"
        assert defer_covers is True
        return {
            "queryId": "702",
            "seriesId": "702",
            "seriesTitle": "收藏缓存测试",
            "items": [{
                "sourceId": "702",
                "seriesId": "702",
                "seriesTitle": "收藏缓存测试",
                "chapterIndex": 1,
                "title": "收藏缓存测试",
                "authors": ["作者"],
                "tags": ["标签"],
                "pageCount": 1,
            }],
        }

    def fake_download(source_id, output_path, progress=None, selected_source_ids=None):
        assert source_id == "702"
        assert selected_source_ids == ["702"]
        root = Path(output_path) / "JM702" / "JM702"
        root.mkdir(parents=True, exist_ok=True)
        (root / "1.jpg").write_bytes(b"image")
        return {"items": [{
            "sourceId": "702",
            "seriesId": "702",
            "seriesTitle": "收藏缓存测试",
            "chapterIndex": 1,
            "title": "收藏缓存测试",
            "rootPath": str(root),
        }]}

    app.state.provider.lookup = fake_lookup
    app.state.provider.download = fake_download
    with TestClient(app) as client:
        queued = client.post("/api/provider/favorites/702/shelf", headers=HEADERS)
        assert queued.status_code == 202
        assert queued.json()["comicCount"] == 1
        assert queued.json()["createdCount"] == 1

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            task = client.get("/api/caches").json()["items"][0]
            if task["status"] == "completed":
                break
            time.sleep(0.01)
        assert task["status"] == "completed"
        comic = client.get("/api/comics").json()[0]
        assert comic["storageKind"] == "cache"
        assert client.get(f"/api/comics/{comic['id']}/pages").json()[0]["name"] == "1.jpg"


def test_progressive_cache_opens_after_head_pages_while_tail_keeps_downloading(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    head_ready = threading.Event()
    fifth_ready = threading.Event()
    release_tail = threading.Event()
    release_rest = threading.Event()

    def fake_download(
        source_id,
        output_path,
        progress=None,
        selected_source_ids=None,
        stream_first_pages=0,
    ):
        assert source_id == "702"
        assert selected_source_ids == ["702"]
        assert stream_first_pages == 4
        root = Path(output_path) / "JM702" / "JM702"
        root.mkdir(parents=True, exist_ok=True)
        for index in range(1, 5):
            (root / f"{index}.jpg").write_bytes(b"image")
        if progress:
            progress({
                "phase": "前 4 页已就绪，可开始阅读",
                "streamReady": True,
                "streamingRoot": str(root),
                "activeSourceId": "702",
                "availablePages": 4,
                "expectedPages": 8,
            })
        head_ready.set()
        release_tail.wait(2)
        (root / "5.jpg").write_bytes(b"image")
        if progress:
            progress({
                "phase": "第 5 页已就绪",
                "streamReady": True,
                "streamingRoot": str(root),
                "activeSourceId": "702",
                "availablePages": 5,
                "expectedPages": 8,
            })
        fifth_ready.set()
        release_rest.wait(2)
        for index in range(6, 9):
            (root / f"{index}.jpg").write_bytes(b"image")
        return {"items": [{
            "sourceId": "702",
            "seriesId": "702",
            "seriesTitle": "流式缓存",
            "chapterIndex": 1,
            "title": "流式缓存",
            "pageCount": 8,
            "rootPath": str(root),
        }]}

    app.state.provider.download = fake_download
    with TestClient(app) as client:
        try:
            comic = client.post("/api/comics", headers=HEADERS, json={
                "sourceId": "702",
                "seriesId": "702",
                "seriesTitle": "流式缓存",
                "chapterIndex": 1,
                "title": "流式缓存",
                "pageCount": 8,
                "storageKind": "remote",
            }).json()
            queued = client.post("/api/caches", headers=HEADERS, json={
                "comicIds": [comic["id"]],
                "progressive": True,
            })
            assert queued.status_code == 202
            assert head_ready.wait(1)

            task = client.get("/api/caches").json()["items"][0]
            assert task["status"] == "running"
            assert task["streamReady"] is True
            assert task["availablePages"] == 4
            assert len(client.get(f"/api/comics/{comic['id']}/pages").json()) == 4

            release_tail.set()
            assert fifth_ready.wait(1)
            assert len(client.get(f"/api/comics/{comic['id']}/pages").json()) == 5
            release_rest.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                task = client.get("/api/caches").json()["items"][0]
                if task["status"] == "completed":
                    break
                time.sleep(0.01)
            assert task["status"] == "completed"
            assert len(client.get(f"/api/comics/{comic['id']}/pages").json()) == 8
        finally:
            release_tail.set()
            release_rest.set()


def test_online_preview_returns_head_pages_without_adding_to_shelf(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    head_ready = threading.Event()
    second_ready = threading.Event()
    release_tail = threading.Event()
    release_rest = threading.Event()

    app.state.provider.inspect_series = lambda source_id: {
        "queryId": source_id,
        "seriesId": source_id,
        "seriesTitle": "在线流式预览",
        "itemCount": 1,
        "items": [{
            "sourceId": source_id,
            "title": "在线流式预览",
            "chapterIndex": 1,
        }],
    }

    def fake_download(
        source_id,
        output_path,
        progress=None,
        selected_source_ids=None,
        stream_first_pages=0,
        lookup_hint=None,
        foreground=False,
    ):
        assert source_id == "703"
        assert selected_source_ids == ["703"]
        assert stream_first_pages == 4
        assert lookup_hint["items"][0]["sourceId"] == "703"
        assert foreground is True
        root = Path(output_path) / "JM703"
        root.mkdir(parents=True, exist_ok=True)
        for index in range(1, 5):
            (root / f"{index}.jpg").write_bytes(b"image")
        if progress:
            progress({
                "streamReady": True,
                "streamingRoot": str(root),
                "activeSourceId": "703",
                "availablePages": 4,
                "expectedPages": 8,
            })
        head_ready.set()
        release_tail.wait(2)
        for index in range(5, 8):
            (root / f"{index}.jpg").write_bytes(b"image")
        if progress:
            progress({
                "streamReady": True,
                "streamingRoot": str(root),
                "activeSourceId": "703",
                "availablePages": 7,
                "expectedPages": 8,
            })
        second_ready.set()
        release_rest.wait(2)
        (root / "8.jpg").write_bytes(b"image")
        return {"items": [{
            "sourceId": "703",
            "title": "在线流式预览",
            "pageCount": 8,
            "rootPath": str(root),
        }]}

    app.state.provider.download = fake_download
    with TestClient(app) as client:
        try:
            started = client.post("/api/online/703/preview", headers=HEADERS)
            assert started.status_code == 200
            assert head_ready.wait(1)
            preview = client.get("/api/online/703/preview").json()
            assert preview["status"] == "loading"
            assert preview["availablePages"] == 4
            assert len(preview["pages"]) == 4
            assert client.get(preview["pages"][0]["url"]).status_code == 200
            assert client.get("/api/comics").json() == []

            release_tail.set()
            assert second_ready.wait(1)
            preview = client.get("/api/online/703/preview").json()
            assert preview["status"] == "loading"
            assert preview["availablePages"] == 4
            assert len(preview["pages"]) == 4
            release_rest.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                preview = client.get("/api/online/703/preview").json()
                if preview["status"] == "ready":
                    break
                time.sleep(0.01)
            assert preview["status"] == "ready"
            assert preview["availablePages"] == 8
            assert len(preview["pages"]) == 8
        finally:
            release_tail.set()
            release_rest.set()


def test_online_preview_starts_on_explicit_open_and_supports_chapter_selection(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    captured: dict[str, object] = {}
    app.state.provider.inspect_series = lambda source_id: {
        "queryId": source_id,
        "seriesId": source_id,
        "seriesTitle": "卡片预热",
        "itemCount": 1,
        "items": [{"sourceId": source_id, "title": "卡片预热", "chapterIndex": 1}],
    }

    def fake_download(
        source_id,
        output_path,
        progress=None,
        selected_source_ids=None,
        stream_first_pages=0,
        lookup_hint=None,
        foreground=False,
        max_pages=0,
        cancel_event=None,
        transfer_priority="background",
    ):
        captured.update({
            "streamFirstPages": stream_first_pages,
            "foreground": foreground,
            "maxPages": max_pages,
            "priority": transfer_priority,
            "cancelEvent": cancel_event,
        })
        root = Path(output_path) / f"JM{source_id}"
        root.mkdir(parents=True, exist_ok=True)
        for index in range(1, 5):
            (root / f"{index}.jpg").write_bytes(b"image")
        selected_source_id = str((selected_source_ids or [source_id])[0])
        return {"items": [{
            "sourceId": selected_source_id,
            "title": "卡片预热",
            "pageCount": 12,
            "rootPath": str(root),
        }]}

    app.state.provider.download = fake_download
    with TestClient(app) as client:
        preview_root = app.state.data_root / "cache" / "previews"
        assert not preview_root.exists()
        started = client.post(
            "/api/online/704/preview",
            headers=HEADERS,
            json={"chapterId": "705"},
        )
        assert started.status_code == 200
        deadline = time.monotonic() + 2
        preview = started.json()
        while preview["status"] == "loading" and time.monotonic() < deadline:
            time.sleep(0.01)
            preview = client.get("/api/online/704/preview").json()
        assert preview["status"] == "ready"
        assert preview["sourceId"] == "705"
        assert len(preview["pages"]) == 4
        cancelled = client.post("/api/online/704/preview/cancel", headers=HEADERS)
        assert cancelled.json()["cancelled"] is True

    assert captured["streamFirstPages"] == 4
    assert captured["foreground"] is True
    assert captured["maxPages"] == 0
    assert captured["priority"] == "preview"
    assert captured["cancelEvent"].is_set()


def test_switching_preview_chapters_ignores_stale_worker_completion(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    first_started = threading.Event()
    release_first = threading.Event()

    def fake_download(source_id, output_path, selected_source_ids=None, **_kwargs):
        selected = str((selected_source_ids or [source_id])[0])
        root = Path(output_path) / f"JM{selected}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "1.jpg").write_bytes(selected.encode("ascii"))
        if selected == "705":
            first_started.set()
            release_first.wait(2)
        return {"items": [{
            "sourceId": selected,
            "title": f"章节 {selected}",
            "pageCount": 1,
            "rootPath": str(root),
        }]}

    app.state.provider.download = fake_download
    with TestClient(app) as client:
        try:
            first = client.post(
                "/api/online/704/preview",
                headers=HEADERS,
                json={"chapterId": "705"},
            )
            assert first.status_code == 200
            assert first_started.wait(1)

            second = client.post(
                "/api/online/704/preview",
                headers=HEADERS,
                json={"chapterId": "706"},
            )
            assert second.status_code == 200
            deadline = time.monotonic() + 2
            preview = second.json()
            while preview["status"] == "loading" and time.monotonic() < deadline:
                time.sleep(0.01)
                preview = client.get("/api/online/704/preview").json()
            assert preview["status"] == "ready"
            assert preview["sourceId"] == "706"

            release_first.set()
            time.sleep(0.05)
            preview = client.get("/api/online/704/preview").json()
            assert preview["status"] == "ready"
            assert preview["sourceId"] == "706"
        finally:
            release_first.set()


def test_preview_pruning_never_deletes_loading_sessions(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    release = threading.Event()
    created = {str(album_id): threading.Event() for album_id in range(800, 809)}

    app.state.provider.inspect_series = lambda source_id: {
        "queryId": source_id,
        "seriesId": source_id,
        "seriesTitle": f"预览 {source_id}",
        "itemCount": 1,
        "items": [{"sourceId": source_id, "title": f"预览 {source_id}", "chapterIndex": 1}],
    }

    def fake_download(
        source_id,
        output_path,
        progress=None,
        selected_source_ids=None,
        stream_first_pages=0,
        lookup_hint=None,
        foreground=False,
    ):
        root = Path(output_path) / f"JM{source_id}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "1.jpg").write_bytes(b"image")
        if progress:
            progress({
                "streamReady": True,
                "streamingRoot": str(root),
                "activeSourceId": source_id,
                "availablePages": 1,
                "expectedPages": 1,
            })
        created[source_id].set()
        release.wait(3)
        return {"items": [{
            "sourceId": source_id,
            "title": f"预览 {source_id}",
            "pageCount": 1,
            "rootPath": str(root),
        }]}

    app.state.provider.download = fake_download
    with TestClient(app) as client:
        try:
            for album_id, ready in created.items():
                response = client.post(f"/api/online/{album_id}/preview", headers=HEADERS)
                assert response.status_code == 200
                assert ready.wait(1)
            preview_root = app.state.data_root / "cache" / "previews"
            assert all((preview_root / f"JM{album_id}").is_dir() for album_id in created)
        finally:
            release.set()


def test_preview_cache_keeps_four_recent_albums(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")

    def fake_download(source_id, output_path, progress=None, selected_source_ids=None, **_kwargs):
        selected = str((selected_source_ids or [source_id])[0])
        root = Path(output_path) / f"JM{selected}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "1.jpg").write_bytes(b"image")
        return {"items": [{
            "sourceId": selected,
            "title": f"预览 {source_id}",
            "pageCount": 1,
            "rootPath": str(root),
        }]}

    app.state.provider.download = fake_download
    with TestClient(app) as client:
        for album_id in range(930000, 930005):
            started = client.post(f"/api/online/{album_id}/preview", headers=HEADERS, json={})
            assert started.status_code == 200
            deadline = time.monotonic() + 2
            preview = started.json()
            while preview["status"] == "loading" and time.monotonic() < deadline:
                time.sleep(0.01)
                preview = client.get(f"/api/online/{album_id}/preview").json()
            assert preview["status"] == "ready"

        preview_root = app.state.data_root / "cache" / "previews"
        assert len([folder for folder in preview_root.iterdir() if folder.is_dir()]) == 4
