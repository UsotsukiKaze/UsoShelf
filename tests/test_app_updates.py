import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from jmshelf.app_updates import AppUpdateManager, version_key
from jmshelf.updater import apply_update_plan


def make_manifest(**overrides):
    manifest = {
        "version": "1.1.0",
        "releaseNotes": ["第一项", "第二项"],
        "portable": {
            "url": "https://apps.usotsuki-kaze.com/releases/jmshelf/1.1.0/jmshelf-1.1.0-portable.zip",
            "size": 1024,
            "sha256": "A" * 64,
        },
    }
    manifest.update(overrides)
    return manifest


def write_update_zip(path: Path, *, unsafe_name: str | None = None) -> None:
    with zipfile.ZipFile(path, "w") as bundle:
        if unsafe_name:
            bundle.writestr(unsafe_name, b"unsafe")
            return
        bundle.writestr("JmShelf/JmShelf.exe", b"new executable")
        bundle.writestr("JmShelf/JmShelfUpdater.exe", b"standalone updater")
        bundle.writestr("JmShelf/_internal/public/index.html", b"new interface")


def test_version_key_handles_semantic_and_prefixed_versions() -> None:
    assert version_key("v1.10.2") > version_key("1.9.9")
    assert version_key("1.0.0") == (1, 0, 0)
    assert version_key("preview") == (0,)


def test_manifest_validation_requires_same_https_zip_host(tmp_path: Path) -> None:
    manager = AppUpdateManager("1.0.0", tmp_path)
    validated = manager._validate_manifest(make_manifest())
    assert validated["version"] == "1.1.0"
    assert validated["portable"]["sha256"] == "A" * 64

    with pytest.raises(ValueError, match="同域名"):
        manager._validate_manifest(make_manifest(portable={
            "url": "https://downloads.example.com/JmShelf.zip",
            "size": 1024,
            "sha256": "A" * 64,
        }))

    with pytest.raises(ValueError, match="ZIP"):
        manager._validate_manifest(make_manifest(portable={
            "url": "https://apps.usotsuki-kaze.com/JmShelf.exe",
            "size": 1024,
            "sha256": "A" * 64,
        }))


