from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
_NATURAL_PARTS = re.compile(r"(\d+)")
_PLATE = re.compile(r"(?:JM\s*)?(\d{3,})", re.IGNORECASE)


def natural_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part.casefold() for part in _NATURAL_PARTS.split(value)]


def normalize_plate(value: Any) -> str | None:
    match = _PLATE.search(str(value or ""))
    return match.group(1) if match else None


def normalize_chapter_index(value: Any, fallback: int | float = 1) -> int | float:
    """Keep fractional JM chapter numbers JSON/SQLite friendly."""
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return fallback
    if not number.is_finite():
        return fallback
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS


def find_images(root: Path, max_depth: int = 4) -> list[Path]:
    root = root.resolve()
    found: list[Path] = []

    def walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda item: natural_key(item.name))
        except (OSError, PermissionError):
            return
        for entry in entries:
            if entry.is_dir():
                walk(entry, depth + 1)
            elif is_image(entry):
                found.append(entry.resolve())

    walk(root, 0)
    return found


def json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def path_is_inside(parent: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
