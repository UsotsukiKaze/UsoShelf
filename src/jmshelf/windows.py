from __future__ import annotations

import base64
import ctypes
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path


_tray_lock = threading.Lock()
_tray_icon = None
_tray_window_handle: int | None = None


def _window_api():
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindowAsync.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    return user32


def _powershell(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-EncodedCommand", encoded,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stdout = completed.stdout.decode("utf-8", errors="replace").strip()
    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    if completed.returncode:
        raise RuntimeError(stderr or f"PowerShell exited with code {completed.returncode}")
    return stdout


def pick_folder() -> str | None:
    script = r"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select a comic folder or a directory containing comics'
$dialog.ShowNewFolderButton = $false
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Write($dialog.SelectedPath)
}
"""
    selected = _powershell(script)
    return selected or None


def pick_image_file() -> str | None:
    script = r"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Select a cover image'
$dialog.Filter = 'Image files|*.jpg;*.jpeg;*.png;*.webp;*.bmp;*.gif;*.avif|All files|*.*'
$dialog.Multiselect = $false
$dialog.CheckFileExists = $true
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Write($dialog.FileName)
}
"""
    selected = _powershell(script)
    return selected or None


def minimize_foreground_window(delay: float = 0.18) -> None:
    time.sleep(delay)
    user32 = _window_api()
    handle = user32.GetForegroundWindow()
    if handle:
        user32.ShowWindowAsync(handle, 6)


def _restore_tray_window(icon=None, _item=None) -> None:
    global _tray_icon, _tray_window_handle
    with _tray_lock:
        handle = _tray_window_handle
        active_icon = _tray_icon
        _tray_icon = None
        _tray_window_handle = None
    if handle:
        user32 = _window_api()
        user32.ShowWindowAsync(handle, 9)
        user32.SetForegroundWindow(handle)
    if active_icon:
        active_icon.stop()
    elif icon:
        icon.stop()


def _start_tray_icon(handle: int, icon_path: Path) -> None:
    global _tray_icon, _tray_window_handle
    from PIL import Image
    import pystray

    with _tray_lock:
        if _tray_icon is not None:
            _tray_window_handle = handle
            return
        image = Image.open(icon_path).convert("RGBA")
        menu = pystray.Menu(
            pystray.MenuItem("显示 JmShelf", _restore_tray_window, default=True),
        )
        tray = pystray.Icon("JmShelf", image, "JmShelf", menu)
        _tray_icon = tray
        _tray_window_handle = handle
        threading.Thread(target=tray.run, name="jmshelf-tray", daemon=True).start()


def hide_foreground_to_tray(icon_path: Path, delay: float = 0.18) -> None:
    time.sleep(delay)
    user32 = _window_api()
    handle = user32.GetForegroundWindow()
    if not handle:
        return
    _start_tray_icon(handle, icon_path)
    user32.ShowWindowAsync(handle, 0)


def stop_tray_icon() -> None:
    global _tray_icon, _tray_window_handle
    with _tray_lock:
        tray = _tray_icon
        _tray_icon = None
        _tray_window_handle = None
    if tray:
        tray.stop()


def reveal_in_explorer(path: Path) -> None:
    target = path.expanduser().resolve()
    if not target.exists():
        raise ValueError("本地路径不存在")
    subprocess.Popen(
        ["explorer.exe", f"/select,{target}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )
