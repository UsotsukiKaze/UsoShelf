from datetime import UTC, datetime, timedelta
import time

from jmshelf.db import LibraryDatabase
from jmshelf.updates import SeriesUpdateChecker


class StubProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def inspect_series(self, source_id: str):
        self.calls.append(source_id)
        return {
            "seriesId": source_id,
            "itemCount": 3,
            "items": [
                {"sourceId": "1019294", "title": "第一话", "chapterIndex": 1},
                {"sourceId": "1096733", "title": "第二话", "chapterIndex": 2},
                {"sourceId": "1200000", "title": "第三话", "chapterIndex": 3},
            ],
        }


def test_series_is_checked_once_per_day_and_records_new_chapters() -> None:
    database = LibraryDatabase()
    first = database.upsert_comic({
        "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一话",
    })
    second = database.upsert_comic({
        "sourceId": "1096733", "seriesId": "1019294", "chapterIndex": 2, "title": "第二话",
    })
    series = database.create_library_series([first["id"], second["id"]])
    provider = StubProvider()
    checker = SeriesUpdateChecker(database, provider)  # type: ignore[arg-type]

    assert checker.is_due(series) is True
    checked = checker.check(series["id"])
    assert provider.calls == ["1019294"]
    assert checked["newCount"] == 1
    refreshed = checked["series"]
    checked_at = datetime.fromisoformat(refreshed["lastCheckedAt"])
    assert checker.is_due(refreshed, checked_at + timedelta(hours=23)) is False
    assert checker.is_due(refreshed, checked_at + timedelta(days=1, seconds=1)) is True
    assert checked_at.tzinfo is not None and checked_at.tzinfo.utcoffset(checked_at) == UTC.utcoffset(checked_at)
    database.close()


def test_single_source_comic_is_checked_for_its_first_new_chapter() -> None:
    database = LibraryDatabase()
    first = database.upsert_comic({
        "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一话",
    })
    series = database.ensure_source_series("1019294")
    assert series is not None
    provider = StubProvider()
    checker = SeriesUpdateChecker(database, provider)  # type: ignore[arg-type]

    checked = checker.check(series["id"])

    assert checked["newCount"] == 2
    assert [item["sourceId"] for item in checked["newItems"]] == ["1096733", "1200000"]
    assert checked["series"]["memberIds"] == [first["id"]]
    checker.close()
    database.close()


def test_startup_check_runs_even_when_series_was_checked_recently() -> None:
    database = LibraryDatabase()
    first = database.upsert_comic({
        "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一话",
    })
    second = database.upsert_comic({
        "sourceId": "1096733", "seriesId": "1019294", "chapterIndex": 2, "title": "第二话",
    })
    series = database.create_library_series([first["id"], second["id"]])
    database.record_library_series_check(series["id"], [], checked_at=datetime.now(UTC).isoformat())
    provider = StubProvider()
    checker = SeriesUpdateChecker(database, provider)  # type: ignore[arg-type]

    assert checker.check_due() == []
    startup_results = checker.check_all()

    assert len(startup_results) == 1
    assert provider.calls == ["1019294"]
    checker.close()
    database.close()


def test_startup_worker_exposes_completed_update_summary() -> None:
    database = LibraryDatabase()
    first = database.upsert_comic({
        "sourceId": "1019294", "seriesId": "1019294", "chapterIndex": 1, "title": "第一话",
    })
    second = database.upsert_comic({
        "sourceId": "1096733", "seriesId": "1019294", "chapterIndex": 2, "title": "第二话",
    })
    series = database.create_library_series([first["id"], second["id"]])
    provider = StubProvider()
    checker = SeriesUpdateChecker(
        database,
        provider,  # type: ignore[arg-type]
        initial_delay_seconds=0,
        poll_seconds=60,
    )
    checker.start()
    deadline = time.monotonic() + 2
    snapshot = checker.startup_snapshot()
    while snapshot["state"] != "complete" and time.monotonic() < deadline:
        time.sleep(0.01)
        snapshot = checker.startup_snapshot()

    assert snapshot["state"] == "complete"
    assert snapshot["eligibleCount"] == 1
    assert snapshot["checkedCount"] == 1
    assert snapshot["failedCount"] == 0
    assert snapshot["updates"] == [{
        "seriesId": series["id"],
        "displayName": series["displayName"],
        "newCount": 1,
        "items": [{"sourceId": "1200000", "title": "第三话", "chapterIndex": 3}],
    }]
    checker.close()
    database.close()
