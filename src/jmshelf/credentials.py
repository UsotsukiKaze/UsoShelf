from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
from typing import Any

from ctypes import wintypes


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class WindowsCredentialStore:
    """Stores JM account data as readable local JSON, with one-time DPAPI migration."""

    def __init__(self, path: str | Path, legacy_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.legacy_path = Path(legacy_path) if legacy_path else None

    @staticmethod
    def _protect(raw: bytes) -> bytes:
        if os.name != "nt":
            raise RuntimeError("账号凭据存储仅支持 Windows")
        buffer = ctypes.create_string_buffer(raw)
        source = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        target = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if not crypt32.CryptProtectData(
            ctypes.byref(source),
            "JmShelf JM session",
            None,
            None,
            None,
            0x01,
            ctypes.byref(target),
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            kernel32.LocalFree(target.pbData)

    @staticmethod
    def _unprotect(protected: bytes) -> bytes:
        if os.name != "nt":
            raise RuntimeError("账号凭据存储仅支持 Windows")
        buffer = ctypes.create_string_buffer(protected)
        source = _DataBlob(len(protected), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        target = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            0x01,
            ctypes.byref(target),
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            kernel32.LocalFree(target.pbData)

    def load(self) -> dict[str, Any] | None:
        if self.path.is_file():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                return payload if isinstance(payload, dict) else None
            except Exception:
                return None
        if self.legacy_path and self.legacy_path.is_file():
            try:
                protected = base64.b64decode(self.legacy_path.read_bytes(), validate=True)
                payload = json.loads(self._unprotect(protected).decode("utf-8"))
                if not isinstance(payload, dict):
                    return None
                self.save(payload)
                return payload
            except Exception:
                return None
        return None

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
        if self.legacy_path:
            self.legacy_path.unlink(missing_ok=True)
