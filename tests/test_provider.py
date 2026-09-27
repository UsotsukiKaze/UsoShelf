from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from urllib.parse import urlparse
import threading

import pytest

from jmshelf.db import LibraryDatabase
from jmshelf.provider import JmcomicProvider, _AdaptiveTransferGate, _PriorityQueryGate


class FakePhoto:
    def __init__(self, photo_id: str, title: str, page_count: int, index: int) -> None:
        self.id = photo_id
        self.title = title
        self.album_id = "1019294"
        self.page_count = page_count
        self.index = index
        self.from_album = None

    def __len__(self) -> int:
        return self.page_count


class FakeClient:
    def __init__(self) -> None:
        self.album = SimpleNamespace(
            id="1019294",
            title="系列标题",
            episode_list=[("1019294", "1", ""), ("1096733", "2", "")],
            authors=["作者"],
            tags=["标签"],
            description="简介",
            pub_date="2026-01-01",
            update_date="2026-01-02",
        )
        self.photos = {
            "1019294": FakePhoto("1019294", "第一话", 38, 1),
            "1096733": FakePhoto("1096733", "第二话", 36, 2),
        }
        self.cover_calls: list[str] = []
        self.cover_sizes: list[str] = []
        self.detail_calls: list[str] = []
        self.album_calls: list[str] = []
        self.scramble_flags: list[bool] = []
        self.search_calls: list[tuple[str, str, dict]] = []

    def get_album_detail(self, _source_id: str):
        self.album_calls.append(_source_id)
        return self.album

    def get_photo_detail(self, photo_id: str, _fetch_album: bool, _fetch_scramble: bool):
        self.detail_calls.append(photo_id)
        self.scramble_flags.append(_fetch_scramble)
        return self.photos[photo_id]

    def download_album_cover(self, photo_id: str, path: str, size: str = "") -> None:
        self.cover_calls.append(photo_id)
        self.cover_sizes.append(size)
        Path(path).write_bytes(b"cover")

    def _search_page(self, kind: str, query: str, **kwargs):
        self.search_calls.append((kind, query, kwargs))
        return SimpleNamespace(
            content=[
                ("700001", {"name": "作者的新作", "author": ["作者"], "tags": ["标签", "全彩"]}),
                ("700002", {"name": "其他作品", "author": ["其他"], "tags": ["标签"]}),
            ],
            total=2,
            page_count=1,
            page_size=80,
        )

    def search_site(self, query: str, **kwargs):
        return self._search_page("site", query, **kwargs)

    def search_author(self, query: str, **kwargs):
        return self._search_page("author", query, **kwargs)

    def search_tag(self, query: str, **kwargs):
        return self._search_page("tag", query, **kwargs)


class FakeOption:
    def __init__(self, client: FakeClient, download_calls: list[str]) -> None:
        self.client = client
        self.download_calls = download_calls
        self.dir_rule = SimpleNamespace(base_dir="")
        self.download = SimpleNamespace(threading=SimpleNamespace(image=1))

    def new_jm_client(self, **kwargs) -> FakeClient:
        return self.client

    def download_photo(self, photo_id: str, **_kwargs):
        self.download_calls.append(photo_id)
        root = Path(self.dir_rule.base_dir) / f"photo-{photo_id}"
        root.mkdir(parents=True)
        detail = self.client.photos[photo_id]
        for index in range(len(detail)):
            (root / f"{index + 1}.jpg").write_bytes(b"image")
        detail.save_path = str(root)
        return SimpleNamespace(detail=detail, duration=0.1)


def build_provider(tmp_path: Path):
    database = LibraryDatabase()
    provider = JmcomicProvider(database, tmp_path)
    client = FakeClient()
    download_calls: list[str] = []
    provider._option = lambda: FakeOption(client, download_calls)  # type: ignore[method-assign]
    return provider, database, client, download_calls


def test_transfer_gate_borrows_idle_capacity_and_protects_foreground() -> None:
    gate = _AdaptiveTransferGate(total_limit=24, protected_background_limit=16)
    with ExitStack() as idle_transfers:
        for _ in range(24):
            idle_transfers.enter_context(gate.image())
        assert gate.snapshot()["activeBackground"] == 24

    gate.set_focus("online")
    entered = threading.Event()
    release = threading.Event()
    worker = None
    try:
        with ExitStack() as protected_transfers:
            for _ in range(9):
                protected_transfers.enter_context(gate.image())

            def wait_for_background_slot():
                with gate.image():
                    entered.set()
                    release.wait(2)

            worker = threading.Thread(target=wait_for_background_slot)
            worker.start()
            assert not entered.wait(0.1)
            with gate.image(foreground=True):
                snapshot = gate.snapshot()
                assert snapshot["activeTotal"] == 10
                assert snapshot["activeBackground"] == 9

            gate.set_focus("library")
            assert entered.wait(1)
            release.set()
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=2)


