"""Install-time paths, tool lookup, and loopback port choice.

Development keeps the existing home-directory defaults. A frozen Windows build
keeps settings, cache, and logs under LocalAppData, outside the install folder.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "CraftsPrepare"
HEALTH_NAME = "Crafts Prepare"

# Relative to the standard Windows directory named by the environment variable.
_WINDOWS_TOOL_PATHS = {
    "ffmpeg": (
        ("ProgramFiles", "ffmpeg/bin/ffmpeg.exe"),
        ("ProgramFiles", "FFmpeg/bin/ffmpeg.exe"),
        ("LOCALAPPDATA", "Programs/ffmpeg/bin/ffmpeg.exe"),
        ("LOCALAPPDATA", "Microsoft/WinGet/Links/ffmpeg.exe"),
        ("ProgramData", "chocolatey/bin/ffmpeg.exe"),
    ),
    "ffprobe": (
        ("ProgramFiles", "ffmpeg/bin/ffprobe.exe"),
        ("ProgramFiles", "FFmpeg/bin/ffprobe.exe"),
        ("LOCALAPPDATA", "Programs/ffmpeg/bin/ffprobe.exe"),
        ("LOCALAPPDATA", "Microsoft/WinGet/Links/ffprobe.exe"),
        ("ProgramData", "chocolatey/bin/ffprobe.exe"),
    ),
    "gyroflow": (
        ("ProgramFiles", "Gyroflow/gyroflow.exe"),
        ("LOCALAPPDATA", "Programs/Gyroflow/gyroflow.exe"),
        ("LOCALAPPDATA", "Gyroflow/gyroflow.exe"),
    ),
}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    return Path(meipass)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def frontend_dist() -> Path:
    if is_frozen():
        bundled = bundle_dir()
        if bundled is not None:
            return bundled / "frontend" / "dist"
    return repo_root() / "frontend" / "dist"


def user_root() -> Path:
    """Writable per-user directory. Never the installation directory."""
    if is_frozen():
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / APP_DIR_NAME
    return Path.home() / ".crafts-prepare"


def settings_path() -> Path:
    override = os.environ.get("CRAFTS_SETTINGS_PATH")
    if override:
        return Path(override)
    return user_root() / "settings.json"


def data_dir() -> Path:
    override = os.environ.get("CRAFTS_DATA_DIR")
    if override:
        return Path(override)
    return user_root() / "cache"


def log_path() -> Path:
    override = os.environ.get("CRAFTS_LOG_PATH")
    if override:
        return Path(override)
    return user_root() / "logs" / "crafts-prepare.log"


def instance_path() -> Path:
    return user_root() / "instance.json"


def _looks_like_path(value: str) -> bool:
    if "/" in value or "\\" in value:
        return True
    return len(value) >= 2 and value[1] == ":"


def common_tool_candidates(name: str, environ: dict[str, str] | None = None) -> list[Path]:
    """Well-known install locations. No personal or machine-specific paths."""
    env = environ if environ is not None else os.environ
    key = Path(name).name.lower()
    if key.endswith(".exe"):
        key = key[:-4]
    relatives = _WINDOWS_TOOL_PATHS.get(key, ())
    found: list[Path] = []
    for variable, relative in relatives:
        root = env.get(variable)
        if not root:
            continue
        found.append(Path(root) / relative)
    return found


def discover_tool(configured: str, *, which) -> str | None:
    """Resolve an explicit file, a PATH command, or a common install location.

    An explicit path that is not a file is not replaced with a different install.
    """
    if not configured or not isinstance(configured, str):
        return None
    configured = configured.strip()
    if not configured:
        return None
    path = Path(configured)
    if path.is_absolute() or _looks_like_path(configured):
        return str(path.resolve()) if path.is_file() else None
    found = which(configured)
    if found:
        return found
    for candidate in common_tool_candidates(configured):
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def bundle_path_entries() -> list[Path]:
    if not is_frozen():
        return []
    entries: list[Path] = []
    bundled = bundle_dir()
    if bundled is not None:
        entries.append(bundled)
    try:
        exe_dir = Path(sys.executable).resolve().parent
    except OSError:
        return entries
    entries.append(exe_dir)
    entries.append(exe_dir / "_internal")
    return entries


def external_tool_environ(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for FFmpeg and Gyroflow.

    A frozen build removes the bundled runtime directories from PATH so those
    tools load their own DLLs instead of the copy next to the launcher.
    """
    env = dict(os.environ if base is None else base)
    if not is_frozen():
        return env
    skip: set[str] = set()
    for entry in bundle_path_entries():
        try:
            skip.add(str(entry.resolve()))
        except OSError:
            continue
    kept: list[str] = []
    for part in env.get("PATH", "").split(os.pathsep):
        if not part:
            continue
        try:
            if str(Path(part).resolve()) in skip:
                continue
        except OSError:
            pass
        kept.append(part)
    env["PATH"] = os.pathsep.join(kept)
    return env


def choose_port(preferred: int, *, can_bind, is_ours, attempts: int = 16) -> tuple[str, int]:
    """Pick a loopback port without disturbing a listener that is not ours.

    `can_bind` must only try to bind. `is_ours` may read a health response.
    Neither callback should terminate a foreign process.
    """
    if not 1 <= preferred <= 65535:
        raise ValueError("preferred port is invalid")
    for offset in range(max(1, attempts)):
        port = preferred + offset
        if port > 65535:
            break
        if can_bind(port):
            return ("bind", port)
        if is_ours(port):
            return ("reuse", port)
    raise RuntimeError(f"No free loopback port from {preferred} and no running Crafts Prepare instance.")


def can_bind_loopback(port: int) -> bool:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        sock.close()
    return True
