# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build. Run from packaging/build-windows.ps1."""

from pathlib import Path

spec_dir = Path(SPECPATH)
root = spec_dir.parent

a = Analysis(
    [str(root / "backend" / "crafts_prepare_entry.py")],
    pathex=[str(root / "backend")],
    binaries=[],
    datas=[
        (str(root / "frontend" / "dist"), "frontend/dist"),
        (str(root / "THIRD_PARTY_NOTICES.md"), "."),
        (str(spec_dir / "PREREQUISITES.txt"), "."),
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CraftsPrepare",
)
