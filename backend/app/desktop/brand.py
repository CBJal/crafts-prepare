"""Locate the approved app icon in a source checkout or a frozen bundle."""

from __future__ import annotations

from pathlib import Path

from ..runtime import bundle_dir, is_frozen, repo_root

_PNG = "app-icon.png"
_ICO = "app-icon.ico"


def brand_directory() -> Path | None:
    """Return the folder that holds both approved brand files."""
    candidates: list[Path] = []
    if is_frozen():
        bundled = bundle_dir()
        if bundled is not None:
            candidates.append(bundled / "assets" / "brand")
    candidates.append(repo_root() / "assets" / "brand")
    for candidate in candidates:
        if (candidate / _PNG).is_file() and (candidate / _ICO).is_file():
            return candidate
    return None


def brand_file(name: str) -> Path | None:
    folder = brand_directory()
    if folder is None:
        return None
    path = folder / name
    return path if path.is_file() else None


def load_app_icon():
    """QIcon built from the approved PNG and ICO. Empty if the files are missing."""
    from PySide6.QtGui import QIcon

    icon = QIcon()
    folder = brand_directory()
    if folder is None:
        return icon
    png = folder / _PNG
    ico = folder / _ICO
    if png.is_file():
        icon.addFile(str(png))
    if ico.is_file():
        icon.addFile(str(ico))
    return icon
