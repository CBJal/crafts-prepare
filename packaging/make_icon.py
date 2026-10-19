"""Copy the approved app icon into packaging/icon.ico.

The artwork lives in assets/brand. This script does not draw a replacement.
A future build must not invent a different mark.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def approved_icon() -> Path:
    return _ROOT / "assets" / "brand" / "app-icon.ico"


def write_icon(path: Path) -> None:
    source = approved_icon()
    if not source.is_file():
        raise SystemExit(f"Approved icon is missing: {source}")
    data = source.read_bytes()
    if len(data) < 64 or data[:4] != b"\x00\x00\x01\x00":
        raise SystemExit("Approved icon is not an ICO file. Refusing to write a generated substitute.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


if __name__ == "__main__":
    destination = Path(__file__).with_name("icon.ico")
    write_icon(destination)
    print(destination)