def test_transfer_gate_throttles_background_during_preview_without_interrupting_active_work() -> None:
    gate = _AdaptiveTransferGate(total_limit=6, protected_background_limit=4, preview_background_limit=2)
    entered = threading.Event()
    release = threading.Event()
    worker = None
    with gate.preview_session():
        with ExitStack() as active_background:
            for _ in range(2):
                active_background.enter_context(gate.image())

            def wait_for_background_slot():
                with gate.image():
                    entered.set()
                    release.wait(2)

            worker = threading.Thread(target=wait_for_background_slot)
            worker.start()
            assert not entered.wait(0.1)
            with gate.image(priority="streaming"):
                snapshot = gate.snapshot()
                assert snapshot["activeBackground"] == 2
                assert snapshot["activeStreaming"] == 1
                assert snapshot["activePreviews"] == 1

    try:
        assert entered.wait(1)
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=2)


def test_transfer_gate_chooses_image_parallelism_from_focus() -> None:
    gate = _AdaptiveTransferGate(total_limit=24, protected_background_limit=10, preview_background_limit=6)
    assert gate.recommended_parallelism("background") == 24
    gate.set_focus("online")
    assert gate.recommended_parallelism("background") == 9
    assert gate.recommended_parallelism("visible") == 15
    gate.set_focus("preview")
    assert gate.recommended_parallelism("background") == 6
    assert gate.recommended_parallelism("streaming") == 18
    assert gate.recommended_parallelism("preview") == 4


def test_query_gate_reserves_capacity_for_interactive_requests() -> None:
    gate = _PriorityQueryGate(total_limit=6, background_limit=4)
    background_entered = threading.Event()
    release_background = threading.Event()
    worker = None
    with ExitStack() as active_background:
        for _ in range(4):
            active_background.enter_context(gate.slot(background=True))

        def wait_for_background_slot():
            with gate.slot(background=True):
                background_entered.set()
                release_background.wait(2)

        worker = threading.Thread(target=wait_for_background_slot)
        worker.start()
        assert not background_entered.wait(0.1)
        with gate.slot():
            snapshot = gate.snapshot()
            assert snapshot["activeTotal"] == 5
            assert snapshot["activeBackground"] == 4

    try:
        assert background_entered.wait(1)
    finally:
        release_background.set()
        if worker is not None:
            worker.join(timeout=2)


def test_lookup_splits_series_into_independent_photo_ids_with_default_covers(tmp_path: Path) -> None:
    provider, database, client, _download_calls = build_provider(tmp_path)
    result = provider.lookup("1019294")
    assert [item["sourceId"] for item in result["items"]] == ["1019294", "1096733"]
    assert [item["pageCount"] for item in result["items"]] == [38, 36]
    assert sorted(client.cover_calls) == ["1019294", "1096733"]
    assert all(Path(item["coverPath"]).is_file() for item in result["items"])
    database.close()


def test_online_search_maps_web_filters_and_normalizes_results(tmp_path: Path) -> None:
    provider, database, client, _download_calls = build_provider(tmp_path)
    result = provider.search("+标签 -排除", mode="tag", order="views", time_range="week", category="doujin")
    assert result["total"] == 2
    assert result["items"][0]["albumId"] == "700001"
    assert result["items"][0]["authors"] == ["作者"]
    assert result["items"][0]["coverUrl"] == "/media/source-thumbnail/700001"
    assert result["items"][0]["directCoverUrl"].endswith("/700001_3x4.jpg")
    kind, query, arguments = client.search_calls[0]
    assert (kind, query) == ("tag", "+标签 -排除")
    assert arguments["order_by"] == "mv"
    assert arguments["time"] == "w"
    assert arguments["category"] == "doujin"
    database.close()


def test_recommendations_score_author_and_tag_overlap(tmp_path: Path) -> None:
    provider, database, _client, _download_calls = build_provider(tmp_path)
    result = provider.recommendations("1019294", limit=5)
    assert result["seed"]["sourceId"] == "1019294"
    assert result["profile"]["topTags"][0]["name"] == "标签"
    assert result["items"][0]["albumId"] == "700001"
    assert result["items"][0]["recommendationScore"] > result["items"][1]["recommendationScore"]
    assert "偏好作者" in result["items"][0]["recommendationReason"]
    database.close()


