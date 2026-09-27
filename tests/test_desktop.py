import sqlite3
from pathlib import Path

import pytest

import jmshelf.desktop as desktop
from jmshelf.desktop import DesktopApi


def test_desktop_api_keeps_native_objects_out_of_public_bridge_state() -> None:
    api = DesktopApi()

    assert not [name for name in vars(api) if not name.startswith("_")]


def test_desktop_api_tracks_window_controls_without_public_state() -> None:
    api = DesktopApi()
    actions: list[tuple[str, object]] = []
    api._find_window_handle = lambda: 42
    api._current_bounds = lambda _handle: (100, 80, 1360, 860)
    api._working_area = lambda _handle: (0, 0, 1920, 1040)
    api._animate_bounds = lambda _handle, bounds: actions.append(("bounds", bounds))
    api._minimize = lambda _handle: actions.append(("minimize", None))
    api._begin_drag = lambda _handle: actions.append(("drag", None))
    api._invoke_on_ui_thread = lambda callback: callback()

    assert api.window_action("toggle-maximize") == {"ok": True, "maximized": True}
    assert api.window_action("toggle-maximize") == {"ok": True, "maximized": False}
    assert api.window_action("minimize") == {"ok": True, "maximized": False}
    assert api.begin_drag() == {"ok": True}
    assert actions == [
        ("bounds", (0, 0, 1920, 1040)),
        ("bounds", (100, 80, 1360, 860)),
        ("minimize", None),
        ("drag", None),
    ]


def test_desktop_api_rejects_unknown_resize_edge() -> None:
    api = DesktopApi()
    try:
        api.begin_resize("diagonal", 0, 0)
    except ValueError as error:
        assert str(error) == "未知窗口缩放方向"
    else:
        raise AssertionError("unknown resize edge was accepted")


def test_desktop_api_resizes_from_transparent_edge() -> None:
    api = DesktopApi()
    actions: list[tuple[int, int, int, int]] = []
    api._find_window_handle = lambda: 42
    api._current_bounds = lambda _handle: (100, 80, 1200, 800)
    api._set_bounds = lambda _handle, bounds: actions.append(bounds)

    assert api.begin_resize("bottom-right", 200, 150, 1) == {"ok": True}
    assert api.resize_window(260, 190) == {"ok": True}
    assert api.end_resize() == {"ok": True}
    assert actions == [(100, 80, 1260, 840)]


def test_development_data_migration_leaves_existing_target_untouched_on_copy_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    source = project / "data"
    source.mkdir(parents=True)
    project.joinpath("pyproject.toml").write_text("[project]", encoding="utf-8")
    connection = sqlite3.connect(source / "library.sqlite3")
    connection.execute("CREATE TABLE comics (cover_path TEXT, root_path TEXT)")
    connection.commit()
    connection.close()
    source.joinpath("copy-me.txt").write_text("source", encoding="utf-8")
    executable = project / "build" / "release" / "JmShelf.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"exe")
    target = tmp_path / "local" / "data"
    target.mkdir(parents=True)
    target.joinpath("keep.txt").write_text("keep", encoding="utf-8")

    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", str(executable))
    monkeypatch.setattr(desktop, "default_data_root", lambda: target)
    monkeypatch.setattr(desktop.shutil, "copy2", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy failed")))

    with pytest.raises(OSError, match="copy failed"):
        desktop._migrate_development_data()

    assert target.joinpath("keep.txt").read_text(encoding="utf-8") == "keep"
    assert not target.joinpath("library.sqlite3").exists()
    assert not list(target.parent.glob(".data.migration-*"))
