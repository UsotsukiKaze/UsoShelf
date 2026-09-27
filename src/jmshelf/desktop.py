from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn

from .main import PUBLIC_ROOT, create_app, default_data_root


APP_TITLE = "JmShelf"
_OUTPUT_SINKS: list[Any] = []
_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_WS_SYSMENU = 0x00080000
_WS_THICKFRAME = 0x00040000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _Rect),
        ("rcWork", _Rect),
        ("dwFlags", wintypes.DWORD),
    ]


def _desktop_window_api():
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_Rect)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindowAsync.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.ReleaseCapture.argtypes = []
    user32.ReleaseCapture.restype = wintypes.BOOL
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
    get_window_long.restype = ctypes.c_ssize_t
    set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_window_long.restype = ctypes.c_ssize_t
    return user32


def _configure_native_window() -> None:
    """Restore Windows shell semantics without bringing back a title bar."""
    user32 = _desktop_window_api()
    handle = user32.FindWindowW(None, APP_TITLE)
    if not handle:
        raise RuntimeError("未找到 JmShelf 窗口")

    get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    style = int(get_window_long(handle, _GWL_STYLE))
    shell_style = _WS_SYSMENU | _WS_MINIMIZEBOX
    desired_style = (style | shell_style) & ~(_WS_THICKFRAME | _WS_MAXIMIZEBOX)
    if desired_style != style:
        set_window_long(handle, _GWL_STYLE, desired_style)

    exstyle = int(get_window_long(handle, _GWL_EXSTYLE))
    desired_exstyle = (exstyle | _WS_EX_APPWINDOW) & ~_WS_EX_TOOLWINDOW
    if desired_exstyle != exstyle:
        set_window_long(handle, _GWL_EXSTYLE, desired_exstyle)

    # Apply the style update without changing position, size, z-order or focus.
    flags = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
    if not user32.SetWindowPos(handle, None, 0, 0, 0, 0, flags):
        raise ctypes.WinError()


