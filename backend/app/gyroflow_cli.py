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
import shutil
import sys
import threading
import uuid
from pathlib import Path

from .errors import Cancelled, CraftsError
from .probe import discover_executable, probe_file
from .process import run_captured, tool_version
from .security import exclusive_output, publish_exclusive, same_file

MEDIA_SUFFIXES = {".mp4", ".mov", ".mxf", ".mkv", ".m2ts", ".mts"}

PROGRESS_RE = re.compile(
    r"Rendering progress:\s+(\d+)/(\d+)\s+frames\s+\(([0-9.]+)%\)"
)
SYNC_RE = re.compile(r"Synchronization progress:\s+([0-9.]+)%")
FORBIDDEN = {"--overwrite", "-f", "--watch", "--when-done", "-d", "--open"}


def launch_argv(executable: str) -> list[str]:
    """How to start the configured tool.

    A real Gyroflow install is an executable. A `.py` path is the test double
    and is started with this interpreter so the same file runs on Windows,
    where a POSIX shebang is not executable.
    """
    path = Path(executable)
    if path.suffix.lower() == ".py":
        return [sys.executable, str(path)]
    return [executable]


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
    args = launch_argv(executable)
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
    ok, version = tool_version([*launch_argv(executable), "--version"])
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
    if final.suffix.lower() not in MEDIA_SUFFIXES:
        raise CraftsError(
            "Gyroflow output needs a media extension such as .mp4 or .mov.",
            code="bad_name",
        )
    protected = [item for item in (media, project, gyro_file, preset, source_for_alias) if item is not None]
    _reject_protected(final, protected)
    staging_dir = final.parent / f".crafts-gyro-{uuid.uuid4().hex[:12]}"
    staging = staging_dir / f"staged{final.suffix}"
    staging_dir.mkdir()
    try:
        args = build_argv(
            executable,
            media=media,
            project=project if motion["kind"] == "project" else None,
            gyro_file=motion.get("gyro_file"),
            preset=preset,
            output=staging,
        )
        try:
            code, stdout, stderr = run_captured(
                args,
                timeout=60 * 60 * 6,
                cancel=cancel,
                on_stdout=lambda line: _progress(line, on_progress),
            )
        except Cancelled:
            raise
        combined = (stdout + "\n" + stderr)[-2000:]
        if code != 0:
            raise CraftsError(f"Gyroflow exited {code}. {combined}", code="gyro_failed")
        if not staging.exists() or staging.stat().st_size == 0:
            raise CraftsError(
                "Gyroflow exited 0 but the staging file is missing or empty. "
                "This is not reported as success. " + combined,
                code="gyro_unverified",
            )
        _reject_protected(staging, protected)
        try:
            probed = probe_file(ffprobe, staging)
        except CraftsError as exc:
            raise CraftsError(
                "ffprobe could not probe the Gyroflow staging file, so it was not published. " + exc.message,
                code="gyro_unverified",
            ) from exc
        if not probed.get("video"):
            raise CraftsError("Gyroflow staging file has no video stream. It was not published.", code="gyro_unverified")
        _reject_protected(final, protected)
        if final.exists() or final.is_symlink():
            raise CraftsError(
                f"Output appeared during the job and was left untouched: {final}",
                code="collision",
                status=409,
            )
        publish_exclusive(staging, final, source_for_alias)
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
            "note": (
                "Output was produced by the installed Gyroflow CLI and probed with ffprobe. "
                "This job processes the whole source or project. Export trim and grade settings are not applied. "
                "A full ffmpeg decode was not run."
            ),
        }
    finally:
        _remove_owned_staging(staging_dir, final)


def _reject_protected(path: Path, protected: list[Path]) -> None:
    for item in protected:
        try:
            if path.resolve(strict=False) == item.resolve(strict=False):
                raise CraftsError(
                    "Refusing to write onto an input file or an alias of it.",
                    code="source_overwrite",
                )
        except OSError:
            continue
        if item.exists() and path.exists() and same_file(path, item):
            raise CraftsError(
                "Refusing to write onto an input file or an alias of it.",
                code="source_overwrite",
            )


def _remove_owned_staging(staging_dir: Path, final: Path) -> None:
    """Delete only the directory this job created. Never the requested output."""
    try:
        staging_real = staging_dir.resolve(strict=False)
        final_real = final.resolve(strict=False)
        parent_real = final.parent.resolve(strict=False)
    except OSError:
        return
    if staging_real in {final_real, parent_real}:
        return
    if not staging_dir.is_dir():
        return
    shutil.rmtree(staging_dir, ignore_errors=True)


def _progress(line: str, on_progress) -> None:
    value = parse_progress(line)
    if value is not None:
        on_progress(value)