def test_recommendation_tag_filter_is_case_insensitive_and_runs_before_paging(tmp_path: Path) -> None:
    provider, database, _client, _download_calls = build_provider(tmp_path)
    provider.schedule_recommendation_refresh = lambda: {"refreshing": False}  # type: ignore[method-assign]
    provider._recommendation_snapshot = {
        "updatedAt": "2026-09-21T00:00:00+00:00",
        "sourceCount": 3,
        "result": {
            "profile": {"topTags": []},
            "items": [
                {"albumId": "1", "tags": [], "recommendationTags": ["TSUNDERE"], "recommendationScore": 30},
                {"albumId": "2", "tags": ["纯爱"], "recommendationScore": 20},
                {"albumId": "3", "tags": ["剧情"], "recommendationScore": 10},
            ],
        },
    }

    result = provider.recommendation_feed(limit=2, excluded_tags=["tsundere"])

    assert [item["albumId"] for item in result["items"]] == ["2", "3"]
    assert result["batchCount"] == 1
    assert result["excludedCount"] == 1
    assert result["excludedTags"] == ["tsundere"]
    database.close()


def test_series_inspection_is_lightweight_and_skips_photo_covers(tmp_path: Path) -> None:
    provider, database, client, _download_calls = build_provider(tmp_path)
    result = provider.inspect_series("1019294")
    assert [item["sourceId"] for item in result["items"]] == ["1019294", "1096733"]
    assert [item["chapterIndex"] for item in result["items"]] == [1, 2]
    assert client.detail_calls == []
    assert client.cover_calls == []
    database.close()


def test_preview_manifest_hint_is_local_and_optimistic_until_cached(tmp_path: Path) -> None:
    database = LibraryDatabase()
    provider = JmcomicProvider(database, tmp_path / "data")
    initial = provider.preview_manifest_hint("701")
    assert initial["items"][0]["sourceId"] == "701"
    assert initial["previewOptimistic"] is True

    database.upsert_online_metadata({
        "albumId": "701",
        "title": "缓存系列",
        "firstPhotoId": "702",
        "episodeManifest": [
            {"sourceId": "702", "title": "第一话", "chapterIndex": 1},
            {"sourceId": "703", "title": "第二话", "chapterIndex": 2},
        ],
    })
    cached = provider.preview_manifest_hint("701")
    assert cached["items"][0]["sourceId"] == "702"
    assert cached["itemCount"] == 2
    assert cached["previewOptimistic"] is False
    database.close()


def test_anonymous_option_skips_blocking_cookie_bootstrap_request(tmp_path: Path) -> None:
    database = LibraryDatabase()
    provider = JmcomicProvider(database, tmp_path / "data")
    metadata: dict[str, object] = {}

    class Option:
        def __init__(self) -> None:
            self.client = SimpleNamespace(postman=SimpleNamespace(meta_data=metadata))

        def update_cookies(self, cookies):
            metadata.setdefault("cookies", {}).update(cookies)

    option = Option()
    provider._module = lambda: SimpleNamespace(JmOption=SimpleNamespace(default=lambda: option))
    provider.credentials.load = lambda: None
    assert provider._option() is option
    assert metadata["cookies"] == {"jmshelf_client": "1"}
    assert provider.auth_status()["authenticated"] is False
    database.close()


def test_download_auto_detects_and_downloads_each_photo_id(tmp_path: Path) -> None:
    provider, database, _client, download_calls = build_provider(tmp_path / "data")
    ready: list[str] = []
    result = provider.download(
        "1019294", tmp_path / "downloads",
        on_chapter_complete=lambda item: ready.append(item["sourceId"]),
    )
    assert sorted(download_calls) == ["1019294", "1096733"]
    assert sorted(ready) == ["1019294", "1096733"]
    assert [item["sourceId"] for item in result["items"]] == ["1019294", "1096733"]
    assert all(Path(item["rootPath"]).is_dir() for item in result["items"])
    database.close()


def test_download_can_limit_cache_work_to_selected_photo_ids(tmp_path: Path) -> None:
    provider, database, _client, download_calls = build_provider(tmp_path / "data")
    result = provider.download(
        "1019294",
        tmp_path / "cache",
        selected_source_ids=["1096733"],
    )
    assert download_calls == ["1096733"]
    assert [item["sourceId"] for item in result["items"]] == ["1096733"]
    database.close()


def test_image_cdn_selection_spreads_load_and_cools_failed_nodes(tmp_path: Path) -> None:
    database = LibraryDatabase()
    provider = JmcomicProvider(database, tmp_path / "data")
    module = SimpleNamespace(JmModuleConfig=SimpleNamespace(DOMAIN_IMAGE_LIST=[]))
    first_routes = [provider._image_domains(module, spread=True)[0] for _ in range(6)]
    assert len(set(first_routes)) == 6

    failed = first_routes[0]
    provider._record_image_domain(failed, success=False)
    routes = provider._image_domains(module, spread=True)
    assert routes[-1] == failed
    assert failed not in routes[:5]
    provider._record_image_domain(failed, success=True)
    assert failed in provider._image_domains(module)[:6]
    database.close()


