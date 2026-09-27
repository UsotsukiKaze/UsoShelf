from __future__ import annotations

import asyncio
import inspect
import mimetypes
import os
import shutil
import signal
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .app_updates import AppUpdateManager
from .db import LibraryDatabase
from .downloads import DownloadQueue, ProgressCallback
from .importer import inspect_comic_folder, scan_import_root
from .provider import JmcomicProvider
from .updates import SeriesUpdateChecker
from .utils import find_images, is_image, normalize_plate, path_is_inside
from .windows import hide_foreground_to_tray, minimize_foreground_window, pick_folder, pick_image_file, reveal_in_explorer, stop_tray_icon

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)).resolve()
PUBLIC_ROOT = RESOURCE_ROOT / "public"


def default_data_root() -> Path:
    configured = os.environ.get("JMSHELF_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return (local_app_data / "JmShelf" / "data").resolve()
    return (PROJECT_ROOT / "data").resolve()


def _error(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


def _comic_pages(comic: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not comic or not comic.get("rootPath"):
        return []
    root = Path(comic["rootPath"])
    if not root.is_dir():
        return []
    return [
        {"index": index, "name": image.name, "relativePath": str(image.relative_to(root))}
        for index, image in enumerate(find_images(root))
    ]


def _series_update_output(series: dict[str, Any], configured_path: str, fallback: Path) -> Path:
    source_series_id = str(series.get("sourceSeriesId") or "")
    expected_folder = f"jm{source_series_id}".casefold()
    for member in series.get("members", []):
        root_path = member.get("rootPath")
        if not root_path:
            continue
        root = Path(root_path).expanduser().resolve()
        matches = [candidate for candidate in (root, *root.parents) if candidate.name.casefold() == expected_folder]
        if matches:
            # A full-series download is <base>/JM<series>/JM<photo>/<title>.
            # Use the outer series folder so an update becomes its sibling.
            return matches[-1]
    return Path(configured_path or fallback).expanduser().resolve()


def create_app(data_root: Path | None = None) -> FastAPI:
    resolved_data = (data_root or default_data_root()).resolve()
    resolved_data.mkdir(parents=True, exist_ok=True)
    cache_root = (resolved_data / "cache").resolve()
    preview_root = (cache_root / "previews").resolve()
    database = LibraryDatabase(resolved_data / "library.sqlite3")
    database.ensure_source_subscriptions()
    provider = JmcomicProvider(database, resolved_data)
    app_updates = AppUpdateManager(
        __version__,
        resolved_data / "updates",
        proxy_getter=lambda: database.get_setting("provider.proxy", ""),
    )
    state = {"lastHeartbeat": time.monotonic()}
    page_cache_lock = threading.RLock()
    page_cache: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    storage_mutation_lock = threading.RLock()
    preview_sessions: dict[str, dict[str, Any]] = {}
    preview_state_lock = threading.RLock()
    preview_prune_lock = threading.Lock()
    preview_threads: set[threading.Thread] = set()
    preview_cleanup_stop = threading.Event()

    def cached_comic_pages(comic: dict[str, Any] | None, *, refresh: bool = False) -> list[dict[str, Any]]:
        if not comic:
            return []
        comic_id = str(comic.get("id") or "")
        root_path = str(comic.get("rootPath") or "")
        if not comic_id:
            return _comic_pages(comic)
        with page_cache_lock:
            cached = page_cache.get(comic_id)
            if not refresh and cached and cached[0] == root_path:
                return cached[1]
            pages = _comic_pages(comic)
            page_cache[comic_id] = (root_path, pages)
            return pages

    def invalidate_comic_pages(comic_ids: set[str] | list[str]) -> None:
        with page_cache_lock:
            for comic_id in comic_ids:
                page_cache.pop(str(comic_id), None)

    def import_download_result(result: dict[str, Any], storage_kind: str = "download") -> dict[str, Any]:
        comics: list[dict[str, Any]] = []
        result_items = [item for item in result.get("items", []) if isinstance(item, dict)]
        if not result_items:
            raise RuntimeError("下载未返回任何可用章节")
        missing_items: list[str] = []
        for item in result_items:
            downloaded_root = Path(item["rootPath"])
            inspected = inspect_comic_folder(downloaded_root) if downloaded_root.exists() else None
            if inspected:
                comics.append(database.upsert_comic({
                    **inspected,
                    **item,
                    "storageKind": storage_kind,
                    "sourceStatus": "matched",
                    "sourceError": None,
                }))
            else:
                missing_items.append(f"JM{item.get('sourceId') or '?'}")
        if missing_items:
            raise RuntimeError(f"下载目录中没有可读取图片：{', '.join(missing_items)}")
        source_series_ids = list(dict.fromkeys(
            str(comic["seriesId"])
            for comic in comics
            if comic.get("seriesId")
        ))
        library_series_items = [
            series
            for source_series_id in source_series_ids
            if (series := database.ensure_source_series(source_series_id)) is not None
        ]
        library_series = library_series_items[0] if library_series_items else None
        return {
            "result": result,
            "comics": comics,
            "comic": comics[0] if comics else None,
            "librarySeries": library_series,
            "librarySeriesItems": library_series_items,
        }

    def run_queued_download(source_id: str, output_path: Path, progress: ProgressCallback) -> dict[str, Any]:
        imported_items = 0

        def import_finished_chapter(item: dict[str, Any]) -> None:
            nonlocal imported_items
            if downloads.closing:
                raise RuntimeError("JmShelf 已关闭，任务未执行入库")
            import_download_result({"items": [item]}, "download")
            imported_items += 1
            progress({"importedItems": imported_items})

        keyword_args: dict[str, Any] = {"progress": progress}
        if "on_chapter_complete" in inspect.signature(provider.download).parameters:
            keyword_args["on_chapter_complete"] = import_finished_chapter
        result = provider.download(source_id, output_path, **keyword_args)
        if downloads.closing:
            raise RuntimeError("JmShelf 已关闭，任务未执行入库")
        progress({"phase": "扫描下载文件", "progress": 96})
        return import_download_result(result, "download")

    def run_queued_cache(
        source_id: str,
        output_path: Path,
        progress: ProgressCallback,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        selected_source_ids = [str(item) for item in context.get("selectedSourceIds", []) if item]
        parameters = inspect.signature(provider.download).parameters
        progressive = bool(context.get("progressive"))
        registered_streams: set[str] = set()

        def cache_progress(patch: dict[str, Any]) -> None:
            if progressive and patch.get("streamReady"):
                active_source_id = str(patch.get("activeSourceId") or "")
                raw_root = str(patch.get("streamingRoot") or "")
                stream_root = Path(raw_root).resolve() if raw_root else None
                comic = (
                    database.get_comic_by_source(active_source_id)
                    if active_source_id and active_source_id not in registered_streams
                    else None
                )
                if comic and stream_root and path_is_inside(cache_root, stream_root):
                    updated = database.update_comic(comic["id"], {
                        "rootPath": str(stream_root),
                        "storageKind": "cache",
                        "pageCount": max(
                            int(comic.get("pageCount") or 0),
                            int(patch.get("expectedPages") or 0),
                        ),
                    })
                    if updated:
                        registered_streams.add(active_source_id)
                        invalidate_comic_pages([str(comic["id"])])
            progress(patch)

        if "selected_source_ids" in parameters:
            keyword_args: dict[str, Any] = {
                "progress": cache_progress,
                "selected_source_ids": selected_source_ids,
            }
            if "stream_first_pages" in parameters:
                keyword_args["stream_first_pages"] = 4 if progressive else 0
            if progressive and len(selected_source_ids) == 1 and "lookup_hint" in parameters:
                chapter = database.get_comic_by_source(selected_source_ids[0])
                if chapter and str(chapter.get("seriesId") or chapter["sourceId"]) == source_id:
                    # The shelf already knows the exact next photo. Fetching the
                    # entire album and every chapter again delays its first pages.
                    hint = provider.preview_manifest_hint(source_id, selected_source_ids[0])
                    item = {
                        **hint["items"][0],
                        "sourceId": selected_source_ids[0],
                        "seriesId": source_id,
                        "seriesTitle": str(chapter.get("seriesTitle") or hint["seriesTitle"]),
                        "title": str(chapter.get("title") or hint["items"][0].get("title") or ""),
                        "chapterIndex": chapter.get("chapterIndex") or hint["items"][0].get("chapterIndex") or 1,
                        "authors": chapter.get("authors") or hint["items"][0].get("authors") or [],
                        "tags": chapter.get("tags") or hint["items"][0].get("tags") or [],
                        "works": chapter.get("works") or [],
                        "actors": chapter.get("actors") or [],
                        "description": chapter.get("description") or hint["items"][0].get("description") or "",
                    }
                    keyword_args["lookup_hint"] = {
                        **hint,
                        "seriesTitle": item["seriesTitle"],
                        "items": [item],
                    }
            if context.get("readerAhead") and "transfer_priority" in parameters:
                keyword_args["transfer_priority"] = "reader_next"
            if "seed_page_paths" in parameters:
                keyword_args["seed_page_paths"] = context.get("seedPagePaths") or {}
            result = provider.download(source_id, output_path, **keyword_args)
        else:
            # Keeps injected/test providers with the older callable shape compatible.
            result = provider.download(source_id, output_path, progress=cache_progress)
        if caches.closing:
            raise RuntimeError("JmShelf 已关闭，缓存任务未执行入库")
        progress({"phase": "扫描缓存文件", "progress": 96})
        imported = import_download_result(result, "cache")

        def prune_after_completion() -> None:
            # The queue marks this task completed immediately after the handler
            # returns. Wait for that transition so normal active-task guards
            # still protect every directory that is genuinely being written.
            for _attempt in range(50):
                if not caches.is_active(source_id, output_path):
                    break
                time.sleep(0.1)
            try:
                prune_library_cache()
            except Exception:
                pass

        threading.Thread(
            target=prune_after_completion,
            name=f"jmshelf-cache-prune-{source_id}",
            daemon=True,
        ).start()
        return imported

    downloads = DownloadQueue(run_queued_download, state_path=resolved_data / "download-queue.json")
    caches = DownloadQueue(run_queued_cache, state_path=resolved_data / "cache-queue.json")
    series_updates = SeriesUpdateChecker(database, provider)

    def streaming_page_limit(comic: dict[str, Any]) -> int | None:
        source_id = str(comic.get("sourceId") or "")
        if not source_id:
            return None
        for task in caches.snapshot()["items"]:
            if task.get("status") not in DownloadQueue.ACTIVE_STATUSES:
                continue
            if str(task.get("activeSourceId") or "") != source_id or not task.get("streamReady"):
                continue
            return max(0, int(task.get("availablePages") or 0))
        return None

    def active_cache_task_for_source(source_id: str) -> dict[str, Any] | None:
        clean_source_id = str(source_id or "")
        if not clean_source_id:
            return None
        for task in caches.snapshot()["items"]:
            if task.get("status") not in DownloadQueue.ACTIVE_STATUSES:
                continue
            selected = {
                str(item)
                for item in (task.get("context") or {}).get("selectedSourceIds", [])
                if item
            }
            if clean_source_id in selected or (not selected and str(task.get("sourceId") or "") == clean_source_id):
                return task
        return None

    def cache_next_source_chapter(comic_id: str) -> dict[str, Any] | None:
        next_comic = database.get_next_source_series_member(comic_id)
        if not next_comic or not next_comic.get("sourceId"):
            return None
        active = active_cache_task_for_source(str(next_comic["sourceId"]))
        if active:
            caches.prioritize(str(active["id"]))
            return {"task": active, "created": False, "comic": next_comic}
        queued = enqueue_comic_caches(
            [next_comic["id"]],
            progressive=True,
            priority_comic_id=next_comic["id"],
            reader_ahead=True,
        )
        return {
            "task": queued["tasks"][0] if queued["tasks"] else None,
            "created": bool(queued["createdCount"]),
            "comic": next_comic,
        }

    def series_update_output(series: dict[str, Any]) -> Path:
        configured_path = database.get_setting("download.defaultPath", "").strip()
        return _series_update_output(series, configured_path, resolved_data / "downloads")

    def assert_comics_idle(comics: list[dict[str, Any]], action: str) -> None:
        active_sources: set[str] = set()
        for task in [*downloads.snapshot()["items"], *caches.snapshot()["items"]]:
            if task["status"] not in DownloadQueue.ACTIVE_STATUSES:
                continue
            active_sources.add(str(task["sourceId"]))
            active_sources.update(
                str(item) for item in (task.get("context") or {}).get("selectedSourceIds", [])
            )
        if any(
            str(value) in active_sources
            for comic in comics
            for value in (comic.get("sourceId"), comic.get("seriesId"))
            if value
        ):
            raise ValueError(f"相关下载或缓存任务正在进行，请完成后再{action}")

    def remove_comics_from_shelf(comics: list[dict[str, Any]]) -> dict[str, Any]:
        with storage_mutation_lock:
            assert_comics_idle(comics, "移出书架")
            selected_ids = {str(comic["id"]) for comic in comics}
            deleted_count = database.delete_comics(list(selected_ids), require_all=True)
            invalidate_comic_pages(selected_ids)
            return {
                "ok": True,
                "deletedCount": deleted_count,
                "deletedPaths": [],
                "cleanupPending": [],
            }

    def delete_comics_with_storage(comics: list[dict[str, Any]]) -> dict[str, Any]:
        with storage_mutation_lock:
            selected_ids = {str(comic["id"]) for comic in comics}
            assert_comics_idle(comics, "删除")

            targets: list[Path] = []
            fallback_download = (resolved_data / "downloads").resolve()
            protected = {
                resolved_data,
                fallback_download,
                cache_root,
                PROJECT_ROOT.resolve(),
                Path.home().resolve(),
            }
            configured_download = database.get_setting("download.defaultPath", "").strip()
            if configured_download:
                protected.add(Path(configured_download).expanduser().resolve())

            for comic in comics:
                raw_root = str(comic.get("rootPath") or "").strip()
                if not raw_root:
                    continue
                original = Path(raw_root).expanduser()
                is_junction = getattr(original, "is_junction", lambda: False)
                if original.is_symlink() or is_junction():
                    raise ValueError(f"拒绝删除链接目录：{original}")
                root = original.resolve()
                drive_root = Path(root.anchor).resolve() if root.anchor else root
                contains_protected_path = any(path_is_inside(root, path) for path in protected)
                inside_project_files = (
                    path_is_inside(PROJECT_ROOT, root)
                    and not path_is_inside(fallback_download, root)
                    and not path_is_inside(cache_root, root)
                )
                if root == drive_root or contains_protected_path or inside_project_files:
                    raise ValueError(f"拒绝删除受保护目录：{root}")
                if root.exists() and not root.is_dir():
                    raise ValueError(f"本地储存路径不是文件夹：{root}")
                if root not in targets:
                    targets.append(root)

            for other in database.list_comics():
                if str(other["id"]) in selected_ids or not other.get("rootPath"):
                    continue
                other_root = Path(other["rootPath"]).expanduser().resolve()
                for target in targets:
                    if (
                        other_root == target
                        or path_is_inside(target, other_root)
                        or path_is_inside(other_root, target)
                    ):
                        raise ValueError(f"目录还包含其他书架条目，已停止删除：{target}")

            roots_to_delete = [
                target for target in targets
                if not any(target != parent and path_is_inside(parent, target) for parent in targets)
            ]
            staged: list[tuple[Path, Path]] = []
            try:
                for root in roots_to_delete:
                    if not root.exists():
                        continue
                    quarantine = root.with_name(f".{root.name}.jmshelf-delete-{uuid.uuid4().hex}")
                    root.replace(quarantine)
                    staged.append((root, quarantine))
            except OSError as exc:
                for original, quarantine in reversed(staged):
                    if quarantine.exists() and not original.exists():
                        quarantine.replace(original)
                raise RuntimeError(f"无法安全暂存本地储存目录：{exc}") from exc

            try:
                deleted_count = database.delete_comics(list(selected_ids), require_all=True)
            except Exception:
                for original, quarantine in reversed(staged):
                    if quarantine.exists() and not original.exists():
                        quarantine.replace(original)
                raise

            cleanup_errors: list[str] = []
            for _original, quarantine in staged:
                try:
                    shutil.rmtree(quarantine)
                except OSError as exc:
                    cleanup_errors.append(f"{quarantine}: {exc}")
            invalidate_comic_pages(selected_ids)
            return {
                "ok": True,
                "deletedCount": deleted_count,
                "deletedPaths": [str(root) for root in roots_to_delete],
                "cleanupPending": cleanup_errors,
            }

    def enqueue_comic_caches(
        comic_ids: list[Any],
        *,
        progressive: bool = False,
        priority_comic_id: Any = None,
        seed_page_paths: dict[str, list[str]] | None = None,
        reader_ahead: bool = False,
    ) -> dict[str, Any]:
        ordered_ids = list(dict.fromkeys(str(comic_id) for comic_id in comic_ids if comic_id))
        if not ordered_ids:
            raise ValueError("没有可缓存的书架条目")
        if len(ordered_ids) > 2000:
            raise ValueError("一次最多缓存 2000 话")

        comics: list[dict[str, Any]] = []
        missing: list[str] = []
        for comic_id in ordered_ids:
            comic = database.get_comic(comic_id)
            if not comic:
                missing.append(comic_id)
            elif comic.get("sourceId"):
                available_pages = _comic_pages(comic)
                expected_pages = max(0, int(comic.get("pageCount") or 0))
                if not available_pages or (expected_pages and len(available_pages) < expected_pages):
                    comics.append(comic)
        if missing:
            raise ValueError("缓存请求中包含不存在的书架条目")

        groups: list[tuple[str, list[dict[str, Any]]]] = []
        if progressive:
            groups = [
                (str(comic.get("seriesId") or comic["sourceId"]), [comic])
                for comic in comics
            ]
        else:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for comic in comics:
                query_id = str(comic.get("seriesId") or comic["sourceId"])
                grouped.setdefault(query_id, []).append(comic)
            groups = list(grouped.items())

        tasks: list[dict[str, Any]] = []
        created_count = 0
        priority_id = str(priority_comic_id or "")
        first_progressive_member: dict[str, str] = {}
        if progressive:
            progressive_families: dict[str, list[dict[str, Any]]] = {}
            for comic in comics:
                family_id = str(comic.get("seriesId") or comic["sourceId"])
                progressive_families.setdefault(family_id, []).append(comic)
            for family_id, family_members in progressive_families.items():
                family_members.sort(
                    key=lambda item: (float(item.get("chapterIndex") or 1), str(item["sourceId"]))
                )
                first_progressive_member[family_id] = str(family_members[0]["sourceId"])
        for query_id, members in groups:
            members.sort(key=lambda item: (float(item.get("chapterIndex") or 1), str(item["sourceId"])))
            selected_source_ids = [str(item["sourceId"]) for item in members]
            if progressive and len(selected_source_ids) == 1:
                active = active_cache_task_for_source(selected_source_ids[0])
                if active:
                    if priority_id and str(members[0]["id"]) == priority_id:
                        caches.prioritize(str(active["id"]))
                    tasks.append(active)
                    continue
            title = str(
                members[0].get("displayName")
                if progressive
                else members[0].get("seriesTitle") or members[0]["displayName"]
            )
            task_context: dict[str, Any] = {"selectedSourceIds": selected_source_ids}
            reusable_pages = {
                source_id: [str(path) for path in (seed_page_paths or {}).get(source_id, []) if path]
                for source_id in selected_source_ids
                if (seed_page_paths or {}).get(source_id)
            }
            if reusable_pages:
                task_context["seedPagePaths"] = reusable_pages
            if progressive:
                task_context["progressive"] = True
            if reader_ahead:
                task_context["readerAhead"] = True
            task, created = caches.enqueue(
                query_id,
                cache_root,
                title,
                task_context,
                queue_priority=(
                    "normal"
                    if not progressive
                    or str(members[0]["sourceId"]) == first_progressive_member.get(query_id)
                    else "background"
                ),
            )
            if priority_id and any(str(member["id"]) == priority_id for member in members):
                caches.prioritize(str(task["id"]))
            tasks.append(task)
            created_count += int(created)
        return {
            "tasks": tasks,
            "createdCount": created_count,
            "skippedCount": len(ordered_ids) - len(comics),
            "progressive": progressive,
        }

    def clear_comic_caches(comic_ids: list[Any]) -> dict[str, Any]:
        ordered_ids = list(dict.fromkeys(str(comic_id) for comic_id in comic_ids if comic_id))
        if not ordered_ids:
            raise ValueError("没有可清理的缓存")
        comics: list[dict[str, Any]] = []
        for comic_id in ordered_ids:
            comic = database.get_comic(comic_id)
            if not comic:
                raise ValueError("缓存清理请求中包含不存在的书架条目")
            if comic.get("storageKind") == "cache":
                comics.append(comic)

        active_sources: set[str] = set()
        for task in caches.snapshot()["items"]:
            if task["status"] not in DownloadQueue.ACTIVE_STATUSES:
                continue
            active_sources.add(str(task["sourceId"]))
            active_sources.update(str(item) for item in (task.get("context") or {}).get("selectedSourceIds", []))
        if any(str(comic.get("sourceId") or "") in active_sources for comic in comics):
            raise ValueError("缓存任务正在进行，请完成后再清理")

        targets: list[Path] = []
        for comic in comics:
            raw_root = str(comic.get("rootPath") or "").strip()
            if not raw_root:
                continue
            root = Path(raw_root).expanduser().resolve()
            if root == cache_root or not path_is_inside(cache_root, root):
                raise ValueError(f"拒绝清理非应用缓存目录：{root}")
            if root not in targets:
                targets.append(root)

        for other in database.list_comics():
            if str(other["id"]) in ordered_ids or not other.get("rootPath"):
                continue
            other_root = Path(other["rootPath"]).expanduser().resolve()
            if any(
                other_root == target
                or path_is_inside(target, other_root)
                or path_is_inside(other_root, target)
                for target in targets
            ):
                raise ValueError(f"缓存目录仍被其他书架条目使用：{other_root}")

        roots_to_delete = [
            target for target in targets
            if not any(target != parent and path_is_inside(parent, target) for parent in targets)
        ]
        for root in roots_to_delete:
            if root.exists():
                shutil.rmtree(root)
            parent = root.parent
            while parent != cache_root and path_is_inside(cache_root, parent):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

        updated: list[dict[str, Any]] = []
        for comic in comics:
            cover_path = str(comic.get("coverPath") or "").strip()
            if cover_path and any(path_is_inside(root, Path(cover_path)) for root in roots_to_delete):
                cover_path = ""
            refreshed = database.update_comic(comic["id"], {
                "rootPath": "",
                "coverPath": cover_path,
                "storageKind": "remote",
            })
            if refreshed:
                updated.append(refreshed)
        invalidate_comic_pages(ordered_ids)
        return {
            "ok": True,
            "clearedCount": len(updated),
            "deletedPaths": [str(root) for root in roots_to_delete],
            "comics": updated,
        }

    def prune_library_cache(limit: int = 8) -> dict[str, Any]:
        """Keep the eight most recently used cache-backed chapters.

        Source-series members are deliberately counted one chapter at a time,
        so a large JM multi-P work shares the same pool as standalone works.
        """
        maximum = max(0, int(limit))
        cached = [
            comic for comic in database.list_comics()
            if comic.get("storageKind") == "cache" and comic.get("rootPath")
        ]
        cached.sort(
            key=lambda comic: (
                str(comic.get("lastReadAt") or ""),
                str(comic.get("updatedAt") or comic.get("createdAt") or ""),
            ),
            reverse=True,
        )
        if len(cached) <= maximum:
            return {"ok": True, "clearedCount": 0, "retainedCount": len(cached)}

        active_sources: set[str] = set()
        for task in [*downloads.snapshot()["items"], *caches.snapshot()["items"]]:
            if task.get("status") not in DownloadQueue.ACTIVE_STATUSES:
                continue
            active_sources.add(str(task.get("sourceId") or ""))
            active_sources.update(
                str(value) for value in (task.get("context") or {}).get("selectedSourceIds", [])
            )
        removable = [
            comic for comic in cached[maximum:]
            if str(comic.get("sourceId") or "") not in active_sources
            and str(comic.get("seriesId") or "") not in active_sources
        ]
        if not removable:
            return {"ok": True, "clearedCount": 0, "retainedCount": len(cached)}
        result = clear_comic_caches([comic["id"] for comic in removable])
        result["retainedCount"] = len(cached) - int(result.get("clearedCount") or 0)
        return result

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        series_updates.start()
        provider.start_background_refreshes()
        preview_cleanup_stop.clear()

        def cleanup_previews() -> None:
            while not preview_cleanup_stop.wait(5 * 60):
                try:
                    prune_preview_cache("")
                    prune_library_cache()
                except Exception:
                    continue

        preview_cleanup_thread = threading.Thread(
            target=cleanup_previews,
            name="jmonline-preview-cleanup",
            daemon=True,
        )
        preview_cleanup_thread.start()
        if os.environ.get("JMSHELF_DESKTOP") == "1":
            def watchdog() -> None:
                while True:
                    time.sleep(20)
                    if time.monotonic() - state["lastHeartbeat"] > 90:
                        os.kill(os.getpid(), signal.SIGTERM)
                        return

            threading.Thread(target=watchdog, daemon=True).start()
        yield
        preview_cleanup_stop.set()
        preview_cleanup_thread.join(timeout=1)
        stop_tray_icon()
        updates_stopped = series_updates.close()
        caches_stopped = caches.close()
        downloads_stopped = downloads.close()
        with preview_state_lock:
            for session in preview_sessions.values():
                cancel_event = session.get("cancelEvent")
                if isinstance(cancel_event, threading.Event):
                    cancel_event.set()
        preview_deadline = time.monotonic() + 5
        for thread in list(preview_threads):
            thread.join(timeout=max(0.0, preview_deadline - time.monotonic()))
        previews_stopped = all(not thread.is_alive() for thread in preview_threads)
        provider_stopped = provider.close()
        # A slow provider call may outlive the ASGI shutdown timeout. Its worker
        # still owns this connection, so do not close it underneath the thread.
        if updates_stopped and caches_stopped and downloads_stopped and previews_stopped and provider_stopped:
            database.close()

    app = FastAPI(title="JmShelf", version=__version__, lifespan=lifespan)
    app.state.database = database
    app.state.provider = provider
    app.state.downloads = downloads
    app.state.caches = caches
    app.state.series_updates = series_updates
    app.state.data_root = resolved_data
    app.state.prune_library_cache = prune_library_cache

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})

    @app.exception_handler(Exception)
    async def general_error_handler(_request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"error": str(exc) or "服务器错误"})

    @app.middleware("http")
    async def local_mutation_guard(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD"}:
            if request.headers.get("x-jmshelf-request") != "1":
                return JSONResponse(status_code=403, content={"error": "请求来源无效"})
        response = await call_next(request)
        if not request.url.path.startswith(("/api/", "/media/")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "backend": "python"}

    @app.get("/api/app/about")
    def app_about():
        return {
            "name": "JmShelf",
            "version": __version__,
            "description": "轻量、快速、安心的本地漫画书架与阅读器",
            "website": "https://apps.usotsuki-kaze.com",
        }

    @app.post("/api/heartbeat")
    def heartbeat():
        state["lastHeartbeat"] = time.monotonic()
        return {"ok": True}

    @app.post("/api/activity/focus")
    def activity_focus(payload: dict[str, Any] = Body(default_factory=dict)):
        return provider.set_activity_focus(str(payload.get("scope") or "library"))

    @app.get("/api/provider/status")
    async def provider_status(probe: bool = False):
        if probe:
            try:
                return await asyncio.to_thread(provider.probe)
            except Exception:
                return provider.detect()
        return provider.detect()

    @app.post("/api/provider/login")
    async def provider_login(payload: dict[str, Any] = Body(default_factory=dict)):
        return await asyncio.to_thread(provider.login, payload.get("username"), payload.get("password"))

    @app.delete("/api/provider/login")
    def provider_logout():
        return provider.logout()

    @app.get("/api/provider/favorites")
    async def provider_favorites(search: str = "", refresh: bool = False):
        favorite_data = await asyncio.to_thread(provider.favorites, fresh=refresh)
        database.upsert_online_metadata_many(favorite_data.get("items", []))
        local_comics = database.list_comics()
        local_by_album: dict[str, list[dict[str, Any]]] = {}
        for comic in local_comics:
            keys = {
                str(value)
                for value in (comic.get("sourceId"), comic.get("seriesId"))
                if value
            }
            for key in keys:
                local_by_album.setdefault(key, []).append(comic)

        query = search.strip().casefold()
        local_ids: set[str] = set()
        remote_items: list[dict[str, Any]] = []
        visible_favorites = 0
        for item in favorite_data["items"]:
            album_id = str(item["albumId"])
            matches = local_by_album.get(album_id, [])
            haystack = " ".join([
                album_id,
                str(item.get("title") or ""),
                *[str(value) for value in item.get("authors", [])],
                *[str(value) for value in item.get("tags", [])],
                *[str(comic.get("displayName") or "") for comic in matches],
            ]).casefold()
            if query and query not in haystack:
                continue
            visible_favorites += 1
            if matches:
                local_ids.update(str(comic["id"]) for comic in matches)
            else:
                remote_items.append(item)

        return {
            **favorite_data,
            "visibleTotal": visible_favorites,
            "localComics": [comic for comic in local_comics if str(comic["id"]) in local_ids],
            "remoteItems": remote_items,
        }

    @app.post("/api/provider/favorites/{album_id}/shelf", status_code=202)
    async def add_provider_favorite_to_shelf(album_id: str):
        source_id = normalize_plate(album_id)
        if not source_id:
            raise ValueError("无效车牌")
        metadata = await asyncio.to_thread(provider.lookup, source_id, defer_covers=True)
        items = metadata.get("items") or [metadata]
        comics = [
            database.upsert_comic({
                **item,
                "storageKind": "remote",
                "sourceStatus": "matched",
                "sourceError": None,
            })
            for item in items
        ]
        source_series_ids = list(dict.fromkeys(
            str(comic["seriesId"])
            for comic in comics
            if comic.get("seriesId")
        ))
        library_series_items = [
            series
            for series_id in source_series_ids
            if (series := database.ensure_source_series(series_id)) is not None
        ]
        preview_seeds: dict[str, list[str]] = {}
        with preview_state_lock:
            preview = preview_sessions.get(source_id)
            if preview:
                cancel_event = preview.get("cancelEvent")
                if isinstance(cancel_event, threading.Event):
                    cancel_event.set()
                preview_source_id = str(preview.get("sourceId") or source_id)
                root_value = str(preview.get("rootPath") or "")
                root = Path(root_value).resolve() if root_value else None
                if root and path_is_inside(preview_root, root):
                    reusable = [
                        str((root / page["relativePath"]).resolve())
                        for page in preview_session_pages(preview)
                        if is_image((root / page["relativePath"]).resolve())
                    ]
                    if reusable:
                        preview_seeds[preview_source_id] = reusable
        # Cache chapters as independent FIFO tasks. The first chapter becomes
        # readable as soon as it finishes while later chapters keep filling in
        # quietly instead of delaying the entire series import.
        cache_result = enqueue_comic_caches(
            [comic["id"] for comic in comics],
            progressive=True,
            priority_comic_id=comics[0]["id"] if comics else None,
            seed_page_paths=preview_seeds,
        )
        return {
            **cache_result,
            "comicCount": len(comics),
            "comics": comics,
            "librarySeriesItems": library_series_items,
        }

    def annotate_online_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Persist only albums that actually reached the current UI response.
        # Partial search metadata is merged now; tag/detail hydration upgrades
        # the same indexed row later without scanning the remote catalogue.
        database.upsert_online_metadata_many(items)
        cached_metadata = {
            item["albumId"]: item
            for item in database.get_online_metadata([
                candidate.get("albumId") for candidate in items if candidate.get("albumId")
            ])
        }
        for item in items:
            album_id = str(item.get("albumId") or "")
            cached = cached_metadata.get(album_id)
            if not cached:
                continue
            if not item.get("authors"):
                item["authors"] = cached.get("authors", [])
            if not item.get("tags"):
                item["tags"] = cached.get("tags", [])
            if not item.get("works"):
                item["works"] = cached.get("works", [])
            if not item.get("actors"):
                item["actors"] = cached.get("actors", [])
            if not item.get("description"):
                item["description"] = cached.get("description", "")
            if not item.get("publishedAt"):
                item["publishedAt"] = cached.get("publishedAt", "")
            if not item.get("updatedAtSource"):
                item["updatedAtSource"] = cached.get("updatedAtSource", "")
            if not int(item.get("pageCount") or 0):
                item["pageCount"] = int(cached.get("pageCount") or 0)
            manifest = list(cached.get("episodeManifest") or [])
            if manifest:
                item["episodeManifest"] = manifest
                item["episodeCount"] = len(manifest)
                item["firstPhotoId"] = str(cached.get("firstPhotoId") or manifest[0].get("sourceId") or "")
                item["latestSourceId"] = str(manifest[-1].get("sourceId") or "")
                item["isSourceSeries"] = len(manifest) > 1
            item["metadataLoaded"] = bool(
                cached.get("detailsComplete") and manifest
            )
        local_comics = database.list_comics()
        local_by_source: dict[str, list[dict[str, Any]]] = {}
        for comic in local_comics:
            for value in (comic.get("sourceId"), comic.get("seriesId")):
                if value:
                    local_by_source.setdefault(str(value), []).append(comic)
        for item in items:
            matches = local_by_source.get(str(item.get("albumId") or ""), [])
            item["inShelf"] = bool(matches)
            item["localComicIds"] = list(dict.fromkeys(str(comic["id"]) for comic in matches))
            item["cached"] = any(
                comic.get("storageKind") == "cache"
                and comic.get("rootPath")
                for comic in matches
            )
            item["saved"] = any(
                comic.get("storageKind") in {"local", "download"}
                and comic.get("rootPath")
                for comic in matches
            )
            item["fullyAvailable"] = bool(matches) and all(
                comic.get("rootPath")
                and (
                    not int(comic.get("pageCount") or 0)
                    or len(_comic_pages(comic)) >= int(comic.get("pageCount") or 0)
                )
                for comic in matches
            )
        return items

    @app.get("/api/online/search")
    async def online_search(
        q: str = Query(min_length=1, max_length=200),
        page: int = Query(default=1, ge=1, le=500),
        mode: str = "site",
        sort: str = "latest",
        timeRange: str = "all",
        category: str = "all",
        dateFrom: str = "",
        dateTo: str = "",
    ):
        result = await asyncio.to_thread(
            provider.search,
            q,
            page=page,
            mode=mode,
            order=sort,
            time_range=timeRange,
            category=category,
            date_from=dateFrom,
            date_to=dateTo,
        )
        result["items"] = annotate_online_items(result["items"])
        return result

    @app.get("/api/online/preferences")
    def online_preferences():
        return provider.online_preferences()

    @app.patch("/api/online/preferences")
    def update_online_preferences(payload: dict[str, Any] = Body(default_factory=dict)):
        return provider.update_online_preferences(payload)

    @app.get("/api/online/recommendations")
    async def online_recommendations(
        sourceId: str = "",
        limit: int = Query(default=12, ge=1, le=24),
        batch: int = Query(default=0, ge=0, le=1000),
        sort: str = "diverse",
        excludeTag: list[str] = Query(default=[]),
    ):
        if sourceId:
            result = await asyncio.to_thread(
                provider.recommendations,
                sourceId,
                limit=limit,
                offset=batch,
                order=sort,
                excluded_tags=excludeTag,
            )
        else:
            result = await asyncio.to_thread(
                provider.recommendation_feed,
                limit=limit,
                offset=batch,
                order=sort,
                excluded_tags=excludeTag,
            )
        result["items"] = annotate_online_items(result["items"])
        return result

    @app.get("/api/online/home")
    async def online_home():
        result = await asyncio.to_thread(provider.home_feed)
        for section in result.get("sections", []):
            section["items"] = annotate_online_items(section.get("items", []))
        return result

    @app.get("/api/online/categories")
    async def online_categories(
        page: int = Query(default=1, ge=1, le=500),
        category: str = "all",
        sort: str = "latest",
        timeRange: str = "all",
    ):
        result = await asyncio.to_thread(
            provider.category_listing,
            page=page,
            category=category,
            order=sort,
            time_range=timeRange,
        )
        result["items"] = annotate_online_items(result["items"])
        return result

    @app.post("/api/online/metadata")
    async def online_metadata(payload: dict[str, Any] = Body(default_factory=dict)):
        return await asyncio.to_thread(
            provider.online_metadata,
            payload.get("albumIds", []),
            background=not bool(payload.get("priority")),
        )

    def prune_preview_cache(current_album_id: str) -> None:
        """Keep four recently used preview albums; active workers always win."""
        with preview_prune_lock, preview_state_lock:
            if not preview_root.is_dir():
                return
            protected_ids = {
                source_id
                for source_id, session in preview_sessions.items()
                if source_id == current_album_id
                or session.get("status") == "loading"
            }
            folders: list[Path] = []
            for folder in preview_root.iterdir():
                try:
                    resolved = folder.resolve()
                    if folder.is_dir() and path_is_inside(preview_root, resolved):
                        folders.append(resolved)
                except OSError:
                    continue
            protected_folders = {
                (preview_root / f"JM{source_id}").resolve()
                for source_id in protected_ids
            }
            candidates = sorted(
                (folder for folder in folders if folder not in protected_folders),
                key=lambda folder: folder.stat().st_mtime,
                reverse=True,
            )
            reserved = len(protected_ids)
            keep_count = max(0, 4 - reserved)
            stale_candidates = candidates[keep_count:]
            for stale in stale_candidates:
                shutil.rmtree(stale, ignore_errors=True)
                for source_id, session in list(preview_sessions.items()):
                    raw_root = str(session.get("rootPath") or "")
                    session_root = Path(raw_root).resolve() if raw_root else None
                    if session.get("status") != "loading" and session_root and path_is_inside(stale, session_root):
                        preview_sessions.pop(source_id, None)
            now = time.monotonic()
            for source_id, session in list(preview_sessions.items()):
                folder = (preview_root / f"JM{source_id}").resolve()
                idle_for = now - float(session.get("lastAccessMonotonic") or 0)
                if session.get("status") != "loading" and idle_for >= 60 * 60 and not folder.exists():
                    preview_sessions.pop(source_id, None)

    app.state.prune_preview_cache = prune_preview_cache

    def preview_session_pages(session: dict[str, Any]) -> list[dict[str, Any]]:
        root_value = str(session.get("rootPath") or "")
        page_files = session.get("pageFiles")
        if root_value and isinstance(page_files, list):
            root = Path(root_value).resolve()
            pages: list[dict[str, Any]] = []
            for raw_relative in page_files:
                relative = Path(str(raw_relative))
                target = (root / relative).resolve()
                if path_is_inside(root, target) and is_image(target):
                    pages.append({
                        "index": len(pages),
                        "name": target.name,
                        "relativePath": str(target.relative_to(root)),
                    })
            if pages:
                return pages
        return _comic_pages(session)

    def online_preview_payload(album_id: str) -> dict[str, Any]:
        with preview_state_lock:
            stored = preview_sessions.get(album_id)
            if stored:
                stored["lastAccessMonotonic"] = time.monotonic()
                preview_folder = preview_root / f"JM{album_id}"
                if preview_folder.is_dir():
                    try:
                        os.utime(preview_folder, None)
                    except OSError:
                        pass
            session = dict(stored or {})
        if not session:
            raise ValueError("预览任务不存在")
        pages = preview_session_pages(session)
        if session.get("status") == "loading":
            available = min(len(pages), max(0, int(session.get("availablePages") or 0)))
            # Publish streaming previews in stable four-page groups. The last
            # short group is exposed only when the preview has completed.
            pages = pages[:available - available % 4]
        return {
            "albumId": album_id,
            "sourceId": str(session.get("sourceId") or album_id),
            "title": str(session.get("title") or f"JM{album_id}"),
            "displayName": str(session.get("displayName") or session.get("title") or f"JM{album_id}"),
            "status": str(session.get("status") or "loading"),
            "error": str(session.get("error") or ""),
            "phase": str(session.get("phase") or ""),
            "pageCount": max(len(pages), int(session.get("expectedPages") or 0)),
            "availablePages": len(pages),
            "pages": [
                {
                    "index": page["index"],
                    "name": page["name"],
                    "url": f"/media/online-preview/{album_id}/{page['index']}",
                }
                for page in pages
            ],
        }

    def start_online_preview(album_id: str, *, chapter_id: str = "") -> dict[str, Any]:
        requested_source_id = normalize_plate(chapter_id) or ""
        with preview_state_lock:
            existing = preview_sessions.get(album_id)
            existing_pages = preview_session_pages(existing) if existing else []
            same_chapter = bool(
                existing
                and (
                    not requested_source_id
                    or str(existing.get("requestedSourceId") or existing.get("sourceId") or "") == requested_source_id
                )
            )
            if existing and existing.get("status") == "loading" and same_chapter:
                existing["lastAccessMonotonic"] = time.monotonic()
                should_start = False
            elif existing and existing_pages and existing.get("status") == "ready" and same_chapter:
                existing["lastAccessMonotonic"] = time.monotonic()
                should_start = False
            else:
                old_cancel_event = (existing or {}).get("cancelEvent")
                if isinstance(old_cancel_event, threading.Event):
                    old_cancel_event.set()
                cancel_event = threading.Event()
                session_token = uuid.uuid4().hex
                session = dict(existing or {})
                session.update({
                    "albumId": album_id,
                    "sourceId": requested_source_id or album_id,
                    "requestedSourceId": requested_source_id,
                    "title": str(session.get("title") or f"JM{album_id}"),
                    "displayName": str(session.get("displayName") or f"JM{album_id}"),
                    "status": "loading",
                    "phase": "正在选取最优链路…",
                    "availablePages": len(existing_pages) if same_chapter else 0,
                    "expectedPages": max(len(existing_pages), int(session.get("expectedPages") or 0)) if same_chapter else 0,
                    "error": "",
                    "cancelEvent": cancel_event,
                    "sessionToken": session_token,
                    "rootPath": str(session.get("rootPath") or "") if same_chapter else "",
                    "pageFiles": list(session.get("pageFiles") or []) if same_chapter else [],
                    "lastAccessMonotonic": time.monotonic(),
                })
                preview_sessions[album_id] = session
                should_start = True

        if not should_start:
            return online_preview_payload(album_id)

        def worker() -> None:
            try:
                with nullcontext():
                    if cancel_event.is_set():
                        raise RuntimeError("在线预览已取消")
                    preview_root.mkdir(parents=True, exist_ok=True)
                    current_folder = (preview_root / f"JM{album_id}").resolve()
                    prune_preview_cache(album_id)

                    def preview_lookup(force_manifest: bool = False) -> tuple[dict[str, Any], str, str]:
                        # The common path is entirely local: cached manifests
                        # win, otherwise an album id is optimistically used as
                        # its first photo id. Only uncommon multi-volume ids
                        # rejected by the chapter endpoint pay for /album.
                        if force_manifest:
                            metadata = provider.inspect_series(album_id)
                            items = metadata.get("items") or [metadata]
                            first = next((
                                item for item in items
                                if item.get("sourceId")
                                and (not requested_source_id or str(item.get("sourceId")) == requested_source_id)
                            ), None)
                            if not first:
                                raise RuntimeError("没有找到可预览的章节")
                            resolved_title = str(
                                metadata.get("seriesTitle") or first.get("title") or f"JM{album_id}"
                            )
                            first_hint = {
                                **first,
                                "seriesId": str(metadata.get("seriesId") or album_id),
                                "seriesTitle": resolved_title,
                                "authors": [],
                                "tags": [],
                                "description": "",
                                "pageCount": int(first.get("pageCount") or 0),
                            }
                            return ({
                                **first_hint,
                                "queryId": album_id,
                                "items": [first_hint],
                                "itemCount": int(metadata.get("itemCount") or len(items) or 1),
                                "previewOptimistic": False,
                            }, str(first_hint["sourceId"]), resolved_title)

                        hint = provider.preview_manifest_hint(album_id, requested_source_id)
                        first = next(
                            (item for item in (hint.get("items") or []) if item.get("sourceId")),
                            None,
                        )
                        if not first:
                            raise RuntimeError("没有找到可预览的章节")
                        return (
                            hint,
                            str(first["sourceId"]),
                            str(hint.get("seriesTitle") or first.get("title") or f"JM{album_id}"),
                        )

                    lookup_hint, source_id, title = preview_lookup()

                    def preview_progress(patch: dict[str, Any]) -> None:
                        phase = str(patch.get("phase") or "")
                        raw_root = str(patch.get("streamingRoot") or "")
                        root = Path(raw_root).resolve() if raw_root else None
                        page_files: list[str] = []
                        if root and path_is_inside(preview_root, root):
                            for raw_path in patch.get("availablePagePaths") or []:
                                target = Path(str(raw_path)).resolve()
                                if path_is_inside(root, target) and is_image(target):
                                    page_files.append(str(target.relative_to(root)))
                        with preview_state_lock:
                            current = preview_sessions.get(album_id)
                            if (
                                not current
                                or current.get("status") != "loading"
                                or current.get("sessionToken") != session_token
                            ):
                                return
                            update = {
                                "sourceId": source_id,
                                "title": title,
                                "displayName": title,
                                "availablePages": max(
                                    int(current.get("availablePages") or 0),
                                    int(patch.get("availablePages") or 0),
                                ),
                                "expectedPages": max(
                                    int(current.get("expectedPages") or 0),
                                    int(patch.get("expectedPages") or 0),
                                ),
                                "lastAccessMonotonic": time.monotonic(),
                            }
                            if phase:
                                update["phase"] = phase
                            if root and path_is_inside(preview_root, root):
                                update["rootPath"] = str(root)
                            current.update(update)
                            if page_files:
                                current["pageFiles"] = page_files

                    parameters = inspect.signature(provider.download).parameters
                    keyword_args: dict[str, Any] = {}
                    if "progress" in parameters:
                        keyword_args["progress"] = preview_progress
                    if "selected_source_ids" in parameters:
                        keyword_args["selected_source_ids"] = [source_id]
                    if "stream_first_pages" in parameters:
                        keyword_args["stream_first_pages"] = 4
                    if "lookup_hint" in parameters:
                        keyword_args["lookup_hint"] = lookup_hint
                    if "foreground" in parameters:
                        keyword_args["foreground"] = True
                    if "max_pages" in parameters:
                        keyword_args["max_pages"] = 0
                    if "cancel_event" in parameters:
                        keyword_args["cancel_event"] = cancel_event
                    if "transfer_priority" in parameters:
                        keyword_args["transfer_priority"] = "preview"
                    try:
                        result = provider.download(album_id, current_folder, **keyword_args)
                    except Exception:
                        if not lookup_hint.get("previewOptimistic") or cancel_event.is_set():
                            raise
                        with preview_state_lock:
                            current = preview_sessions.get(album_id)
                            if (
                                current
                                and current.get("status") == "loading"
                                and current.get("sessionToken") == session_token
                            ):
                                current["phase"] = "正在选取最优链路…"
                        lookup_hint, source_id, title = preview_lookup(force_manifest=True)
                        if "selected_source_ids" in parameters:
                            keyword_args["selected_source_ids"] = [source_id]
                        if "lookup_hint" in parameters:
                            keyword_args["lookup_hint"] = lookup_hint
                        result = provider.download(album_id, current_folder, **keyword_args)
                    downloaded = next(
                        (item for item in result.get("items", []) if str(item.get("sourceId")) == source_id),
                        None,
                    )
                    if not downloaded:
                        raise RuntimeError("在线预览没有返回可用章节")
                    root = Path(str(downloaded.get("rootPath") or "")).resolve()
                    if not path_is_inside(preview_root, root):
                        raise RuntimeError("在线预览缓存目录无效")
                    final_pages = _comic_pages({"rootPath": str(root)})
                    if not final_pages:
                        raise RuntimeError("在线预览未找到可阅读图片")
                    session = {
                        **downloaded,
                        "albumId": album_id,
                        "sourceId": source_id,
                        "title": title,
                        "displayName": title,
                        "rootPath": str(root),
                        "preview": True,
                        "status": "ready",
                        "phase": "在线预览已完整载入",
                        "availablePages": len(final_pages),
                        "pageFiles": [page["relativePath"] for page in final_pages],
                        "expectedPages": max(int(downloaded.get("pageCount") or 0), len(final_pages)),
                        "error": "",
                        "cancelEvent": cancel_event,
                        "sessionToken": session_token,
                        "lastAccessMonotonic": time.monotonic(),
                    }
                    with preview_state_lock:
                        current = preview_sessions.get(album_id) or {}
                        if current.get("sessionToken") != session_token:
                            return
                        preview_sessions[album_id] = session
                    prune_preview_cache(album_id)
            except Exception as exc:
                with preview_state_lock:
                    session = preview_sessions.get(album_id)
                    if not session or session.get("sessionToken") != session_token:
                        return
                    pages = preview_session_pages(session)
                    cancelled = cancel_event.is_set()
                    session.update({
                        "status": "cancelled" if cancelled else "failed",
                        "availablePages": len(pages),
                        "error": "" if cancelled else str(exc) or type(exc).__name__,
                        "lastAccessMonotonic": time.monotonic(),
                    })
            finally:
                with preview_state_lock:
                    preview_threads.discard(threading.current_thread())

        thread = threading.Thread(target=worker, name=f"jmonline-preview-{album_id}", daemon=True)
        with preview_state_lock:
            preview_threads.add(thread)
        thread.start()
        return online_preview_payload(album_id)

    @app.post("/api/online/{album_id}/preview")
    def online_preview(album_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
        source_id = normalize_plate(album_id)
        if not source_id:
            raise ValueError("无效车牌")
        chapter_id = normalize_plate(payload.get("chapterId")) or ""
        return start_online_preview(source_id, chapter_id=chapter_id)

    @app.post("/api/online/{album_id}/preview/cancel")
    def cancel_online_preview(album_id: str):
        source_id = normalize_plate(album_id)
        if not source_id:
            raise ValueError("无效车牌")
        with preview_state_lock:
            session = preview_sessions.get(source_id)
            if not session:
                return {"ok": True, "cancelled": False}
            cancel_event = session.get("cancelEvent")
            if isinstance(cancel_event, threading.Event):
                cancel_event.set()
            session["lastAccessMonotonic"] = time.monotonic()
        return {"ok": True, "cancelled": True}

    @app.get("/api/online/{album_id}/preview")
    def online_preview_status(album_id: str):
        source_id = normalize_plate(album_id)
        if not source_id:
            raise ValueError("无效车牌")
        return online_preview_payload(source_id)

    @app.delete("/api/online/cache")
    def clear_online_cache():
        """Clear disposable online metadata, thumbnails and idle previews only."""
        with preview_state_lock:
            for session in preview_sessions.values():
                cancel_event = session.get("cancelEvent")
                if hasattr(cancel_event, "set"):
                    cancel_event.set()

        metadata_rows = database.clear_online_metadata()
        removed_previews = 0
        with preview_prune_lock, preview_state_lock:
            if preview_root.is_dir():
                for folder in list(preview_root.iterdir()):
                    try:
                        resolved = folder.resolve()
                        source_id = folder.name.removeprefix("JM")
                        session = preview_sessions.get(source_id)
                        if folder.is_dir() and path_is_inside(preview_root, resolved) and session and session.get("status") == "loading":
                            continue
                        if folder.is_dir() and path_is_inside(preview_root, resolved):
                            shutil.rmtree(resolved, ignore_errors=True)
                            removed_previews += 1
                    except OSError:
                        continue
            for source_id, session in list(preview_sessions.items()):
                if session.get("status") != "loading":
                    preview_sessions.pop(source_id, None)

        thumbnail_root = (resolved_data / "covers" / "thumbnails").resolve()
        removed_thumbnails = 0
        if thumbnail_root.is_dir():
            for path in list(thumbnail_root.iterdir()):
                try:
                    resolved = path.resolve()
                    if path.is_file() and path_is_inside(thumbnail_root, resolved):
                        path.unlink(missing_ok=True)
                        removed_thumbnails += 1
                except OSError:
                    continue
        provider.invalidate_cache()
        return {
            "ok": True,
            "metadataRows": metadata_rows,
            "previewFolders": removed_previews,
            "thumbnails": removed_thumbnails,
        }

    @app.post("/api/online/{album_id}/shelf", status_code=202)
    async def add_online_to_shelf(album_id: str):
        return await add_provider_favorite_to_shelf(album_id)

    @app.get("/api/comics")
    def list_comics(search: str = "", collection: str | None = None):
        return database.list_comics(search, collection)

    @app.post("/api/comics", status_code=201)
    def create_comic(payload: dict[str, Any] = Body(default_factory=dict)):
        comic = database.upsert_comic(payload)
        if comic.get("seriesId"):
            database.ensure_source_series(str(comic["seriesId"]))
        return comic

    @app.get("/api/comics/{comic_id}")
    def get_comic(comic_id: str):
        comic = database.get_comic(comic_id)
        if not comic:
            raise _error(404, "未找到本子")
        return comic

    @app.patch("/api/comics/{comic_id}")
    def update_comic(comic_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
        if "coverPath" in payload:
            cover = Path(str(payload.get("coverPath") or "")).expanduser().resolve()
            if not is_image(cover):
                raise ValueError("请选择有效的本地图片作为封面")
            payload["coverPath"] = str(cover)
        comic = database.update_comic(comic_id, payload)
        if not comic:
            raise _error(404, "未找到本子")
        return comic

    @app.delete("/api/comics/{comic_id}")
    def delete_comic(
        comic_id: str,
        delete_files: bool = Query(False, alias="deleteFiles"),
    ):
        comic = database.get_comic(comic_id)
        if not comic:
            raise _error(404, "未找到本子")
        return delete_comics_with_storage([comic]) if delete_files else remove_comics_from_shelf([comic])

    @app.put("/api/comics/{comic_id}/collections")
    def set_comic_collections(comic_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
        comic = database.set_comic_collections(comic_id, payload.get("collectionIds", []))
        if not comic:
            raise _error(404, "未找到本子")
        return comic

    @app.get("/api/comics/{comic_id}/pages")
    def comic_pages(comic_id: str):
        comic = database.get_comic(comic_id)
        if not comic:
            raise _error(404, "未找到本子")
        pages = cached_comic_pages(comic, refresh=True)
        stream_limit = streaming_page_limit(comic)
        return pages[:stream_limit] if stream_limit is not None else pages

    @app.put("/api/comics/{comic_id}/progress")
    def comic_progress(comic_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
        comic = database.get_comic(comic_id)
        if not comic:
            raise _error(404, "未找到本子")
        page = max(0, min(int(payload.get("page", 0)), max(0, comic["pageCount"] - 1)))
        return database.update_comic(comic_id, {"progressPage": page, "lastReadAt": datetime.now(UTC).isoformat()})

    @app.post("/api/comics/{comic_id}/read")
    def record_comic_read(comic_id: str):
        comic = database.record_comic_open(comic_id)
        if not comic:
            raise _error(404, "未找到本子")
        try:
            cache_next_source_chapter(comic_id)
        except ValueError:
            # Reading must remain available while the cache queue is shutting
            # down or while a concurrent library mutation changes the chapter.
            pass
        return comic

    @app.post("/api/comics/{comic_id}/reveal", status_code=202)
    def reveal_comic(comic_id: str, background_tasks: BackgroundTasks):
        comic = database.get_comic(comic_id)
        raw_root = str(comic.get("rootPath") or "").strip() if comic else ""
        if not raw_root:
            raise _error(404, "本子本地目录不存在")
        root_path = Path(raw_root).resolve()
        if not root_path.exists():
            raise _error(404, "本子本地目录不存在")
        background_tasks.add_task(reveal_in_explorer, root_path)
        return {"ok": True}

    @app.get("/api/library-series")
    def list_library_series():
        return database.list_library_series()

    @app.get("/api/library-series/startup-check")
    def startup_library_series_check():
        return series_updates.startup_snapshot()

    @app.post("/api/library-series", status_code=201)
    def create_library_series(payload: dict[str, Any] = Body(default_factory=dict)):
        kind = str(payload.get("kind") or "custom")
        if kind == "source":
            series = database.ensure_library_series(payload.get("comicIds", []), kind="source")
            if series is None:
                raise _error(409, "这些分 P 已属于不同系列，无法自动合并")
            return series
        return database.create_library_series(
            payload.get("comicIds", []),
            kind=kind,
        )

    @app.post("/api/library-series/{series_id}/refresh", status_code=202)
    async def refresh_library_series(series_id: str):
        check = await asyncio.to_thread(series_updates.check, series_id)
        series = database.get_library_series(series_id)
        if not series:
            raise _error(404, "未找到系列")
        cache_backed = bool(series.get("members")) and not any(
            member.get("rootPath") and member.get("storageKind") != "cache"
            for member in series["members"]
        )
        output_path = cache_root if cache_backed else series_update_output(series)
        tasks: list[dict[str, Any]] = []
        created_count = 0
        for item in check["newItems"]:
            if cache_backed:
                task, created = caches.enqueue(
                    str(item["sourceId"]),
                    output_path,
                    f"{series['displayName']} · {item['title']}",
                    {"selectedSourceIds": [str(item["sourceId"])]},
                )
            else:
                task, created = downloads.enqueue(
                    str(item["sourceId"]),
                    output_path,
                    f"{series['displayName']} · {item['title']}",
                )
            tasks.append(task)
            created_count += int(created)
        return {
            **check,
            "series": database.get_library_series(series_id),
            "tasks": tasks,
            "queuedCount": created_count,
            "outputPath": str(output_path),
            "queueKind": "cache" if cache_backed else "download",
        }

    @app.delete("/api/library-series/{series_id}")
    def delete_library_series(
        series_id: str,
        delete_comics: bool = Query(False, alias="deleteComics"),
    ):
        series = database.get_library_series(series_id)
        if not series:
            raise _error(404, "未找到系列")
        if delete_comics:
            return {**delete_comics_with_storage(series["members"]), "seriesDeleted": True}
        if not database.delete_library_series(series_id):
            raise _error(404, "未找到系列")
        return {"ok": True, "deletedCount": 0, "deletedPaths": [], "seriesDeleted": True}

    @app.get("/api/collections")
    def list_collections():
        return database.list_collections()

    @app.post("/api/collections", status_code=201)
    def create_collection(payload: dict[str, Any] = Body(default_factory=dict)):
        return database.create_collection(str(payload.get("name", "")), payload.get("parentId"))

    @app.patch("/api/collections/{collection_id}")
    def update_collection(collection_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
        collection = database.update_collection(
            collection_id,
            payload.get("name"),
            payload.get("parentId"),
            "parentId" in payload,
        )
        if not collection:
            raise _error(404, "未找到收藏夹")
        return collection

    @app.delete("/api/collections/{collection_id}")
    def delete_collection(collection_id: str):
        if not database.delete_collection(collection_id):
            raise _error(404, "未找到收藏夹")
        return {"ok": True}

    @app.post("/api/collections/{collection_id}/items")
    def add_collection_items(collection_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
        return database.add_comics_to_collection(collection_id, payload.get("comicIds", []))

    @app.post("/api/dialog/folder")
    async def folder_dialog():
        return {"path": await asyncio.to_thread(pick_folder)}

    @app.post("/api/dialog/image")
    async def image_dialog():
        return {"path": await asyncio.to_thread(pick_image_file)}

    @app.post("/api/import/folder", status_code=201)
    def import_folder(payload: dict[str, Any] = Body(default_factory=dict)):
        selected = payload.get("path")
        if not selected or not Path(selected).exists():
            raise ValueError("文件夹不存在")
        return [database.upsert_comic(item) for item in scan_import_root(selected)]

    @app.post("/api/source/lookup")
    async def source_lookup(payload: dict[str, Any] = Body(default_factory=dict)):
        metadata = await asyncio.to_thread(provider.lookup, payload.get("plate"), defer_covers=True, fresh=bool(payload.get("comicId")))
        comic_id = payload.get("comicId")
        if not comic_id:
            items = metadata.get("items") or [metadata]
            local_by_source: dict[str, list[dict[str, Any]]] = {}
            for local in database.list_comics():
                source_id = str(local.get("sourceId") or "")
                if source_id:
                    local_by_source.setdefault(source_id, []).append(local)
            existing_count = 0
            for item in items:
                matches = local_by_source.get(str(item.get("sourceId") or ""), [])
                item["inShelf"] = bool(matches)
                item["existingComicIds"] = [str(local["id"]) for local in matches]
                existing_count += int(bool(matches))
            metadata["existingCount"] = existing_count
            metadata["inShelf"] = bool(items) and existing_count == len(items)
            return metadata
        comic = database.get_comic(comic_id)
        if not comic:
            raise _error(404, "未找到待匹配本子")
        items = metadata.get("items") or [metadata]
        matched = next(
            (item for item in items if str(item.get("sourceId")) == str(comic.get("sourceId"))),
            items[0],
        )
        updated = database.update_comic(comic_id, {**matched, "sourceStatus": "matched", "sourceError": None})
        if not updated:
            raise _error(404, "未找到待匹配本子")
        if updated.get("seriesId"):
            database.ensure_source_series(str(updated["seriesId"]))
        return updated

    @app.post("/api/source/download")
    async def source_download(payload: dict[str, Any] = Body(default_factory=dict)):
        source_id = normalize_plate(payload.get("plate"))
        if not source_id:
            raise ValueError("无效车牌")
        configured_path = database.get_setting("download.defaultPath", "").strip()
        output_base = Path(payload.get("outputPath") or configured_path or resolved_data / "downloads").resolve()
        result = await asyncio.to_thread(provider.download, source_id, output_base)
        return import_download_result(result, "download")

    @app.get("/api/caches")
    def list_caches():
        return caches.snapshot()

    @app.post("/api/caches", status_code=202)
    def enqueue_caches(payload: dict[str, Any] = Body(default_factory=dict)):
        return enqueue_comic_caches(
            payload.get("comicIds", []),
            progressive=bool(payload.get("progressive")),
            priority_comic_id=payload.get("priorityComicId"),
        )

    @app.post("/api/caches/clear")
    def clear_caches(payload: dict[str, Any] = Body(default_factory=dict)):
        return clear_comic_caches(payload.get("comicIds", []))

    @app.get("/api/downloads")
    def list_downloads():
        return downloads.snapshot()

    @app.post("/api/downloads", status_code=202)
    def enqueue_download(payload: dict[str, Any] = Body(default_factory=dict)):
        source_id = normalize_plate(payload.get("plate"))
        if not source_id:
            raise ValueError("无效车牌")
        configured_path = database.get_setting("download.defaultPath", "").strip()
        output_base = Path(payload.get("outputPath") or configured_path or resolved_data / "downloads").resolve()
        task, created = downloads.enqueue(source_id, output_base, str(payload.get("title") or ""))
        return {"task": task, "created": created}

    @app.post("/api/downloads/{task_id}/retry", status_code=202)
    def retry_download(task_id: str):
        task, created = downloads.retry(task_id)
        return {"task": task, "created": created}

    @app.get("/api/settings")
    def get_settings():
        return {
            "optionPath": database.get_setting("provider.optionPath", ""),
            "proxy": database.get_setting("provider.proxy", ""),
            "downloadPath": database.get_setting("download.defaultPath", ""),
            "sanityMode": database.get_setting("privacy.sanityMode", "0") == "1",
        }

    @app.patch("/api/settings")
    def update_settings(payload: dict[str, Any] = Body(default_factory=dict)):
        if "optionPath" in payload:
            option_path = str(payload["optionPath"] or "").strip()
            if option_path and not Path(option_path).expanduser().is_file():
                raise ValueError("jmcomic 配置文件不存在")
            database.set_setting("provider.optionPath", option_path)
        if "proxy" in payload:
            database.set_setting("provider.proxy", provider.normalize_proxy(payload["proxy"]))
        if "downloadPath" in payload:
            download_path = str(payload["downloadPath"] or "").strip()
            if download_path and Path(download_path).exists() and not Path(download_path).is_dir():
                raise ValueError("默认下载地址必须是文件夹")
            database.set_setting("download.defaultPath", download_path)
        if "sanityMode" in payload:
            database.set_setting("privacy.sanityMode", "1" if bool(payload["sanityMode"]) else "0")
        provider.invalidate_cache()
        return {"ok": True, **get_settings()}

    @app.get("/api/app/update")
    def app_update_status():
        return app_updates.snapshot()

    @app.post("/api/app/update/check")
    def check_app_update():
        return app_updates.check()

    @app.post("/api/app/update/download", status_code=202)
    def download_app_update():
        return app_updates.start_download()

    @app.post("/api/app/update/apply", status_code=202)
    @app.post("/api/app/update/install", status_code=202, include_in_schema=False)
    def apply_app_update(background_tasks: BackgroundTasks):
        if not getattr(sys, "frozen", False):
            raise _error(409, "文件级更新仅在已打包的 Windows 应用中可用")
        executable = Path(sys.executable).resolve()
        app_updates.launch_update(executable.parent, executable.name, os.getpid())

        def stop_for_update() -> None:
            time.sleep(0.7)
            stop_tray_icon()
            os.kill(os.getpid(), signal.SIGTERM)

        background_tasks.add_task(stop_for_update)
        return {"ok": True, "state": "applying"}

    @app.post("/api/window/minimize", status_code=202)
    def minimize(background_tasks: BackgroundTasks):
        background_tasks.add_task(minimize_foreground_window)
        return {"ok": True}

    @app.post("/api/window/to-tray", status_code=202)
    def to_tray(background_tasks: BackgroundTasks):
        background_tasks.add_task(hide_foreground_to_tray, PUBLIC_ROOT / "ukp.png")
        return {"ok": True}

    @app.post("/api/app/exit", status_code=202)
    def exit_app(background_tasks: BackgroundTasks):
        def stop() -> None:
            time.sleep(0.15)
            stop_tray_icon()
            os.kill(os.getpid(), signal.SIGTERM)

        background_tasks.add_task(stop)
        return {"ok": True}

    @app.get("/media/cover/{comic_id}")
    def comic_cover(comic_id: str):
        comic = database.get_comic(comic_id)
        cover = Path(comic["coverPath"]) if comic and comic.get("coverPath") else None
        if cover and not cover.is_file() and comic.get("sourceId"):
            expected = resolved_data / "covers" / "chapters" / f"{comic['sourceId']}.jpg"
            if cover.resolve() == expected:
                cover = provider.get_cover(comic["sourceId"])
        if not cover or not cover.is_file():
            raise _error(404, "封面不存在")
        return FileResponse(cover, media_type=mimetypes.guess_type(cover.name)[0])

    @app.get("/media/source-cover/{source_id}")
    def source_cover(source_id: str):
        normalized = normalize_plate(source_id)
        cover = provider.get_cover(normalized) if normalized else None
        if not cover or not cover.is_file():
            raise _error(404, "封面不存在")
        return FileResponse(
            cover,
            media_type=mimetypes.guess_type(cover.name)[0],
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/media/source-thumbnail/{source_id}")
    def source_thumbnail(source_id: str):
        normalized = normalize_plate(source_id)
        cover = provider.get_thumbnail(normalized) if normalized else None
        if not cover or not cover.is_file():
            raise _error(404, "封面不存在")
        return FileResponse(
            cover,
            media_type=mimetypes.guess_type(cover.name)[0],
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/media/page/{comic_id}/{page_index}")
    def comic_page(comic_id: str, page_index: int):
        comic = database.get_comic(comic_id)
        pages = cached_comic_pages(comic)
        if not comic or page_index < 0 or page_index >= len(pages):
            raise _error(404, "页面不存在")
        root = Path(comic["rootPath"]).resolve()
        target = (root / pages[page_index]["relativePath"]).resolve()
        if not path_is_inside(root, target) or not target.is_file():
            raise _error(404, "页面不存在")
        return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0])

    @app.get("/media/online-preview/{album_id}/{page_index}")
    def online_preview_page(album_id: str, page_index: int):
        source_id = normalize_plate(album_id)
        if not source_id:
            raise _error(404, "预览页面不存在")
        with preview_state_lock:
            stored = preview_sessions.get(source_id)
            if stored:
                stored["lastAccessMonotonic"] = time.monotonic()
            session = dict(stored or {})
        pages = preview_session_pages(session)
        if session.get("status") == "loading":
            pages = pages[:max(0, int(session.get("availablePages") or 0))]
        if not session or page_index < 0 or page_index >= len(pages):
            raise _error(404, "预览页面不存在")
        root = Path(str(session["rootPath"])).resolve()
        target = (root / pages[page_index]["relativePath"]).resolve()
        if not path_is_inside(preview_root, target) or not target.is_file():
            raise _error(404, "预览页面不存在")
        return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0])

    app.mount("/", StaticFiles(directory=PUBLIC_ROOT, html=True), name="public")
    return app


def run() -> None:
    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=int(os.environ.get("JMSHELF_PORT", "17318")),
        log_level="info",
    )


if __name__ == "__main__":
    run()
