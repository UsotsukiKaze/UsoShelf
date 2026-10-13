"""Generate a DPAPI-protected update-signing key without publishing its private part."""
from __future__ import annotations

import base64
from pathlib import Path

from Crypto.PublicKey import ECC
from jmshelf.credentials import WindowsCredentialStore


def configure(root: Path) -> str:
    path = root / "data" / "release-signing.dat"
    if path.exists():
        key = ECC.import_key(WindowsCredentialStore._unprotect(path.read_bytes()))
    else:
        key = ECC.generate(curve="Ed25519")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(WindowsCredentialStore._protect(key.export_key(format="DER")))
    return base64.b64encode(key.public_key().export_key(format="raw")).decode("ascii")


if __name__ == "__main__":
    print(configure(Path(__file__).resolve().parents[1]))