@pytest.mark.parametrize("failed_routes", [2, 99])
def test_full_download_switches_image_cdns_with_bounded_attempts(
    tmp_path: Path, failed_routes: int
) -> None:
    database = LibraryDatabase()
    provider = JmcomicProvider(database, tmp_path / "data")
    attempts: list[str] = []
    image = SimpleNamespace(
        index=1,
        img_url="https://original.example/media/photos/701/00001.webp",
        cache=False,
        exists=False,
    )

    class Photo:
        id = "701"
        save_path = ""
        skip = False

        def __len__(self):
            return 1

        def __iter__(self):
            return iter([image])

    photo = Photo()

    class Client:
        def get_photo_detail(self, _source_id):
            return photo

        def download_by_image_detail(self, item, path):
            attempts.append(urlparse(item.img_url).netloc)
            assert not Path(path).exists(), "failed route left a partial image"
            if len(attempts) <= failed_routes:
                Path(path).write_bytes(b"partial")
                raise TimeoutError("image CDN timed out")
            Path(path).write_bytes(b"image")

    class Downloader:
        def __init__(self, option):
            self.option = option
            self.client = option.new_jm_client()

        def begin_manifest(self, _photo):
            pass

        def finish_manifest(self, _photo):
            pass

        def before_photo(self, _photo):
            pass

        def after_photo(self, _photo):
            pass

        def after_image(self, _item, _path):
            pass

        def download_by_photo_detail(self, detail):
            detail.save_path = self.option.decide_image_save_dir(detail)
            self.before_photo(detail)
            for item in detail:
                self.download_by_image_detail(item)
            self.after_photo(detail)

        def download_by_image_detail(self, item):
            path = self.option.decide_image_filepath(item)
            item.save_path = path
            self.client.download_by_image_detail(item, path)
            self.after_image(item, path)

    class Option:
        def __init__(self):
            self.client = SimpleNamespace(
                retry_times=5,
                postman=SimpleNamespace(meta_data={}),
            )
            self.dir_rule = SimpleNamespace(base_dir="")
            self.download = SimpleNamespace(threading=SimpleNamespace(image=30))
            self.image_client = Client()

        def new_jm_client(self):
            return self.image_client

        def decide_image_save_dir(self, _photo):
            root = Path(self.dir_rule.base_dir) / "photo-701"
            root.mkdir(parents=True, exist_ok=True)
            return str(root)

        def decide_image_filepath(self, item):
            return str(Path(photo.save_path) / f"{item.index:05d}.webp")

        def download_photo(self, photo_id, downloader):
            detail = downloader(self).download_photo(photo_id)
            return SimpleNamespace(detail=detail, duration=0.1)

    option = Option()
    provider._option = lambda: option
    provider._module = lambda: SimpleNamespace(
        JmDownloader=Downloader,
        JmModuleConfig=SimpleNamespace(DOMAIN_IMAGE_LIST=[]),
    )
    provider.lookup = lambda *_args, **_kwargs: {
        "seriesId": "701",
        "seriesTitle": "测试作品",
        "items": [{"sourceId": "701", "title": "测试作品", "pageCount": 1}],
    }

    if failed_routes == 99:
        with pytest.raises(TimeoutError, match="image CDN timed out"):
            provider.download("701", tmp_path / "downloads")
        assert len(attempts) == 6
    else:
        result = provider.download("701", tmp_path / "downloads")
        assert Path(result["items"][0]["rootPath"]).joinpath("00001.webp").read_bytes() == b"image"
        assert len(attempts) == 3
    assert len(attempts) == len(set(attempts))
    assert option.client.retry_times == 0
    assert option.client.postman.meta_data["timeout"] == 12
    database.close()