def test_manifest_validation_rejects_invalid_hash_or_size(tmp_path: Path) -> None:
    manager = AppUpdateManager("1.0.0", tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        manager._validate_manifest(make_manifest(portable={
            "url": "https://apps.usotsuki-kaze.com/JmShelf.zip",
            "size": 1024,
            "sha256": "bad",
        }))
    with pytest.raises(ValueError, match="大小"):
        manager._validate_manifest(make_manifest(portable={
            "url": "https://apps.usotsuki-kaze.com/JmShelf.zip",
            "size": 0,
            "sha256": "B" * 64,
        }))


def test_portable_package_is_safely_extracted(tmp_path: Path) -> None:
    manager = AppUpdateManager("1.0.0", tmp_path / "updates")
    target_dir = manager.update_root / "1.1.0"
    target_dir.mkdir(parents=True)
    target_dir.joinpath("staged").mkdir()
    target_dir.joinpath("staged", "old.exe").write_bytes(b"stale package")
    archive = target_dir / "update.zip"
    write_update_zip(archive)

    staged = manager._extract_package(archive, target_dir)

    assert staged.parent == target_dir
    assert staged.name.startswith("staged.")
    assert staged.joinpath("JmShelf.exe").read_bytes() == b"new executable"
    assert staged.joinpath("_internal/public/index.html").is_file()


def test_portable_package_rejects_path_traversal(tmp_path: Path) -> None:
    manager = AppUpdateManager("1.0.0", tmp_path / "updates")
    target_dir = manager.update_root / "1.1.0"
    target_dir.mkdir(parents=True)
    archive = target_dir / "unsafe.zip"
    write_update_zip(archive, unsafe_name="JmShelf/../../outside.exe")

    with pytest.raises(ValueError, match="不安全路径"):
        manager._extract_package(archive, target_dir)
    assert not tmp_path.joinpath("outside.exe").exists()


def test_launch_update_starts_detached_file_replacer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = AppUpdateManager("1.0.0", tmp_path / "updates")
    staged = manager.update_root / "1.1.0" / "staged"
    staged.joinpath("_internal").mkdir(parents=True)
    staged.joinpath("JmShelf.exe").write_bytes(b"new")
    install = tmp_path / "Programs" / "JmShelf"
    install.mkdir(parents=True)
    install.joinpath("JmShelf.exe").write_bytes(b"old")
    install.joinpath("JmShelfUpdater.exe").write_bytes(b"updater")
    manager.status.update(state="ready", latestVersion="1.1.0", stagedPath=str(staged))
    calls: list[tuple[list[str], dict]] = []

    class RunningUpdater:
        def poll(self):
            return None

        def terminate(self):
            raise AssertionError("a responsive updater must not be terminated")

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        plan = json.loads(Path(command[command.index("--plan") + 1]).read_text(encoding="utf-8"))
        Path(plan["statusPath"]).write_text(json.dumps({
            "operationId": plan["operationId"],
            "state": "ready",
            "message": "ready",
        }), encoding="utf-8")
        return RunningUpdater()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    helper = manager.launch_update(install, "JmShelf.exe", 12345)

    assert helper.is_file()
    assert calls and Path(calls[0][0][0]).name.startswith("JmShelfUpdater.")
    assert "--plan" in calls[0][0]
    assert calls[0][1]["close_fds"] is True
    assert manager.snapshot()["state"] == "applying"
    assert helper.name.startswith("JmShelfUpdater.")


def test_launch_update_keeps_app_running_when_helper_exits_early(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = AppUpdateManager("1.0.0", tmp_path / "updates")
    staged = manager.update_root / "1.1.0" / "staged"
    staged.joinpath("_internal").mkdir(parents=True)
    staged.joinpath("JmShelf.exe").write_bytes(b"new")
    install = tmp_path / "Programs" / "JmShelf"
    install.mkdir(parents=True)
    install.joinpath("JmShelf.exe").write_bytes(b"old")
    install.joinpath("JmShelfUpdater.exe").write_bytes(b"updater")
    manager.status.update(state="ready", latestVersion="1.1.0", stagedPath=str(staged))

    class FailedUpdater:
        def poll(self):
            return 5

        def terminate(self):
            raise AssertionError("an exited updater must not be terminated")

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: FailedUpdater())

    with pytest.raises(ValueError, match="独立更新器启动失败"):
        manager.launch_update(install, "JmShelf.exe", 12345)

    assert manager.snapshot()["state"] == "ready"


def write_apply_plan(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "staged"
    source.joinpath("_internal", "public").mkdir(parents=True)
    source.joinpath("JmShelf.exe").write_bytes(b"new executable")
    source.joinpath("JmShelfUpdater.exe").write_bytes(b"new updater")
    source.joinpath("_internal", "public", "index.html").write_bytes(b"new interface")
    target = tmp_path / "installed"
    target.joinpath("_internal", "public").mkdir(parents=True)
    target.joinpath("JmShelf.exe").write_bytes(b"old executable")
    target.joinpath("JmShelfUpdater.exe").write_bytes(b"old updater")
    target.joinpath("_internal", "public", "index.html").write_bytes(b"old interface")
    target.joinpath("unins000.exe").write_bytes(b"keep installer metadata")
    plan_path = tmp_path / "apply-plan.json"
    plan_path.write_text(json.dumps({
        "schemaVersion": 1,
        "operationId": "test-operation",
        "version": "1.1.4",
        "processId": 123,
        "sourceRoot": str(source),
        "targetRoot": str(target),
        "executableName": "JmShelf.exe",
        "statusPath": str(tmp_path / "apply-status.json"),
        "lastStatusPath": str(tmp_path / "last-apply.json"),
        "logPath": str(tmp_path / "apply-update.log"),
    }), encoding="utf-8")
    return plan_path, source, target


def test_standalone_updater_replaces_files_and_preserves_installer_metadata(tmp_path: Path) -> None:
    plan_path, source, target = write_apply_plan(tmp_path)

    class RunningApplication:
        returncode = None

        def poll(self):
            return None

    result = apply_update_plan(
        plan_path,
        wait_for_exit=lambda process_id, timeout: None,
        launch_application=lambda executable, working_directory, arguments: RunningApplication(),
        startup_grace_seconds=0,
    )

    assert result == 0
    assert target.joinpath("JmShelf.exe").read_bytes() == b"new executable"
    assert target.joinpath("_internal/public/index.html").read_bytes() == b"new interface"
    assert target.joinpath("unins000.exe").read_bytes() == b"keep installer metadata"
    assert not source.exists()
    assert json.loads(tmp_path.joinpath("last-apply.json").read_text(encoding="utf-8"))["state"] == "completed"


def test_standalone_updater_rolls_back_when_new_app_does_not_start(tmp_path: Path) -> None:
    plan_path, _source, target = write_apply_plan(tmp_path)
    launches = 0

    class Application:
        def __init__(self, returncode):
            self.returncode = returncode

        def poll(self):
            return self.returncode

    def launch(_executable, _working_directory, _arguments):
        nonlocal launches
        launches += 1
        return Application(9 if launches == 1 else None)

    result = apply_update_plan(
        plan_path,
        wait_for_exit=lambda process_id, timeout: None,
        launch_application=launch,
        startup_grace_seconds=0,
    )

    status = json.loads(tmp_path.joinpath("last-apply.json").read_text(encoding="utf-8"))
    assert result == 1
    assert status["state"] == "failed"
    assert status["rolledBack"] is True
    assert target.joinpath("JmShelf.exe").read_bytes() == b"old executable"
    assert target.joinpath("_internal/public/index.html").read_bytes() == b"old interface"
    assert launches == 2


def test_standalone_updater_waits_for_real_app_health_before_committing(tmp_path: Path) -> None:
    plan_path, source, target = write_apply_plan(tmp_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    health_path = tmp_path / "health.json"
    plan["healthPath"] = str(health_path)
    plan["launchArguments"] = ["--update-operation", plan["operationId"], "--update-health", str(health_path)]
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    class RunningApplication:
        returncode = None

        def poll(self):
            return None

    def launch(_executable, _working_directory, arguments):
        path = Path(arguments[arguments.index("--update-health") + 1])
        path.write_text(json.dumps({
            "operationId": "test-operation",
            "state": "ready",
        }), encoding="utf-8")
        return RunningApplication()

    result = apply_update_plan(
        plan_path,
        wait_for_exit=lambda _process_id, _timeout: None,
        launch_application=launch,
        startup_health_timeout=1,
    )

    assert result == 0
    assert not source.exists()
    assert target.joinpath("JmShelf.exe").read_bytes() == b"new executable"


def test_standalone_updater_rolls_back_when_health_handshake_times_out(tmp_path: Path) -> None:
    plan_path, _source, target = write_apply_plan(tmp_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["healthPath"] = str(tmp_path / "health.json")
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    launches = 0
    terminated = False

    class Application:
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            nonlocal terminated
            terminated = True

        def wait(self, timeout=None):
            return 0

    def launch(*_args):
        nonlocal launches
        launches += 1
        return Application()

    result = apply_update_plan(
        plan_path,
        wait_for_exit=lambda _process_id, _timeout: None,
        launch_application=launch,
        startup_health_timeout=0,
    )

    assert result == 1
    assert terminated is True
    assert launches == 2
    assert target.joinpath("JmShelf.exe").read_bytes() == b"old executable"
