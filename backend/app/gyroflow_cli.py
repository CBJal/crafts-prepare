"""Adapter for a caller-installed Gyroflow executable.

The argument list follows the public CLI:
https://docs.gyroflow.xyz/app/advanced-usage/command-line-cli

Progress lines match the public CLI's --stdout-progress text
(`[id] Rendering progress: current/total frames (percent%)`).
No Gyroflow source is included in this repository.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from .errors import Cancelled, CraftsError
from .probe import discover_executable, probe_file
from .process import run_captured
from .security import exclusive_output, publish_exclusive, same_file

MEDIA_SUFFIXES = {".mp4", ".mov", ".mxf", ".mkv", ".m2ts", ".mts"}

MAX_PROJECT_BYTES = 64 * 1024 * 1024
_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_VERSION_RE = re.compile(r"Gyroflow v\d+\.\d+\.\d+\S*")
# Path fields a .gyroflow project or preset may store. The output object is
# handled separately so a filename there is not treated as a media reference.
_MEDIA_PATH_KEYS = {"videofile", "video_file", "gyrofile", "gyro_file", "filepath", "gyro_path"}
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


def extract_gyroflow_version(text: str) -> str | None:
    """Pick the Gyroflow banner out of a version dump that may start with Qt debug lines."""
    match = _VERSION_RE.search(text or "")
    if match:
        return match.group(0)
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return None


def directory_file_uri(path: Path) -> str:
    """file URI for a directory, with a trailing slash, using Path.as_uri()."""
    uri = path.resolve().as_uri()
    return uri if uri.endswith("/") else uri + "/"


def is_absolute_reference(value: str) -> bool:
    """True for absolute filesystem paths and absolute file URIs. Relative file: URIs are not."""
    if value.lower().startswith("file:"):
        rest = value[5:]
        # file:///path, file://host/path, and file:/path are absolute. file:clip.mp4 is not.
        if rest.startswith("//") or rest.startswith("/"):
            return True
        return bool(_WIN_DRIVE.match(rest))
    if _WIN_DRIVE.match(value) or value.startswith("\\\\") or value.startswith("/"):
        return True
    return False


def resolve_reference(value: str, base: Path) -> str:
    """Resolve a relative media or gyro reference against the original project directory.

    Absolute paths and absolute file URIs are returned unchanged, including
    percent-encoding, spaces, Unicode, and Windows drive letters.
    """
    if not isinstance(value, str) or not value.strip() or "\x00" in value or "\n" in value or "\r" in value:
        raise CraftsError("A project path reference is empty or unsafe to stage.", code="gyro_stage")
    if "://" in value and not value.lower().startswith("file:"):
        raise CraftsError(
            "Only local files and file URIs can be staged. Remote references are not followed.",
            code="gyro_stage",
        )
    if is_absolute_reference(value):
        return value
    relative = value[5:].lstrip("/") if value.lower().startswith("file:") else value
    if _WIN_DRIVE.match(relative) or relative.startswith("\\\\") or relative.startswith("/"):
        return value
    resolved = (base / relative).resolve(strict=False)
    return str(resolved)


def file_uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if _WIN_DRIVE.match(path[1:] if path.startswith("/") else path):
        path = path[1:] if path.startswith("/") else path
    return Path(path)


def native_reference_path(value: str) -> Path | None:
    """A path this operating system can check, or None for a foreign absolute path."""
    if _WIN_DRIVE.match(value) or value.startswith("\\\\"):
        return Path(value) if os.name == "nt" else None
    if value.lower().startswith("file:"):
        if not is_absolute_reference(value):
            return None
        path = file_uri_to_path(value)
        text = str(path)
        if _WIN_DRIVE.match(text) or text.startswith("\\\\"):
            return path if os.name == "nt" else None
        return path
    return Path(value)


def stage_gyroflow_document(source: Path, staging_dir: Path, staging_output: Path, *, role: str) -> Path:
    """Write an owned copy whose output target is the staging file.

    Original bytes are not modified. Only output location fields are overridden.
    Relative media and gyro references are resolved against the original directory.
    """
    if source.suffix.lower() != ".gyroflow":
        raise CraftsError(f"A {role} must be a .gyroflow file.", code="gyro_stage")
    size = source.stat().st_size
    if size <= 0 or size > MAX_PROJECT_BYTES:
        raise CraftsError(f"The {role} file is empty or larger than the staging limit.", code="gyro_stage")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise CraftsError(f"The {role} file is not UTF-8 JSON.", code="gyro_stage") from exc
    except json.JSONDecodeError as exc:
        raise CraftsError(f"The {role} file is not JSON, so it was not sent to Gyroflow.", code="gyro_stage") from exc
    if not isinstance(payload, dict):
        raise CraftsError(f"The {role} file must be a JSON object.", code="gyro_stage")
    base = source.parent.resolve()
    _rewrite_relative_inputs(payload, base)
    if role == "project" and not isinstance(payload.get("videofile"), str):
        raise CraftsError("The .gyroflow project has no videofile, so it was not rendered.", code="gyro_stage")
    output = payload.get("output")
    destination = staging_dir / ("project.gyroflow" if role == "project" else "preset.gyroflow")
    if output is None:
        # Gyroflow 1.6.3 replaces the whole render-options object when a project
        # or preset contains "output". Inventing a path-only object would drop
        # codec and pixel format. With no output section, the CLI --out-params
        # path is what the tool keeps, and that path is this staging file.
        destination.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return destination
    if not isinstance(output, dict):
        raise CraftsError(f"The {role} output section is not an object.", code="gyro_stage")
    if staging_output.parent.resolve() != staging_dir.resolve():
        raise CraftsError("The staging output is not inside the job staging directory.", code="gyro_stage")
    # Folder then filename are what 1.6.3 reads. output_path is the older field
    # and is applied first, then overwritten by those two, so all three name
    # the same staging file. A macOS output_folder_bookmark is removed on the
    # copy only: import resolves it back over output_folder.
    output.pop("output_folder_bookmark", None)
    output["output_folder"] = directory_file_uri(staging_output.parent)
    output["output_filename"] = staging_output.name
    output["output_path"] = str(staging_output.resolve())
    destination.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return destination


def _rewrite_relative_inputs(node: object, base: Path) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key == "output":
                continue
            if key in _MEDIA_PATH_KEYS and isinstance(value, str):
                resolved = resolve_reference(value, base)
                _require_local_file(resolved, key)
                node[key] = resolved
            else:
                _rewrite_relative_inputs(value, base)
    elif isinstance(node, list):
        for item in node:
            _rewrite_relative_inputs(item, base)


def _require_local_file(reference: str, key: str) -> None:
    native = native_reference_path(reference)
    if native is None:
        return
    if not native.is_file():
        raise CraftsError(
            f"Could not stage {key}: {reference} is not a local file. Gyroflow was not started.",
            code="gyro_stage",
        )


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
    try:
        version_code, version_out, version_err = run_captured(
            [*launch_argv(executable), "--version"],
            timeout=20,
        )
    except (OSError, CraftsError) as exc:
        raise CraftsError(
            "Gyroflow did not respond to --version, so no render was started. " + str(exc),
            code="gyro_missing",
            status=424,
        ) from exc
    version = f"{version_out}\n{version_err}"
    if version_code != 0:
        raise CraftsError(
            "Gyroflow did not respond to --version, so no render was started. " + version[-500:],
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
        # A loaded .gyroflow project or preset overrides CLI --out-params with its
        # own output_folder and output_filename. Rewrite those fields on owned
        # copies so the external tool can only target this job's staging file.
        project_for_cli = (
            stage_gyroflow_document(project, staging_dir, staging, role="project") if project is not None else None
        )
        preset_for_cli = (
            stage_gyroflow_document(preset, staging_dir, staging, role="preset") if preset is not None else None
        )
        args = build_argv(
            executable,
            media=media,
            project=project_for_cli,
            gyro_file=motion.get("gyro_file"),
            preset=preset_for_cli,
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
            "gyroflow_version": extract_gyroflow_version(version),
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