def test_preview_download_fetches_metadata_concurrently_and_streams_four_page_head(tmp_path: Path) -> None:
    database = LibraryDatabase()
    provider = JmcomicProvider(database, tmp_path / "data")
    progress_events: list[tuple[dict, int]] = []
    metadata_calls: list[tuple] = []
    hedge_gate_counts: list[int] = []
    hedge_barrier = threading.Barrier(2)

    class StreamImage:
        def __init__(self, index: int) -> None:
            self.index = index
            self.img_url = f"https://cdn-msp.jmapiproxy1.cc/media/photos/702/{index:05d}.webp"
            self.scramble_id = "220980"
            self.skip = False

    class StreamPhoto:
        def __init__(self) -> None:
            self.id = "702"
            self.title = "流式缓存"
            self.album_id = "702"
            self.save_path = ""
            self.skip = False
            self.images = [StreamImage(index) for index in range(1, 9)]

        def __len__(self) -> int:
            return len(self.images)

        def __iter__(self):
            return iter(self.images)

    photo = StreamPhoto()

    class StreamClient:
        def get_photo_detail(self, _photo_id, fetch_album=True, fetch_scramble=True):
            metadata_calls.append(("photo", fetch_album, fetch_scramble))
            return photo

        def get_scramble_id(self, _photo_id):
            metadata_calls.append(("scramble",))
            return "220980"

        def check_photo(self, _photo):
            return None

        def download_by_image_detail(self, _image, img_save_path, decode_image=True):
            Path(img_save_path).write_bytes(b"image")

        def get_jm_image(self, _url):
            hedge_gate_counts.append(provider._transfer_gate.snapshot()["activeTotal"])
            hedge_barrier.wait(timeout=2)
            return SimpleNamespace(require_success=lambda: None)

        def save_image_resp(self, _decode, path, _url, _response, _scramble_id):
            Path(path).write_bytes(b"image")

    class StreamDownloader:
        def __init__(self, option):
            self.option = option
            self.client = option.new_jm_client()

        def begin_manifest(self, _photo):
            return None

        def finish_manifest(self, _photo):
            return None

        def before_photo(self, _photo):
            return None

        def after_photo(self, _photo):
            return None

        def after_image(self, _image, _path):
            return None

        def before_image(self, _image, _path):
            return None

        def do_filter(self, detail):
            return detail

        def execute_on_condition(self, iter_objs, apply, count_batch):
            for item in iter_objs:
                apply(item)

        def download_by_image_detail(self, image):
            path = self.option.decide_image_filepath(image)
            self.client.download_by_image_detail(image, path)
            self.after_image(image, path)

    class StreamOption:
        def __init__(self) -> None:
            self.dir_rule = SimpleNamespace(base_dir="")
            self.download = SimpleNamespace(threading=SimpleNamespace(image=1))
            self.client = StreamClient()

        def new_jm_client(self):
            return self.client

        def decide_image_save_dir(self, _photo):
            root = Path(self.dir_rule.base_dir) / "photo-702"
            root.mkdir(parents=True, exist_ok=True)
            return str(root)

        def decide_image_filepath(self, image):
            return str(Path(photo.save_path) / f"{image.index}.jpg")

        def decide_download_cache(self, _image):
            return False

        def decide_download_image_decode(self, _image):
            return False

        def download_photo(self, photo_id, downloader):
            detail = downloader(self).download_photo(photo_id)
            return SimpleNamespace(detail=detail, duration=0.1)

    lookup_payload = {
        "queryId": "702",
        "seriesId": "702",
        "seriesTitle": "流式缓存",
        "itemCount": 1,
        "items": [{
            "sourceId": "702",
            "seriesId": "702",
            "seriesTitle": "流式缓存",
            "title": "流式缓存",
            "pageCount": 8,
        }],
    }
    provider.lookup = lambda *_args, **_kwargs: lookup_payload
    provider._module = lambda: SimpleNamespace(JmDownloader=StreamDownloader, JmApiClient=StreamClient)
    provider._option = StreamOption

    def progress(patch):
        root = Path(str(patch.get("streamingRoot") or ""))
        page_count = len(list(root.glob("*.jpg"))) if root.is_dir() else 0
        progress_events.append((dict(patch), page_count))

    result = provider.download(
        "702",
        tmp_path / "cache",
        progress=progress,
        stream_first_pages=4,
        lookup_hint=lookup_payload,
        foreground=True,
    )
    stream_event, page_count_at_event = next(
        event for event in progress_events if event[0].get("streamReady")
    )
    assert stream_event["availablePages"] == 4
    assert stream_event["expectedPages"] == 8
    assert page_count_at_event == 4
    visible_counts = [
        patch["availablePages"]
        for patch, _page_count in progress_events
        if patch.get("streamReady")
    ]
    assert visible_counts == [4, 5, 6, 7, 8]
    assert ("photo", False, False) in metadata_calls
    assert ("scramble",) in metadata_calls
    assert max(hedge_gate_counts) >= 2
    assert hash(photo.from_album)
    assert len(photo.from_album) == 1
    assert len(list(Path(result["items"][0]["rootPath"]).glob("*.jpg"))) == 8
    database.close()


def test_zero_page_download_is_reported_as_failure(tmp_path: Path) -> None:
    provider, database, client, download_calls = build_provider(tmp_path / "data")

    class EmptyOption(FakeOption):
        def download_photo(self, photo_id, **_kwargs):
            self.download_calls.append(photo_id)
            root = Path(self.dir_rule.base_dir) / f"photo-{photo_id}"
            root.mkdir(parents=True)
            detail = FakePhoto(photo_id, self.client.photos[photo_id].title, 0, 1)
            detail.save_path = str(root)
            return SimpleNamespace(detail=detail, duration=0.1)

    provider._option = lambda: EmptyOption(client, download_calls)
    with pytest.raises(RuntimeError, match="没有可读取图片|图片不完整"):
        provider.download("1096733", tmp_path / "downloads")
    database.close()


