"""Write the Windows version resource consumed by PyInstaller."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def app_version() -> str:
    text = (ROOT / "backend" / "app" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION = "([^"]+)"', text)
    if not match:
        raise SystemExit("APP_VERSION was not found.")
    version = match.group(1)
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SystemExit(f"APP_VERSION must look like 0.1.0, not {version!r}.")
    return version


def version_info_text(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    tuple_text = f"({major}, {minor}, {patch}, 0)"
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={tuple_text},
    prodvers={tuple_text},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'Crafts Prepare'),
        StringStruct('FileDescription', 'Crafts Prepare'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'CraftsPrepare'),
        StringStruct('OriginalFilename', 'CraftsPrepare.exe'),
        StringStruct('ProductName', 'Crafts Prepare'),
        StringStruct('ProductVersion', '{version}')])
      ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_version_info(destination: Path, version: str | None = None) -> str:
    resolved = version or app_version()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(version_info_text(resolved), encoding="utf-8")
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    version = write_version_info(args.output)
    sys.stdout.write(version + "\n")


if __name__ == "__main__":
    main()
