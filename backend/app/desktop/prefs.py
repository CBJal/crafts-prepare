"""Per-user window preferences. Projects and tool paths stay in the service."""

from __future__ import annotations

import errno
import json
import logging
import os
import time
import uuid
from pathlib import Path

from ..runtime import user_root

MAX_RECENT = 8
_REPLACE_ATTEMPTS = 6
_TRANSIENT_WINERRORS = {5, 32, 33}
log = logging.getLogger(__name__)


class PrefsWriteError(OSError):
    """The previous preferences file is unchanged."""


def state_path() -> Path:
    return user_root() / "desktop-state.json"


def load_state(path: Path | None = None) -> dict:
    destination = path or state_path()
    try:
        payload = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    theme = payload.get("theme")
    if theme not in {"light", "dark", "system"}:
        theme = "system"
    recent = [item for item in payload.get("recent") or [] if isinstance(item, str)]
    folders = [item for item in payload.get("folders") or [] if isinstance(item, str)]
    favorites = [item for item in payload.get("favorites") or [] if isinstance(item, str)]
    return {
        "theme": theme,
        "recent": recent[:MAX_RECENT],
        "folders": folders[:MAX_RECENT],
        "favorites": favorites[:MAX_RECENT],
        "geometry": payload.get("geometry") if isinstance(payload.get("geometry"), str) else "",
        "window_state": payload.get("window_state") if isinstance(payload.get("window_state"), str) else "",
    }


def _transient(exc: OSError) -> bool:
    winerror = getattr(exc, "winerror", None)
    if winerror in _TRANSIENT_WINERRORS:
        return True
    if isinstance(exc, PermissionError):
        return True
    return exc.errno in {errno.EACCES, errno.EPERM, errno.EBUSY}


def _replace_preferences(source: Path, dest: Path) -> None:
    delay = 0.05
    last: OSError | None = None
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, dest)
            return
        except OSError as exc:
            last = exc
            if not _transient(exc) or attempt + 1 >= _REPLACE_ATTEMPTS:
                break
            time.sleep(delay)
            delay = min(delay * 2, 0.4)
    raise PrefsWriteError("Could not replace the window preferences. The previous file was left unchanged.") from last


def save_state(payload: dict, path: Path | None = None) -> None:
    destination = path or state_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    recent = [item for item in payload.get("recent") or [] if isinstance(item, str)]
    folders = [item for item in payload.get("folders") or [] if isinstance(item, str)]
    favorites = [item for item in payload.get("favorites") or [] if isinstance(item, str)]
    body = {
        "theme": payload.get("theme") if payload.get("theme") in {"light", "dark", "system"} else "system",
        "recent": recent[:MAX_RECENT],
        "folders": folders[:MAX_RECENT],
        "favorites": favorites[:MAX_RECENT],
        "geometry": payload.get("geometry") or "",
        "window_state": payload.get("window_state") or "",
    }
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(body, indent=2), encoding="utf-8")
        _replace_preferences(temporary, destination)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            log.warning("Left an unreplaced preferences temp in place: %s", temporary.name)
        raise
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def remember_project(payload: dict, path: str) -> dict:
    recent = [item for item in payload.get("recent") or [] if item != path]
    recent.insert(0, path)
    payload["recent"] = recent[:MAX_RECENT]
    return payload