def test_fractional_and_duplicate_sort_chapters_are_all_downloaded(tmp_path: Path) -> None:
    provider, database, client, download_calls = build_provider(tmp_path / "data")
    client.album.episode_list = [
        ("1019294", "1", "第 1 话"),
        ("1096733", "1", "第 1.1 话"),
        ("1100000", "1.2", "第 1.2 话"),
        ("1200000", "27.5", "第 27.5 话"),
    ]
    client.photos.update({
        "1100000": FakePhoto("1100000", "第 1.2 话", 12, 3),
        "1200000": FakePhoto("1200000", "第 27.5 话", 18, 4),
    })
    result = provider.download("1019294", tmp_path / "downloads")
    assert [item["sourceId"] for item in result["items"]] == [
        "1019294", "1096733", "1100000", "1200000",
    ]
    assert [item["chapterIndex"] for item in result["items"]] == [1, 1, 1.2, 27.5]
    assert sorted(download_calls) == ["1019294", "1096733", "1100000", "1200000"]
    database.close()


def test_api_album_adapter_keeps_duplicate_and_fractional_episode_sorts(tmp_path: Path) -> None:
    provider, database, _client, _ = build_provider(tmp_path)
    raw_episodes = [
        ("1019294", "1", "第 1 话"),
        ("1096733", "1", "第 1.1 话"),
        ("1100000", "1.2", "第 1.2 话"),
        ("1200000", "27.5", "第 27.5 话"),
    ]
    payload = {
        "id": "1019294",
        "name": "测试系列",
        "likes": "0",
        "tags": [],
        "works": [],
        "actors": [],
        "related_list": [],
        "description": "",
        "author": ["作者"],
        "total_views": "0",
        "comment_total": "0",
        "series": [
            {"id": photo_id, "sort": sort, "name": title}
            for photo_id, sort, title in raw_episodes
        ],
    }
    jmcomic = provider._module()
    album = jmcomic.JmApiAdaptTool.parse_entity(payload, provider._complete_album_class())
    assert album.episode_list == raw_episodes
    database.close()


def test_later_photo_id_resolves_only_that_photo(tmp_path: Path) -> None:
    provider, database, _client, _download_calls = build_provider(tmp_path)
    result = provider.lookup("1096733")
    assert [item["sourceId"] for item in result["items"]] == ["1096733"]
    assert result["seriesId"] == "1019294"
    database.close()


def test_later_photo_lookup_refetches_the_real_album_metadata(tmp_path: Path) -> None:
    provider, database, client, _download_calls = build_provider(tmp_path)
    root_album = client.album
    chapter_shell = SimpleNamespace(
        id="1096733",
        title="第二话",
        episode_list=[("1096733", "2", "第二话")],
        authors=[],
        tags=[],
        description="",
        pub_date="0",
        update_date="0",
    )

    def album_for_id(source_id):
        client.album_calls.append(source_id)
        return chapter_shell if source_id == "1096733" else root_album

    client.get_album_detail = album_for_id
    result = provider.lookup("1096733", defer_covers=True)
    assert client.album_calls == ["1096733", "1019294"]
    assert result["seriesTitle"] == "系列标题"
    assert result["items"][0]["authors"] == ["作者"]
    database.close()


def test_saved_proxy_is_injected_into_jmcomic_option(tmp_path: Path) -> None:
    database = LibraryDatabase()
    database.set_setting("provider.proxy", "http://127.0.0.1:7890")
    provider = JmcomicProvider(database, tmp_path)
    option = provider._option()
    assert option.client.postman.meta_data.proxies == "http://127.0.0.1:7890"
    database.close()


def test_fast_lookup_runs_album_and_photo_together_without_scramble_or_cover(tmp_path: Path) -> None:
    provider, database, client, _ = build_provider(tmp_path)
    barrier = threading.Barrier(2)
    get_album, get_photo = client.get_album_detail, client.get_photo_detail

    def album(source_id):
        barrier.wait(timeout=3)
        return get_album(source_id)

    def photo(source_id, fetch_album, fetch_scramble):
        if source_id == "1019294":
            barrier.wait(timeout=3)
        return get_photo(source_id, fetch_album, fetch_scramble)

    client.get_album_detail, client.get_photo_detail = album, photo
    metadata = provider.lookup("1019294", defer_covers=True)
    assert metadata["itemCount"] == 2
    assert client.scramble_flags == [False, False]
    assert client.cover_calls == []
    assert metadata["items"][0]["pageCount"] == 38
    assert metadata["items"][0]["coverUrl"] == "/media/source-cover/1019294"
    database.close()


