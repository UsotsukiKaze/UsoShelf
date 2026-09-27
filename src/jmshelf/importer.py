from __future__ import annotations

from pathlib import Path

from .utils import find_images, is_image, natural_key, normalize_plate


def inspect_comic_folder(folder_path: str | Path) -> dict | None:
    folder = Path(folder_path).resolve()
    images = find_images(folder)
    if not images:
        return None
    numeric = folder.name.isdigit()
    return {
        "title": f"JM{folder.name}" if numeric else folder.name,
        "sourceId": normalize_plate(folder.name) if numeric else None,
        "rootPath": str(folder),
        "coverPath": str(images[0]),
        "pageCount": len(images),
        "sourceStatus": "pending" if numeric else "local",
    }


def scan_import_root(root_path: str | Path) -> list[dict]:
    root = Path(root_path).resolve()
    if not root.is_dir():
        raise ValueError("请选择有效的文件夹")
    entries = sorted(root.iterdir(), key=lambda item: natural_key(item.name))
    if any(is_image(entry) for entry in entries):
        inspected = inspect_comic_folder(root)
        return [inspected] if inspected else []
    return [item for entry in entries if entry.is_dir() if (item := inspect_comic_folder(entry))]
