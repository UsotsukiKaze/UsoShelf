from __future__ import annotations

import importlib.metadata
import json
import math
import shutil
import threading
import time
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import urlparse

from .credentials import WindowsCredentialStore
from .db import LibraryDatabase
from .utils import find_images, normalize_chapter_index, normalize_plate


class _PreviewAlbumContext:
    """Hashable album stand-in for the jmcomic download bookkeeping path."""

    __slots__ = ("id", "title", "name", "authors", "author", "tags", "page_count", "item_count")

    def __init__(self, lookup: dict[str, Any], fallback_id: str) -> None:
        self.id = str(lookup.get("seriesId") or fallback_id)
        self.title = str(lookup.get("seriesTitle") or lookup.get("title") or f"JM{self.id}")
        self.name = self.title
        self.authors = [str(value) for value in (lookup.get("authors") or [])]
        self.author = self.authors[0] if self.authors else ""
        self.tags = [str(value) for value in (lookup.get("tags") or [])]
        self.page_count = max(0, int(lookup.get("pageCount") or 0))
        self.item_count = max(1, int(lookup.get("itemCount") or 1))

    def __len__(self) -> int:
        return self.item_count


class _AdaptiveTransferGate:
    """Borrow idle bandwidth while reserving busy-time slots for visible work."""

    def __init__(
        self,
        total_limit: int = 24,
        protected_background_limit: int = 16,
        preview_background_limit: int | None = None,
    ) -> None:
        self.total_limit = max(1, total_limit)
        self.protected_background_limit = max(1, min(protected_background_limit, self.total_limit))
        self.preview_background_limit = max(
            1,
            min(
                preview_background_limit or max(1, self.protected_background_limit // 2),
                self.protected_background_limit,
            ),
        )
        self._condition = threading.Condition()
        self._active_total = 0
        self._active_background = 0
        self._active_streaming = 0
        self._active_visible = 0
        self._waiting_preview = 0
        self._waiting_streaming = 0
        self._waiting_visible = 0
        self._active_previews = 0
        self._active_queries = 0
        self._focus_scope = "library"

    def set_focus(self, scope: str) -> None:
        selected = scope if scope in {"online", "library", "preview"} else "library"
        with self._condition:
            self._focus_scope = selected
            self._condition.notify_all()

    @contextmanager
    def query(self):
        with self._condition:
            self._active_queries += 1
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._active_queries = max(0, self._active_queries - 1)
                self._condition.notify_all()

    @contextmanager
    def preview_session(self):
        with self._condition:
            self._active_previews += 1
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._active_previews = max(0, self._active_previews - 1)
                self._condition.notify_all()

    @contextmanager
    def image(self, *, foreground: bool = False, priority: str | None = None):
        kind = priority if priority in {"preview", "streaming", "visible", "background"} else "background"
        if foreground:
            kind = "preview"
        waiting_attribute = {
            "preview": "_waiting_preview",
            "streaming": "_waiting_streaming",
            "visible": "_waiting_visible",
        }.get(kind)
        with self._condition:
            if waiting_attribute:
                setattr(self, waiting_attribute, getattr(self, waiting_attribute) + 1)
                self._condition.notify_all()
            try:
                while True:
                    preview_busy = self._active_previews > 0 or self._waiting_preview > 0
                    protected = (
                        preview_busy
                        or self._active_queries > 0
                        or self._waiting_streaming > 0
                        or self._waiting_visible > 0
                    )
                    if preview_busy or self._focus_scope == "preview":
                        background_limit = self.preview_background_limit
                    elif self._focus_scope == "online":
                        # Keep at least 60% of the 24 lanes free for search,
                        # visible covers, tag hydration and preview warming.
                        background_limit = min(9, self.total_limit)
                    elif protected:
                        # A focused local shelf keeps at least 80% for durable
                        # cache/download work even if a few online requests linger.
                        background_limit = min(max(1, math.ceil(self.total_limit * .8)), self.total_limit)
                    else:
                        background_limit = self.total_limit
                    visible_limit = (
                        2 if preview_busy or self._focus_scope == "preview"
                        else min(15 if self._focus_scope == "online" else 4, self.total_limit)
                    )
                    higher_waiting = (
                        (kind != "preview" and self._waiting_preview > 0)
                        or (kind == "background" and (self._waiting_streaming > 0 or self._waiting_visible > 0))
                    )
                    allowed = self._active_total < self.total_limit and not higher_waiting
                    if kind == "streaming":
                        allowed = allowed and self._waiting_preview == 0 and self._active_streaming < min(18, self.total_limit)
                    elif kind == "visible":
                        allowed = allowed and self._waiting_preview == 0 and self._active_visible < visible_limit
                    elif kind == "background":
                        allowed = allowed and self._active_background < background_limit
                    if allowed:
                        break
                    self._condition.wait()
                if kind == "background":
                    self._active_background += 1
                elif kind == "streaming":
                    self._active_streaming += 1
                elif kind == "visible":
                    self._active_visible += 1
                self._active_total += 1
            finally:
                if waiting_attribute:
                    setattr(self, waiting_attribute, max(0, getattr(self, waiting_attribute) - 1))
        try:
            yield
        finally:
            with self._condition:
                self._active_total = max(0, self._active_total - 1)
                if kind == "background":
                    self._active_background = max(0, self._active_background - 1)
                elif kind == "streaming":
                    self._active_streaming = max(0, self._active_streaming - 1)
                elif kind == "visible":
                    self._active_visible = max(0, self._active_visible - 1)
                self._condition.notify_all()

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            return {
                "activeTotal": self._active_total,
                "activeBackground": self._active_background,
                "activeStreaming": self._active_streaming,
                "activeVisible": self._active_visible,
                "waitingForeground": self._waiting_preview,
                "waitingStreaming": self._waiting_streaming,
                "waitingVisible": self._waiting_visible,
                "activePreviews": self._active_previews,
                "activeQueries": self._active_queries,
            }

    def recommended_parallelism(self, priority: str = "background") -> int:
        """Choose worker fan-out from the current activity focus.

        The gate remains the hard global limit. This value only controls how
        many ready requests one chapter may keep queued, so the application can
        react to focus changes without exposing a tuning knob to users.
        """
        kind = priority if priority in {"preview", "streaming", "visible", "background"} else "background"
        with self._condition:
            if kind == "preview":
                return min(4, self.total_limit)
            if kind == "streaming":
                return min(18, self.total_limit)
            if kind == "visible":
                return min(15 if self._focus_scope == "online" else 4, self.total_limit)
            if self._focus_scope == "preview":
                return self.preview_background_limit
            if self._focus_scope == "online":
                return min(9, self.total_limit)
            return self.total_limit


class _PriorityQueryGate:
    """Reserve query capacity for interactive work while metadata hydrates."""

    def __init__(self, total_limit: int = 6, background_limit: int = 4) -> None:
        self.total_limit = max(1, total_limit)
        self.background_limit = max(1, min(background_limit, self.total_limit))
        self._condition = threading.Condition()
        self._active_total = 0
        self._active_background = 0
        self._waiting_interactive = 0

    def set_focus(self, scope: str) -> None:
        with self._condition:
            # JMonline reserves four of six query lanes for foreground search
            # and visible tag/detail hydration. Local mode restores four lanes
            # to background upkeep while leaving two interactive lanes.
            self.background_limit = 2 if scope == "online" else 1 if scope == "preview" else min(4, self.total_limit)
            self._condition.notify_all()

    @contextmanager
    def slot(self, *, background: bool = False):
        with self._condition:
            if not background:
                self._waiting_interactive += 1
                self._condition.notify_all()
            try:
                while (
                    self._active_total >= self.total_limit
                    or (
                        background
                        and (
                            self._active_background >= self.background_limit
                            or self._waiting_interactive > 0
                        )
                    )
                ):
                    self._condition.wait()
                self._active_total += 1
                if background:
                    self._active_background += 1
            finally:
                if not background:
                    self._waiting_interactive = max(0, self._waiting_interactive - 1)
        try:
            yield
        finally:
            with self._condition:
                self._active_total = max(0, self._active_total - 1)
                if background:
                    self._active_background = max(0, self._active_background - 1)
                self._condition.notify_all()

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            return {
                "activeTotal": self._active_total,
                "activeBackground": self._active_background,
                "waitingInteractive": self._waiting_interactive,
            }


class JmcomicProvider:
    _PREVIEW_IMAGE_DOMAINS = (
        "cdn-msp.jmapiproxy1.cc",
        "cdn-msp2.jmapiproxy2.cc",
        "cdn-msp.jmapiproxy2.cc",
        "cdn-msp3.jmapiproxy2.cc",
        "cdn-msp.jmapinodeudzn.net",
        "cdn-msp3.jmapinodeudzn.net",
    )

    def __init__(self, database: LibraryDatabase, data_root: Path) -> None:
        self.database = database
        self.data_root = data_root
        self.credentials = WindowsCredentialStore(
            data_root / "jm-account.json",
            legacy_path=data_root / "jm-account.dat",
        )
        self._lookup_cache: OrderedDict[tuple[str, bool], tuple[float, dict[str, Any]]] = OrderedDict()
        self._cache_lock = threading.RLock()
        self._cache_generation = 0
        self._lookup_locks = [threading.Lock() for _ in range(16)]
        self._cover_locks = [threading.Lock() for _ in range(32)]
        self._cover_failures: OrderedDict[str, float] = OrderedDict()
        self._favorites_cache: tuple[float, dict[str, Any]] | None = None
        self._recommendations_cache: tuple[float, tuple[Any, ...], dict[str, Any]] | None = None
        self._favorites_lock = threading.Lock()
        self._login_lock = threading.RLock()
        self._login_restore_done = threading.Event()
        self._login_restore_done.set()
        self._login_restore_running = False
        self._login_restore_error = ""
        self._metadata_fetch_locks = [threading.Lock() for _ in range(64)]
        self._metadata_clients = threading.local()
        self._cover_clients = threading.local()
        self._background_lock = threading.Lock()
        self._background_stop = threading.Event()
        self._background_threads: list[threading.Thread] = []
        self._recommendation_store_path = data_root / "jmonline-recommendations.json"
        self._home_store_path = data_root / "jmonline-home.json"
        self._preferences_store_path = data_root / "jmonline-preferences.json"
        self._recommendation_snapshot = self._read_json_file(self._recommendation_store_path)
        self._home_snapshot = self._read_json_file(self._home_store_path)
        self._preferences = self._read_json_file(self._preferences_store_path) or {}
        self._recommendation_refreshing = False
        self._home_refreshing = False
        self._query_gate = _PriorityQueryGate(total_limit=6, background_limit=4)
        self._cover_slots = threading.BoundedSemaphore(4)
        self._thumbnail_slots = threading.BoundedSemaphore(10)
        self._thumbnail_locks = [threading.Lock() for _ in range(32)]
        self._transfer_gate = _AdaptiveTransferGate(
            total_limit=24,
            protected_background_limit=10,
            preview_background_limit=6,
        )
        self._download_locks = [threading.Lock() for _ in range(64)]
        self._album_class_lock = threading.Lock()
        self._complete_album_classes: dict[type, type] = {}
        self._connection_lock = threading.Lock()
        self._connection_reachable: bool | None = None
        self._connection_error = ""
        self._connection_checked_at = ""
        self._image_domain_lock = threading.Lock()
        self._image_domain_failures: dict[str, float] = {}
        self._preferred_image_domain = self._PREVIEW_IMAGE_DOMAINS[0]
        self._image_domain_cursor = 0

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache_generation += 1
            self._lookup_cache.clear()
            self._cover_failures.clear()
            self._favorites_cache = None
            self._recommendations_cache = None

    def set_activity_focus(self, scope: str) -> dict[str, Any]:
        selected = scope if scope in {"online", "library", "preview"} else "library"
        self._transfer_gate.set_focus(selected)
        self._query_gate.set_focus(selected)
        return {"focus": selected, "transfers": self._transfer_gate.snapshot(), "queries": self._query_gate.snapshot()}

    @contextmanager
    def _query_slot(self, *, background: bool = False):
        # Signal image scheduling before waiting for one of the six query slots,
        # so queued user queries also stop new background transfers from
        # consuming the protected capacity.
        with self._transfer_gate.query():
            with self._query_gate.slot(background=background):
                yield

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    @staticmethod
    def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def online_preferences(self) -> dict[str, Any]:
        with self._cache_lock:
            excluded = self._search_list(self._preferences.get("excludedRecommendationTags"))
        return {
            "excludedRecommendationTags": list(dict.fromkeys(excluded)),
            "persisted": self._preferences_store_path.is_file(),
        }

    def update_online_preferences(self, payload: dict[str, Any]) -> dict[str, Any]:
        unique: dict[str, str] = {}
        for value in payload.get("excludedRecommendationTags", []):
            clean = str(value or "").strip()
            if clean:
                unique.setdefault(clean.casefold(), clean)
        excluded = list(unique.values())[:100]
        preferences = {
            "schemaVersion": 1,
            "excludedRecommendationTags": excluded,
            "updatedAt": datetime.now(UTC).isoformat(),
        }
        self._write_json_file(self._preferences_store_path, preferences)
        with self._cache_lock:
            self._preferences = preferences
        return self.online_preferences()

    def close(self, timeout: float = 2.0) -> bool:
        self._background_stop.set()
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in list(self._background_threads):
            thread.join(max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in self._background_threads)

    def _new_query_client(self, cover: bool = False, *, direct: bool = False):
        option = self._option()
        self._enable_query_session(option)
        config = getattr(getattr(getattr(option, "client", None), "postman", None), "meta_data", None)
        if direct and config is not None:
            config.proxies = {}
        timeout = config.get("timeout", 12) if config is not None else 12
        client = option.new_jm_client(timeout=timeout)
        if hasattr(client, "retry_times"):
            client.retry_times = min(int(client.retry_times), 0 if cover else 1)
        return client

    def _new_html_client(self, *, direct: bool = False):
        option = self._option()
        self._enable_query_session(option)
        config = getattr(getattr(getattr(option, "client", None), "postman", None), "meta_data", None)
        if direct and config is not None:
            config.proxies = {}
        timeout = config.get("timeout", 12) if config is not None else 12
        client = option.new_jm_client(impl="html", timeout=timeout)
        if hasattr(client, "retry_times"):
            client.retry_times = min(int(client.retry_times), 1)
        return client

    @staticmethod
    def _enable_query_session(option) -> None:
        """Reuse TCP/TLS connections for sequential metadata requests.

        Downloaders keep their existing transport because one sync downloader
        shares its client across image threads. Query clients are thread-local,
        so a curl-cffi Session is both safe and substantially cheaper through a
        local proxy.
        """
        postman = getattr(getattr(option, "client", None), "postman", None)
        config = getattr(postman, "src_dict", None)
        if isinstance(config, dict) and config.get("type") == "curl_cffi":
            config["type"] = "curl_cffi_session"

    def _proxy_is_explicit(self) -> bool:
        return bool(
            self.database.get_setting("provider.proxy", "").strip()
            or self.database.get_setting("provider.optionPath", "").strip()
        )

    def _record_connection(self, reachable: bool, error: Exception | None = None) -> None:
        with self._connection_lock:
            self._connection_reachable = reachable
            self._connection_error = "" if reachable or error is None else str(error)
            self._connection_checked_at = datetime.now(UTC).isoformat()

    def _run_query(self, operation: Callable[[Any], Any], *, cover: bool = False):
        """Run a JM request and bypass an unusable system proxy when safe."""
        try:
            result = operation(self._new_query_client(cover=cover))
        except Exception as first_error:
            if self._proxy_is_explicit():
                self._record_connection(False, first_error)
                raise
            try:
                result = operation(self._new_query_client(cover=cover, direct=True))
            except Exception as direct_error:
                self._record_connection(False, direct_error)
                raise direct_error from first_error
        self._record_connection(True)
        return result

    @staticmethod
    def _complete_episode_list(episode_list: list | tuple) -> list[tuple]:
        """Preserve every distinct photo id, including duplicate/fractional sort values."""
        prepared: list[tuple[tuple[Any, ...], int, Decimal | None]] = []
        seen_photo_ids: set[str] = set()
        for position, raw_episode in enumerate(episode_list):
            episode = tuple(raw_episode)
            if len(episode) < 2:
                continue
            photo_id = str(episode[0])
            if not photo_id or photo_id in seen_photo_ids:
                continue
            seen_photo_ids.add(photo_id)
            try:
                sort_value = Decimal(str(episode[1]).strip())
                if not sort_value.is_finite():
                    sort_value = None
            except (InvalidOperation, TypeError, ValueError):
                sort_value = None
            prepared.append((episode, position, sort_value))

        prepared.sort(key=lambda item: (
            0 if item[2] is not None else 1,
            item[2] if item[2] is not None else Decimal(item[1]),
            item[1],
        ))
        return [item[0] for item in prepared]

    def _complete_album_class(self) -> type:
        base_class = self._module().JmModuleConfig.album_class()
        with self._album_class_lock:
            cached = self._complete_album_classes.get(base_class)
            if cached is not None:
                return cached
            complete_episode_list = self._complete_episode_list

            class CompleteEpisodeAlbum(base_class):
                @staticmethod
                def distinct_episode(episode_list: list):
                    return complete_episode_list(episode_list)

            CompleteEpisodeAlbum.__name__ = f"JmShelfComplete{base_class.__name__}"
            self._complete_album_classes[base_class] = CompleteEpisodeAlbum
            return CompleteEpisodeAlbum

    def _get_album_detail(self, client, source_id: str):
        # The API adapter normally drops entries sharing a sort value and casts
        # sorts to int. Ask it to instantiate our compatible album subclass so
        # 1.2 / 27.5 and duplicate-sort photo ids survive parsing.
        fetch_detail = getattr(client, "fetch_detail_entity", None)
        if callable(fetch_detail):
            return fetch_detail(source_id, self._complete_album_class())
        return client.get_album_detail(source_id)

    def _episode_list(self, album) -> list[tuple]:
        episodes = getattr(album, "episode_list", None) or []
        if not episodes:
            episodes = [(
                str(getattr(album, "id", "")),
                1,
                str(getattr(album, "title", "") or ""),
            )]
        return self._complete_episode_list(episodes)

    def _series_manifest(self, album) -> dict[str, Any]:
        episodes = self._episode_list(album)
        total = len(episodes)
        album_title = str(getattr(album, "title", "") or "").strip()
        items: list[dict[str, Any]] = []
        for episode in episodes:
            photo_id, episode_index, episode_name = episode[:3]
            chapter_index = normalize_chapter_index(episode_index)
            chapter_title = str(episode_name or "").strip()
            if not chapter_title:
                chapter_title = album_title if total == 1 else f"{album_title} · 第 {chapter_index} 话"
            items.append({
                "sourceId": str(photo_id),
                "title": chapter_title,
                "chapterIndex": chapter_index,
            })
        return {
            "seriesId": str(album.id),
            "seriesTitle": album_title,
            "updatedAtSource": self._search_date(getattr(album, "update_date", "")),
            "itemCount": len(items),
            "items": items,
        }

    @staticmethod
    def _module():
        try:
            import jmcomic  # type: ignore
        except ImportError as exc:
            raise RuntimeError("jmcomic 尚未安装，请先运行 scripts/setup-jm-provider.ps1") from exc
        return jmcomic

    def detect(self) -> dict[str, Any]:
        try:
            self._module()
            with self._connection_lock:
                connection = {
                    "reachable": self._connection_reachable,
                    "connectionError": self._connection_error,
                    "connectionCheckedAt": self._connection_checked_at,
                }
            return {
                "available": True,
                "runtime": "python",
                "command": "uv environment",
                "version": importlib.metadata.version("jmcomic"),
                **connection,
                **self.auth_status(),
            }
        except Exception as exc:
            return {
                "available": False,
                "reachable": False,
                "reason": str(exc),
                "connectionError": str(exc),
                "connectionCheckedAt": datetime.now(UTC).isoformat(),
                **self.auth_status(),
            }

    def probe(self) -> dict[str, Any]:
        self._run_query(lambda client: client.setting())
        return self.detect()

    def _option(self):
        jmcomic = self._module()
        option_path = self.database.get_setting("provider.optionPath", "").strip()
        if option_path:
            resolved = Path(option_path).expanduser().resolve()
            if not resolved.is_file():
                raise RuntimeError(f"jmcomic 配置文件不存在：{resolved}")
            option = jmcomic.create_option_by_file(str(resolved))
        else:
            option = jmcomic.JmOption.default()
        proxy = self.database.get_setting("provider.proxy", "").strip()
        if proxy:
            option.client.postman.meta_data.proxies = proxy
        account = self.credentials.load()
        cookies = account.get("cookies") if account else None
        if isinstance(cookies, dict) and cookies:
            option.update_cookies(cookies)
        else:
            # The mobile API only checks whether a cookie jar is present. If it
            # is empty jmcomic performs a blocking /setting request for every
            # cold process before the actual chapter request. A harmless local
            # marker avoids that extra round trip without pretending the user
            # is authenticated.
            configured = getattr(getattr(getattr(option, "client", None), "postman", None), "meta_data", None)
            existing = configured.get("cookies") if configured is not None else None
            if not existing:
                option.update_cookies({"jmshelf_client": "1"})
        return option

    def _image_domains(self, module, current: str = "", *, spread: bool = False) -> list[str]:
        config = getattr(module, "JmModuleConfig", None)
        configured = [str(value).strip() for value in getattr(config, "DOMAIN_IMAGE_LIST", [])]
        with self._image_domain_lock:
            preferred = self._preferred_image_domain
            blocked = dict(self._image_domain_failures)
            cursor = self._image_domain_cursor
            if spread:
                self._image_domain_cursor += 1
        now = time.monotonic()
        # Keep a custom/current photo host reachable within the bounded set of
        # attempts instead of letting the six built-in hosts crowd it out.
        ordered = list(dict.fromkeys((preferred, current, *configured, *self._PREVIEW_IMAGE_DOMAINS)))
        healthy = [domain for domain in ordered if domain and blocked.get(domain, 0) <= now]
        cooling = [domain for domain in ordered if domain and domain not in healthy]
        if spread and healthy:
            # An idle download may launch two chapters and 24 image workers at
            # once. Spread their first attempts instead of stampeding one CDN.
            offset = cursor % len(healthy)
            healthy = healthy[offset:] + healthy[:offset]
        return healthy + cooling

    def _record_image_domain(self, domain: str, *, success: bool) -> None:
        if not domain:
            return
        with self._image_domain_lock:
            if success:
                self._preferred_image_domain = domain
                self._image_domain_failures.pop(domain, None)
            else:
                # A failed or slow node cools down for both previews and durable
                # downloads, but remains a last-resort route if all nodes fail.
                self._image_domain_failures[domain] = time.monotonic() + 120

    @staticmethod
    def _image_url_on_domain(url: str, domain: str) -> str:
        parsed = urlparse(str(url))
        if not parsed.netloc or not domain:
            return str(url)
        return parsed._replace(netloc=domain).geturl()

    def auth_status(self) -> dict[str, Any]:
        account = self.credentials.load()
        username = str(account.get("username") or "") if account else ""
        cookies = account.get("cookies") if account else None
        authenticated = bool(username and isinstance(cookies, dict) and cookies)
        return {"authenticated": authenticated, "username": username if authenticated else ""}

    def login(self, username: Any, password: Any) -> dict[str, Any]:
        clean_username = str(username or "").strip()
        clean_password = str(password or "")
        if not clean_username or not clean_password:
            raise ValueError("请输入账号和密码")
        with self._login_lock:
            client, response = self._run_query(
                lambda query_client: (query_client, query_client.login(clean_username, clean_password))
            )
            cookies = dict(client["cookies"] or {})
            if not cookies.get("AVS"):
                raise RuntimeError("登录失败，服务端未返回有效会话")
            response_data = getattr(response, "res_data", None) or {}
            saved_username = str(response_data.get("username") or clean_username)
            self.credentials.save({
                "username": saved_username,
                "password": clean_password,
                "cookies": cookies,
                "updatedAt": datetime.now(UTC).isoformat(),
            })
            self.invalidate_cache()
            return self.auth_status()

    def restore_login(self) -> dict[str, Any]:
        """Refresh a persisted JM session without blocking application startup."""
        # Keep the file read and the refreshed credential write in the same
        # critical section as logout. Otherwise logout could delete the file
        # after this read but before login() saves the refreshed cookies,
        # silently restoring an account the user explicitly signed out of.
        with self._login_lock:
            account = self.credentials.load() or {}
            username = str(account.get("username") or "").strip()
            password = str(account.get("password") or "")
            if not username or not password:
                return {"attempted": False, "restored": False, **self.auth_status()}
            try:
                status = self.login(username, password)
            except Exception as exc:
                return {
                    "attempted": True,
                    "restored": False,
                    "error": str(exc) or type(exc).__name__,
                    **self.auth_status(),
                }
            return {"attempted": True, "restored": True, "error": "", **status}

    def schedule_login_restore(self) -> dict[str, Any]:
        account = self.credentials.load() or {}
        if not str(account.get("username") or "").strip() or not str(account.get("password") or ""):
            self._login_restore_done.set()
            return {"scheduled": False}
        with self._background_lock:
            if self._login_restore_running:
                return {"scheduled": False, "running": True}
            self._login_restore_running = True
            self._login_restore_error = ""
            self._login_restore_done.clear()

        def worker() -> None:
            result: dict[str, Any] = {}
            try:
                if not self._background_stop.is_set():
                    result = self.restore_login()
            finally:
                with self._background_lock:
                    self._login_restore_error = str(result.get("error") or "")
                    self._login_restore_running = False
                self._login_restore_done.set()

        thread = threading.Thread(target=worker, name="jm-login-restore", daemon=True)
        self._background_threads.append(thread)
        thread.start()
        return {"scheduled": True, "running": True}

    def logout(self) -> dict[str, Any]:
        with self._login_lock:
            self.credentials.clear()
            self.invalidate_cache()
            return self.auth_status()

    @staticmethod
    def _favorite_string_list(value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        clean = str(value or "").strip()
        return [clean] if clean else []

    def favorites(self, *, fresh: bool = False) -> dict[str, Any]:
        # An immediate click after launch should use the newly refreshed cookies
        # rather than racing the background login with a stale saved session.
        self._login_restore_done.wait(timeout=30)
        account = self.auth_status()
        if not account["authenticated"]:
            raise ValueError("请先登录 JM 账号后查看收藏")
        with self._favorites_lock:
            with self._cache_lock:
                cached = self._favorites_cache
                if not fresh and cached and time.monotonic() - cached[0] < 5 * 60:
                    return deepcopy(cached[1])

            username = str(account["username"])

            def fetch_page(page_number: int):
                with self._query_slot():
                    return self._run_query(
                        lambda client: client.favorite_folder(page=page_number, folder_id="0", username=username)
                    )

            try:
                first_page = fetch_page(1)
                page_count = max(1, int(getattr(first_page, "page_count", 1) or 1))
                pages: dict[int, Any] = {1: first_page}
                if page_count > 1:
                    workers = min(4, page_count - 1)
                    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="jmshelf-favorites") as executor:
                        futures = {executor.submit(fetch_page, page): page for page in range(2, page_count + 1)}
                        for future in as_completed(futures):
                            pages[futures[future]] = future.result()
            except Exception as exc:
                if "401" in str(exc):
                    self.credentials.clear()
                    with self._cache_lock:
                        self._favorites_cache = None
                    raise ValueError("JM 登录会话已失效，请重新登录") from exc
                raise

            items: list[dict[str, Any]] = []
            seen: set[str] = set()
            for page_number in range(1, page_count + 1):
                page = pages.get(page_number)
                for raw_id, raw_info in getattr(page, "content", []) or []:
                    album_id = normalize_plate(raw_id)
                    if not album_id or album_id in seen:
                        continue
                    seen.add(album_id)
                    info = getattr(raw_info, "src_dict", raw_info)
                    info = info if isinstance(info, dict) else {}
                    authors = self._favorite_string_list(info.get("author") or info.get("authors"))
                    items.append({
                        "albumId": album_id,
                        "title": str(info.get("name") or info.get("title") or f"JM{album_id}").strip(),
                        "authors": authors,
                        "tags": self._favorite_string_list(info.get("tags")),
                        "description": str(info.get("description") or "").strip(),
                        "coverUrl": f"/media/source-cover/{album_id}",
                        "latestEpisode": str(info.get("latest_ep") or "").strip(),
                        "latestEpisodeId": str(info.get("latest_ep_aid") or "").strip(),
                    })

            result = {
                "authenticated": True,
                "username": username,
                "total": max(len(items), int(getattr(first_page, "total", len(items)) or len(items))),
                "items": items,
                "fetchedAt": datetime.now(UTC).isoformat(),
            }
            with self._cache_lock:
                self._favorites_cache = (time.monotonic(), deepcopy(result))
            return result

    @staticmethod
    def _search_list(value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        clean = str(value or "").strip()
        if not clean:
            return []
        return [part.strip() for part in clean.replace("，", ",").split(",") if part.strip()]

    @staticmethod
    def _search_date(value: Any) -> str:
        clean = str(value or "").strip()
        if not clean or clean == "0":
            return ""
        if clean.isdigit() and 9 <= len(clean) <= 13:
            try:
                timestamp = int(clean) / (1000 if len(clean) == 13 else 1)
                return datetime.fromtimestamp(timestamp, UTC).date().isoformat()
            except (OverflowError, OSError, ValueError):
                pass
        for candidate in (clean, clean[:10]):
            try:
                return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                pass
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(clean, fmt).date().isoformat()
            except ValueError:
                pass
        return ""

    def _search_item(self, raw_id: Any, raw_info: Any) -> dict[str, Any]:
        album_id = normalize_plate(raw_id)
        info = getattr(raw_info, "src_dict", raw_info)
        info = info if isinstance(info, dict) else {}
        authors = self._search_list(info.get("author") or info.get("authors"))
        tags = self._search_list(info.get("tags"))
        published = self._search_date(
            info.get("pub_date")
            or info.get("published_at")
            or info.get("addtime")
            or info.get("adddate")
            or info.get("date")
        )
        updated = self._search_date(info.get("update_date") or info.get("updated_at") or info.get("update_at"))
        return {
            "albumId": album_id,
            "title": str(info.get("name") or info.get("title") or f"JM{album_id}").strip(),
            "authors": authors,
            "tags": tags,
            "works": self._search_list(info.get("works") or info.get("work")),
            "actors": self._search_list(info.get("actors") or info.get("actor")),
            "description": str(info.get("description") or "").strip(),
            "publishedAt": published,
            "updatedAtSource": updated,
            "coverUrl": f"/media/source-thumbnail/{album_id}",
            "directCoverUrl": f"https://cdn-msp.jmapiproxy1.cc/media/albums/{album_id}_3x4.jpg",
            "category": str(
                (info.get("category") or {}).get("title", "")
                if isinstance(info.get("category"), dict)
                else getattr(info.get("category"), "title", "") or ""
            ).strip(),
        }

    def search(
        self,
        query: Any,
        *,
        page: int = 1,
        mode: str = "site",
        order: str = "latest",
        time_range: str = "all",
        category: str = "all",
        date_from: str = "",
        date_to: str = "",
        background: bool = False,
    ) -> dict[str, Any]:
        clean_query = str(query or "").strip()
        if not clean_query:
            raise ValueError("请输入搜索关键词")
        page = max(1, int(page or 1))
        jmcomic = self._module()
        constants = jmcomic.JmMagicConstants
        modes = {
            "site": "search_site",
            "work": "search_work",
            "author": "search_author",
            "tag": "search_tag",
            "actor": "search_actor",
        }
        orders = {
            "latest": constants.ORDER_BY_LATEST,
            "views": constants.ORDER_BY_VIEW,
            "pictures": constants.ORDER_BY_PICTURE,
            "likes": constants.ORDER_BY_LIKE,
            "rating": constants.ORDER_BY_SCORE,
            "comments": constants.ORDER_BY_COMMENT,
        }
        times = {
            "all": constants.TIME_ALL,
            "today": constants.TIME_TODAY,
            "week": constants.TIME_WEEK,
            "month": constants.TIME_MONTH,
        }
        categories = {
            "all": constants.CATEGORY_ALL,
            "doujin": constants.CATEGORY_DOUJIN,
            "single": constants.CATEGORY_SINGLE,
            "short": constants.CATEGORY_SHORT,
            "another": constants.CATEGORY_ANOTHER,
            "hanman": constants.CATEGORY_HANMAN,
            "meiman": constants.CATEGORY_MEIMAN,
            "cosplay": constants.CATEGORY_DOUJIN_COSPLAY,
            "3d": constants.CATEGORY_3D,
            "english": constants.CATEGORY_ENGLISH_SITE,
        }
        if mode not in modes or order not in orders or category.casefold() not in categories:
            raise ValueError("不支持的在线搜索筛选条件")
        extended_time = time_range in {"quarter", "year", "custom"}
        if not extended_time and time_range not in times:
            raise ValueError("不支持的时间范围")
        with self._query_slot(background=background):
            result_page = self._run_query(
                lambda client: getattr(client, modes[mode])(
                    clean_query,
                    page=page,
                    order_by=orders[order],
                    time=times.get(time_range, constants.TIME_ALL),
                    category=categories[category.casefold()],
                )
            )
        items = [self._search_item(raw_id, raw_info) for raw_id, raw_info in (result_page.content or [])]
        items = [item for item in items if item["albumId"]]
        partial_time_filter = False
        if extended_time:
            today = datetime.now(UTC).date()
            if time_range == "quarter":
                start, end = today - timedelta(days=92), today
            elif time_range == "year":
                start, end = today - timedelta(days=366), today
            else:
                try:
                    start = date.fromisoformat(date_from) if date_from else date.min
                    end = date.fromisoformat(date_to) if date_to else today
                except ValueError as exc:
                    raise ValueError("自定义日期格式无效") from exc
                if start > end:
                    raise ValueError("开始日期不能晚于结束日期")

            missing = [item for item in items if not item["publishedAt"]]
            # The upstream search response omits dates. Hydrate a bounded result
            # page so this extension remains responsive and honest about scope.
            hydrate = missing[:32]
            if hydrate:
                clients = threading.local()

                def hydrate_date(item: dict[str, Any]) -> tuple[str, str, str]:
                    if not hasattr(clients, "client"):
                        clients.client = self._new_query_client()
                    with self._query_slot(background=background):
                        album = self._get_album_detail(clients.client, item["albumId"])
                    return (
                        item["albumId"],
                        self._search_date(getattr(album, "pub_date", "")),
                        self._search_date(getattr(album, "update_date", "")),
                    )

                with ThreadPoolExecutor(max_workers=min(4, len(hydrate)), thread_name_prefix="jm-search-date") as executor:
                    for album_id, published, updated in executor.map(hydrate_date, hydrate):
                        item = next(candidate for candidate in items if candidate["albumId"] == album_id)
                        item["publishedAt"] = published
                        item["updatedAtSource"] = updated
            partial_time_filter = len(missing) > len(hydrate)
            dated_items = []
            for item in items:
                try:
                    published = date.fromisoformat(item["publishedAt"])
                except (TypeError, ValueError):
                    continue
                if start <= published <= end:
                    dated_items.append(item)
            items = dated_items

        page_size = int(getattr(result_page, "page_size", len(items) or 1) or 1)
        total = int(getattr(result_page, "total", len(items)) or len(items))
        page_count = int(getattr(result_page, "page_count", 1) or 1)
        if extended_time:
            total = len(items)
            page_count = 1
        return {
            "query": clean_query,
            "page": page,
            "pageSize": page_size,
            "pageCount": page_count,
            "total": total,
            "items": items,
            "timeFilterMode": "page-post-filter" if extended_time else "upstream",
            "partialTimeFilter": partial_time_filter,
        }

    @staticmethod
    def _recommend_tokens(value: str) -> set[str]:
        normalized = str(value or "").casefold()
        for punctuation in "[]()（）【】·・,，.!！?？/\\-_":
            normalized = normalized.replace(punctuation, " ")
        return {token for token in normalized.split() if len(token) > 1}

    @staticmethod
    def _recommendation_page(result: dict[str, Any], *, limit: int, offset: int) -> dict[str, Any]:
        candidates = result.get("items") or []
        batch_size = max(1, min(24, int(limit or 12)))
        batch_count = max(1, math.ceil(len(candidates) / batch_size))
        effective_batch = max(0, int(offset or 0)) % batch_count
        batch_start = effective_batch * batch_size
        return {
            **result,
            "batch": effective_batch,
            "batchCount": batch_count,
            "items": deepcopy(candidates[batch_start:batch_start + batch_size]),
        }

    @staticmethod
    def _ordered_recommendations(result: dict[str, Any], order: str) -> dict[str, Any]:
        selected = order if order in {"diverse", "match", "latest", "pictures"} else "diverse"
        ordered = deepcopy(result)
        items = list(ordered.get("items") or [])
        if selected == "match":
            items.sort(key=lambda item: (-float(item.get("recommendationScore") or 0), str(item.get("albumId") or "")))
        elif selected == "latest":
            items.sort(key=lambda item: (
                str(item.get("publishedAt") or item.get("updatedAtSource") or ""),
                str(item.get("albumId") or ""),
            ), reverse=True)
        elif selected == "pictures":
            items.sort(key=lambda item: (-int(item.get("pageCount") or 0), -float(item.get("recommendationScore") or 0)))
        ordered["items"] = items
        ordered["sort"] = selected
        return ordered

    def _without_recommendation_tags(
        self,
        result: dict[str, Any],
        excluded_tags: list[str] | tuple[str, ...] | None,
    ) -> dict[str, Any]:
        excluded = {
            clean.casefold()
            for value in (excluded_tags or [])
            if (clean := str(value or "").strip())
        }
        filtered = deepcopy(result)
        candidates = list(filtered.get("items") or [])
        if excluded:
            cached_metadata = {
                item["albumId"]: item
                for item in self.database.get_online_metadata([
                    candidate.get("albumId") for candidate in candidates
                ])
            }
            candidates = [
                item for item in candidates
                if excluded.isdisjoint({
                    str(tag or "").strip().casefold()
                    for tag in (
                        self._search_list(item.get("tags"))
                        + self._search_list(item.get("recommendationTags"))
                        + self._search_list(cached_metadata.get(str(item.get("albumId") or ""), {}).get("tags"))
                    )
                    if str(tag or "").strip()
                })
            ]
        filtered["items"] = candidates
        filtered["excludedTags"] = sorted(excluded)
        filtered["excludedCount"] = len((result.get("items") or [])) - len(candidates)
        return filtered

    def recommendations(
        self,
        raw_plate: Any = None,
        *,
        limit: int = 12,
        offset: int = 0,
        order: str = "diverse",
        background: bool = False,
        excluded_tags: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Mine a taste profile from the shelf, then diversity-rerank remote candidates."""
        source_id = normalize_plate(raw_plate) if raw_plate else ""
        if source_id:
            metadata = self.lookup(source_id, defer_covers=True)
            shelf_items = [
                {**item, "seriesTitle": metadata.get("seriesTitle") or item.get("title", "")}
                for item in (metadata.get("items") or [metadata])
            ]
        else:
            shelf_items = [comic for comic in self.database.list_comics() if comic.get("sourceId")]
        if not shelf_items:
            return {
                "seed": None,
                "profile": None,
                "items": [],
                "reason": "书架里还没有可用于推荐的 JM 作品",
            }

        profile_key = tuple(sorted((
            str(comic.get("sourceId") or ""),
            str(comic.get("seriesId") or ""),
            tuple(sorted(self._search_list(comic.get("tags")), key=str.casefold)),
            tuple(sorted(self._search_list(comic.get("authors")), key=str.casefold)),
            max(0, int(comic.get("readCount") or 0)),
            str(comic.get("lastReadAt") or ""),
            max(0, int(comic.get("progressPage") or 0)),
            max(0, int(comic.get("pageCount") or 0)),
            tuple(sorted(str(value) for value in (comic.get("collections") or []))),
        ) for comic in shelf_items))
        with self._cache_lock:
            cached_recommendations = self._recommendations_cache
            if (
                cached_recommendations
                and cached_recommendations[1] == profile_key
                and time.monotonic() - cached_recommendations[0] < 300
            ):
                return self._recommendation_page(
                    self._without_recommendation_tags(
                        self._ordered_recommendations(cached_recommendations[2], order),
                        excluded_tags,
                    ),
                    limit=limit,
                    offset=offset,
                )

        groups: dict[str, dict[str, Any]] = {}
        excluded_ids: set[str] = set()
        for comic in shelf_items:
            comic_source = str(comic.get("sourceId") or "")
            comic_series = str(comic.get("seriesId") or comic_source)
            excluded_ids.update(value for value in (comic_source, comic_series) if value)
            group = groups.setdefault(comic_series, {
                "items": [],
                "tags": {},
                "authors": {},
            })
            group["items"].append(comic)
            for tag in self._search_list(comic.get("tags")):
                group["tags"].setdefault(tag.casefold(), tag)
            for author in self._search_list(comic.get("authors")):
                group["authors"].setdefault(author.casefold(), author)

        now = datetime.now(UTC)
        generic_tags = {
            "中文", "中國翻譯", "中国翻译", "漢化", "汉化", "全彩", "無修正", "无修正",
            "dl版", "同人", "單本", "单本", "chinese", "full color", "uncensored",
        }
        generic_tags = {value.casefold() for value in generic_tags}
        tag_scores: dict[str, float] = defaultdict(float)
        author_scores: dict[str, float] = defaultdict(float)
        tag_sources: dict[str, int] = defaultdict(int)
        author_sources: dict[str, int] = defaultdict(int)
        tag_labels: dict[str, str] = {}
        author_labels: dict[str, str] = {}
        engaged_groups = 0

        def parsed_datetime(value: Any) -> datetime | None:
            clean = str(value or "").strip()
            if not clean:
                return None
            try:
                parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

        for group in groups.values():
            items = group["items"]
            reads = sum(max(0, int(item.get("readCount") or 0)) for item in items)
            completion = max((
                min(1.0, (int(item.get("progressPage") or 0) + 1) / max(1, int(item.get("pageCount") or 0)))
                if item.get("lastReadAt") else 0.0
                for item in items
            ), default=0.0)
            last_reads = [value for item in items if (value := parsed_datetime(item.get("lastReadAt")))]
            days_since_read = (now - max(last_reads)).total_seconds() / 86400 if last_reads else None
            recency = 3.2 * math.exp(-max(0.0, days_since_read) / 75) if days_since_read is not None else 0.0
            engagement = min(3.2, math.log2(reads + 1) * 1.15)
            collection_signal = 0.55 if any(item.get("collections") for item in items) else 0.0
            group_weight = 0.8 + recency + engagement + completion * 1.35 + collection_signal
            if reads or last_reads or completion:
                engaged_groups += 1
            for key, label in group["tags"].items():
                specificity = 0.28 if key in generic_tags else 1.0
                tag_scores[key] += group_weight * specificity
                tag_sources[key] += 1
                tag_labels.setdefault(key, label)
            for key, label in group["authors"].items():
                author_scores[key] += group_weight * 1.08
                author_sources[key] += 1
                author_labels.setdefault(key, label)

        top_tag_keys = sorted(tag_scores, key=lambda key: (-tag_scores[key], -tag_sources[key], tag_labels[key]))
        top_author_keys = sorted(author_scores, key=lambda key: (-author_scores[key], -author_sources[key], author_labels[key]))
        profile_tags = [
            {"name": tag_labels[key], "weight": round(tag_scores[key], 2), "sourceCount": tag_sources[key]}
            for key in top_tag_keys[:10]
        ]
        profile_authors = [
            {"name": author_labels[key], "weight": round(author_scores[key], 2), "sourceCount": author_sources[key]}
            for key in top_author_keys[:6]
        ]

        # Query several independent taste signals. Each signal contributes to
        # one shared candidate pool; batch selection happens after the full
        # diversity pass so "换一批" does not simply reshuffle the same cards.
        query_terms = (
            [("author", author_labels[key], key) for key in top_author_keys[:2]]
            + [("tag", tag_labels[key], key) for key in top_tag_keys[:6]]
        )
        candidates: dict[str, dict[str, Any]] = {}

        def fetch_candidates(search_spec: tuple[str, str, str]):
            mode, value, key = search_spec
            result = self.search(
                value,
                mode=mode,
                order="views",
                time_range="all",
                background=background,
            )
            return mode, key, result["items"]

        with ThreadPoolExecutor(max_workers=min(4, len(query_terms) or 1), thread_name_prefix="jm-recommend") as executor:
            futures = {executor.submit(fetch_candidates, spec): spec for spec in query_terms}
            for future in as_completed(futures):
                try:
                    mode, key, result_items = future.result()
                except Exception:
                    continue
                for item in result_items:
                    if item["albumId"] in excluded_ids:
                        continue
                    candidate = candidates.setdefault(item["albumId"], {**item, "signals": []})
                    candidate["signals"].append((mode, key))

        ranked: list[dict[str, Any]] = []
        for item in candidates.values():
            candidate_authors = {value.casefold() for value in item["authors"]}
            candidate_tags = {value.casefold() for value in item["tags"]}
            author_overlap = candidate_authors & set(author_scores)
            tag_overlap = candidate_tags & set(tag_scores)
            score = sum(author_scores[key] * 1.35 for key in author_overlap)
            score += sum(tag_scores[key] for key in tag_overlap)
            # Search hits remain useful when the result adapter omits author or tag metadata.
            signals = list(item.pop("signals"))
            for mode, key in signals:
                score += (author_scores.get(key, 0.0) if mode == "author" else tag_scores.get(key, 0.0)) * 0.5
            if score <= 0:
                continue
            signalled_tags = {key for mode, key in signals if mode == "tag" and key in tag_scores}
            signalled_authors = {key for mode, key in signals if mode == "author" and key in author_scores}
            matched_tags = sorted(tag_overlap | signalled_tags, key=lambda key: -tag_scores[key])
            matched_authors = sorted(author_overlap | signalled_authors, key=lambda key: -author_scores[key])
            reasons = []
            if matched_authors:
                reasons.append(f"偏好作者 {author_labels[matched_authors[0]]}")
            if matched_tags:
                reasons.append("偏好标签 " + " / ".join(tag_labels[key] for key in matched_tags[:3]))
            reasons.append("书架画像匹配")
            ranked.append({
                **item,
                "recommendationScore": round(score, 2),
                "recommendationReason": " · ".join(reasons),
                "recommendationTags": [tag_labels[key] for key in matched_tags[:5]],
                "recommendationAuthors": [author_labels[key] for key in matched_authors[:3]],
            })
        ranked.sort(key=lambda item: (-item["recommendationScore"], item["albumId"]))

        # Greedy diversity reranking: retain relevance but penalize repeated
        # authors/tags so the shelf profile produces a discovery feed, not clones.
        diversified: list[dict[str, Any]] = []
        author_repeats: dict[str, int] = defaultdict(int)
        tag_repeats: dict[str, int] = defaultdict(int)
        pool = ranked[:]
        while pool:
            def diversity_score(item: dict[str, Any]) -> float:
                repeated_authors = sum(author_repeats[value.casefold()] for value in item["authors"])
                repeated_tags = sum(tag_repeats[value.casefold()] for value in item.get("recommendationTags", []))
                stable_variation = (int(item["albumId"]) % 29) / 100
                return item["recommendationScore"] - repeated_authors * 4.5 - repeated_tags * 0.75 + stable_variation

            best = max(pool, key=diversity_score)
            pool.remove(best)
            diversified.append(best)
            for author in best["authors"]:
                author_repeats[author.casefold()] += 1
            for tag in best.get("recommendationTags", []):
                tag_repeats[tag.casefold()] += 1

        profile = {
            "libraryItemCount": len(groups),
            "engagedItemCount": engaged_groups,
            "topTags": profile_tags,
            "topAuthors": profile_authors,
            "strategy": "标签频次 × 阅读次数 × 最近阅读 × 完成度 × 收藏归类，并做作者与标签多样性重排",
            "generatedAt": now.isoformat(),
        }
        result = {
            "seed": {"sourceId": source_id, "title": f"整个书架 · {len(groups)} 个兴趣样本"},
            "profile": profile,
            "candidateCount": len(ranked),
            "items": diversified,
        }
        with self._cache_lock:
            self._recommendations_cache = (time.monotonic(), profile_key, deepcopy(result))
        return self._recommendation_page(
            self._without_recommendation_tags(
                self._ordered_recommendations(result, order),
                excluded_tags,
            ),
            limit=limit,
            offset=offset,
        )

    def _current_recommendation_source_ids(self) -> list[str]:
        return sorted({
            str(comic.get("sourceId") or "")
            for comic in self.database.list_comics()
            if comic.get("sourceId")
        })

    def schedule_recommendation_refresh(self, *, force: bool = False, delay: float = 0.0) -> dict[str, Any]:
        current_ids = self._current_recommendation_source_ids()
        snapshot_ids = set(str(value) for value in (self._recommendation_snapshot or {}).get("sourceIds", []))
        pending_count = len(set(current_ids) - snapshot_ids)
        changed_count = len(set(current_ids) ^ snapshot_ids)
        threshold = 10
        if not current_ids:
            return {"refreshing": False, "pendingNewItemCount": 0, "threshold": threshold, "emptyLibrary": True}
        should_refresh = (
            force
            or self._recommendation_snapshot is None
            or int(self._recommendation_snapshot.get("schemaVersion") or 0) < 3
            or not isinstance(self._recommendation_snapshot.get("result"), dict)
            or pending_count >= threshold
            or changed_count >= threshold
        )
        with self._background_lock:
            if not should_refresh or self._recommendation_refreshing:
                return {
                    "refreshing": self._recommendation_refreshing,
                    "pendingNewItemCount": pending_count,
                    "threshold": threshold,
                }
            self._recommendation_refreshing = True

        def worker() -> None:
            try:
                if delay > 0 and self._background_stop.wait(delay):
                    return
                if self._background_stop.is_set():
                    return
                self.recommendations(limit=24, offset=0, background=True)
                with self._cache_lock:
                    cached = deepcopy(self._recommendations_cache[2]) if self._recommendations_cache else None
                if not cached:
                    return
                snapshot_source_ids = self._current_recommendation_source_ids()
                payload = {
                    "schemaVersion": 3,
                    "updatedAt": datetime.now(UTC).isoformat(),
                    "sourceCount": len(snapshot_source_ids),
                    "sourceIds": snapshot_source_ids,
                    "result": cached,
                }
                self._write_json_file(self._recommendation_store_path, payload)
                with self._background_lock:
                    self._recommendation_snapshot = payload
            finally:
                with self._background_lock:
                    self._recommendation_refreshing = False

        thread = threading.Thread(target=worker, name="jmonline-profile-refresh", daemon=True)
        self._background_threads.append(thread)
        thread.start()
        return {"refreshing": True, "pendingNewItemCount": pending_count, "threshold": threshold}

    def recommendation_feed(
        self,
        *,
        limit: int = 12,
        offset: int = 0,
        order: str = "diverse",
        excluded_tags: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        refresh = self.schedule_recommendation_refresh()
        with self._background_lock:
            snapshot = deepcopy(self._recommendation_snapshot)
        if not snapshot or not isinstance(snapshot.get("result"), dict):
            return {
                "seed": None,
                "profile": None,
                "items": [],
                "batch": 0,
                "batchCount": 1,
                "updatedAt": None,
                "reason": (
                    "书架里还没有可用于推荐的 JM 作品"
                    if refresh.get("emptyLibrary")
                    else "正在后台生成首次书架画像，当前操作不会被阻塞"
                ),
                **refresh,
            }
        result = self._recommendation_page(
            self._without_recommendation_tags(
                self._ordered_recommendations(snapshot["result"], order),
                excluded_tags,
            ),
            limit=limit,
            offset=offset,
        )
        return {
            **result,
            "updatedAt": snapshot.get("updatedAt"),
            "sourceCount": int(snapshot.get("sourceCount") or 0),
            **refresh,
        }

    def category_listing(
        self,
        *,
        page: int = 1,
        category: str = "all",
        order: str = "latest",
        time_range: str = "all",
        background: bool = False,
    ) -> dict[str, Any]:
        jmcomic = self._module()
        constants = jmcomic.JmMagicConstants
        categories = {
            "all": constants.CATEGORY_ALL,
            "doujin": constants.CATEGORY_DOUJIN,
            "single": constants.CATEGORY_SINGLE,
            "short": constants.CATEGORY_SHORT,
            "another": constants.CATEGORY_ANOTHER,
            "hanman": constants.CATEGORY_HANMAN,
            "meiman": constants.CATEGORY_MEIMAN,
            "cosplay": constants.CATEGORY_DOUJIN_COSPLAY,
            "3d": constants.CATEGORY_3D,
            "english": constants.CATEGORY_ENGLISH_SITE,
        }
        orders = {
            "latest": constants.ORDER_BY_LATEST,
            "views": constants.ORDER_BY_VIEW,
            "pictures": constants.ORDER_BY_PICTURE,
            "likes": constants.ORDER_BY_LIKE,
            "rating": constants.ORDER_BY_SCORE,
            "comments": constants.ORDER_BY_COMMENT,
        }
        times = {
            "all": constants.TIME_ALL,
            "today": constants.TIME_TODAY,
            "week": constants.TIME_WEEK,
            "month": constants.TIME_MONTH,
        }
        category_key = str(category or "all").casefold()
        if category_key not in categories or order not in orders or time_range not in times:
            raise ValueError("不支持的在线分类筛选条件")
        page = max(1, int(page or 1))
        with self._query_slot(background=background):
            result_page = self._run_query(
                lambda client: client.categories_filter(
                    page,
                    times[time_range],
                    categories[category_key],
                    orders[order],
                )
            )
        items = [self._search_item(raw_id, raw_info) for raw_id, raw_info in (result_page.content or [])]
        items = [item for item in items if item["albumId"]]
        return {
            "resultKind": "category",
            "category": category_key,
            "page": page,
            "pageSize": int(getattr(result_page, "page_size", len(items) or 1) or 1),
            "pageCount": int(getattr(result_page, "page_count", 1) or 1),
            "total": int(getattr(result_page, "total", len(items)) or len(items)),
            "items": items,
        }

    def _build_home_snapshot(self) -> dict[str, Any]:
        specs = [
            ("latest", "最新上架", "刚刚更新", "latest", "all"),
            ("day", "今日热门", "24 小时观看排行", "views", "today"),
            ("week", "本周热门", "本周观看排行", "views", "week"),
            ("month", "本月热门", "本月观看排行", "views", "month"),
        ]

        def fetch(spec: tuple[str, str, str, str, str]) -> dict[str, Any]:
            key, title, subtitle, order, time_range = spec
            result = self.category_listing(
                page=1,
                category="all",
                order=order,
                time_range=time_range,
                background=True,
            )
            return {"key": key, "title": title, "subtitle": subtitle, "items": result["items"][:12]}

        sections: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="jmonline-home") as executor:
            futures = {executor.submit(fetch, spec): spec[0] for spec in specs}
            for future in as_completed(futures):
                try:
                    section = future.result()
                except Exception:
                    continue
                sections[section["key"]] = section
        return {
            "schemaVersion": 2,
            "updatedAt": datetime.now(UTC).isoformat(),
            "sections": [sections[key] for key, *_rest in specs if key in sections],
        }

    def schedule_home_refresh(self, *, force: bool = False, delay: float = 0.0) -> dict[str, Any]:
        updated_at = str((self._home_snapshot or {}).get("updatedAt") or "")
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(updated_at.replace("Z", "+00:00"))).total_seconds()
        except (TypeError, ValueError):
            age = float("inf")
        snapshot_version = int((self._home_snapshot or {}).get("schemaVersion") or 0)
        should_refresh = force or self._home_snapshot is None or snapshot_version < 2 or age >= 6 * 60 * 60
        with self._background_lock:
            if not should_refresh or self._home_refreshing:
                return {"refreshing": self._home_refreshing}
            self._home_refreshing = True

        def worker() -> None:
            try:
                if delay > 0 and self._background_stop.wait(delay):
                    return
                if self._background_stop.is_set():
                    return
                payload = self._build_home_snapshot()
                if not payload.get("sections"):
                    return
                self._write_json_file(self._home_store_path, payload)
                with self._background_lock:
                    self._home_snapshot = payload
            finally:
                with self._background_lock:
                    self._home_refreshing = False

        thread = threading.Thread(target=worker, name="jmonline-home-refresh", daemon=True)
        self._background_threads.append(thread)
        thread.start()
        return {"refreshing": True}

    def home_feed(self) -> dict[str, Any]:
        refresh = self.schedule_home_refresh()
        with self._background_lock:
            snapshot = deepcopy(self._home_snapshot)
        if not snapshot:
            return {
                "updatedAt": None,
                "sections": [],
                "reason": "首页榜单正在后台更新",
                **refresh,
            }
        return {**snapshot, **refresh}

    def start_background_refreshes(self) -> None:
        self.schedule_login_restore()
        self.schedule_home_refresh(delay=1.5)
        self.schedule_recommendation_refresh(delay=8.0)

    def online_metadata(self, album_ids: list[Any], *, background: bool = True) -> dict[str, Any]:
        ordered_ids = list(dict.fromkeys(
            source_id for value in album_ids if (source_id := normalize_plate(value))
        ))[:40]
        if not ordered_ids:
            return {"items": []}

        def complete(metadata: dict[str, Any] | None) -> bool:
            return bool(metadata and metadata.get("detailsComplete") and metadata.get("episodeManifest"))

        stored_items = {
            item["albumId"]: item
            for item in self.database.get_online_metadata(ordered_ids)
        }
        missing = [source_id for source_id in ordered_ids if not complete(stored_items.get(source_id))]

        clients = self._metadata_clients

        def client_for_thread(*, direct: bool = False):
            generation = self._cache_generation
            state = getattr(clients, "state", None)
            if not isinstance(state, dict) or state.get("generation") != generation:
                state = {"generation": generation}
                clients.state = state
            attribute = "directClient" if direct else "client"
            if attribute not in state:
                state[attribute] = self._new_html_client(direct=direct)
            return state[attribute]

        def fetch(source_id: str) -> tuple[str, dict[str, Any] | None]:
            # Different cards may hydrate concurrently. Only duplicate requests
            # for the same album are coalesced; unrelated albums never wait on
            # a global metadata lock while network I/O is in progress.
            with self._metadata_fetch_locks[hash(source_id) % len(self._metadata_fetch_locks)]:
                cached_items = self.database.get_online_metadata([source_id])
                cached = cached_items[0] if cached_items else None
                if complete(cached):
                    return source_id, cached
                try:
                    with self._query_slot(background=background):
                        album = client_for_thread().get_album_detail(source_id)
                except Exception as first_error:
                    if self._proxy_is_explicit():
                        self._record_connection(False, first_error)
                        raise
                    try:
                        with self._query_slot(background=background):
                            album = client_for_thread(direct=True).get_album_detail(source_id)
                    except Exception as direct_error:
                        self._record_connection(False, direct_error)
                        raise direct_error from first_error
                self._record_connection(True)
                manifest = self._series_manifest(album)
                episodes = manifest.get("items") or []
                metadata = {
                    "albumId": source_id,
                    "title": str(getattr(album, "title", "") or ""),
                    "publishedAt": self._search_date(getattr(album, "pub_date", "")),
                    "updatedAtSource": self._search_date(getattr(album, "update_date", "")),
                    "authors": [str(value) for value in (getattr(album, "authors", None) or [])],
                    "tags": [str(value) for value in (getattr(album, "tags", None) or [])],
                    "works": [str(value) for value in (getattr(album, "works", None) or [])],
                    "actors": [str(value) for value in (getattr(album, "actors", None) or [])],
                    "description": str(getattr(album, "description", "") or ""),
                    "pageCount": int(getattr(album, "page_count", 0) or 0),
                    "firstPhotoId": str(episodes[0]["sourceId"]) if episodes else source_id,
                    "episodeManifest": episodes,
                    "detailsComplete": True,
                    "fetchedAt": datetime.now(UTC).isoformat(),
                }
                # Persist each completed album immediately. A later page or app
                # launch performs an indexed lookup for only the requested IDs.
                stored = self.database.upsert_online_metadata(metadata)
                return source_id, stored or metadata

        if len(missing) == 1:
            try:
                fetch(missing[0])
            except Exception:
                pass
        elif missing:
            with ThreadPoolExecutor(max_workers=min(6, len(missing)), thread_name_prefix="jm-online-metadata") as executor:
                futures = [executor.submit(fetch, source_id) for source_id in missing]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        continue
        return {"items": self.database.get_online_metadata(ordered_ids)}

    @staticmethod
    def normalize_proxy(value: Any) -> str:
        proxy = str(value or "").strip()
        if not proxy:
            return ""
        if "://" not in proxy:
            proxy = f"http://{proxy}"
        parsed = urlparse(proxy)
        if parsed.scheme.casefold() not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
            raise ValueError("代理格式无效，请使用 http://127.0.0.1:7890 或 socks5://127.0.0.1:7890")
        if parsed.port is None:
            raise ValueError("代理地址需要包含端口")
        return proxy

    def _cached_cover(self, source_id: str, *, thumbnail: bool = False) -> Path | None:
        cover_dir = self.data_root / "covers" / ("thumbnails" if thumbnail else "chapters")
        if not cover_dir.is_dir():
            return None
        return next((path for path in cover_dir.glob(f"{source_id}.*") if path.stem == source_id and path.is_file() and path.stat().st_size > 0), None)

    def _download_default_cover(self, client, photo, *, thumbnail: bool = False) -> tuple[Path | None, str | None]:
        source_id = str(photo.id)
        locks = self._thumbnail_locks if thumbnail else self._cover_locks
        with locks[hash(source_id) % len(locks)]:
            return self._download_cover_locked(client, source_id, thumbnail=thumbnail)

    def _download_cover_locked(self, client, source_id: str, *, thumbnail: bool = False) -> tuple[Path | None, str | None]:
        cached = self._cached_cover(source_id, thumbnail=thumbnail)
        if cached:
            return cached, None
        warning = "封面 CDN 暂时不可用，元数据仍可正常使用"
        failure_key = f"thumbnail:{source_id}" if thumbnail else source_id
        with self._cache_lock:
            if time.monotonic() < self._cover_failures.get(failure_key, 0):
                return None, warning
        cover_dir = self.data_root / "covers" / ("thumbnails" if thumbnail else "chapters")
        cover_dir.mkdir(parents=True, exist_ok=True)
        cover_path = cover_dir / f"{source_id}.jpg"
        temporary_path = cover_dir / f".{source_id}.part.jpg"
        try:
            client.download_album_cover(source_id, str(temporary_path), size="_3x4" if thumbnail else "")
            if not temporary_path.is_file() or not temporary_path.stat().st_size:
                raise RuntimeError("封面为空")
            temporary_path.replace(cover_path)
            return cover_path, None
        except Exception:
            temporary_path.unlink(missing_ok=True)
            with self._cache_lock:
                self._cover_failures[failure_key] = time.monotonic() + 60
                self._cover_failures.move_to_end(failure_key)
                while len(self._cover_failures) > 512:
                    self._cover_failures.popitem(last=False)
            return None, warning

    def _thread_cover_client(self):
        generation = self._cache_generation
        if getattr(self._cover_clients, "generation", None) != generation:
            self._cover_clients.client = self._new_query_client(cover=True)
            self._cover_clients.generation = generation
        return self._cover_clients.client

    def get_cover(self, raw_plate: Any) -> Path | None:
        source_id = normalize_plate(raw_plate)
        if not source_id:
            return None
        cached = self._cached_cover(source_id)
        if cached:
            return cached
        with self._cover_slots:
            with self._transfer_gate.image(priority="visible"):
                client = self._thread_cover_client()
                cover, _ = self._download_default_cover(client, SimpleNamespace(id=source_id))
                return cover

    def get_thumbnail(self, raw_plate: Any) -> Path | None:
        source_id = normalize_plate(raw_plate)
        if not source_id:
            return None
        cached = self._cached_cover(source_id, thumbnail=True)
        if cached:
            return cached
        # A full cover already on disk is immediately usable and avoids an
        # unnecessary network request for existing shelf entries.
        full_cover = self._cached_cover(source_id)
        if full_cover:
            return full_cover
        with self._thumbnail_slots:
            with self._transfer_gate.image(priority="visible"):
                client = self._thread_cover_client()
                cover, _ = self._download_default_cover(
                    client,
                    SimpleNamespace(id=source_id),
                    thumbnail=True,
                )
                return cover

    @staticmethod
    def _episode_title(album, photo, index: int | float, total: int) -> str:
        title = str(getattr(photo, "title", "") or "").strip()
        if title:
            return title
        album_title = str(getattr(album, "title", "") or "").strip()
        return album_title if total == 1 else f"{album_title} · 第 {index} 话"

    def _chapter_metadata(self, client, album, episode: tuple, total: int, *, defer_covers: bool = False, photo=None) -> dict[str, Any]:
        photo_id, episode_index, _episode_name = episode[:3]
        # Scramble parameters are only needed when downloading/decoding pages.
        photo = photo if photo is not None else client.get_photo_detail(str(photo_id), False, False)
        photo.from_album = album
        if defer_covers:
            cover_path = self._cached_cover(str(photo.id)) or self.data_root / "covers" / "chapters" / f"{photo.id}.jpg"
            cover_warning = None
        else:
            cover_path, cover_warning = self._download_default_cover(client, photo)
        series_id = str(getattr(photo, "album_id", "") or album.id)
        chapter_index = normalize_chapter_index(episode_index)
        metadata = {
            "sourceId": str(photo.id),
            "title": self._episode_title(album, photo, chapter_index, total),
            "authors": [str(item) for item in (getattr(album, "authors", None) or [])],
            "tags": [str(item) for item in (getattr(album, "tags", None) or [])],
            "works": [str(item) for item in (getattr(album, "works", None) or [])],
            "actors": [str(item) for item in (getattr(album, "actors", None) or [])],
            "description": str(getattr(album, "description", "") or ""),
            "pageCount": len(photo),
            "publishedAt": self._search_date(getattr(album, "pub_date", "")),
            "updatedAtSource": self._search_date(getattr(album, "update_date", "")),
            "seriesId": series_id,
            "seriesTitle": str(getattr(album, "title", "") or ""),
            "chapterIndex": chapter_index,
            "coverPath": str(cover_path) if cover_path else None,
            "coverUrl": f"/media/source-cover/{photo.id}" if cover_path else None,
            "chapters": [],
        }
        if cover_warning:
            metadata["sourceWarning"] = cover_warning
        return metadata

    def inspect_series(self, raw_plate: Any) -> dict[str, Any]:
        """Fetch a lightweight episode manifest without touching image CDNs."""
        source_id = normalize_plate(raw_plate)
        if not source_id:
            raise ValueError("请输入至少 3 位数字的车牌")
        with self._metadata_fetch_locks[hash(source_id) % len(self._metadata_fetch_locks)]:
            # Recheck under the same per-album lock used by card hydration. A
            # click can then reuse an in-flight card request instead of issuing
            # a competing album request of its own.
            cached_items = self.database.get_online_metadata([source_id])
            cached = cached_items[0] if cached_items else None
            cached_manifest = list((cached or {}).get("episodeManifest") or [])
            if cached_manifest:
                return {
                    "seriesId": source_id,
                    "seriesTitle": str(cached.get("title") or f"JM{source_id}"),
                    "updatedAtSource": str(cached.get("updatedAtSource") or ""),
                    "itemCount": len(cached_manifest),
                    "items": cached_manifest,
                }
            with self._query_slot():
                album = self._run_query(lambda client: self._get_album_detail(client, source_id))
            manifest = self._series_manifest(album)
            items = manifest.get("items") or []
            self.database.upsert_online_metadata({
                "albumId": str(album.id),
                "title": str(getattr(album, "title", "") or ""),
                "publishedAt": self._search_date(getattr(album, "pub_date", "")),
                "updatedAtSource": self._search_date(getattr(album, "update_date", "")),
                "authors": [str(value) for value in (getattr(album, "authors", None) or [])],
                "tags": [str(value) for value in (getattr(album, "tags", None) or [])],
                "works": [str(value) for value in (getattr(album, "works", None) or [])],
                "actors": [str(value) for value in (getattr(album, "actors", None) or [])],
                "description": str(getattr(album, "description", "") or ""),
                "pageCount": int(getattr(album, "page_count", 0) or 0),
                "firstPhotoId": str(items[0]["sourceId"]) if items else source_id,
                "episodeManifest": items,
                "detailsComplete": True,
                "fetchedAt": datetime.now(UTC).isoformat(),
            })
            return manifest

    def preview_manifest_hint(self, raw_plate: Any, raw_chapter: Any = None) -> dict[str, Any]:
        """Build a preview lookup without waiting for album metadata.

        Album ids are also the first photo id for the usual single-volume
        shape. Cached multi-chapter manifests take precedence; otherwise the
        downloader tries that common fast path and the caller can fall back to
        :meth:`inspect_series` only if the chapter endpoint rejects it.
        """
        source_id = normalize_plate(raw_plate)
        if not source_id:
            raise ValueError("请输入至少 3 位数字的车牌")
        cached_rows = self.database.get_online_metadata([source_id])
        cached = cached_rows[0] if cached_rows else {}
        manifest = list(cached.get("episodeManifest") or [])
        first_photo_id = normalize_plate(cached.get("firstPhotoId"))
        requested_source_id = normalize_plate(raw_chapter)
        first = next((
            dict(item) for item in manifest
            if item.get("sourceId")
            and (not requested_source_id or str(item.get("sourceId")) == requested_source_id)
        ), None)
        if first is None:
            first = {
                "sourceId": requested_source_id or first_photo_id or source_id,
                "title": str(cached.get("title") or f"JM{source_id}"),
                "chapterIndex": 1,
            }
        title = str(cached.get("title") or first.get("title") or f"JM{source_id}")
        item = {
            **first,
            "seriesId": source_id,
            "seriesTitle": title,
            "authors": list(cached.get("authors") or []),
            "tags": list(cached.get("tags") or []),
            "description": str(cached.get("description") or ""),
            "pageCount": 0,
        }
        return {
            **item,
            "queryId": source_id,
            "seriesId": source_id,
            "seriesTitle": title,
            "items": [item],
            "itemCount": max(1, len(manifest)),
            "previewOptimistic": not bool(manifest or first_photo_id),
        }

    def lookup(self, raw_plate: Any, *, defer_covers: bool = False, fresh: bool = False) -> dict[str, Any]:
        source_id = normalize_plate(raw_plate)
        if not source_id:
            raise ValueError("请输入至少 3 位数字的车牌")
        key = (source_id, defer_covers)
        with self._lookup_locks[hash(key) % len(self._lookup_locks)]:
            with self._cache_lock:
                cached = self._lookup_cache.get(key)
                if not fresh and cached and time.monotonic() - cached[0] < 900:
                    self._lookup_cache.move_to_end(key)
                    return deepcopy(cached[1])
                generation = self._cache_generation
            try:
                result = self._lookup_uncached(source_id, defer_covers)
            except Exception as first_error:
                if self._proxy_is_explicit():
                    self._record_connection(False, first_error)
                    raise
                try:
                    result = self._lookup_uncached(source_id, defer_covers, direct=True)
                except Exception as direct_error:
                    self._record_connection(False, direct_error)
                    raise direct_error from first_error
            self._record_connection(True)
            with self._cache_lock:
                if generation == self._cache_generation:
                    self._lookup_cache[key] = (time.monotonic(), deepcopy(result))
                    self._lookup_cache.move_to_end(key)
                    while len(self._lookup_cache) > 32:
                        self._lookup_cache.popitem(last=False)
            return result

    def _lookup_uncached(self, source_id: str, defer_covers: bool, *, direct: bool = False) -> dict[str, Any]:
        clients = threading.local()

        def client_for_thread():
            if not hasattr(clients, "client"):
                clients.client = self._new_query_client(direct=direct)
            return clients.client

        def get_album():
            with self._query_slot():
                return self._get_album_detail(client_for_thread(), source_id)

        def get_photo():
            with self._query_slot():
                return client_for_thread().get_photo_detail(source_id, False, False)

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="jm-lookup") as executor:
            album_future = executor.submit(get_album)
            photo_future = executor.submit(get_photo)
            album = album_future.result()
            try:
                queried_photo = photo_future.result()
            except Exception:
                # Some album ids are not valid photo ids. The album manifest is
                # still enough to fetch each real photo below.
                queried_photo = None
            actual_album_id = str(getattr(queried_photo, "album_id", "") or "")
            if actual_album_id and actual_album_id != str(album.id):
                def get_actual_album():
                    with self._query_slot():
                        return self._get_album_detail(client_for_thread(), actual_album_id)

                album = executor.submit(get_actual_album).result()
            episodes = self._episode_list(album)
            first_photo_id = str(episodes[0][0])
            matching = [episode for episode in episodes if str(episode[0]) == source_id]
            selected = matching if matching and source_id != first_photo_id else episodes

            def fetch_metadata(episode):
                photo = queried_photo if queried_photo is not None and str(episode[0]) == source_id else None
                with self._query_slot():
                    return self._chapter_metadata(client_for_thread(), album, episode, len(episodes), defer_covers=defer_covers, photo=photo)

            # map preserves source order even when later chapters finish first.
            items = list(executor.map(fetch_metadata, selected))
        if not items:
            raise RuntimeError("没有找到可用的话")
        series_id = items[0]["seriesId"]
        return {
            **items[0],
            "queryId": source_id,
            "seriesId": series_id,
            "seriesTitle": str(album.title),
            "items": items,
            "itemCount": len(items),
        }

    def download(
        self,
        raw_plate: Any,
        output_path: str | Path,
        progress: Callable[[dict[str, Any]], None] | None = None,
        selected_source_ids: list[str] | None = None,
        stream_first_pages: int = 0,
        lookup_hint: dict[str, Any] | None = None,
        foreground: bool = False,
        max_pages: int = 0,
        cancel_event: threading.Event | None = None,
        transfer_priority: str = "background",
        seed_page_paths: dict[str, list[str]] | None = None,
        on_chapter_complete: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        source_id = normalize_plate(raw_plate)
        if not source_id:
            raise ValueError("无效车牌")
        output = Path(output_path).resolve()
        output.mkdir(parents=True, exist_ok=True)
        if progress:
            progress({"phase": "读取作品信息", "progress": 2})
        lookup = deepcopy(lookup_hint) if lookup_hint else self.lookup(source_id, defer_covers=True)
        if selected_source_ids is not None:
            selected = {str(item) for item in selected_source_ids if str(item)}
            lookup = {
                **lookup,
                "items": [item for item in lookup["items"] if str(item.get("sourceId")) in selected],
            }
            if not lookup["items"]:
                raise RuntimeError("没有找到需要缓存的话")
        total_items = len(lookup["items"])
        fast_preview = bool(lookup_hint is not None and total_items == 1)
        if progress:
            progress({
                "title": str(lookup.get("seriesTitle") or lookup["items"][0].get("title") or f"JM{source_id}"),
                "phase": "准备下载",
                "progress": 7,
                "totalItems": total_items,
            })
        downloaded: list[dict[str, Any] | None] = [None] * total_items
        selected_series = selected_source_ids is not None and (
            int(lookup.get("itemCount") or total_items) > 1
            or any(str(item.get("sourceId")) != str(lookup["seriesId"]) for item in lookup["items"])
        )
        series_base = output / f"JM{lookup['seriesId']}" if total_items > 1 or selected_series else output
        series_base.mkdir(parents=True, exist_ok=True)
        # Chapter scheduling is intentionally automatic: one chapter starts
        # immediately and a second may overlap while the global focus-aware
        # transfer gate protects the foreground workload.
        chapter_workers = max(1, min(2, total_items))
        worker_priority = "preview" if foreground else "streaming" if transfer_priority == "reader_next" else transfer_priority
        image_workers = (
            4
            if fast_preview
            else self._transfer_gate.recommended_parallelism(worker_priority)
        )
        progress_lock = threading.Lock()
        chapter_progress = [0.0] * total_items
        completed_chapters: set[int] = set()

        def report(item_index: int, fraction: float, phase: str, **extra: Any) -> None:
            if not progress:
                return
            with progress_lock:
                chapter_progress[item_index] = max(chapter_progress[item_index], min(1.0, fraction))
                if fraction >= 1:
                    completed_chapters.add(item_index)
                progress({
                    "phase": phase,
                    "progress": round(8 + sum(chapter_progress) / total_items * 84),
                    "currentItem": item_index + 1,
                    "currentItemTitle": str(lookup["items"][item_index].get("title") or ""),
                    "completedItems": len(completed_chapters),
                    **extra,
                })

        def download_chapter(item_index: int, metadata: dict[str, Any]) -> dict[str, Any]:
            photo_id = metadata["sourceId"]
            reusable_page_paths = [
                Path(str(path)).resolve()
                for path in (seed_page_paths or {}).get(str(photo_id), [])
                if str(path)
            ]
            chapter_base = series_base / f"JM{photo_id}"
            chapter_base.mkdir(parents=True, exist_ok=True)
            option = self._option()
            option.dir_rule.base_dir = str(chapter_base)
            option.download.threading.image = image_workers
            jmcomic = self._module()
            # jmcomic retries an absolute image URL on the same CDN even when
            # it advances its API-domain retry index. Give each image node one
            # bounded attempt, then explicitly switch the image URL below.
            client_config = getattr(option, "client", None)
            if client_config is not None and hasattr(client_config, "retry_times"):
                client_config.retry_times = 0
            postman_meta = getattr(getattr(client_config, "postman", None), "meta_data", None)
            if postman_meta is not None:
                image_timeout = 8 if foreground else 12
                configured_timeout = postman_meta.get("timeout")
                if configured_timeout is None or (
                    isinstance(configured_timeout, (int, float)) and configured_timeout > image_timeout
                ):
                    postman_meta["timeout"] = image_timeout
            if fast_preview:
                # Preview storage is disposable and does not need a user's
                # album-based directory rule. Keeping it at chapter_base also
                # lets us omit the otherwise redundant album-detail request.
                def preview_save_dir(_photo, ensure_exists=True):
                    if ensure_exists:
                        chapter_base.mkdir(parents=True, exist_ok=True)
                    return str(chapter_base)

                option.decide_image_save_dir = preview_save_dir
            image_lock = threading.Lock()
            image_state = {
                "completed": 0,
                "total": max(1, int(metadata.get("pageCount") or 0)),
                "foregroundHead": False,
                "streaming": False,
                "contiguous": 0,
                "completedIndices": set(),
                "completedPaths": {},
                "imageIndices": {},
                "streamingRoot": "",
                "activeSourceId": "",
            }
            provider_owner = self

            class VerifiedDownloader(jmcomic.JmDownloader):
                def download_preview_image_hedged(self, image, domains, priority):
                    required_option_methods = (
                        "decide_image_filepath",
                        "decide_download_cache",
                        "decide_download_image_decode",
                    )
                    if not all(callable(getattr(self.option, name, None)) for name in required_option_methods):
                        return None, False
                    if not all(callable(getattr(self.client, name, None)) for name in ("get_jm_image", "save_image_resp")):
                        return None, False

                    img_save_path = self.option.decide_image_filepath(image)
                    image.save_path = img_save_path
                    image.exists = Path(img_save_path).is_file()
                    image.cache = self.option.decide_download_cache(image)
                    self.before_image(image, img_save_path)
                    if image.skip:
                        return None, True
                    if image.cache and image.exists:
                        self.after_image(image, img_save_path)
                        return None, True

                    decode_image = self.option.decide_download_image_decode(image)
                    original_url = str(image.img_url)
                    stop_hedge = threading.Event()

                    def request_on_domain(domain):
                        # A two-node hedge consumes two actual network lanes.
                        # Keep the losing request accounted for even after the
                        # winning page is ready for the reader.
                        with provider_owner._transfer_gate.image(priority=priority):
                            if stop_hedge.is_set() or (cancel_event is not None and cancel_event.is_set()):
                                return None
                            return self.client.get_jm_image(
                                provider_owner._image_url_on_domain(original_url, domain),
                            )

                    executor = ThreadPoolExecutor(max_workers=len(domains), thread_name_prefix="jm-preview-cdn")
                    futures = {
                        executor.submit(request_on_domain, domain): domain
                        for domain in domains
                    }
                    last_error: Exception | None = None
                    try:
                        for future in as_completed(futures):
                            domain = futures[future]
                            winning_url = provider_owner._image_url_on_domain(original_url, domain)
                            try:
                                response = future.result()
                                if response is None:
                                    continue
                                response.require_success()
                                self.client.save_image_resp(
                                    decode_image,
                                    img_save_path,
                                    winning_url,
                                    response,
                                    int(image.scramble_id),
                                )
                            except Exception as exc:
                                last_error = exc
                                provider_owner._record_image_domain(domain, success=False)
                                Path(img_save_path).unlink(missing_ok=True)
                                continue
                            image.img_url = winning_url
                            provider_owner._record_image_domain(domain, success=True)
                            self.after_image(image, img_save_path)
                            return None, True
                    finally:
                        stop_hedge.set()
                        for future in futures:
                            future.cancel()
                        executor.shutdown(wait=False, cancel_futures=True)
                    if last_error is not None:
                        raise last_error
                    return None, False

                def fetch_preview_photo(self, requested_photo_id):
                    client = self.client
                    api_client_type = getattr(jmcomic, "JmApiClient", ())
                    if not api_client_type or not isinstance(client, api_client_type):
                        return client.get_photo_detail(requested_photo_id)
                    # Photo detail and scramble parameters are independent API
                    # requests. Fetch them together; album detail is already in
                    # lookup_hint and is unnecessary for disposable preview IO.
                    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="jm-preview-head") as executor:
                        photo_future = executor.submit(
                            client.get_photo_detail,
                            requested_photo_id,
                            False,
                            False,
                        )
                        scramble_future = executor.submit(client.get_scramble_id, requested_photo_id)
                        photo = photo_future.result()
                        photo.scramble_id = str(scramble_future.result())
                    # jmcomic stores download results in a dictionary keyed by
                    # photo.from_album and also calls len() on it in callbacks.
                    # SimpleNamespace is unhashable, so use the small identity-
                    # hashable context above instead of fetching the album again.
                    photo.from_album = _PreviewAlbumContext(lookup, source_id)
                    return photo

                def download_photo(self, requested_photo_id):
                    photo = None
                    for attempt in range(3):
                        try:
                            photo = (
                                self.fetch_preview_photo(requested_photo_id)
                                if fast_preview
                                else self.client.get_photo_detail(requested_photo_id)
                            )
                        except Exception:
                            if attempt == 2:
                                raise
                            self.client = self.option.new_jm_client()
                            continue
                        if len(photo) > 0:
                            break
                        if attempt < 2:
                            self.client = self.option.new_jm_client()
                    if photo is None or len(photo) == 0:
                        raise RuntimeError(f"JM{requested_photo_id} 当前返回 0 页，章节图片可能尚未生成，请稍后重试")
                    self.begin_manifest(photo)
                    try:
                        self.download_by_photo_detail(photo)
                    finally:
                        self.finish_manifest(photo)
                    return photo

                def download_by_photo_detail(self, photo):
                    requested_limit = max(0, int(max_pages or 0))
                    effective_count = min(len(photo), requested_limit) if requested_limit else len(photo)
                    leading_count = max(0, min(int(stream_first_pages or 0), effective_count))
                    if fast_preview:
                        domains = provider_owner._image_domains(
                            jmcomic,
                            str(getattr(photo, "data_original_domain", "") or ""),
                        )
                        if domains:
                            # Do this before image entities are materialised so
                            # every URL in the first batch starts on the known
                            # low-latency route rather than a random CDN.
                            photo.data_original_domain = domains[0]
                    if leading_count <= 0 or leading_count >= len(photo):
                        with image_lock:
                            image_state["foregroundHead"] = bool(foreground and leading_count > 0)
                        try:
                            return super().download_by_photo_detail(photo)
                        finally:
                            with image_lock:
                                image_state["foregroundHead"] = False

                    photo.save_path = self.option.decide_image_save_dir(photo)
                    self.client.check_photo(photo)
                    self.before_photo(photo)
                    if photo.skip:
                        return
                    images = list(self.do_filter(photo))[:effective_count]
                    leading = images[:leading_count]
                    remaining = images[leading_count:]
                    with image_lock:
                        image_state["imageIndices"] = {
                            id(image): index for index, image in enumerate(images)
                        }
                        image_state["streamingRoot"] = str(photo.save_path)
                        image_state["activeSourceId"] = str(photo.id)
                    if leading:
                        with image_lock:
                            image_state["foregroundHead"] = foreground
                        try:
                            self.execute_on_condition(
                                iter_objs=leading,
                                apply=self.download_by_image_detail,
                                count_batch=max(1, min(4, len(leading))),
                            )
                        finally:
                            with image_lock:
                                image_state["foregroundHead"] = False
                                image_state["streaming"] = True
                                available_pages = int(image_state["contiguous"])
                                available_page_paths = [
                                    image_state["completedPaths"][index]
                                    for index in range(available_pages)
                                    if index in image_state["completedPaths"]
                                ]
                        report(
                            item_index,
                            len(leading) / max(1, len(images)),
                            f"前 {len(leading)} 页已就绪，可开始阅读",
                            streamReady=True,
                            streamingRoot=str(photo.save_path),
                            activeSourceId=str(photo.id),
                            availablePages=available_pages,
                            availablePagePaths=available_page_paths,
                            expectedPages=len(photo),
                        )
                    if remaining:
                        # Four-page batches keep request bursts short and make
                        # completed groups reach the reader at a steady cadence
                        # instead of waiting behind a congested 12-page burst.
                        tail_batch_size = 4 if leading_count > 0 else len(remaining)
                        for offset in range(0, len(remaining), tail_batch_size):
                            if cancel_event is not None and cancel_event.is_set():
                                raise RuntimeError("在线预览已取消")
                            batch = remaining[offset:offset + tail_batch_size]
                            self.execute_on_condition(
                                iter_objs=batch,
                                apply=self.download_by_image_detail,
                                count_batch=max(1, min(image_workers, len(batch))),
                            )
                    self.after_photo(photo)

                def before_photo(self, photo):
                    if reusable_page_paths:
                        destination = Path(str(getattr(photo, "save_path", "") or chapter_base)).resolve()
                        destination.mkdir(parents=True, exist_ok=True)
                        for source in reusable_page_paths:
                            if not source.is_file() or source.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                                continue
                            target = destination / source.name
                            if not target.exists():
                                shutil.copy2(source, target)
                    with image_lock:
                        image_state["total"] = max(1, len(photo))
                    report(item_index, 0, f"并行下载第 {item_index + 1}/{total_items} 话")
                    return super().before_photo(photo)

                def download_by_image_detail(self, image):
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("在线预览已取消")
                    with image_lock:
                        foreground_head = bool(image_state["foregroundHead"])
                        streaming = bool(image_state["streaming"])
                    priority = (
                        "preview"
                        if foreground_head
                        else "streaming" if transfer_priority == "reader_next" and not streaming
                        else "streaming" if foreground and streaming
                        else "visible" if transfer_priority == "visible" else "background"
                    )
                    original_url = str(getattr(image, "img_url", "") or "")
                    parsed_url = urlparse(original_url)
                    if not parsed_url.netloc or not parsed_url.path.startswith("/media/photos/"):
                        with provider_owner._transfer_gate.image(priority=priority):
                            return super().download_by_image_detail(image)
                    domains = provider_owner._image_domains(
                        jmcomic, parsed_url.netloc, spread=not foreground,
                    )
                    domains = domains[:3 if foreground else 6]
                    image_index = max(0, int(getattr(image, "index", 0) or 0))
                    if fast_preview and image_index <= 4 and len(domains) >= 2:
                        try:
                            hedged_result, handled = self.download_preview_image_hedged(
                                image, domains[:2], priority,
                            )
                            if handled:
                                return hedged_result
                        except Exception:
                            raw_path = str(getattr(image, "save_path", "") or "")
                            if raw_path:
                                Path(raw_path).unlink(missing_ok=True)
                    last_error: Exception | None = None
                    for attempt, domain in enumerate(domains):
                        if cancel_event is not None and cancel_event.is_set():
                            raise RuntimeError("在线预览已取消")
                        image.img_url = provider_owner._image_url_on_domain(original_url, domain)
                        attempt_started = time.perf_counter()
                        try:
                            with provider_owner._transfer_gate.image(priority=priority):
                                result = super().download_by_image_detail(image)
                        except Exception as exc:
                            last_error = exc
                            provider_owner._record_image_domain(domain, success=False)
                            raw_path = str(getattr(image, "save_path", "") or "")
                            if raw_path:
                                Path(raw_path).unlink(missing_ok=True)
                            if attempt + 1 < len(domains):
                                report(
                                    item_index,
                                    image_state["completed"] / max(1, image_state["total"]),
                                    "正在选取最优链路…",
                                    routeSwitch=True,
                                )
                            continue
                        elapsed = time.perf_counter() - attempt_started
                        if getattr(image, "cache", False) and getattr(image, "exists", False):
                            return result
                        if elapsed > 2.5 and len(domains) > 1:
                            # A successful but sluggish node should not hold
                            # the next page group hostage.
                            provider_owner._record_image_domain(domain, success=False)
                            report(
                                item_index,
                                image_state["completed"] / max(1, image_state["total"]),
                                "正在选取最优链路…",
                                routeSwitch=True,
                            )
                        else:
                            provider_owner._record_image_domain(domain, success=True)
                        return result
                    if last_error is not None:
                        raise last_error
                    with provider_owner._transfer_gate.image(priority=priority):
                        return super().download_by_image_detail(image)

                def after_image(self, image, img_save_path):
                    result_after = super().after_image(image, img_save_path)
                    with image_lock:
                        image_state["completed"] += 1
                        completed = image_state["completed"]
                        total = image_state["total"]
                        image_index = image_state["imageIndices"].get(id(image))
                        if image_index is not None:
                            image_state["completedIndices"].add(image_index)
                            image_state["completedPaths"][image_index] = str(Path(img_save_path).resolve())
                            while image_state["contiguous"] in image_state["completedIndices"]:
                                image_state["contiguous"] += 1
                        streaming = bool(image_state["streaming"])
                        available_pages = int(image_state["contiguous"])
                        streaming_root = str(image_state["streamingRoot"])
                        active_source_id = str(image_state["activeSourceId"])
                        available_page_paths = [
                            image_state["completedPaths"][index]
                            for index in range(available_pages)
                            if index in image_state["completedPaths"]
                        ]
                    stream_patch = ({
                        "streamReady": True,
                        "streamingRoot": streaming_root,
                        "activeSourceId": active_source_id,
                        "availablePages": available_pages,
                        "availablePagePaths": available_page_paths,
                        "expectedPages": total,
                    } if streaming else {})
                    report(
                        item_index,
                        min(.99, completed / total),
                        f"并行下载第 {item_index + 1}/{total_items} 话 · {min(completed, total)}/{total} 页",
                        **stream_patch,
                    )
                    return result_after

            # Separate queue tasks can refer to overlapping albums/chapters.
            preview_context = self._transfer_gate.preview_session() if foreground else None
            if preview_context is not None:
                preview_context.__enter__()
            try:
                with self._download_locks[hash(str(chapter_base)) % len(self._download_locks)]:
                    result = option.download_photo(photo_id, downloader=VerifiedDownloader)
            finally:
                if preview_context is not None:
                    preview_context.__exit__(None, None, None)
            detail = result.detail
            root_path = Path(str(getattr(detail, "save_path", "") or chapter_base)).resolve()
            actual_images = find_images(root_path)
            expected_pages = max(int(metadata.get("pageCount") or 0), len(detail))
            if expected_pages <= 0:
                raise RuntimeError(f"JM{photo_id} 当前返回 0 页，章节图片可能尚未生成，请稍后重试")
            required_pages = min(expected_pages, max(1, int(max_pages))) if max_pages else expected_pages
            if len(actual_images) < required_pages:
                raise RuntimeError(f"JM{photo_id} 图片不完整：已保存 {len(actual_images)}/{expected_pages} 页，请重试")
            item = {
                **metadata,
                "rootPath": str(root_path),
                "outputPath": str(root_path),
                "pageCount": expected_pages if max_pages else len(actual_images),
                "downloadedPageCount": len(actual_images),
                "duration": getattr(result, "duration", None),
            }
            report(item_index, 1, f"第 {item_index + 1}/{total_items} 话下载完成")
            return item

        with ThreadPoolExecutor(max_workers=chapter_workers, thread_name_prefix="jm-download-chapter") as executor:
            futures = {executor.submit(download_chapter, index, metadata): index for index, metadata in enumerate(lookup["items"])}
            for future in as_completed(futures):
                item = future.result()
                downloaded[futures[future]] = item
                if on_chapter_complete is not None:
                    on_chapter_complete(item)
        if progress:
            progress({"phase": "整理文件并加入书架", "progress": 94})
        return {
            "ok": True,
            "queryId": source_id,
            "seriesId": lookup["seriesId"],
            "seriesTitle": lookup["seriesTitle"],
            "outputPath": str(output),
            "items": downloaded,
        }