def test_query_cache_copies_data_and_fresh_lookup_bypasses_cache(tmp_path: Path) -> None:
    provider, database, client, _ = build_provider(tmp_path)
    provider.lookup("1019294", defer_covers=True)["items"][0]["title"] = "client mutation"
    cached = provider.lookup("1019294", defer_covers=True)
    assert cached["items"][0]["title"] == "第一话"
    assert len(client.album_calls) == 1
    provider.lookup("1019294", defer_covers=True, fresh=True)
    assert len(client.album_calls) == 2
    provider.invalidate_cache()
    provider.lookup("1019294", defer_covers=True)
    assert len(client.album_calls) == 3
    database.close()


def test_online_metadata_for_different_albums_is_not_globally_serialized(tmp_path: Path) -> None:
    database = LibraryDatabase()
    provider = JmcomicProvider(database, tmp_path)
    barrier = threading.Barrier(2)

    class MetadataClient:
        def get_album_detail(self, source_id):
            barrier.wait(timeout=2)
            return SimpleNamespace(
                id=source_id,
                pub_date="2026-09-20",
                update_date="2026-09-20",
                authors=[f"作者{source_id}"],
                tags=[f"标签{source_id}"],
                description="详情",
                page_count=12,
            )

    provider._new_html_client = lambda direct=False: MetadataClient()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(provider.online_metadata, ["701"])
        second = executor.submit(provider.online_metadata, ["702"])
        results = [first.result(timeout=3), second.result(timeout=3)]

    assert [result["items"][0]["albumId"] for result in results] == ["701", "702"]
    assert all(result["items"][0]["tags"] for result in results)
    database.close()


def test_online_metadata_persists_in_sqlite_and_skips_network_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "library.sqlite3"
    database = LibraryDatabase(database_path)
    provider = JmcomicProvider(database, tmp_path)
    calls: list[str] = []

    class MetadataClient:
        def get_album_detail(self, source_id):
            calls.append(source_id)
            return SimpleNamespace(
                id=source_id,
                title="持久化详情",
                pub_date="2026-09-20",
                update_date="2026-09-20",
                authors=["作者"],
                tags=["标签"],
                description="详情",
                page_count=12,
            )

    provider._new_html_client = lambda direct=False: MetadataClient()
    first = provider.online_metadata(["701"])["items"][0]
    assert first["tags"] == ["标签"]
    assert calls == ["701"]
    assert not (tmp_path / "jmonline-metadata.json").exists()
    database.close()

    reopened = LibraryDatabase(database_path)
    next_provider = JmcomicProvider(reopened, tmp_path)
    next_provider._new_html_client = lambda direct=False: (_ for _ in ()).throw(AssertionError("network should not run"))
    second = next_provider.online_metadata(["701"])["items"][0]
    assert second["title"] == "持久化详情"
    assert second["tags"] == ["标签"]
    reopened.close()


def test_download_lookup_hint_skips_full_series_lookup(tmp_path: Path) -> None:
    provider, database, _client, download_calls = build_provider(tmp_path / "data")
    provider.lookup = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full lookup should not run"))
    hint = {
        "queryId": "1019294",
        "seriesId": "1019294",
        "seriesTitle": "轻量预览",
        "itemCount": 2,
        "items": [{
            "sourceId": "1019294",
            "seriesId": "1019294",
            "seriesTitle": "轻量预览",
            "chapterIndex": 1,
            "title": "第一话",
            "pageCount": 38,
        }],
    }
    result = provider.download(
        "1019294",
        tmp_path / "preview",
        selected_source_ids=["1019294"],
        lookup_hint=hint,
        foreground=True,
    )
    assert download_calls == ["1019294"]
    assert result["items"][0]["sourceId"] == "1019294"
    database.close()


def test_startup_login_restore_reads_local_password_and_refreshes_cookies(tmp_path: Path) -> None:
    database = LibraryDatabase()
    provider = JmcomicProvider(database, tmp_path / "data")
    provider.credentials.save({
        "username": "saved-user",
        "password": "saved-password",
        "cookies": {"AVS": "stale"},
        "updatedAt": "2026-01-01T00:00:00+00:00",
    })
    login_calls: list[tuple[str, str]] = []

    class LoginClient(dict):
        def login(self, username: str, password: str):
            login_calls.append((username, password))
            self["cookies"] = {"AVS": "fresh", "token": "renewed"}
            return SimpleNamespace(res_data={"username": username})

    client = LoginClient(cookies={"AVS": "stale"})
    provider._run_query = lambda operation, **_kwargs: operation(client)  # type: ignore[method-assign]

    scheduled = provider.schedule_login_restore()
    assert scheduled["scheduled"] is True
    assert provider._login_restore_done.wait(1)
    saved = provider.credentials.load()
    assert login_calls == [("saved-user", "saved-password")]
    assert saved is not None
    assert saved["password"] == "saved-password"
    assert saved["cookies"] == {"AVS": "fresh", "token": "renewed"}
    assert provider.auth_status() == {"authenticated": True, "username": "saved-user"}
    database.close()


