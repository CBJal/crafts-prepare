# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the native Qt app. Run from packaging/build-windows.ps1."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH)))
from bundle_guard import filter_bundle

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
        (str(root / "LICENSE"), "."),
        (str(root / "THIRD_PARTY_NOTICES.md"), "."),
        (str(spec_dir / "PREREQUISITES.txt"), "."),
        (str(license_dir), "licenses"),
        (str(root / "assets" / "brand"), "assets/brand"),
    ],
    hiddenimports=[
        "app",
        "app.desktop",
        "app.desktop.main",
        "app.desktop.window",
        "app.desktop.smoke",
        "app.service",
        "app.runtime",
        "app.version",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtSvg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "pyinstaller",
        "PySide6.QtWebEngine",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebView",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtVirtualKeyboard",
    ],
    noarchive=False,
)

a.binaries, rejected_binaries = filter_bundle(a.binaries)
a.datas, rejected_datas = filter_bundle(a.datas)
manifest = {
    "kept_binaries": [{"name": str(entry[0]), "source": str(entry[1])} for entry in a.binaries],
    "rejected": rejected_binaries + rejected_datas,
}
output_root = os.environ.get("CRAFTS_OUTPUT_ROOT", "").strip()
manifest_path = Path(output_root) / "dependency-sources.json" if output_root else spec_dir / "dependency-sources.json"
manifest_path.parent.mkdir(parents=True, exist_ok=True)
manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
a.datas.append(("dependency-sources.json", str(manifest_path), "DATA"))
foreign = [item for item in manifest["rejected"] if "ICU" in item["reason"]]
if foreign:
    print("Excluded ICU libraries so Qt6Core loads the Windows copy at runtime:")
    for item in foreign:
        print(f"  {item['source']}")

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
