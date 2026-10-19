"""Per-user window preferences. Projects and tool paths stay in the service."""

from __future__ import annotations

import json
from pathlib import Path

from ..runtime import user_root

MAX_RECENT = 8


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
    return {
        "theme": theme,
        "recent": recent[:MAX_RECENT],
        "geometry": payload.get("geometry") if isinstance(payload.get("geometry"), str) else "",
        "window_state": payload.get("window_state") if isinstance(payload.get("window_state"), str) else "",
    }


def save_state(payload: dict, path: Path | None = None) -> None:
    destination = path or state_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    recent = [item for item in payload.get("recent") or [] if isinstance(item, str)]
    body = {
        "theme": payload.get("theme") if payload.get("theme") in {"light", "dark", "system"} else "system",
        "recent": recent[:MAX_RECENT],
        "geometry": payload.get("geometry") or "",
        "window_state": payload.get("window_state") or "",
    }
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(body, indent=2), encoding="utf-8")
    temporary.replace(destination)


def remember_project(payload: dict, path: str) -> dict:
    recent = [item for item in payload.get("recent") or [] if item != path]
    recent.insert(0, path)
    payload["recent"] = recent[:MAX_RECENT]
    return payload
