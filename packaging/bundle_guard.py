"""Decide which discovered binaries may be frozen into the Windows app.

PyInstaller follows every DLL on PATH. A Poppler or other unrelated ICU build
must not replace the Windows system ICU that Qt6Core expects. Unused GPL-only
Qt modules are left out as well.
"""

from __future__ import annotations

from pathlib import Path

_UNUSED = (
    "virtualkeyboard",
    "webengine",
    "qtwebview",
    "qtpdf",
)
_FOREIGN = ("poppler",)


def bundle_decision(dest_name: str, source_path: str) -> tuple[bool, str]:
    dest = dest_name.replace("\\", "/").lower()
    source = source_path.replace("\\", "/").lower()
    base = Path(dest).name
    if any(piece in dest or piece in source for piece in _UNUSED):
        return False, "unused Qt module"
    if "/qml/" in dest or "/qml/" in source:
        return False, "unused Qt Quick payload"
    if base.startswith("icu") and base.endswith(".dll"):
        # Qt6Core on Windows loads the system ICU. Freezing whichever icuuc.dll
        # appears first on PATH, including a copy taken from System32, breaks that.
        return False, "ICU is supplied by Windows at runtime"
    if any(piece in source for piece in _FOREIGN):
        return False, "library from an unrelated install"
    return True, "kept"


def filter_bundle(entries: list) -> tuple[list, list[dict]]:
    kept = []
    rejected = []
    for entry in entries:
        dest = str(entry[0])
        source = str(entry[1]) if len(entry) > 1 else ""
        allow, reason = bundle_decision(dest, source)
        if allow:
            kept.append(entry)
        else:
            rejected.append({"name": dest, "source": source, "reason": reason})
    return kept, rejected