def test_parallel_identical_queries_share_one_result(tmp_path: Path) -> None:
    provider, database, client, _ = build_provider(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: provider.lookup("1019294", defer_covers=True), range(4)))
    assert len(client.album_calls) == 1
    assert all(result["itemCount"] == 2 for result in results)
    database.close()


def test_covers_download_once_and_partial_files_never_become_cache_hits(tmp_path: Path) -> None:
    provider, database, client, _ = build_provider(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        covers = list(executor.map(lambda _: provider.get_cover("1019294"), range(4)))
    assert len(client.cover_calls) == 1
    assert all(cover and cover.read_bytes() == b"cover" for cover in covers)
    assert list((tmp_path / "covers" / "chapters").glob('*.part.jpg')) == []
    database.close()


def test_online_thumbnail_uses_small_variant_and_keeps_a_separate_cache(tmp_path: Path) -> None:
    provider, database, client, _ = build_provider(tmp_path)
    first = provider.get_thumbnail("1019294")
    second = provider.get_thumbnail("1019294")
    assert first == second
    assert first is not None and first.parent.name == "thumbnails"
    assert client.cover_calls == ["1019294"]
    assert client.cover_sizes == ["_3x4"]
    database.close()


def test_chapter_downloads_overlap_and_return_in_order_with_image_limits(tmp_path: Path) -> None:
    provider, database, client, download_calls = build_provider(tmp_path)
    barrier = threading.Barrier(2)
    configured_images = []

    class ConcurrentOption(FakeOption):
        def download_photo(self, photo_id, **kwargs):
            configured_images.append(self.download.threading.image)
            barrier.wait(timeout=3)
            return super().download_photo(photo_id, **kwargs)

    provider._option = lambda: ConcurrentOption(client, download_calls)
    result = provider.download("1019294", tmp_path / "downloads")
    assert [item["sourceId"] for item in result["items"]] == ["1019294", "1096733"]
    assert configured_images == [24, 24]
    assert client.cover_calls == []
    database.close()


def test_logged_in_favorites_fetch_all_pages_in_order_and_cache_result(tmp_path: Path) -> None:
    provider, database, client, _ = build_provider(tmp_path)
    provider.auth_status = lambda: {"authenticated": True, "username": "tester"}  # type: ignore[method-assign]
    pages = {
        1: SimpleNamespace(
            page_count=2,
            total=3,
            content=[("701", {"name": "收藏一", "author": ["作者一"], "tags": ["标签一"]})],
        ),
        2: SimpleNamespace(
            page_count=2,
            total=3,
            content=[
                ("702", {"name": "收藏二", "author": "作者二", "latest_ep": "2", "latest_ep_aid": "1702"}),
                ("703", {"name": "收藏三"}),
            ],
        ),
    }
    favorite_calls: list[tuple[int, str, str]] = []

    def favorite_folder(page=1, folder_id="0", username=""):
        favorite_calls.append((page, folder_id, username))
        return pages[page]

    client.favorite_folder = favorite_folder  # type: ignore[attr-defined]
    first = provider.favorites()
    cached = provider.favorites()

    assert [item["albumId"] for item in first["items"]] == ["701", "702", "703"]
    assert first["items"][1]["authors"] == ["作者二"]
    assert first["items"][1]["latestEpisodeId"] == "1702"
    assert first["items"][0]["coverUrl"] == "/media/source-cover/701"
    assert sorted(page for page, _folder, _username in favorite_calls) == [1, 2]
    assert all(folder == "0" and username == "tester" for _page, folder, username in favorite_calls)
    assert cached == first
    database.close()


def test_favorites_require_a_logged_in_account(tmp_path: Path) -> None:
    provider, database, _client, _ = build_provider(tmp_path)
    provider.auth_status = lambda: {"authenticated": False, "username": ""}  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="请先登录"):
        provider.favorites()
    database.close()


def test_expired_favorite_session_is_cleared_with_friendly_error(tmp_path: Path) -> None:
    provider, database, client, _ = build_provider(tmp_path)
    provider.auth_status = lambda: {"authenticated": True, "username": "tester"}  # type: ignore[method-assign]
    cleared: list[bool] = []
    provider.credentials.clear = lambda: cleared.append(True)  # type: ignore[method-assign]

    def expired_favorites(**_kwargs):
        raise RuntimeError('{"code":401,"errorMsg":"expired"}')

    client.favorite_folder = expired_favorites  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="登录会话已失效"):
        provider.favorites(fresh=True)
    assert cleared == [True]
    database.close()
