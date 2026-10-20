"""Per-clip preparation recipe. AUTO still resolves separately for each clip."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .colour import file_sha256
from .errors import CraftsError
from .filters import validate_lut

PROFILES = {"auto", "slog3_sgamut3cine", "hlg_bt2020", "preserve"}


def default_recipe() -> dict:
    return {
        "profile": "auto",
        "exposure_ev": 0.0,
        "contrast": 1.0,
        "saturation": 1.0,
        "input_lut": "",
        "input_lut_sha256": "",
        "look_lut": "",
        "look_lut_sha256": "",
        "look_includes_camera": False,
        "gyro_enabled": False,
        "gyro_smoothness": 0.5,
        "gyro_fov": 1.0,
        "gyro_horizon": 0.0,
    }


def _number(label: str, value, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CraftsError(f"{label} must be a number.", code="bad_recipe")
    number = float(value)
    if not math.isfinite(number):
        raise CraftsError(f"{label} must be a finite number.", code="bad_recipe")
    if number < low or number > high:
        raise CraftsError(f"{label} must be between {low:g} and {high:g}.", code="bad_recipe")
    return number


def _lut_field(path: str, digest: str, label: str) -> tuple[str, str]:
    if not path:
        return "", ""
    if not isinstance(path, str) or len(path) > 4096 or "\x00" in path:
        raise CraftsError(f"{label} path is invalid.", code="bad_lut")
    checked = validate_lut(path)
    current = file_sha256(checked)
    if digest and digest != current:
        # The stored hash stays so the UI can show that the file changed.
        # Queueing takes a new snapshot of the bytes it will actually use.
        return str(checked), digest
    return str(checked), current


def normalize_recipe(payload: dict | None, previous: dict | None = None) -> dict:
    base = default_recipe()
    if isinstance(previous, dict):
        base.update({key: previous.get(key) for key in base if key in previous})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise CraftsError("A recipe must be an object.", code="bad_recipe")
    if "profile" in payload:
        profile = payload["profile"]
        if profile not in PROFILES:
            raise CraftsError("Profile must be Auto, S-Log3, HLG, or Preserve input.", code="bad_recipe")
        base["profile"] = profile
    if "exposure_ev" in payload:
        base["exposure_ev"] = _number("Exposure", payload["exposure_ev"], -6.0, 6.0)
    if "contrast" in payload:
        base["contrast"] = _number("Contrast", payload["contrast"], 0.5, 2.0)
    if "saturation" in payload:
        base["saturation"] = _number("Saturation", payload["saturation"], 0.0, 2.0)
    if "look_includes_camera" in payload:
        if not isinstance(payload["look_includes_camera"], bool):
            raise CraftsError("The look-includes-camera control must be on or off.", code="bad_recipe")
        base["look_includes_camera"] = payload["look_includes_camera"]
    if "gyro_enabled" in payload:
        if not isinstance(payload["gyro_enabled"], bool):
            raise CraftsError("Gyro enable must be on or off.", code="bad_recipe")
        base["gyro_enabled"] = payload["gyro_enabled"]
    if "gyro_smoothness" in payload:
        base["gyro_smoothness"] = _number("Gyro strength", payload["gyro_smoothness"], 0.0, 1.0)
    if "gyro_fov" in payload:
        # Gyroflow 1.6.3 FOV slider is 0.1–3. Values above 1 widen the frame.
        base["gyro_fov"] = _number("Field of view", payload["gyro_fov"], 0.1, 3.0)
    if "gyro_horizon" in payload:
        # Gyroflow 1.6.3 horizon lock amount is a percent, 0–100. Zero is off.
        base["gyro_horizon"] = _number("Horizon lock", payload["gyro_horizon"], 0.0, 100.0)
    if "input_lut" in payload:
        path, digest = _lut_field(str(payload.get("input_lut") or ""), str(payload.get("input_lut_sha256") or base["input_lut_sha256"]), "Input LUT")
        base["input_lut"] = path
        base["input_lut_sha256"] = digest if path else ""
    if "look_lut" in payload:
        path, digest = _lut_field(str(payload.get("look_lut") or ""), str(payload.get("look_lut_sha256") or base["look_lut_sha256"]), "Look LUT")
        base["look_lut"] = path
        base["look_lut_sha256"] = digest if path else ""
    return base


def assert_stored_recipe(recipe: dict) -> dict:
    """Validate a recipe loaded from a project or a queued snapshot.

    Numeric fields must be finite even when they were not part of a new patch.
    A LUT path is kept when the file is missing so the project can still open
    and the missing file can be repaired. Queueing still snapshots bytes it
    can read.
    """
    if not isinstance(recipe, dict):
        raise CraftsError("A recipe must be an object.", code="bad_recipe")
    numbers = {
        key: value
        for key, value in recipe.items()
        if key not in {"input_lut", "look_lut", "input_lut_sha256", "look_lut_sha256"}
    }
    checked = normalize_recipe(numbers)
    for key in ("input_lut", "look_lut"):
        if key not in recipe or recipe.get(key) in (None, ""):
            continue
        path = recipe.get(key)
        if not isinstance(path, str) or not path.strip() or len(path) > 4096 or "\x00" in path:
            raise CraftsError(f"{key} path is invalid.", code="bad_lut")
        checked[key] = path
        digest_key = f"{key}_sha256"
        digest = recipe.get(digest_key) or ""
        if digest and (not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
            raise CraftsError(f"{key} checksum is invalid.", code="bad_lut")
        checked[digest_key] = digest if path else ""
    return checked


def recipe_fingerprint(recipe: dict) -> str:
    body = json.dumps(recipe, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def lut_status(recipe: dict) -> list[str]:
    notes = []
    for label, key, digest_key in (
        ("Input LUT", "input_lut", "input_lut_sha256"),
        ("Look LUT", "look_lut", "look_lut_sha256"),
    ):
        raw = recipe.get(key) or ""
        expected = recipe.get(digest_key) or ""
        if not raw:
            continue
        path = Path(raw)
        if not path.is_file():
            notes.append(f"{label} is missing ({path.name}).")
            continue
        try:
            current = file_sha256(path)
        except OSError:
            notes.append(f"{label} could not be read.")
            continue
        if expected and current != expected:
            notes.append(f"{label} changed since it was selected.")
    return notes


def freeze_lut_paths(recipe: dict, directory: Path) -> dict:
    """Copy LUT bytes now. Later edits to the user's file do not change this recipe."""
    frozen = dict(recipe)
    directory.mkdir(parents=True, exist_ok=True)
    for key, name in (("input_lut", "input.cube"), ("look_lut", "look.cube")):
        raw = str(recipe.get(key) or "")
        if not raw:
            frozen[key] = ""
            frozen[f"{key}_sha256"] = ""
            continue
        digest = file_sha256(validate_lut(raw))
        destination = directory / f"{digest}-{name}"
        if not destination.is_file():
            snapshot_lut(raw, directory, destination.name)
        if file_sha256(destination) != digest:
            raise CraftsError(f"{name} snapshot does not match the file that was queued.", code="lut_changed")
        frozen[f"{key}_source"] = raw
        frozen[key] = str(destination)
        frozen[f"{key}_sha256"] = digest
    return frozen


def assert_frozen_luts(recipe: dict) -> None:
    for key, label in (("input_lut", "Input LUT"), ("look_lut", "Look LUT")):
        raw = str(recipe.get(key) or "")
        expected = str(recipe.get(f"{key}_sha256") or "")
        if not raw and not expected:
            continue
        path = Path(raw) if raw else None
        if path is None or not path.is_file():
            raise CraftsError(f"{label} snapshot is missing. The queued file was not read again.", code="lut_changed")
        current = file_sha256(path)
        if expected and current != expected:
            raise CraftsError(
                f"{label} no longer matches the fingerprint stored when the job was queued.",
                code="lut_changed",
            )


def snapshot_lut(path: str, directory: Path, name: str) -> dict:
    if not path:
        return {"path": "", "sha256": "", "snapshot": ""}
    source = validate_lut(path)
    digest = file_sha256(source)
    destination = directory / name
    destination.write_bytes(source.read_bytes())
    if file_sha256(destination) != digest:
        raise CraftsError(f"Could not snapshot {source.name}.", code="bad_lut")
    return {"path": str(source), "sha256": digest, "snapshot": str(destination), "name": name}
