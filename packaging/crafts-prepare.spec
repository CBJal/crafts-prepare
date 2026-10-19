# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build. Run from packaging/build-windows.ps1."""

import os
from pathlib import Path

spec_dir = Path(SPECPATH)
root = spec_dir.parent
license_value = os.environ.get("CRAFTS_LICENSE_DIR", "").strip()
if not license_value:
    raise SystemExit("CRAFTS_LICENSE_DIR is missing. Run packaging/collect_licenses.py before PyInstaller.")
license_dir = Path(license_value)
version_file = Path(os.environ["CRAFTS_VERSION_FILE"]) if os.environ.get("CRAFTS_VERSION_FILE", "").strip() else spec_dir / "file_version_info.txt"
if not license_dir.is_dir() or not any(license_dir.iterdir()):
    raise SystemExit("CRAFTS_LICENSE_DIR does not contain collected license files.")
if not version_file.is_file():
    raise SystemExit("The Windows version resource file is missing.")

a = Analysis(
    [str(root / "backend" / "crafts_prepare_entry.py")],
    pathex=[str(root / "backend")],
    binaries=[],
    datas=[
        (str(root / "frontend" / "dist"), "frontend/dist"),
        (str(root / "THIRD_PARTY_NOTICES.md"), "."),
        (str(spec_dir / "PREREQUISITES.txt"), "."),
        (str(license_dir), "licenses"),
    ],
    hiddenimports=[
        "app",
        "app.launcher",
        "app.main",
        "app.runtime",
        "app.version",
        "app.service",
        "h11",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pyinstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CraftsPrepare",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=str(spec_dir / "icon.ico"),
    version=str(version_file),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CraftsPrepare",
)
