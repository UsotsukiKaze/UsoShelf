"""Independent release signatures; only the public key ships with the app."""
from __future__ import annotations

import base64
import json
from typing import Any

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa


# Configured by scripts/configure-release-signing.py; never trust a key from a manifest.
RELEASE_PUBLIC_KEY = "raiun+zbj9CdsJ7nQ+lef5oiA1XwKE8M/+oUGeHcyZI="


def canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign_release(payload: dict[str, Any], private_key: bytes) -> dict[str, Any]:
    key = ECC.import_key(private_key)
    signature = eddsa.new(key, "rfc8032").sign(canonical_payload(payload))
    return {"payload": payload, "signature": base64.b64encode(signature).decode("ascii")}


def verify_release(value: Any, version: str, package: dict[str, Any], *, public_key: str | None = None) -> None:
    trusted = RELEASE_PUBLIC_KEY if public_key is None else public_key
    try:
        if not trusted or not isinstance(value, dict):
            raise ValueError("missing trusted signature")
        payload = value["payload"]
        key = eddsa.import_public_key(base64.b64decode(trusted, validate=True))
        eddsa.new(key, "rfc8032").verify(canonical_payload(payload), base64.b64decode(value["signature"], validate=True))
        if payload.get("schemaVersion") != 1 or payload.get("version") != version:
            raise ValueError("release mismatch")
        matching = next(item for item in payload["packages"] if item["file"] == f"JmShelf-windows-x64-{version}.zip")
        if int(matching["size"]) != int(package["size"]) or str(matching["sha256"]).upper() != str(package["sha256"]).upper():
            raise ValueError("package mismatch")
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise ValueError("发行签名验证失败，已停止更新") from exc
