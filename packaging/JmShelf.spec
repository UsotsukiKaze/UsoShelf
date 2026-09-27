from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


project_root = Path(SPECPATH).parent
datas = [(str(project_root / "public"), "public")]
binaries = []
hiddenimports = collect_submodules("uvicorn")

for package in ("jmcomic", "webview", "curl_cffi", "pystray", "PIL"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

for distribution in ("jmcomic", "pywebview"):
    try:
        datas += copy_metadata(distribution)
    except Exception:
        pass

a = Analysis(
    [str(project_root / "scripts" / "jmshelf-desktop-entry.py")],
    pathex=[str(project_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "cefpython3", "tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JmShelf",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "packaging" / "JmShelf.ico"),
    version=str(project_root / "packaging" / "version-info.txt"),
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="JmShelf",
)
