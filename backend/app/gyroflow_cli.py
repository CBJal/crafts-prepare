"""Adapter for a caller-installed Gyroflow executable.

The argument list follows the public CLI:
https://docs.gyroflow.xyz/app/advanced-usage/command-line-cli

Progress lines match the public CLI's --stdout-progress text
(`[id] Rendering progress: current/total frames (percent%)`).
No Gyroflow source is included in this repository.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .errors import Cancelled, CraftsError
from .probe import discover_executable, probe_file
from .process import run_captured, tool_version
from .security import exclusive_output, same_file

PROGRESS_RE = re.compile(
    r"Rendering progress:\s+(\d+)/(\d+)\s+frames\s+\(([0-9.]+)%\)"
)
SYNC_RE = re.compile(r"Synchronization progress:\s+([0-9.]+)%")
FORBIDDEN = {"--overwrite", "-f", "--watch", "--when-done", "-d", "--open"}


def build_argv(
    executable: str,
    *,
    media: Path | None,
    project: Path | None,
    gyro_file: Path | None,
    preset: Path | None,
    output: Path,
) -> list[str]:
    if project is None and media is None:
        raise CraftsError("Gyroflow needs a media file or a .gyroflow project.", code="gyro_input")
    output = output.resolve(strict=False)
    params = {
        "output_path": str(output),
        "output_folder": str(output.parent),
        "output_filename": output.name,
    }
    args = [executable]
    if project is not None:
        args.append(str(project))
    else:
        args.append(str(media))
    if gyro_file is not None:
        args.extend(["--gyro-file", str(gyro_file)])
    if preset is not None:
        args.extend(["--preset", str(preset)])
    args.extend(
        [
            "--out-params",
            json.dumps(params, separators=(",", ":")),
            "--stdout-progress",
        ]
    )
    if any(token in FORBIDDEN for token in args):
        raise CraftsError("Refusing a Gyroflow command that includes a blocked flag.", code="gyro_argv")
    return args


def parse_progress(line: str) -> float | None:
    match = PROGRESS_RE.search(line)
    if match:
        current = int(match.group(1))
        total = int(match.group(2))
        if total <= 0:
            return None
        return max(0.0, min(0.99, current / total))
    sync = SYNC_RE.search(line)
    if sync:
        return max(0.0, min(0.5, float(sync.group(1)) / 200))
    if "Rendering completed" in line:
        return 0.99
    return None


def inspect_motion_inputs(
    *,
    media: Path | None,
    project: Path | None,
    gyro_file: Path | None,
) -> dict:
    """Return the motion input we will pass, or an actionable gap."""
    if project is not None:
        if project.suffix.lower() != ".gyroflow":
            raise CraftsError("Project input must be a .gyroflow file.", code="gyro_input")
        return {"kind": "project", "project": project, "gyro_file": gyro_file}
    if gyro_file is not None:
        if gyro_file.suffix.lower() not in {".gcsv", ".gyroflow", ".bbl", ".bfl", ".csv"}:
            raise CraftsError(
                "Gyro file must be .gcsv, .csv, .bbl, .bfl, or .gyroflow.",
                code="gyro_input",
            )
        return {"kind": "gyro-file", "gyro_file": gyro_file}
    if media is not None:
        sidecar = media.with_suffix(".gcsv")
        if sidecar.is_file():
            return {"kind": "sidecar", "gyro_file": sidecar}
    return {
        "kind": "missing",
        "message": (
            "No motion data was provided. Pass a .gcsv (or other gyro log), a .gyroflow project, "
            "or place a .gcsv sidecar next to the media. Embedded camera metadata is not assumed "
            "to contain gyro, and a missing file is not reported as a finished stabilize."
        ),
    }


def run_gyroflow(
    *,
    settings: dict,
    media: Path | None,
    project: Path | None,
    gyro_file: Path | None,
    preset: Path | None,
    output: Path,
    ffprobe: str,
    cancel: threading.Event,
    on_progress,
) -> dict:
    executable = discover_executable(settings.get("gyroflow") or "gyroflow")
    if not executable:
        raise CraftsError(
            "Gyroflow is not installed or not on PATH. Install Gyroflow yourself and set the executable "
            "in Settings. Crafts Prepare does not simulate a gyro render.",
            code="gyro_missing",
            status=424,
        )
    ok, version = tool_version([executable, "--version"])
    if not ok:
        raise CraftsError(
            "Gyroflow did not respond to --version, so no render was started. "
            + (version or ""),
            code="gyro_missing",
            status=424,
        )
    motion = inspect_motion_inputs(media=media, project=project, gyro_file=gyro_file)
    if motion["kind"] == "missing":
        raise CraftsError(motion["message"], code="gyro_motion_missing", status=424)
    source_for_alias = media or project
    if source_for_alias is None:
        raise CraftsError("Missing input.", code="gyro_input")
    final = exclusive_output(output, source_for_alias)
    if gyro_file and same_file(final, gyro_file):
        raise CraftsError("Refusing to overwrite the gyro log.", code="source_overwrite")
    args = build_argv(
        executable,
        media=media,
        project=project if motion["kind"] == "project" else None,
        gyro_file=motion.get("gyro_file"),
        preset=preset,
        output=final,
    )
    try:
        code, stdout, stderr = run_captured(
            args,
            timeout=60 * 60 * 6,
            cancel=cancel,
            on_stdout=lambda line: _progress(line, on_progress),
        )
    except Cancelled:
        if final.exists() and not same_file(final, source_for_alias):
            final.unlink(missing_ok=True)
        raise
    combined = (stdout + "\n" + stderr)[-2000:]
    if code != 0:
        if final.exists() and not same_file(final, source_for_alias):
            final.unlink(missing_ok=True)
        raise CraftsError(f"Gyroflow exited {code}. {combined}", code="gyro_failed")
    if not final.exists() or final.stat().st_size == 0:
        if final.exists() and not same_file(final, source_for_alias):
            final.unlink()
        raise CraftsError(
            "Gyroflow exited 0 but the expected output file is missing or empty. "
            "This is not reported as success. " + combined,
            code="gyro_unverified",
        )
    if same_file(final, source_for_alias):
        raise CraftsError("Gyroflow output aliases the source. Refusing to treat that as success.", code="source_overwrite")
    try:
        probed = probe_file(ffprobe, final)
    except CraftsError as exc:
        final.unlink(missing_ok=True)
        raise CraftsError(
            "Gyroflow wrote a file that ffprobe could not decode, so it was removed. " + exc.message,
            code="gyro_unverified",
        ) from exc
    if not probed.get("video"):
        final.unlink(missing_ok=True)
        raise CraftsError("Gyroflow output has no video stream.", code="gyro_unverified")
    return {
        "output_path": str(final),
        "gyroflow_version": version.splitlines()[0] if version else None,
        "argv": args,
        "validation": {
            "codec": probed["video"][0].get("codec"),
            "duration": probed.get("duration"),
            "width": probed["video"][0].get("width"),
            "height": probed["video"][0].get("height"),
        },
        "note": "Output was produced by the installed Gyroflow CLI and decoded with ffprobe.",
    }


def _progress(line: str, on_progress) -> None:
    value = parse_progress(line)
    if value is not None:
        on_progress(value)
