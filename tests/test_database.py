from pathlib import Path

import pytest

from jmshelf.db import LibraryDatabase
from jmshelf.importer import scan_import_root
from jmshelf.utils import normalize_plate


def test_nickname_is_display_title_without_changing_id() -> None:
    database = LibraryDatabase()
    created = database.upsert_comic({"sourceId": "123456", "title": "Original", "nickname": "My copy"})
    updated = database.update_comic(created["id"], {"nickname": "Renamed"})
    assert updated is not None
    assert updated["id"] == created["id"]
    assert updated["displayName"] == "Renamed"
    database.close()


def test_cover_privacy_preferences_are_persisted_and_clamped() -> None:
    database = LibraryDatabase()
    comic = database.upsert_comic({"title": "本地本子"})
    assert comic["coverPrivacyEnabled"] is True
    assert comic["coverPrivacyDirection"] == "below"
    updated = database.update_comic(comic["id"], {
        "coverPrivacyEnabled": False,
        "coverPrivacyDirection": "above",
        "coverPrivacyStart": 100,
    })
    assert updated is not None
    assert updated["coverPrivacyEnabled"] is False
    assert updated["coverPrivacyDirection"] == "above"
    assert updated["coverPrivacyStart"] == 85
    database.close()


def test_collection_delete_preserves_comic_and_unfiles_it() -> None:
    database = LibraryDatabase()
    comic = database.upsert_comic({"title": "Local comic", "rootPath": r"D:\Library\book"})
    collection = database.create_collection("待读")
    database.set_comic_collections(comic["id"], [collection["id"]])
    assert database.delete_collection(collection["id"])
    preserved = database.get_comic(comic["id"])
    assert preserved is not None
    assert preserved["collections"] == []
    database.close()


def test_collection_cannot_move_under_its_descendant() -> None:
    database = LibraryDatabase()
    parent = database.create_collection("父目录")
    child = database.create_collection("子目录", parent["id"])
    with pytest.raises(ValueError, match="子目录"):
        database.update_collection(parent["id"], parent_id=child["id"], parent_supplied=True)
    database.close()


def test_numeric_folder_is_pending_for_jm_matching(tmp_path: Path) -> None:
    comic = tmp_path / "350234"
    comic.mkdir()
    (comic / "10.jpg").write_bytes(b"x")
    (comic / "2.jpg").write_bytes(b"x")
    imported = scan_import_root(tmp_path)
    assert imported[0]["sourceId"] == "350234"
    assert imported[0]["sourceStatus"] == "pending"
    assert imported[0]["coverPath"].endswith("2.jpg")


def test_plate_parser_accepts_mixed_text() -> None:
    assert normalize_plate("JM 350234") == "350234"
    assert normalize_plate("推荐车牌：350234，已完结") == "350234"
    assert normalize_plate("12") is None


def test_online_metadata_cache_is_incremental_persistent_and_merge_safe(tmp_path: Path) -> None:
    database_path = tmp_path / "library.sqlite3"
    database = LibraryDatabase(database_path)
    assert database.upsert_online_metadata_many([{
        "albumId": "701",
        "title": "搜索标题",
        "tags": ["标签一"],
    }]) == 1
    assert database.get_online_metadata(["701", "999"])[0]["detailsComplete"] is False

    stored = database.upsert_online_metadata({
        "albumId": "701",
        "authors": ["作者"],
        "tags": ["标签一", "标签二"],
        "works": ["原作"],
        "actors": ["角色"],
        "publishedAt": "2026-09-20",
        "pageCount": 20,
        "firstPhotoId": "1701",
        "episodeManifest": [
            {"sourceId": "1701", "title": "第一话", "chapterIndex": 1},
            {"sourceId": "1702", "title": "第二话", "chapterIndex": 2},
        ],
        "detailsComplete": True,
        "fetchedAt": "2026-09-20T00:00:00+00:00",
    })
    assert stored is not None
    assert stored["title"] == "搜索标题"
    assert stored["tags"] == ["标签一", "标签二"]
    assert stored["works"] == ["原作"]
    assert stored["actors"] == ["角色"]
    assert stored["firstPhotoId"] == "1701"
    assert [item["sourceId"] for item in stored["episodeManifest"]] == ["1701", "1702"]
    assert stored["detailsComplete"] is True

    # A later sparse search hit must not erase hydrated tags or dates.
    assert database.upsert_online_metadata_many([{"albumId": "701", "title": "搜索标题"}]) == 0
    database.close()

    reopened = LibraryDatabase(database_path)
    cached = reopened.get_online_metadata(["701"])[0]
    assert cached["authors"] == ["作者"]
    assert cached["works"] == ["原作"]
    assert cached["actors"] == ["角色"]
    assert cached["publishedAt"] == "2026-09-20"
    assert cached["firstPhotoId"] == "1701"
    assert cached["episodeManifest"][1]["chapterIndex"] == 2
    reopened.close()


def test_series_relation_is_preserved_for_independent_photo_records() -> None:
    database = LibraryDatabase()
    comic = database.upsert_comic({
        "sourceId": "1096733",
        "title": "第二话",
        "seriesId": "1019294",
        "seriesTitle": "系列标题",
        "chapterIndex": 2,
    })
    assert comic["sourceId"] == "1096733"
    assert comic["seriesId"] == "1019294"
    assert comic["chapterIndex"] == 2
    database.close()


