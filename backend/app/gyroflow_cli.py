"""Adapter for a caller-installed Gyroflow executable.

The argument list follows the public CLI:
https://docs.gyroflow.xyz/app/advanced-usage/command-line-cli

Progress lines match the public CLI's --stdout-progress text
(`[id] Rendering progress: current/total frames (percent%)`).
No Gyroflow source is included in this repository.
"""

from __future__ import annotations

import json
import math
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
GYRO_FORMATS = {
    "h264_mp4": {
        "codec": "H.264/AVC",
        "codec_options": "",
        "pixel_format": "yuv420p",
        "container": "mp4",
        "ffprobe_codec": "h264",
    },
    "prores_422": {
        "codec": "ProRes",
        "codec_options": "Standard",
        "pixel_format": "yuv422p10le",
        "container": "mov",
        "ffprobe_codec": "prores",
    },
}
MAX_MOTION_BYTES = 256 * 1024 * 1024

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


def stage_gyroflow_document(
    source: Path,
    staging_dir: Path,
    staging_output: Path,
    *,
    role: str,
    output_overrides: dict | None = None,
) -> Path:
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
    if output is None and not output_overrides:
        # Gyroflow 1.6.3 replaces the whole render-options object when a project
        # or preset contains "output". Inventing a path-only object would drop
        # codec and pixel format. With no output section, the CLI --out-params
        # path is what the tool keeps, and that path is this staging file.
        destination.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return destination
    if output is None:
        output = {}
        payload["output"] = output
    if not isinstance(output, dict):
        raise CraftsError(f"The {role} output section is not an object.", code="gyro_stage")
    if staging_output.parent.resolve() != staging_dir.resolve():
        raise CraftsError("The staging output is not inside the job staging directory.", code="gyro_stage")
    # Folder then filename are what 1.6.3 reads. output_path is the older field
    # and is applied first, then overwritten by those two, so all three name
    # the same staging file. A macOS output_folder_bookmark is removed on the
    # copy only: import resolves it back over output_folder.
    output.pop("output_folder_bookmark", None)
    for key in ("codec", "codec_options", "pixel_format", "audio", "output_width", "output_height"):
        if output_overrides and key in output_overrides and output_overrides[key] is not None:
            output[key] = output_overrides[key]
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


def gyro_format(name: str) -> dict:
    found = GYRO_FORMATS.get(name)
    if found is None:
        raise CraftsError("Gyro output must be H.264 MP4 8-bit or ProRes 422 MOV 10-bit.", code="bad_preset")
    return found


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise Cancelled("Cancelled.")


