from pathlib import Path

from jmshelf.credentials import WindowsCredentialStore


def test_windows_credential_store_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "account.json"
    store = WindowsCredentialStore(path)
    payload = {"username": "test-user", "cookies": {"AVS": "session-value"}}

    store.save(payload)

    assert store.load() == payload
    assert "session-value" in path.read_text(encoding="utf-8")
    store.clear()
    assert store.load() is None