def test_fractional_chapter_index_is_preserved_and_sorted_numerically() -> None:
    database = LibraryDatabase()
    later = database.upsert_comic({
        "sourceId": "1200000", "seriesId": "1019294", "chapterIndex": "27.5", "title": "第 27.5 话",
    })
    earlier = database.upsert_comic({
        "sourceId": "1100000", "seriesId": "1019294", "chapterIndex": "1.2", "title": "第 1.2 话",
    })
    assert database.get_comic(later["id"])["chapterIndex"] == 27.5
    assert database.get_comic(earlier["id"])["chapterIndex"] == 1.2
    series = database.create_library_series([later["id"], earlier["id"]])
    expanded = database.ensure_source_series("1019294")
    assert expanded is not None
    assert expanded["memberIds"] == [earlier["id"], later["id"]]
    database.close()


def test_library_series_preserves_member_order_and_uses_first_name() -> None:
    database = LibraryDatabase()
    first = database.upsert_comic({"title": "第一本"})
    second = database.upsert_comic({"title": "第二本"})
    created = database.create_library_series([second["id"], first["id"]])
    assert created["displayName"] == "第二本"
    assert created["kind"] == "custom"
    assert created["memberIds"] == [second["id"], first["id"]]
    database.update_comic(second["id"], {"nickname": "系列主名称"})
    assert database.list_library_series()[0]["displayName"] == "系列主名称"
    database.close()


def test_native_source_series_has_distinct_kind_and_counts_once_in_collection() -> None:
    database = LibraryDatabase()
    first = database.upsert_comic({
        "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一 P",
    })
    second = database.upsert_comic({
        "sourceId": "1096733", "seriesId": "1019294", "chapterIndex": 2, "title": "第二 P",
    })
    series = database.ensure_source_series("1019294")
    assert series is not None
    assert series["kind"] == "source"
    assert series["isSourceSeries"] is True
    collection = database.create_collection("多 P")
    database.add_comics_to_collection(collection["id"], [first["id"], second["id"]])
    assert database.list_collections()[0]["comicCount"] == 1
    database.close()


def test_next_source_series_member_uses_native_chapter_order_only() -> None:
    database = LibraryDatabase()
    first = database.upsert_comic({
        "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一 P",
    })
    second = database.upsert_comic({
        "sourceId": "1096733", "seriesId": "1019294", "chapterIndex": 2, "title": "第二 P",
    })
    database.ensure_source_series("1019294")
    custom_first = database.upsert_comic({"sourceId": "800001", "title": "自建系列一"})
    custom_second = database.upsert_comic({"sourceId": "800002", "title": "自建系列二"})
    database.create_library_series([custom_first["id"], custom_second["id"]])

    assert database.get_next_source_series_member(first["id"])["id"] == second["id"]
    assert database.get_next_source_series_member(second["id"]) is None
    assert database.get_next_source_series_member(custom_first["id"]) is None
    database.close()


def test_single_source_comic_keeps_hidden_update_subscription() -> None:
    database = LibraryDatabase()
    comic = database.upsert_comic({
        "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一 P",
    })

    series = database.ensure_source_series("1019294")

    assert series is not None
    assert series["kind"] == "source"
    assert series["displayAsSeries"] is False
    assert series["memberIds"] == [comic["id"]]
    assert series["updateSupported"] is True
    database.close()


def test_existing_single_source_comic_subscription_is_backfilled() -> None:
    database = LibraryDatabase()
    comic = database.upsert_comic({
        "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一 P",
    })

    database.ensure_source_subscriptions()

    series = database.list_library_series()
    assert len(series) == 1
    assert series[0]["memberIds"] == [comic["id"]]
    assert series[0]["displayAsSeries"] is False
    database.close()


def test_library_series_resumes_the_most_recently_read_member() -> None:
    database = LibraryDatabase()
    first = database.upsert_comic({"title": "第一话", "pageCount": 20})
    second = database.upsert_comic({"title": "第二话", "pageCount": 30})
    created = database.create_library_series([first["id"], second["id"]])
    assert created["resumeComicId"] == first["id"]
    database.update_comic(first["id"], {"progressPage": 12, "lastReadAt": "2026-09-01T10:00:00+00:00"})
    database.update_comic(second["id"], {"progressPage": 7, "lastReadAt": "2026-09-02T10:00:00+00:00"})
    resumed = database.list_library_series()[0]
    assert resumed["resumeComicId"] == second["id"]
    assert resumed["resumePage"] == 7
    assert resumed["resumeLastReadAt"] == "2026-09-02T10:00:00+00:00"
    database.close()


def test_dissolving_library_series_preserves_comics() -> None:
    database = LibraryDatabase()
    first = database.upsert_comic({"title": "第一本"})
    second = database.upsert_comic({"title": "第二本"})
    created = database.create_library_series([first["id"], second["id"]])
    assert database.delete_library_series(created["id"])
    assert database.get_comic(first["id"]) is not None
    assert database.get_comic(second["id"]) is not None
    database.close()


def test_source_series_update_state_and_new_member_are_preserved() -> None:
    database = LibraryDatabase()
    first = database.upsert_comic({
        "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一话",
    })
    second = database.upsert_comic({
        "sourceId": "1096733", "seriesId": "1019294", "chapterIndex": 2, "title": "第二话",
    })
    series = database.create_library_series([first["id"], second["id"]])
    checked = database.record_library_series_check(series["id"], [{
        "sourceId": "1200000", "title": "第三话", "chapterIndex": 3,
    }])
    assert checked is not None
    assert checked["updateSupported"] is True
    assert checked["autoUpdateEnabled"] is True
    assert checked["updateAvailableCount"] == 1

    third = database.upsert_comic({
        "sourceId": "1200000", "seriesId": "1019294", "chapterIndex": 3, "title": "第三话",
    })
    expanded = database.ensure_source_series("1019294")
    assert expanded is not None
    assert expanded["memberIds"] == [first["id"], second["id"], third["id"]]
    assert expanded["updateAvailableCount"] == 0
    database.close()