def _contained(path: Path, directory: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(directory.resolve(strict=False))
    except ValueError as exc:
        raise CraftsError(
            "Gyroflow was asked to write outside this job's staging directory. The job stopped.",
            code="gyro_stage",
        ) from exc


def _motion_sample_count(path: Path, staging_dir: Path) -> int:
    """Count usable raw_imu samples in the metadata file this job asked Gyroflow to write."""
    _contained(path, staging_dir)
    if not path.is_file() or path.stat().st_size == 0:
        raise CraftsError(
            "Gyroflow did not write parsed metadata inside staging, so this clip was not rendered.",
            code="gyro_motion_missing",
            status=424,
        )
    size = path.stat().st_size
    if size > MAX_MOTION_BYTES:
        raise CraftsError(
            f"Parsed gyro metadata is {size} bytes, above the {MAX_MOTION_BYTES} byte limit. It was not decoded.",
            code="gyro_motion_invalid",
            status=422,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CraftsError(
            "Gyroflow metadata in staging was not JSON, so this clip was not rendered.",
            code="gyro_motion_missing",
            status=424,
        ) from exc
    raw = payload.get("raw_imu") if isinstance(payload, dict) else None
    if not _usable_imu(raw):
        raise CraftsError(
            "Gyroflow parsed this clip but the raw IMU samples are missing, malformed, or not a progressing sequence. "
            "A data stream, lens tag, or another clip's project is not treated as motion. This clip was not rendered.",
            code="gyro_motion_missing",
            status=424,
        )
    return len(raw)


def _usable_imu(raw: object) -> bool:
    """True for a sequence of finite, strictly increasing timestamped gyro vectors."""
    if not isinstance(raw, list) or len(raw) < 2:
        return False
    previous: float | None = None
    for item in raw:
        if not isinstance(item, dict):
            return False
        timestamp = item.get("timestamp_ms")
        gyro = item.get("gyro")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(float(timestamp)):
            return False
        if not _finite_vector(gyro):
            return False
        if "accl" in item and item.get("accl") is not None and not _finite_vector(item.get("accl")):
            return False
        stamp = float(timestamp)
        if previous is not None and stamp <= previous:
            return False
        previous = stamp
    return True


def _finite_vector(value: object) -> bool:
    if not isinstance(value, list) or len(value) != 3:
        return False
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            return False
    return True


def _argv_with_out(executable: str, media: Path, output: Path, extra: list[str]) -> list[str]:
    params = {
        "output_path": str(output),
        "output_folder": directory_file_uri(output.parent),
        "output_filename": output.name,
    }
    args = [*launch_argv(executable), str(media), *extra, "--out-params", json.dumps(params, separators=(",", ":")), "--stdout-progress"]
    if any(token in FORBIDDEN for token in args):
        raise CraftsError("Refusing a Gyroflow command that includes a blocked flag.", code="gyro_argv")
    return args


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
    automatic: bool = False,
    output_settings: dict | None = None,
    stabilization: dict | None = None,
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
    if automatic:
        if media is None or not media.is_file():
            raise CraftsError("Automatic gyro needs the clip's own media file.", code="gyro_input")
        if project is not None or gyro_file is not None:
            raise CraftsError("Automatic gyro does not reuse another clip's project or gyro log.", code="gyro_input")
        chosen = gyro_format((output_settings or {}).get("format") or "")
        motion = {"kind": "embedded-preflight", "samples": 0}
    else:
        chosen = None
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
        if automatic:
            assert media is not None and chosen is not None
            project, motion = _prepare_embedded_project(
                executable,
                media,
                staging_dir,
                cancel,
                on_progress,
            )
            if stabilization:
                motion["stabilization_written"] = _write_stabilization(project, stabilization)
        # A loaded .gyroflow project or preset overrides CLI --out-params with its
        # own output_folder and output_filename. Rewrite those fields on owned
        # copies so the external tool can only target this job's staging file.
        overrides = None
        expected = None
        if automatic and chosen is not None:
            assert media is not None
            expected = _automatic_contract(chosen, output_settings or {}, media, ffprobe, staging.name)
            overrides = {
                "codec": expected["codec"],
                "codec_options": expected["codec_options"],
                "pixel_format": expected["pixel_format"],
                "audio": expected["audio"],
                "output_width": expected["output_width"],
                "output_height": expected["output_height"],
            }
        project_for_cli = (
            stage_gyroflow_document(project, staging_dir, staging, role="project", output_overrides=overrides)
            if project is not None
            else None
        )
        preset_for_cli = (
            stage_gyroflow_document(preset, staging_dir, staging, role="preset", output_overrides=overrides)
            if preset is not None
            else None
        )
        if automatic and expected is not None:
            _assert_staged_contract(staging_dir, staging, expected)
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
        if automatic and expected is not None:
            _assert_rendered_contract(probed, expected)
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
                (
                    f"Gyroflow reported {motion.get('samples')} raw IMU samples for this clip, "
                    "then rendered a project that was written only inside this job's staging directory. "
                    "Trim, LUT, exposure, and visual stabilization were not applied. "
                    "Lens and camera calibration are only what Gyroflow stored from this file."
                )
                if automatic
                else (
                    "Output was produced by the installed Gyroflow CLI and probed with ffprobe. "
                    "This job processes the whole source or project. Export trim and grade settings are not applied. "
                    "A full ffmpeg decode was not run."
                )
            ),
            "motion_samples": motion.get("samples"),
            "stabilization_written": motion.get("stabilization_written") or {},
        }
    finally:
        _remove_owned_staging(staging_dir, final)


def _automatic_contract(chosen: dict, settings: dict, media: Path, ffprobe: str, filename: str) -> dict:
    width = settings.get("width")
    height = settings.get("height")
    if not width or not height:
        probed = probe_file(ffprobe, media)
        video = (probed.get("video") or [None])[0] or {}
        width = video.get("width")
        height = video.get("height")
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int):
        raise CraftsError(
            "Source dimensions could not be read, so the gyro output size was not left to a preset.",
            code="gyro_output_mismatch",
        )
    return {
        "codec": chosen["codec"],
        "codec_options": chosen["codec_options"],
        "pixel_format": chosen["pixel_format"],
        "ffprobe_codec": chosen["ffprobe_codec"],
        "audio": bool(settings.get("audio", True)),
        "output_width": width,
        "output_height": height,
        "output_filename": filename,
    }


