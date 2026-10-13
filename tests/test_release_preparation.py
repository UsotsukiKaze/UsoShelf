import importlib.util
from pathlib import Path
import zipfile
import base64
from Crypto.PublicKey import ECC

import pytest

spec = importlib.util.spec_from_file_location("prepare_release", Path(__file__).parents[1] / "scripts" / "prepare-release.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
TEST_KEY = ECC.generate(curve="Ed25519")


def prepare(root):
    return module.prepare(root, private_key=TEST_KEY.export_key(format="DER"),
                          public_key=base64.b64encode(TEST_KEY.public_key().export_key(format="raw")).decode())


def inputs(tmp_path, extra=None):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="1.3.4"\n', encoding="utf-8")
    (tmp_path / "release-notes").mkdir()
    (tmp_path / "release-notes" / "1.3.4.txt").write_text("更新说明", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "JmShelf-Setup-1.3.4-x64.exe").write_bytes(b"MZtest")
    with zipfile.ZipFile(tmp_path / "dist" / "JmShelf-windows-x64-1.3.4.zip", "w") as bundle:
        for name in ("JmShelf/JmShelf.exe", "JmShelf/JmShelfUpdater.exe", "JmShelf/_internal/public/index.html", "JmShelf/_internal/certifi/cacert.pem"):
            bundle.writestr(name, b"test")
        if extra:
            bundle.writestr(extra, b"private")


def test_prepare_artifacts_is_repeatable(tmp_path):
    inputs(tmp_path)
    output = prepare(tmp_path)
    assert prepare(tmp_path) == output
    assert (output / "RELEASE_SIGNATURE.json").is_file()
    assert len((output / "SHA256SUMS.txt").read_text().splitlines()) == 2
    assert module.digest(output / "JmShelf-Setup-1.3.4-x64.exe") == module.digest(tmp_path / "dist" / "JmShelf-Setup-1.3.4-x64.exe")


@pytest.mark.parametrize("private", ["JmShelf/data/login.json", "JmShelf/webview/Cookies", "JmShelf/_internal/.env", "JmShelf/../secret", "JmShelf/private.pem"])
def test_prepare_rejects_private_data(tmp_path, private):
    inputs(tmp_path, private)
    with pytest.raises(ValueError, match="private archive"):
        prepare(tmp_path)


def test_prepare_refuses_changed_version_artifacts(tmp_path):
    inputs(tmp_path)
    prepare(tmp_path)
    (tmp_path / "dist" / "JmShelf-Setup-1.3.4-x64.exe").write_bytes(b"MZchanged")
    with pytest.raises(ValueError, match="different release bytes"):
        prepare(tmp_path)
