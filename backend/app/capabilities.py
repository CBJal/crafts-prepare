"""Discover the caller-installed ffmpeg, ffprobe, Gyroflow, and telemetry tool."""

from __future__ import annotations

import re

from .probe import discover_executable
from .process import run_captured, tool_version

ENCODER_NAMES = (
    "libx264",
    "libx265",
    "prores_ks",
    "prores",
    "dnxhd",
    "aac",
    "pcm_s16le",
    "h264_amf",
    "hevc_amf",
)
FILTER_NAMES = ("vidstabdetect", "vidstabtransform", "deshake", "lut3d", "eq", "scale", "crop", "pad", "transpose")


def inspect_tools(settings: dict, cancel=None) -> dict:
    from .errors import Cancelled

    def _check() -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled()

    ffmpeg = discover_executable(settings.get("ffmpeg") or "ffmpeg")
    ffprobe = discover_executable(settings.get("ffprobe") or "ffprobe")
    gyroflow = discover_executable(settings.get("gyroflow") or "gyroflow")
    telemetry = discover_executable(settings.get("telemetry_parser") or "")
    _check()
    report = {
        "ffmpeg": _ffmpeg_report(ffmpeg, cancel=cancel),
        "ffprobe": _simple_report(ffprobe, ["-version"], "ffprobe", cancel=cancel),
        "gyroflow": _gyroflow_report(gyroflow, cancel=cancel),
        "telemetry_parser": _telemetry_report(telemetry, cancel=cancel),
    }
    return report


def _simple_report(path: str | None, args: list[str], label: str, cancel=None) -> dict:
    if not path:
        return {
            "available": False,
            "path": None,
            "version": None,
            "detail": f"{label} was not found on PATH. Install it separately; Crafts Prepare does not ship this binary.",
        }
    ok, text = tool_version([path, *args], cancel=cancel)
    first = text.splitlines()[0] if text else ""
    return {
        "available": ok,
        "path": path,
        "version": first,
        "detail": text if ok else (text or f"{label} did not respond to a version check."),
    }


def _ffmpeg_report(path: str | None, cancel=None) -> dict:
    from .errors import Cancelled

    base = _simple_report(path, ["-version"], "ffmpeg", cancel=cancel)
    base["encoders"] = {}
    base["filters"] = {}
    base["gpl_build_note"] = (
        "Encoder and filter availability comes from this ffmpeg binary. "
        "A GPL-enabled build (libx264, libx265, libvidstab) is the user's install, not a library redistributed by Crafts Prepare."
    )
    if not path or not base["available"]:
        return base
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    code, stdout, stderr = run_captured([path, "-hide_banner", "-encoders"], timeout=20, cancel=cancel)
    encoder_text = stdout if code == 0 else ""
    found = {}
    for name in ENCODER_NAMES:
        found[name] = bool(re.search(rf"\b{name}\b", encoder_text))
    base["encoders"] = found
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    code, stdout, stderr = run_captured([path, "-hide_banner", "-filters"], timeout=20, cancel=cancel)
    filter_text = stdout if code == 0 else ""
    filters = {}
    for name in FILTER_NAMES:
        filters[name] = bool(re.search(rf"\b{name}\b", filter_text))
    base["filters"] = filters
    if stderr and not base.get("version"):
        base["detail"] = stderr[:500]
    return base


def _gyroflow_report(path: str | None, cancel=None) -> dict:
    if not path:
        return {
            "available": False,
            "path": None,
            "version": None,
            "detail": (
                "Gyroflow was not found. Install the Gyroflow application yourself and set its path. "
                "Crafts Prepare only calls the public CLI (--stdout-progress, --out-params, --gyro-file, --preset). "
                "It does not pass --overwrite, --watch, or --when-done. Gyroflow is GPL software and is not included."
            ),
        }
    ok, text = tool_version([path, "--version"], cancel=cancel)
    return {
        "available": ok,
        "path": path,
        "version": text.splitlines()[0] if text else None,
        "detail": text
        if ok
        else (
            "The configured Gyroflow path did not return --version. "
            "Rendering will stay unavailable until a working CLI binary is set."
        ),
        "license": "Gyroflow is separately licensed (GPL). This app does not copy or distribute it.",
    }


def _telemetry_report(path: str | None, cancel=None) -> dict:
    note = (
        "Optional crafts-telemetry binary links telemetry-parser 0.2.6 (MIT OR Apache-2.0, Adrian Eddy). "
        "The published crate is older than the current git master, which needs a newer Rust edition than this environment pins. "
        "A parse result is not a claim of camera support."
    )
    if not path:
        return {"available": False, "path": None, "version": None, "detail": note}
    ok, text = tool_version([path, "--version"], cancel=cancel)
    return {
        "available": ok,
        "path": path,
        "version": text.splitlines()[0] if text else None,
        "detail": text if ok else note,
    }