def _assert_staged_contract(staging_dir: Path, staging_output: Path, expected: dict) -> None:
    """Reject a staged project or preset whose output section is not the requested contract."""
    folder = directory_file_uri(staging_output.parent)
    found = False
    for name in ("project.gyroflow", "preset.gyroflow"):
        path = staging_dir / name
        if not path.is_file():
            continue
        found = True
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CraftsError(f"{name} could not be read back before render.", code="gyro_output_mismatch") from exc
        output = payload.get("output") if isinstance(payload, dict) else None
        if not isinstance(output, dict):
            raise CraftsError(f"{name} has no output section for the requested format.", code="gyro_output_mismatch")
        mismatches = []
        for key in ("codec", "codec_options", "pixel_format", "audio", "output_width", "output_height", "output_filename"):
            if output.get(key) != expected[key]:
                mismatches.append(f"{key}={output.get(key)!r}")
        if output.get("output_folder") != folder:
            mismatches.append("output_folder")
        if mismatches:
            raise CraftsError(
                f"{name} would not render the requested gyro output ({', '.join(mismatches)}). Nothing was published.",
                code="gyro_output_mismatch",
            )
    if not found:
        raise CraftsError("No staged Gyroflow document was available to check.", code="gyro_output_mismatch")


def _assert_rendered_contract(probed: dict, expected: dict) -> None:
    video = (probed.get("video") or [None])[0] or {}
    audios = probed.get("audio") if isinstance(probed.get("audio"), list) else []
    problems = []
    if video.get("codec") != expected["ffprobe_codec"]:
        problems.append(f"codec {video.get('codec')}")
    if video.get("pix_fmt") != expected["pixel_format"]:
        problems.append(f"pixel format {video.get('pix_fmt')}")
    if video.get("width") != expected["output_width"] or video.get("height") != expected["output_height"]:
        problems.append(f"size {video.get('width')}x{video.get('height')}")
    if expected["audio"] and not audios:
        problems.append("missing audio")
    if not expected["audio"] and audios:
        problems.append("unexpected audio")
    if problems:
        raise CraftsError(
            "The staged render does not match the requested gyro output ("
            + ", ".join(problems)
            + "). It was not published.",
            code="gyro_output_mismatch",
        )


def _write_stabilization(project: Path, values: dict) -> dict:
    """Record requested Gyroflow stabilization fields on an owned project copy.

    The keys are the public Gyroflow project fields ``smoothness``, ``fov``, and
    ``horizon_lock_amount``. Writing them is not a claim that every Gyroflow
    build honors them; the job result lists exactly the fields written.
    """
    payload = json.loads(project.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CraftsError("The exported Gyroflow project is not an object.", code="gyro_stage")
    current = payload.get("stabilization")
    if not isinstance(current, dict):
        current = {}
    written = {}
    for key in ("smoothness", "fov", "horizon_lock_amount"):
        if key not in values or values[key] is None:
            continue
        number = float(values[key])
        if not math.isfinite(number):
            raise CraftsError("Gyro stabilization values must be finite.", code="bad_recipe")
        current[key] = number
        written[key] = number
    payload["stabilization"] = current
    project.write_text(json.dumps(payload), encoding="utf-8")
    return written


def _prepare_embedded_project(
    executable: str,
    media: Path,
    staging_dir: Path,
    cancel: threading.Event,
    on_progress,
) -> tuple[Path, dict]:
    """Inspect this file's parsed IMU, then export a project that can only render into staging."""
    _check_cancel(cancel)
    metadata = staging_dir / "motion.json"
    _contained(metadata, staging_dir)
    meta_args = _argv_with_out(
        executable,
        media,
        staging_dir / "preflight-unused.mp4",
        ["--export-metadata", f"2:{metadata}"],
    )
    code, stdout, stderr = run_captured(meta_args, timeout=60 * 30, cancel=cancel, on_stdout=lambda line: _progress(line, on_progress))
    if code != 0:
        raise CraftsError(
            f"Gyroflow metadata export failed ({code}). This clip was not rendered. {(stdout + stderr)[-800:]}",
            code="gyro_failed",
        )
    samples = _motion_sample_count(metadata, staging_dir)
    on_progress(0.1)
    _check_cancel(cancel)
    prepared = staging_dir / "prepared.gyroflow"
    project_args = _argv_with_out(executable, media, prepared, ["--export-project", "2"])
    # The public CLI takes the project type as the option value, not as a filename.
    # _argv_with_out already placed the media path and the extra tokens.
    code, stdout, stderr = run_captured(
        project_args,
        timeout=60 * 30,
        cancel=cancel,
        on_stdout=lambda line: _progress(line, on_progress),
    )
    if code != 0:
        raise CraftsError(
            f"Gyroflow project export failed ({code}). This clip was not rendered. {(stdout + stderr)[-800:]}",
            code="gyro_failed",
        )
    _contained(prepared, staging_dir)
    if not prepared.is_file() or prepared.stat().st_size == 0:
        raise CraftsError(
            "Gyroflow did not write prepared.gyroflow inside staging, so this clip was not rendered.",
            code="gyro_unverified",
        )
    on_progress(0.2)
    return prepared, {"kind": "embedded-preflight", "samples": samples}


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
