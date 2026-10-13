from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any


UI_KEYS = {
    "jmshelf-theme", "jmshelf-density", "jmshelf-library-filter", "jmshelf-library-sort",
    "jmshelf-online-search-history", "jmshelf-online-recommend-sort", "jmshelf-online-recommend-excluded-tags",
    "jmshelf-reader-mode", "jmshelf-reader-auto-mode", "jmshelf-reader-auto-pace", "jmshelf-reader-book-left-forward",
}


class UiPreferences:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.values = self.clean(payload.get("values", {}))
        except (OSError, ValueError, TypeError, AttributeError):
            self.values = {}

    @staticmethod
    def clean(values: Any) -> dict[str, str]:
        if not isinstance(values, dict):
            raise ValueError("偏好格式无效")
        return {key: value for key, value in values.items()
                if isinstance(key, str) and (key in UI_KEYS or key.startswith("jmshelf-update-result-"))
                and isinstance(value, str) and len(value) <= 16384}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"values": dict(self.values), "persisted": self.path.is_file()}

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            updated = {**self.values, **self.clean(values)}
            for key, value in values.items():
                if value is None:
                    updated.pop(key, None)
            result_keys = [key for key in updated if key.startswith("jmshelf-update-result-")]
            for key in result_keys[:-64]:
                updated.pop(key, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps({"schemaVersion": 1, "values": updated}, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self.values = updated
            return self.snapshot()
