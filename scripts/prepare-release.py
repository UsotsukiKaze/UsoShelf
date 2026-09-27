"""Validate and stage existing Windows artifacts without building or publishing."""
from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
import zipfile
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest().upper()


def prepare(root: Path) -> Path:
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    names = (f"JmShelf-Setup-{version}-x64.exe", f"JmShelf-windows-x64-{version}.zip")
    sources = [root / "dist" / name for name in names]
    notes = root / "release-notes" / f"{version}.txt"
    for source in [*sources, notes]:
        if not source.is_file() or source.stat().st_size == 0:
            raise ValueError(f"Missing release input: {source}")
    with sources[0].open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("Installer has no Windows executable header")
    with zipfile.ZipFile(sources[1]) as bundle:
        members = {name.replace("\\", "/") for name in bundle.namelist()}
        if not {"JmShelf/JmShelf.exe", "JmShelf/JmShelfUpdater.exe", "JmShelf/_internal/public/index.html"} <= members:
            raise ValueError("Incomplete portable application")
        for name in members:
            parts = name.casefold().split("/")
            if (
                parts[0] != "jmshelf" or ".." in parts
                or (len(parts) > 1 and parts[1] in {"data", "webview", ".venv"})
                or any(part in {".env", "option.yml", "option.yaml"} for part in parts)
                or name.casefold().endswith((".sqlite3", ".db", ".key"))
                or (name.casefold().endswith(".pem") and name != "JmShelf/_internal/certifi/cacert.pem")
            ):
                raise ValueError(f"Unexpected/private archive member: {name}")
        corrupt = bundle.testzip()
        if corrupt:
            raise ValueError(f"Corrupt ZIP entry: {corrupt}")
    output = root / "dist" / f"github-v{version}"
    output.mkdir(exist_ok=True)
    checksums = []
    packages = []
    for source in sources:
        sha = digest(source)
        target = output / source.name
        if target.exists() and digest(target) != sha:
            raise ValueError(f"Refusing to replace different release bytes: {target}")
        if not target.exists():
            shutil.copy2(source, target)
        checksums.append(f"{sha}  {source.name}")
        packages.append({"file": source.name, "size": source.stat().st_size, "sha256": sha})
    (output / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    (output / "RELEASE_NOTES.md").write_text(f"# JmShelf {version}\n\n" + notes.read_text(encoding="utf-8"), encoding="utf-8")
    print(json.dumps({"version": version, "directory": str(output), "packages": packages}, ensure_ascii=False, indent=2))
    return output


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parents[1])
