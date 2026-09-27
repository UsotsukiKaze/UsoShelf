from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import urllib.request
import uuid
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse


DEFAULT_MANIFEST_URL = "https://apps.usotsuki-kaze.com/releases/jmshelf/stable/latest.json"
_VERSION_PART = re.compile(r"\d+")
_SAFE_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}")
_MAX_MANIFEST_BYTES = 512 * 1024
_MAX_UNPACKED_BYTES = 1536 * 1024 * 1024
_DOWNLOAD_ATTEMPTS = 4
_UPDATER_READY_TIMEOUT = 90.0
_APPLY_STATUS_SCHEMA = 1


def version_key(value: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in _VERSION_PART.findall(str(value)))
    return parts or (0,)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class AppUpdateManager:
    def __init__(
        self,
        current_version: str,
        update_root: Path,
        proxy_getter: Callable[[], str] | None = None,
        manifest_url: str = DEFAULT_MANIFEST_URL,
    ) -> None:
        self.current_version = current_version
        self.update_root = update_root.resolve()
        self.proxy_getter = proxy_getter or (lambda: "")
        self.manifest_url = manifest_url
        self.lock = threading.RLock()
        self.manifest: dict[str, Any] | None = None
        self.last_apply_path = self.update_root / "last-apply.json"
        self.status: dict[str, Any] = {
            "state": "idle",
            "currentVersion": current_version,
            "available": False,
            "downloaded": 0,
            "total": 0,
            "progress": 0,
            "error": None,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            result = deepcopy(self.status)
            result["manifestUrl"] = self.manifest_url
            if self.manifest:
                result["release"] = deepcopy(self.manifest)
        last_apply = _read_json(self.last_apply_path)
        if last_apply:
            result["lastApply"] = last_apply
        return result

    def _set_status(self, **patch: Any) -> None:
        with self.lock:
            self.status.update(patch)

    def _opener(self) -> urllib.request.OpenerDirector:
        proxy = str(self.proxy_getter() or "").strip()
        handlers: list[Any] = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        return urllib.request.build_opener(*handlers)

    def _request(self, url: str, *, offset: int = 0) -> urllib.request.Request:
        headers = {
            "Accept": "application/json, application/zip;q=0.9, */*;q=0.5",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
            "User-Agent": f"JmShelf/{self.current_version} Windows",
        }
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"
        return urllib.request.Request(url, headers=headers)

    def _validate_manifest(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("更新清单格式无效")
        version = str(value.get("version") or "").strip()
        portable = value.get("portable")
        if not version or not _SAFE_VERSION.fullmatch(version) or not isinstance(portable, dict):
            raise ValueError("更新清单缺少版本或文件更新包信息")
        package_url = str(portable.get("url") or "").strip()
        manifest_host = urlparse(self.manifest_url).hostname
        parsed_package = urlparse(package_url)
        if (
            parsed_package.scheme != "https"
            or parsed_package.hostname != manifest_host
            or not parsed_package.path.casefold().endswith(".zip")
        ):
            raise ValueError("文件更新包必须是更新清单同域名下的 HTTPS ZIP 文件")
        sha256 = str(portable.get("sha256") or "").strip().upper()
        if not re.fullmatch(r"[0-9A-F]{64}", sha256):
            raise ValueError("更新清单中的 SHA-256 无效")
        size = int(portable.get("size") or 0)
        if size <= 0:
            raise ValueError("更新清单中的文件更新包大小无效")
        clean = deepcopy(value)
        clean["version"] = version
        clean["portable"] = {**portable, "url": package_url, "sha256": sha256, "size": size}
        clean["releaseNotes"] = [str(item) for item in value.get("releaseNotes", []) if str(item).strip()][:12]
        return clean

    def check(self) -> dict[str, Any]:
        self._set_status(state="checking", error=None)
        try:
            with self._opener().open(self._request(self.manifest_url), timeout=12) as response:
                payload = response.read(_MAX_MANIFEST_BYTES + 1)
            if len(payload) > _MAX_MANIFEST_BYTES:
                raise ValueError("更新清单体积异常")
            manifest = self._validate_manifest(json.loads(payload.decode("utf-8-sig")))
            available = version_key(manifest["version"]) > version_key(self.current_version)
            with self.lock:
                self.manifest = manifest
            self._set_status(
                state="available" if available else "up-to-date",
                available=available,
                latestVersion=manifest["version"],
                downloaded=0,
                total=manifest["portable"]["size"],
                progress=0,
                error=None,
                packagePath=None,
                stagedPath=None,
            )
        except Exception as exc:
            self._set_status(state="error", error=f"检查更新失败：{exc}")
        return self.snapshot()

    def start_download(self) -> dict[str, Any]:
        with self.lock:
            if self.status["state"] == "downloading":
                return self.snapshot()
            manifest = deepcopy(self.manifest)
            available = bool(self.status.get("available"))
        if not manifest or not available:
            raise ValueError("请先检查并确认存在新版本")
        self._set_status(state="downloading", downloaded=0, progress=0, error=None)
        threading.Thread(target=self._download, args=(manifest,), name="jmshelf-app-update", daemon=True).start()
        return self.snapshot()

    @staticmethod
    def _hash_file(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return size, digest.hexdigest().upper()

    def _download_package(self, package: dict[str, Any], partial: Path) -> tuple[int, str]:
        expected_size = int(package["size"])
        expected_digest = str(package["sha256"])
        if partial.is_file() and partial.stat().st_size > expected_size:
            partial.unlink()
        last_error: Exception | None = None
        for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
            try:
                offset = partial.stat().st_size if partial.is_file() else 0
                if offset == expected_size:
                    size, digest = self._hash_file(partial)
                    if digest == expected_digest:
                        return size, digest
                    partial.unlink()
                    offset = 0
                request = self._request(package["url"], offset=offset)
                with self._opener().open(request, timeout=35) as response:
                    status = int(getattr(response, "status", response.getcode()))
                    append = offset > 0 and status == 206
                    if offset > 0 and not append:
                        offset = 0
                    mode = "ab" if append else "wb"
                    downloaded = offset
                    with partial.open(mode) as output:
                        while chunk := response.read(1024 * 1024):
                            output.write(chunk)
                            downloaded += len(chunk)
                            if downloaded > expected_size:
                                raise ValueError("下载文件超过清单声明大小")
                            self._set_status(
                                downloaded=downloaded,
                                progress=min(96, round(downloaded * 96 / expected_size)),
                            )
                size, digest = self._hash_file(partial)
                if size != expected_size:
                    raise ValueError(f"下载文件大小不一致（{size}/{expected_size}）")
                if digest != expected_digest:
                    partial.unlink(missing_ok=True)
                    raise ValueError("文件更新包 SHA-256 校验失败")
                return size, digest
            except Exception as exc:
                last_error = exc
                if attempt < _DOWNLOAD_ATTEMPTS:
                    time.sleep(min(3.0, 0.45 * attempt))
        raise RuntimeError(f"下载重试 {_DOWNLOAD_ATTEMPTS} 次后仍失败：{last_error}")

    def _download(self, manifest: dict[str, Any]) -> None:
        package = manifest["portable"]
        version = manifest["version"]
        filename = Path(urlparse(package["url"]).path).name
        target_dir = (self.update_root / version).resolve()
        target = target_dir / filename
        partial = target.with_suffix(target.suffix + ".part")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if target.is_file():
                size, digest = self._hash_file(target)
                if size != package["size"] or digest != package["sha256"]:
                    target.unlink()
                else:
                    self._set_status(downloaded=size, progress=97)
            if not target.is_file():
                downloaded, digest = self._download_package(package, partial)
                if digest != package["sha256"]:
                    partial.unlink(missing_ok=True)
                    raise ValueError("文件更新包 SHA-256 校验失败")
                os.replace(partial, target)
                self._set_status(downloaded=downloaded, progress=97)
            staged = self._extract_package(target, target_dir)
            self._set_status(
                state="ready",
                downloaded=package["size"],
                progress=100,
                packagePath=str(target),
                stagedPath=str(staged),
                error=None,
            )
        except Exception as exc:
            self._set_status(state="error", error=f"准备更新失败：{exc}")

    def _extract_package(self, archive: Path, target_dir: Path) -> Path:
        attempt_id = uuid.uuid4().hex
        staged = target_dir / f"staged.{attempt_id}"
        staged.mkdir(parents=True)
        total_unpacked = 0
        try:
            with zipfile.ZipFile(archive) as bundle:
                for entry in bundle.infolist():
                    relative = PurePosixPath(entry.filename.replace("\\", "/"))
                    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                        raise ValueError("文件更新包包含不安全路径")
                    unix_mode = entry.external_attr >> 16
                    if stat.S_ISLNK(unix_mode):
                        raise ValueError("文件更新包不能包含符号链接")
                    total_unpacked += max(0, entry.file_size)
                    if total_unpacked > _MAX_UNPACKED_BYTES:
                        raise ValueError("文件更新包解压体积异常")
                    if relative.parts[0].casefold() != "jmshelf":
                        raise ValueError("文件更新包目录结构无效")
                    destination = staged.joinpath(*relative.parts[1:])
                    if entry.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(entry) as source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
            if (
                not staged.joinpath("JmShelf.exe").is_file()
                or not staged.joinpath("JmShelfUpdater.exe").is_file()
                or not staged.joinpath("_internal").is_dir()
            ):
                raise ValueError("文件更新包缺少主程序、独立更新器或运行时目录")
            self._cleanup_old_staging(target_dir, keep=staged)
            return staged.resolve()
        except Exception:
            shutil.rmtree(staged, ignore_errors=True)
            raise

    @staticmethod
    def _cleanup_old_staging(target_dir: Path, *, keep: Path) -> None:
        for candidate in target_dir.glob("staged.*"):
            if candidate != keep:
                shutil.rmtree(candidate, ignore_errors=True)

    def launch_update(self, install_root: Path, executable_name: str, process_id: int) -> Path:
        with self.lock:
            raw_staged = self.status.get("stagedPath")
            state = self.status.get("state")
            version = str(self.status.get("latestVersion") or "update")
        staged = Path(str(raw_staged or "")).resolve()
        target = install_root.resolve()
        if state != "ready" or not staged.joinpath(executable_name).is_file():
            raise ValueError("更新文件尚未准备完成")
        if not staged.is_relative_to(self.update_root) or staged.is_relative_to(target):
            raise ValueError("更新暂存目录无效")
        if not target.joinpath(executable_name).is_file():
            raise ValueError("当前应用安装目录无效")

        bundled_updater = target / "JmShelfUpdater.exe"
        if not bundled_updater.is_file():
            raise ValueError("当前版本缺少独立更新器，请先使用安装包升级一次")

        operation_id = uuid.uuid4().hex
        operation_dir = self.update_root / version
        operation_dir.mkdir(parents=True, exist_ok=True)
        updater = operation_dir / f"JmShelfUpdater.{operation_id}.exe"
        plan_path = operation_dir / f"apply-plan.{operation_id}.json"
        status_path = operation_dir / f"apply-status.{operation_id}.json"
        health_path = operation_dir / f"app-health.{operation_id}.json"
        log_path = operation_dir / f"apply-update.{operation_id}.log"
        shutil.copy2(bundled_updater, updater)
        plan = {
            "schemaVersion": _APPLY_STATUS_SCHEMA,
            "operationId": operation_id,
            "version": version,
            "processId": int(process_id),
            "sourceRoot": str(staged),
            "targetRoot": str(target),
            "executableName": executable_name,
            "launchArguments": [
                "--update-operation", operation_id,
                "--update-health", str(health_path),
            ],
            "statusPath": str(status_path),
            "lastStatusPath": str(self.last_apply_path),
            "healthPath": str(health_path),
            "logPath": str(log_path),
            "createdAt": datetime.now(UTC).isoformat(),
        }
        _write_json(plan_path, plan)

        flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        process = subprocess.Popen(
            [str(updater), "--plan", str(plan_path)],
            cwd=str(operation_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )

        deadline = time.monotonic() + _UPDATER_READY_TIMEOUT
        while True:
            external = _read_json(status_path)
            if external and external.get("operationId") == operation_id:
                external_state = external.get("state")
                if external_state == "ready":
                    self._set_status(state="applying", applyOperationId=operation_id, error=None)
                    return updater
                if external_state == "failed":
                    raise ValueError(str(external.get("message") or "独立更新器准备失败"))
            return_code = process.poll()
            if return_code is not None:
                detail = str((external or {}).get("message") or "").strip()
                raise ValueError(f"独立更新器启动失败（退出码 {return_code}）{f'：{detail}' if detail else ''}")
            if time.monotonic() >= deadline:
                process.terminate()
                raise ValueError("独立更新器准备超时，JmShelf 保持运行，可直接重试")
            time.sleep(0.08)
