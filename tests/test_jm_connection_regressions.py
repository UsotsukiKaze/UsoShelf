from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from jmshelf.db import LibraryDatabase
from jmshelf.provider import JmcomicProvider


def test_html_discovery_uses_configured_transport_and_explicit_domain(tmp_path: Path):
    db = LibraryDatabase()
    provider = JmcomicProvider(db, tmp_path)
    config = {"type": "curl_cffi", "meta_data": {"proxies": "http://proxy:7890"}}
    postman = SimpleNamespace(src_dict=config, meta_data=config["meta_data"])
    calls = []
    transport = object()
    option = SimpleNamespace(client=SimpleNamespace(postman=postman))
    option.new_jm_client = lambda **kwargs: calls.append(kwargs) or SimpleNamespace(retry_times=5)
    provider._option = lambda: option

    def create(data):
        assert data["type"] == "curl_cffi_session"
        assert data["meta_data"]["proxies"] == "http://proxy:7890"
        assert data["meta_data"]["timeout"] == 12
        return transport

    def discover(client):
        assert client is transport
        return "example.test"

    provider._module = lambda: SimpleNamespace(
        Postmans=SimpleNamespace(create=create),
        JmModuleConfig=SimpleNamespace(DOMAIN_HTML_LIST=None, get_html_domain=discover),
    )
    assert provider._new_html_client().retry_times == 1
    assert calls == [{"impl": "html", "timeout": 12, "domain_list": ["example.test"]}]
    db.close()


def test_html_failure_can_still_load_api_tags(tmp_path: Path):
    db = LibraryDatabase()
    provider = JmcomicProvider(db, tmp_path)
    db.set_setting("provider.proxy", "http://proxy:7890")
    provider._new_html_client = lambda **kwargs: (_ for _ in ()).throw(TimeoutError("HTML offline"))
    album = SimpleNamespace(id="701", title="title", tags=["tag"], episode_list=[])
    provider._run_query = lambda operation: operation(SimpleNamespace(get_album_detail=lambda _: album))
    provider._get_album_detail = lambda client, source: client.get_album_detail(source)
    result = provider.online_metadata(["701"])["items"][0]
    assert result["tags"] == ["tag"]
    assert result["metadataFresh"]
    db.close()


def test_metadata_failure_cooldown_coalesces_repeated_requests(tmp_path: Path):
    db = LibraryDatabase()
    provider = JmcomicProvider(db, tmp_path)
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise TimeoutError("offline")

    provider._new_html_client = fail
    provider._run_query = fail
    for _ in range(5):
        assert provider.online_metadata(["701"]) == {"items": []}
    assert len(calls) == 2
    provider.invalidate_cache()
    provider.online_metadata(["701"])
    assert len(calls) == 4
    db.close()


@pytest.mark.parametrize("kind", ["home", "recommendation"])
def test_failed_feed_refresh_does_not_restart_on_every_poll(tmp_path: Path, kind: str):
    db = LibraryDatabase()
    db.upsert_comic({"sourceId": "701", "title": "title"})
    provider = JmcomicProvider(db, tmp_path)
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise TimeoutError("offline")

    if kind == "home":
        provider._build_home_snapshot = fail
        schedule = provider.schedule_home_refresh
    else:
        provider.recommendations = fail
        schedule = provider.schedule_recommendation_refresh
    assert schedule()["refreshing"]
    provider._background_threads[-1].join(timeout=2)
    for _ in range(5):
        assert not schedule()["refreshing"]
    assert len(calls) == 1
    assert schedule(force=True)["refreshing"]
    provider._background_threads[-1].join(timeout=2)
    assert len(calls) == 2
    provider.close()
    db.close()


def test_partial_metadata_cannot_overwrite_concurrent_tag_hydration():
    db = LibraryDatabase()
    read = db.get_online_metadata
    partial_read = threading.Event()
    release_partial = threading.Event()
    full_started = threading.Event()
    full_done = threading.Event()
    partial_thread = []

    def paused_read(ids):
        rows = read(ids)
        if partial_thread and threading.get_ident() == partial_thread[0]:
            partial_read.set()
            assert release_partial.wait(2)
        return rows

    db.get_online_metadata = paused_read

    def partial():
        partial_thread.append(threading.get_ident())
        db.upsert_online_metadata_many([{"albumId": "701", "title": "search", "tags": []}])

    def full():
        full_started.set()
        db.upsert_online_metadata_many([{"albumId": "701", "tags": ["tag"], "detailsComplete": True}])
        full_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(partial)
        assert partial_read.wait(2)
        hydration = pool.submit(full)
        assert full_started.wait(2)
        try:
            assert not full_done.wait(.1), "read/merge/write must hold one lock"
        finally:
            release_partial.set()
        pending.result(timeout=2)
        hydration.result(timeout=2)
    assert read(["701"])[0]["tags"] == ["tag"]
    db.close()


def test_startup_does_not_prefetch_unused_online_feeds(tmp_path: Path):
    db = LibraryDatabase()
    provider = JmcomicProvider(db, tmp_path)
    calls = []
    provider.schedule_login_restore = lambda: calls.append("login")
    provider.schedule_home_refresh = lambda **kwargs: calls.append("home")
    provider.schedule_recommendation_refresh = lambda **kwargs: calls.append("recommendations")
    provider.start_background_refreshes()
    assert calls == ["login"]
    db.close()
