from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


PLAN_SCHEMA = 1
WAIT_TIMEOUT_SECONDS = 150
RETRY_TIMEOUT_SECONDS = 24


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{_now()} {message}\n")


def _remove_readonly(function: Callable[..., Any], path: str, _error: Any) -> None:
    os.chmod(path, 0o700)
    function(path)


def _remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onerror=_remove_readonly)
    else:
        path.chmod(0o700)
        path.unlink()


def _retry(action: Callable[[], None], *, timeout: float = RETRY_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    pause = 0.08
    while True:
        try:
            action()
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(pause)
            pause = min(0.8, pause * 1.35)


def _move(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _retry(lambda: os.replace(source, destination))


def _wait_for_process_exit(process_id: int, timeout: float) -> None:
    if process_id <= 0:
        return
    if os.name != "nt":
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(process_id, 0)
            except OSError:
                return
            time.sleep(0.1)
        raise TimeoutError("等待 JmShelf 退出超时")

    synchronize = 0x00100000
    wait_object_0 = 0
    wait_timeout = 0x00000102
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(synchronize, False, process_id)
    if not handle:
        return
    try:
        result = kernel32.WaitForSingleObject(handle, max(1, int(timeout * 1000)))
        if result == wait_timeout:
            raise TimeoutError("等待 JmShelf 退出超时")
        if result != wait_object_0:
            raise OSError(f"等待 JmShelf 退出失败（Windows 状态码 {result}）")
    finally:
        kernel32.CloseHandle(handle)


def _launch_application(executable: Path, working_directory: Path, arguments: list[str]) -> subprocess.Popen[Any]:
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    return subprocess.Popen(
        [str(executable), *arguments],
        cwd=str(working_directory),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


def _validate_plan(plan: dict[str, Any]) -> tuple[Path, Path, Path, Path, str, int, str, Path, str, list[str], Path | None]:
    if int(plan.get("schemaVersion") or 0) != PLAN_SCHEMA:
        raise ValueError("更新计划版本不受支持")
    operation_id = str(plan.get("operationId") or "").strip()
    version = str(plan.get("version") or "").strip()
    executable_name = str(plan.get("executableName") or "").strip()
    if not operation_id or not version or executable_name != Path(executable_name).name:
        raise ValueError("更新计划缺少必要信息")
    source = Path(str(plan.get("sourceRoot") or "")).resolve()
    target = Path(str(plan.get("targetRoot") or "")).resolve()
    status_path = Path(str(plan.get("statusPath") or "")).resolve()
    last_status_path = Path(str(plan.get("lastStatusPath") or "")).resolve()
    log_path = Path(str(plan.get("logPath") or "")).resolve()
    process_id = int(plan.get("processId") or 0)
    raw_arguments = plan.get("launchArguments") or []
    if not isinstance(raw_arguments, list) or any(not isinstance(value, str) for value in raw_arguments):
        raise ValueError("重启参数无效")
    launch_arguments = [str(value) for value in raw_arguments][:16]
    raw_health_path = str(plan.get("healthPath") or "").strip()
    health_path = Path(raw_health_path).resolve() if raw_health_path else None
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError("更新源目录与安装目录冲突")
    if not source.joinpath(executable_name).is_file() or not source.joinpath("_internal").is_dir():
        raise ValueError("更新文件不完整")
    if not target.is_dir() or not target.joinpath(executable_name).is_file():
        raise ValueError("JmShelf 安装目录无效")
    return source, target, status_path, last_status_path, executable_name, process_id, operation_id, log_path, version, launch_arguments, health_path


def apply_update_plan(
    plan_path: Path,
    *,
    wait_for_exit: Callable[[int, float], None] = _wait_for_process_exit,
    launch_application: Callable[[Path, Path, list[str]], subprocess.Popen[Any]] = _launch_application,
    startup_grace_seconds: float = 3.0,
    startup_health_timeout: float = 30.0,
) -> int:
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    source, target, status_path, last_status_path, executable_name, process_id, operation_id, log_path, version, launch_arguments, health_path = _validate_plan(plan)
    incoming = target / f".jmshelf-update-incoming-{operation_id}"
    backup = target / f".jmshelf-update-backup-{operation_id}"
    installed_names: list[str] = []
    backed_up_names: list[str] = []
    replacement_started = False
    application_stopped = False

    def report(state: str, message: str, **extra: Any) -> None:
        payload = {
            "schemaVersion": PLAN_SCHEMA,
            "operationId": operation_id,
            "version": version,
            "state": state,
            "message": message,
            "updatedAt": _now(),
            **extra,
        }
        _write_json(last_status_path, payload)
        _write_json(status_path, payload)
        _append_log(log_path, f"[{state}] {message}")

    try:
        report("preparing", "正在把更新文件准备到安装磁盘")
        _remove_path(incoming)
        _remove_path(backup)
        shutil.copytree(source, incoming, copy_function=shutil.copy2)
        if not incoming.joinpath(executable_name).is_file() or not incoming.joinpath("_internal").is_dir():
            raise ValueError("复制后的更新文件校验失败")

        report("ready", "更新器已就绪，正在等待 JmShelf 退出")
        wait_for_exit(process_id, WAIT_TIMEOUT_SECONDS)
        application_stopped = True
        report("replacing", "正在替换程序文件")
        backup.mkdir(parents=True, exist_ok=False)
        replacement_started = True

        for item in sorted(incoming.iterdir(), key=lambda value: value.name.casefold()):
            name = item.name
            target_item = target / name
            if target_item.exists() or target_item.is_symlink():
                _move(target_item, backup / name)
                backed_up_names.append(name)
            _move(item, target_item)
            installed_names.append(name)

        report("restarting", "程序文件已替换，正在重新启动 JmShelf")
        if health_path is not None:
            _remove_path(health_path)
        updated_process = launch_application(target / executable_name, target, launch_arguments)
        if health_path is None:
            # Compatibility path for update plans created by older releases.
            time.sleep(max(0.0, startup_grace_seconds))
            if updated_process.poll() is not None:
                raise RuntimeError(f"新版 JmShelf 启动后立即退出（退出码 {updated_process.returncode}）")
        else:
            health_deadline = time.monotonic() + max(0.0, startup_health_timeout)
            while True:
                return_code = updated_process.poll()
                if return_code is not None:
                    raise RuntimeError(f"新版 JmShelf 启动失败（退出码 {return_code}）")
                try:
                    health = json.loads(health_path.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError, TypeError):
                    health = None
                if (
                    isinstance(health, dict)
                    and health.get("operationId") == operation_id
                    and health.get("state") == "ready"
                ):
                    break
                if time.monotonic() >= health_deadline:
                    updated_process.terminate()
                    wait = getattr(updated_process, "wait", None)
                    if callable(wait):
                        try:
                            wait(timeout=5)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                    raise RuntimeError("新版 JmShelf 未能完成启动自检")
                time.sleep(0.1)

        try:
            report("completed", f"JmShelf {version} 更新完成")
        except Exception as status_exc:
            _append_log(log_path, f"[warning] 更新已完成，但状态文件写入失败：{status_exc}")
        for cleanup_target in (backup, incoming):
            try:
                _remove_path(cleanup_target)
            except OSError as cleanup_exc:
                _append_log(log_path, f"[warning] 清理 {cleanup_target.name} 失败：{cleanup_exc}")
        try:
            _remove_path(source)
        except OSError as cleanup_exc:
            _append_log(log_path, f"[warning] 清理更新源失败：{cleanup_exc}")
        return 0
    except Exception as exc:
        rolled_back = False
        rollback_error: str | None = None
        if replacement_started:
            try:
                for name in reversed(installed_names):
                    target_item = target / name
                    if target_item.exists() or target_item.is_symlink():
                        _retry(lambda current=target_item: _remove_path(current))
                for name in reversed(backed_up_names):
                    backup_item = backup / name
                    if backup_item.exists() or backup_item.is_symlink():
                        _move(backup_item, target / name)
                rolled_back = True
                if target.joinpath(executable_name).is_file():
                    launch_application(target / executable_name, target, launch_arguments)
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
        elif application_stopped and target.joinpath(executable_name).is_file():
            try:
                launch_application(target / executable_name, target, launch_arguments)
            except Exception as restart_exc:
                rollback_error = f"旧版本重新启动失败：{restart_exc}"
        for cleanup_target in (incoming, backup if rolled_back else None):
            if cleanup_target is None:
                continue
            try:
                _remove_path(cleanup_target)
            except OSError:
                pass
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        message = f"更新失败：{detail}"
        if rolled_back:
            message += "；旧版本已恢复"
        if rollback_error:
            message += f"；回滚失败：{rollback_error}"
        try:
            report("failed", message, rolledBack=rolled_back, rollbackError=rollback_error)
        except Exception:
            _append_log(log_path, f"[fatal] {message}\n{traceback.format_exc()}")
        return 1


def self_test() -> int:
    return 0 if PLAN_SCHEMA == 1 else 10


def main() -> None:
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    try:
        index = sys.argv.index("--plan")
        plan_path = Path(sys.argv[index + 1]).resolve()
    except (ValueError, IndexError):
        raise SystemExit(2)
    try:
        raise SystemExit(apply_update_plan(plan_path))
    except Exception:
        crash_log = plan_path.with_suffix(".crash.log")
        crash_log.write_text(traceback.format_exc(), encoding="utf-8")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
