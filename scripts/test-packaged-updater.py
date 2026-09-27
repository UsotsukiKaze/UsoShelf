from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--updater", type=Path, required=True)
    parser.add_argument("--target-base", type=Path)
    args = parser.parse_args()
    updater = args.updater.resolve()
    owns_target_base = args.target_base is None
    target_base = (
        Path(tempfile.mkdtemp(prefix="jmshelf-updater-target-"))
        if owns_target_base
        else args.target_base.resolve()
    )
    operation_id = uuid.uuid4().hex
    target = target_base / operation_id / "installed"
    status: Path | None = None
    probe_name = f"UpdaterProbe{operation_id[:8]}.exe"
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    ping = system_root / "System32" / "ping.exe"
    old_binary = system_root / "System32" / "where.exe"
    if not updater.is_file() or not ping.is_file() or not old_binary.is_file():
        raise FileNotFoundError("更新器端到端测试缺少必要文件")

    source_parent = Path(tempfile.mkdtemp(prefix="jmshelf-updater-source-"))
    source = source_parent / "staged"
    try:
        source.joinpath("_internal").mkdir(parents=True)
        target.joinpath("_internal").mkdir(parents=True)
        shutil.copy2(ping, source / probe_name)
        shutil.copy2(old_binary, target / probe_name)
        source.joinpath("_internal", "version.txt").write_text("new", encoding="utf-8")
        target.joinpath("_internal", "version.txt").write_text("old", encoding="utf-8")
        target.joinpath("unins000.dat").write_text("preserve", encoding="utf-8")
        plan = source_parent / "apply-plan.json"
        status = source_parent / "apply-status.json"
        last_status = source_parent / "last-apply.json"
        log = source_parent / "apply-update.log"
        plan.write_text(json.dumps({
            "schemaVersion": 1,
            "operationId": operation_id,
            "version": "e2e",
            "processId": 2_147_483_647,
            "sourceRoot": str(source),
            "targetRoot": str(target),
            "executableName": probe_name,
            "launchArguments": ["127.0.0.1", "-n", "12"],
            "statusPath": str(status),
            "lastStatusPath": str(last_status),
            "logPath": str(log),
        }, ensure_ascii=False), encoding="utf-8")
        completed = subprocess.run(
            [str(updater), "--plan", str(plan)],
            check=False,
            timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        result = json.loads(status.read_text(encoding="utf-8")) if status.is_file() else {}
        if completed.returncode != 0 or result.get("state") != "completed":
            detail = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else result
            raise RuntimeError(f"独立更新器端到端测试失败：{detail}")
        if digest(target / probe_name) != digest(ping):
            raise RuntimeError("独立更新器没有替换目标程序")
        if target.joinpath("_internal", "version.txt").read_text(encoding="utf-8") != "new":
            raise RuntimeError("独立更新器没有替换运行时目录")
        if target.joinpath("unins000.dat").read_text(encoding="utf-8") != "preserve":
            raise RuntimeError("独立更新器误删了安装器元数据")
        print("Packaged updater end-to-end test passed.")
    finally:
        subprocess.run(
            ["taskkill.exe", "/F", "/IM", probe_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        shutil.rmtree(source_parent, ignore_errors=True)
        shutil.rmtree(target_base / operation_id, ignore_errors=True)
        if owns_target_base:
            shutil.rmtree(target_base, ignore_errors=True)


if __name__ == "__main__":
    main()