def _ensure_output_streams() -> None:
    """Give console-oriented dependencies a harmless stream in windowed builds."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            sink = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, name, sink)
            _OUTPUT_SINKS.append(sink)


class SingleInstance:
    ERROR_ALREADY_EXISTS = 183

    def __init__(self) -> None:
        self.handle = None

    def acquire(self) -> bool:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        # v2 leaves behind the obsolete mutex namespace used by early preview
        # builds, some of which could remain alive without a WebView window.
        self.handle = kernel32.CreateMutexW(None, False, "Local\\JmShelf.Desktop.Singleton.v2")
        return bool(self.handle) and kernel32.GetLastError() != self.ERROR_ALREADY_EXISTS

    def activate_existing(self, timeout: float = 2.0) -> bool:
        user32 = ctypes.windll.user32
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            handle = user32.FindWindowW(None, APP_TITLE)
            if handle:
                user32.ShowWindowAsync(handle, 9)
                user32.SetForegroundWindow(handle)
                return True
            time.sleep(0.08)
        return False

    def close(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


class DesktopApi:
    def __init__(self) -> None:
        # pywebview recursively inspects every public js_api attribute. Native
        # Window/CLR objects must remain private or bridge generation can walk
        # the complete WinForms object graph and block the GUI message pump.
        self._maximized = False
        self._restore_bounds: tuple[int, int, int, int] | None = None
        self._animation_lock = threading.Lock()
        self._resize_state: tuple[str, float, float, float, tuple[int, int, int, int]] | None = None
        self._window: Any | None = None

    @staticmethod
    def _find_window_handle() -> int:
        handle = _desktop_window_api().FindWindowW(None, APP_TITLE)
        if not handle:
            raise RuntimeError("未找到 JmShelf 窗口")
        return int(handle)

    @staticmethod
    def _current_bounds(handle: int) -> tuple[int, int, int, int]:
        rect = _Rect()
        if not _desktop_window_api().GetWindowRect(handle, ctypes.byref(rect)):
            raise ctypes.WinError()
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

    @staticmethod
    def _working_area(handle: int) -> tuple[int, int, int, int]:
        user32 = _desktop_window_api()
        monitor = user32.MonitorFromWindow(handle, 2)
        if not monitor:
            raise ctypes.WinError()
        info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise ctypes.WinError()
        work = info.rcWork
        return work.left, work.top, work.right - work.left, work.bottom - work.top

    @staticmethod
    def _set_bounds(handle: int, bounds: tuple[int, int, int, int]) -> None:
        x, y, width, height = bounds
        # Keep z-order/focus intact while sizing the frameless window to the
        # monitor work area. WinForms' native maximize ignores the taskbar for
        # borderless forms, so it is deliberately not used here.
        if not _desktop_window_api().SetWindowPos(handle, None, x, y, width, height, 0x0004):
            raise ctypes.WinError()

    def _animate_bounds(self, handle: int, target: tuple[int, int, int, int]) -> None:
        start = self._current_bounds(handle)
        if start == target:
            return
        duration = 0.19
        frames = 12
        frame_started = time.monotonic()
        for index in range(1, frames + 1):
            progress = index / frames
            eased = 1 - (1 - progress) ** 3
            bounds = tuple(round(origin + (destination - origin) * eased) for origin, destination in zip(start, target))
            self._set_bounds(handle, bounds)
            if index < frames:
                frame_started += duration / frames
                time.sleep(max(0.0, frame_started - time.monotonic()))

    @staticmethod
    def _minimize(handle: int) -> None:
        _desktop_window_api().ShowWindowAsync(handle, 6)

    @staticmethod
    def _close(handle: int) -> None:
        _desktop_window_api().PostMessageW(handle, 0x0010, 0, 0)

    @staticmethod
    def _begin_drag(handle: int) -> None:
        user32 = _desktop_window_api()
        user32.ReleaseCapture()
        user32.SendMessageW(handle, 0x00A1, 2, 0)  # WM_NCLBUTTONDOWN, HTCAPTION

    def _invoke_on_ui_thread(self, callback: Any) -> None:
        native = getattr(self._window, "native", None)
        if native is None:
            raise RuntimeError("JmShelf 原生窗口尚未就绪")
        from System import Action

        native.BeginInvoke(Action(callback))

    def begin_drag(self) -> dict[str, bool]:
        handle = self._find_window_handle()
        self._invoke_on_ui_thread(lambda: self._begin_drag(handle))
        return {"ok": True}

    def begin_resize(self, edge: str, screen_x: float, screen_y: float, scale: float = 1.0) -> dict[str, bool]:
        valid_edges = {
            "left", "right", "top", "top-left",
            "top-right", "bottom", "bottom-left", "bottom-right",
        }
        if edge not in valid_edges:
            raise ValueError("未知窗口缩放方向")
        handle = self._find_window_handle()
        self._resize_state = (edge, float(screen_x), float(screen_y), max(0.5, float(scale)), self._current_bounds(handle))
        return {"ok": True}

    def resize_window(self, screen_x: float, screen_y: float) -> dict[str, bool]:
        if not self._resize_state:
            return {"ok": False}
        edge, start_x, start_y, scale, start = self._resize_state
        delta_x = round((float(screen_x) - start_x) * scale)
        delta_y = round((float(screen_y) - start_y) * scale)
        x, y, width, height = start
        minimum_width = round(920 * scale)
        minimum_height = round(620 * scale)

        if "left" in edge:
            new_width = max(minimum_width, width - delta_x)
            x += width - new_width
            width = new_width
        elif "right" in edge:
            width = max(minimum_width, width + delta_x)
        if "top" in edge:
            new_height = max(minimum_height, height - delta_y)
            y += height - new_height
            height = new_height
        elif "bottom" in edge:
            height = max(minimum_height, height + delta_y)

        with self._animation_lock:
            self._set_bounds(self._find_window_handle(), (x, y, width, height))
        return {"ok": True}

    def end_resize(self) -> dict[str, bool]:
        self._resize_state = None
        return {"ok": True}

    def window_action(self, action: str) -> dict[str, bool]:
        handle = self._find_window_handle()
        with self._animation_lock:
            if action == "minimize":
                self._minimize(handle)
            elif action == "toggle-maximize":
                if self._maximized:
                    if self._restore_bounds:
                        self._animate_bounds(handle, self._restore_bounds)
                    self._maximized = False
                else:
                    self._restore_bounds = self._current_bounds(handle)
                    self._animate_bounds(handle, self._working_area(handle))
                    self._maximized = True
            elif action == "close":
                self._close(handle)
            else:
                raise ValueError("未知窗口操作")
        return {"ok": True, "maximized": self._maximized}

    def window_state(self) -> dict[str, bool]:
        return {"ok": True, "maximized": self._maximized}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_server(port: int, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(0.25)
            if client.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.08)
    raise RuntimeError("JmShelf 本地服务启动超时")


def _show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, "JmShelf 启动失败", 0x10)


def _write_update_health_marker(arguments: list[str] | None = None) -> None:
    values = arguments if arguments is not None else sys.argv
    try:
        operation_id = values[values.index("--update-operation") + 1]
        health_path = Path(values[values.index("--update-health") + 1]).resolve()
    except (ValueError, IndexError):
        return
    temporary = health_path.with_suffix(health_path.suffix + f".{os.getpid()}.tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps({
        "operationId": operation_id,
        "state": "ready",
        "processId": os.getpid(),
        "readyAt": datetime.now(UTC).isoformat(),
    }, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, health_path)


def _migrate_development_data() -> None:
    if not getattr(sys, "frozen", False) or os.environ.get("JMSHELF_DATA_DIR"):
        return
    target = default_data_root()
    if target.joinpath("library.sqlite3").exists():
        return
    executable = Path(sys.executable).resolve()
    if len(executable.parents) < 3:
        return
    project_root = executable.parents[2]
    source = project_root / "data"
    if not project_root.joinpath("pyproject.toml").is_file() or not source.joinpath("library.sqlite3").is_file():
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    migration_id = uuid.uuid4().hex
    staging = target.parent / f".{target.name}.migration-{migration_id}"
    backup = target.parent / f".{target.name}.pre-migration-{migration_id}"
    target_moved = False
    try:
        if target.exists():
            shutil.copytree(target, staging)
        else:
            staging.mkdir()
        for item in source.iterdir():
            if item.name in {"library.sqlite3", "library.sqlite3-shm", "library.sqlite3-wal"}:
                continue
            destination = staging / item.name
            if item.is_dir():
                shutil.copytree(item, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(item, destination)

        source_db = sqlite3.connect(str(source / "library.sqlite3"))
        target_db = sqlite3.connect(str(staging / "library.sqlite3"))
        try:
            source_db.backup(target_db)
            old_prefix = str(source.resolve())
            new_prefix = str(target.resolve())
            with target_db:
                target_db.execute(
                    "UPDATE comics SET cover_path = replace(cover_path, ?, ?) WHERE cover_path LIKE ?",
                    (old_prefix, new_prefix, f"{old_prefix}%"),
                )
                target_db.execute(
                    "UPDATE comics SET root_path = replace(root_path, ?, ?) WHERE root_path LIKE ?",
                    (old_prefix, new_prefix, f"{old_prefix}%"),
                )
            integrity = target_db.execute("PRAGMA quick_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError("开发数据迁移后的数据库校验失败")
        finally:
            target_db.close()
            source_db.close()

        if target.exists():
            target.replace(backup)
            target_moved = True
        staging.replace(target)
    except Exception:
        if target_moved and backup.exists() and not target.exists():
            backup.replace(target)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and target.exists():
            shutil.rmtree(backup, ignore_errors=True)


def _write_crash_log(exc: BaseException) -> Path:
    root = default_data_root().parent
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "crash.log"
    log_path.write_text(
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        encoding="utf-8",
    )
    return log_path


def run_desktop() -> int:
    if os.name != "nt":
        raise RuntimeError("JmShelf 桌面应用仅支持 Windows")
    _ensure_output_streams()
    instance = SingleInstance()
    if not instance.acquire():
        if instance.activate_existing():
            return 0
        # A previous background process can outlive its WebView and retain the
        # mutex. With no matching top-level window there is nothing to
        # activate, so recover by starting a fresh UI instead of silently
        # exiting forever.

    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    try:
        os.environ["JMSHELF_DESKTOP"] = "1"
        _migrate_development_data()
        port = _free_port()
        application = create_app()
        config = uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            log_config=None,
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None
        server_thread = threading.Thread(target=server.run, name="jmshelf-server", daemon=True)
        server_thread.start()
        _wait_for_server(port)

        import webview

        api = DesktopApi()
        window = webview.create_window(
            APP_TITLE,
            f"http://127.0.0.1:{port}/?desktop=1",
            js_api=api,
            width=1360,
            height=860,
            min_size=(920, 620),
            resizable=True,
            frameless=True,
            easy_drag=False,
            shadow=True,
            maximized=False,
            background_color="#0e0f12",
            text_select=True,
            draggable=True,
        )
        api._window = window
        def on_window_shown() -> None:
            _configure_native_window()
            _write_update_health_marker()

        window.events.shown += on_window_shown
        storage_path = default_data_root().parent / "webview"
        storage_path.mkdir(parents=True, exist_ok=True)
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=False,
            storage_path=str(storage_path),
        )
        return 0
    finally:
        if server:
            server.should_exit = True
        if server_thread:
            server_thread.join(timeout=5)
        instance.close()


def self_test() -> int:
    if not PUBLIC_ROOT.joinpath("index.html").is_file():
        return 10
    test_root = default_data_root().parent / "self-test"
    app = create_app(test_root)
    if app.title != "JmShelf":
        return 11
    app.state.database.close()
    return 0


def main() -> None:
    try:
        exit_code = self_test() if "--self-test" in sys.argv else run_desktop()
    except Exception as exc:
        log_path = _write_crash_log(exc)
        _show_error(f"JmShelf 无法启动。\n\n错误日志：{log_path}\n\n{exc}")
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
