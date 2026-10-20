"""In-memory project, persisted JSON, and the sequential job queue."""

from __future__ import annotations

import errno
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .capabilities import inspect_tools
from .errors import Cancelled, CraftsError
from .export import output_filename, run_export
from .filters import VIDSTAB_AUTO, FilterSpec, validate_adjustment, validate_lut
from .gcsv import parse_gcsv_file
from .gyroflow_cli import gyro_format, run_gyroflow
from .importer import import_path
from .prepare import plan_prepare_clip, run_prepare
from .recipe import freeze_lut_paths
from .presets import PRESETS, conversion_note, preset_availability
from .preview import PROXY_LIMIT_SECONDS, browser_playable, extract_thumbnail, make_proxy
from .probe import discover_executable
from .process import run_captured
from .security import (
    MAX_PROJECT_BYTES,
    discard_owned_output,
    exclusive_output,
    resolve_existing,
    safe_filename,
    safe_suffix,
    same_file,
)

SCHEMA = "crafts-prepare.project"
VERSION = 1
# WinError 5, 32, and 33: access denied, sharing violation, lock violation.
_TRANSIENT_WINERRORS = {5, 32, 33}
_REPLACE_ATTEMPTS = 6
_PROJECT_LOCK_MESSAGE = (
    "Could not replace the project file. The previous file was left unchanged. "
    "The queue is paused until that file can be saved. "
    "Free the file, or use Save As to store the project in another folder."
)
log = logging.getLogger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _transient_replace_error(exc: OSError) -> bool:
    winerror = getattr(exc, "winerror", None)
    if winerror in _TRANSIENT_WINERRORS:
        return True
    if isinstance(exc, PermissionError):
        return True
    return exc.errno in {errno.EACCES, errno.EPERM, errno.EBUSY}


def _replace_durable(source: Path, dest: Path, attempts: int) -> None:
    """Replace dest with source. Retry only transient share/permission failures."""
    delay = 0.05
    last: OSError | None = None
    total = max(1, attempts)
    for attempt in range(total):
        try:
            os.replace(source, dest)
            return
        except OSError as exc:
            last = exc
            if not _transient_replace_error(exc) or attempt + 1 >= total:
                break
            time.sleep(delay)
            delay = min(delay * 2, 0.4)
    raise CraftsError(_PROJECT_LOCK_MESSAGE, code="project_locked") from last


def _discard_owned_temp(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Left an unreplaced project temp in place: %s", path.name)


def _job_view(job: dict) -> dict:
    return {
        "status": job["status"],
        "progress": job["progress"],
        "error": job["error"],
        "code": job["code"],
        "result": job["result"],
        "finished_at": job["finished_at"],
        "attempt": job.get("attempt"),
    }


def default_settings_path() -> Path:
    from .runtime import settings_path

    return settings_path()


def default_data_dir() -> Path:
    from .runtime import data_dir

    return data_dir()


class Service:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.settings_path = default_settings_path()
        self.data_dir = default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings = self._load_settings()
        self.project = new_project()
        self.project_path: Path | None = None
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.cancel_flags: dict[str, threading.Event] = {}
        self.worker: threading.Thread | None = None
        self._last_save = 0.0
        self._caps_cache: dict | None = None
        self.persist_error: str | None = None
        self._claims_paused = False

    def start(self) -> None:
        from .runtime import is_frozen

        if not is_frozen() and os.environ.get("CRAFTS_DISABLE_WORKER") == "1":
            return
        if self.worker and self.worker.is_alive():
            return
        self.stop.clear()
        self.worker = threading.Thread(target=self._loop, name="crafts-queue", daemon=True)
        self.worker.start()

    def shutdown(self, timeout: float = 8.0) -> None:
        """Cancel queued work and wait for the owned worker, unless this is that worker."""
        self.stop.set()
        self.wake.set()
        for flag in self.cancel_flags.values():
            flag.set()
        worker = self.worker
        if worker is None or not worker.is_alive():
            return
        if worker is threading.current_thread():
            return
        worker.join(timeout=timeout)

    def _load_settings(self) -> dict:
        defaults = {
            "ffmpeg": "ffmpeg",
            "ffprobe": "ffprobe",
            "gyroflow": "gyroflow",
            "telemetry_parser": "",
        }
        if self.settings_path.is_file():
            try:
                loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                for key in defaults:
                    if isinstance(loaded.get(key), str):
                        defaults[key] = loaded[key]
        return defaults

    def save_settings(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        os.replace(temporary, self.settings_path)

    def update_settings(self, patch: dict) -> dict:
        with self.lock:
            for key in ("ffmpeg", "ffprobe", "gyroflow", "telemetry_parser"):
                if key in patch and patch[key] is not None:
                    value = patch[key].strip()
                    if "\x00" in value or "\n" in value:
                        raise CraftsError("Tool path contains an invalid character.", code="bad_path")
                    self.settings[key] = value
            self.save_settings()
            self._caps_cache = None
            return self.capabilities()

    def capabilities(self, cancel=None) -> dict:
        if cancel is None and self._caps_cache is not None:
            return self._caps_cache
        report = inspect_tools(self.settings, cancel=cancel)
        encoders = (report.get("ffmpeg") or {}).get("encoders") or {}
        report["presets"] = [preset_availability(preset, encoders) for preset in PRESETS.values()]
        report["vidstab_auto"] = {
            **VIDSTAB_AUTO,
            "meaning": "These numbers are sent to vidstabdetect and vidstabtransform. Auto is not a separate hidden algorithm.",
        }
        report["limits"] = {
            "hdr_display_matching": False,
            "sony_color_transforms": False,
            "gyro_requires_external_gyroflow": True,
            "binaries_bundled": False,
        }
        if cancel is None or not cancel.is_set():
            self._caps_cache = report
        return report

    def _tools(self) -> tuple[str, str]:
        ffmpeg = discover_executable(self.settings.get("ffmpeg") or "ffmpeg")
        ffprobe = discover_executable(self.settings.get("ffprobe") or "ffprobe")
        if not ffmpeg or not ffprobe:
            raise CraftsError(
                "ffmpeg and ffprobe must be installed and visible on PATH, or set in Settings. "
                "Crafts Prepare does not download or bundle them.",
                code="missing_tool",
                status=424,
            )
        return ffmpeg, ffprobe

    def _encoder_flags(self) -> dict:
        report = self.capabilities()
        ffmpeg = report.get("ffmpeg") or {}
        encoders = dict(ffmpeg.get("encoders") or {})
        filters = ffmpeg.get("filters") or {}
        encoders["_vidstab"] = bool(filters.get("vidstabdetect") and filters.get("vidstabtransform"))
        encoders["_deshake"] = bool(filters.get("deshake"))
        encoders["_eq"] = bool(filters.get("eq"))
        encoders["_lut3d"] = bool(filters.get("lut3d"))
        return encoders

    def _reserved_outputs(self) -> set[str]:
        reserved: set[str] = set()
        for job in self.project["jobs"]:
            if job.get("status") not in {"queued", "running"}:
                continue
            request = job.get("request") or {}
            output = request.get("output_path")
            if isinstance(output, str) and output.strip():
                reserved.add(str(Path(output).resolve(strict=False)))
                continue
            folder = request.get("destination_dir")
            filename = request.get("filename")
            if isinstance(folder, str) and isinstance(filename, str):
                reserved.add(str((Path(folder) / filename).resolve(strict=False)))
        return reserved

    def _validate_shared_export(self, shared: dict) -> None:
        if not isinstance(shared, dict):
            raise CraftsError("Export batch settings are missing.", code="bad_batch")
        preset = PRESETS.get(shared.get("preset_id"))
        if preset is None:
            raise CraftsError("Unknown preset.", code="bad_preset")
        flags = self._encoder_flags()
        if not any(flags.get(name) for name in preset["encoder_aliases"]):
            raise CraftsError(
                f"{preset['label']} is unavailable because this ffmpeg build has no {preset['encoder']} encoder.",
                code="encoder_missing",
                status=424,
            )
        if shared.get("audio", True) and not flags.get(preset["audio_encoder_required"]):
            raise CraftsError(
                f"Audio encoder {preset['audio_encoder_required']} is missing. Turn audio off or install it.",
                code="encoder_missing",
                status=424,
            )
        spec = spec_from_body(shared)
        if spec.stabilization == "vidstab" and not flags.get("_vidstab"):
            raise CraftsError("Visual vidstab is not available in this ffmpeg build.", code="filter_missing")
        if spec.stabilization == "deshake" and not flags.get("_deshake"):
            raise CraftsError("deshake is not available in this ffmpeg build.", code="filter_missing")
        if spec.lut_path:
            validate_lut(spec.lut_path)

    def _validate_shared_gyro(self, shared: dict) -> None:
        if not isinstance(shared, dict):
            raise CraftsError("Gyro batch settings are missing.", code="bad_batch")
        gyro_format(shared.get("format") or "")
        width = shared.get("width")
        height = shared.get("height")
        if (width is None) != (height is None):
            raise CraftsError("Set both gyro width and height, or leave both empty to keep source resolution.", code="bad_batch")
        for value, limit, label in ((width, 7680, "width"), (height, 4320, "height")):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 2 or value > limit or value % 2:
                raise CraftsError(f"Gyro {label} must be an even size from 2 to {limit}.", code="bad_batch")
        preset = shared.get("preset_path")
        if preset:
            path = resolve_existing(preset)
            if path.suffix.lower() != ".gyroflow":
                raise CraftsError("A shared Gyroflow preset must be a .gyroflow file.", code="gyro_input")
        if not isinstance(shared.get("audio", True), bool):
            raise CraftsError("Gyro audio flag is invalid.", code="bad_batch")

    def _plan_export_clip(self, clip: dict, shared: dict, destination: Path, suffix: str) -> dict:
        preset = PRESETS[shared["preset_id"]]
        trim_in = float(clip.get("trim_in") or 0)
        trim_out = float(clip.get("trim_out") or 0)
        if trim_out <= trim_in:
            raise CraftsError("Saved trim in/out must be a positive interval.", code="bad_trim")
        duration = float(clip["probe"].get("duration") or 0)
        video = (clip["probe"].get("video") or [None])[0] or {}
        if video and video.get("frame_rate_num") and video.get("frame_rate_den"):
            frame = float(video["frame_rate_den"]) / float(video["frame_rate_num"])
        else:
            frame = 0.04
        if duration and trim_out > duration + frame:
            raise CraftsError("Saved trim out is past the end of the clip.", code="bad_trim")
        note = conversion_note(video.get("pix_fmt"), video.get("bit_depth"), preset)
        if note["bit_depth_conversion"] and not shared.get("acknowledge_bit_depth_conversion"):
            raise CraftsError(note["message"], code="bit_depth", status=409)
        spec = spec_from_body(shared)
        filename = output_filename(_stem(clip["name"]), suffix, preset["container"])
        final = exclusive_output(destination / filename, Path(clip["path"]))
        request = {
            **shared,
            "clip_id": clip["id"],
            "in_seconds": trim_in,
            "out_seconds": trim_out,
            "suffix": suffix,
            "destination_dir": str(destination),
            "filename": filename,
            "source_path": clip["path"],
            "source_sha256": clip["sha256"],
            "resolved_stabilization": {
                "mode": spec.stabilization,
                "smoothing": spec.stab_smoothing,
                "crop": spec.stab_crop,
                "zoom": spec.stab_zoom,
                "optzoom": spec.stab_optzoom,
                "shakiness": shared.get("stab_shakiness", 5),
                "accuracy": shared.get("stab_accuracy", 15),
                "deshake_rx": spec.deshake_rx,
                "deshake_ry": spec.deshake_ry,
            },
            "conversion": note,
        }
        return {"kind": "export", "filename": filename, "output": str(final), "request": request}

    def _plan_gyro_clip(self, clip: dict, shared: dict, destination: Path, suffix: str) -> dict:
        chosen = gyro_format(shared["format"])
        filename = output_filename(_stem(clip["name"]), suffix, chosen["container"])
        final = exclusive_output(destination / filename, Path(clip["path"]))
        preset = shared.get("preset_path") or None
        request = {
            "clip_id": clip["id"],
            "media_path": clip["path"],
            "output_path": str(final),
            "preset_path": preset,
            "automatic": True,
            "filename": filename,
            "source_sha256": clip["sha256"],
            "output_settings": {
                "format": shared["format"],
                "audio": bool(shared.get("audio", True)),
                "width": shared.get("width"),
                "height": shared.get("height"),
            },
        }
        return {"kind": "gyroflow", "filename": filename, "output": str(final), "request": request}

    def import_media(self, raw: str, cancel=None) -> dict:
        from .errors import Cancelled

        _ffmpeg, ffprobe = self._tools()
        result = import_path(raw, ffprobe, cancel=cancel)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        added = []
        duplicates = []
        with self.lock:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            for clip in result["clips"]:
                existing = self._find_duplicate(clip)
                if existing:
                    duplicates.append({"id": existing["id"], "sha256": clip["sha256"], "path": clip["path"]})
                    continue
                self.project["clips"].append(clip)
                added.append(public_clip(clip))
            self._persist()
        return {"added": added, "duplicates": duplicates, "skipped": result["skipped"], "root": result["root"]}

    def _find_duplicate(self, clip: dict) -> dict | None:
        for existing in self.project["clips"]:
            if existing["sha256"] == clip["sha256"]:
                return existing
            if existing.get("device") == clip.get("device") and existing.get("inode") == clip.get("inode"):
                return existing
        return None

    def clips(self) -> list[dict]:
        with self.lock:
            return [public_clip(clip) for clip in self.project["clips"]]

    def clip(self, clip_id: str) -> dict:
        with self.lock:
            return public_clip(self._clip(clip_id))

    def _clip(self, clip_id: str) -> dict:
        for clip in self.project["clips"]:
            if clip["id"] == clip_id:
                return clip
        raise CraftsError("Clip not found.", code="not_found", status=404)

    def refresh_metadata(self, clip_ids: list[str]) -> dict:
        """Re-probe and re-pair selected clips in place. Recipes are kept.

        Re-import deduplicates by checksum and does not refresh a clip that is
        already in the project. This is the path that repairs a changed or
        missing sidecar. A missing source is reported and does not wipe the clip.
        """
        from .importer import sha256_file
        from .probe import probe_file
        from .sony_xml import assess_sidecar, pair_sidecar

        _ffmpeg, ffprobe = self._tools()
        results = []
        with self.lock:
            for clip_id in clip_ids:
                clip = self._clip(clip_id)
                path = Path(clip["path"])
                recipe = clip.get("recipe") if isinstance(clip.get("recipe"), dict) else None
                if not path.is_file():
                    side = dict(clip.get("sidecar") or {})
                    side["refresh"] = "missing"
                    side["message"] = "The source file is missing, so metadata was not replaced. The saved recipe was kept."
                    clip["sidecar"] = side
                    clip["source_missing"] = True
                    results.append({"id": clip_id, "status": "missing", "recipe_kept": recipe is not None})
                    continue
                probed = probe_file(ffprobe, path)
                sidecar = pair_sidecar(path)
                if sidecar.get("status") == "ok":
                    sidecar["assessment"] = assess_sidecar(sidecar, probed)
                clip["probe"] = probed
                clip["sidecar"] = sidecar
                clip["sha256"] = sha256_file(path)
                stat = path.stat()
                clip["size"] = stat.st_size
                clip["device"] = stat.st_dev
                clip["inode"] = stat.st_ino
                clip["source_missing"] = False
                if recipe is not None:
                    clip["recipe"] = recipe
                duration = float(probed.get("duration") or 0)
                if duration and float(clip.get("trim_out") or 0) > duration + 0.05:
                    clip["trim_out"] = duration
                results.append(
                    {
                        "id": clip_id,
                        "status": "refreshed",
                        "assessment": sidecar.get("assessment") or {},
                        "recipe_kept": recipe is not None,
                    }
                )
            self._persist()
        return {"clips": results}

    def update_clip(self, clip_id: str, patch: dict) -> dict:
        with self.lock:
            clip = self._clip(clip_id)
            new_flag = clip.get("flag") or ""
            new_notes = clip.get("notes") or ""
            new_markers = list(clip.get("markers") or [])
            new_in = float(clip.get("trim_in") or 0)
            new_out = float(clip.get("trim_out") or 0)
            if "flag" in patch and patch["flag"] is not None:
                if patch["flag"] not in {"", "OK", "KEEP", "NG"}:
                    raise CraftsError("Flag must be OK, KEEP, NG, or empty.", code="bad_flag")
                new_flag = patch["flag"]
            if "notes" in patch and patch["notes"] is not None:
                notes = patch["notes"]
                if not isinstance(notes, str) or len(notes) > 8000:
                    raise CraftsError("Notes must be text up to 8000 characters.", code="bad_notes")
                new_notes = notes
            if "markers" in patch and patch["markers"] is not None:
                new_markers = _markers(patch["markers"])
            if "trim_in" in patch and patch["trim_in"] is not None:
                new_in = _finite_number("Trim in", patch["trim_in"])
            if "trim_out" in patch and patch["trim_out"] is not None:
                new_out = _finite_number("Trim out", patch["trim_out"])
            new_recipe = clip.get("recipe") if isinstance(clip.get("recipe"), dict) else None
            if "recipe" in patch and patch["recipe"] is not None:
                from .recipe import normalize_recipe

                new_recipe = normalize_recipe(patch["recipe"], previous=new_recipe)
            duration = float(clip["probe"].get("duration") or 0)
            if new_in < 0 or new_out <= new_in:
                raise CraftsError("Trim in/out must be a positive interval.", code="bad_trim")
            if duration and new_out > duration + 0.05:
                raise CraftsError("Trim out is past the end of the clip.", code="bad_trim")
            clip["flag"] = new_flag
            clip["notes"] = new_notes
            clip["markers"] = new_markers
            clip["trim_in"] = new_in
            clip["trim_out"] = new_out
            if new_recipe is not None:
                clip["recipe"] = new_recipe
            self._persist()
            return public_clip(clip)

    def thumbnail(self, clip_id: str, seconds: float, spec: FilterSpec, cancel=None) -> Path:
        ffmpeg, _ffprobe = self._tools()
        with self.lock:
            clip = self._clip(clip_id)
            source = Path(clip["path"])
            duration = float(clip["probe"].get("duration") or 0)
        destination = self.data_dir / f"{clip_id}-{uuid.uuid4().hex}.jpg"
        return extract_thumbnail(
            ffmpeg=ffmpeg,
            source=source,
            seconds=seconds,
            destination=destination,
            spec=spec,
            duration=duration,
            cancel=cancel,
        )

    def stream_path(self, clip_id: str) -> Path:
        with self.lock:
            clip = self._clip(clip_id)
            return Path(clip["path"])

    def proxy(self, clip_id: str, cancel=None, force: bool = False) -> dict:
        ffmpeg, _ffprobe = self._tools()
        with self.lock:
            clip = self._clip(clip_id)
            source = Path(clip["path"])
            digest = clip["sha256"]
            duration = float(clip["probe"].get("duration") or 0)
            playable = browser_playable(clip["probe"])
        limit = min(duration, PROXY_LIMIT_SECONDS) if duration and duration > 0 else PROXY_LIMIT_SECONDS
        if playable and not force:
            return {
                "kind": "source",
                "path": str(source),
                "url": f"/api/media/{clip_id}/file",
                "duration_limit_seconds": None,
                "source_duration": duration,
                "limited": False,
                "note": "Playback is using the source file.",
            }
        destination = self.data_dir / f"proxy-{digest}.mp4"
        if not (destination.is_file() and destination.stat().st_size > 0):
            make_proxy(ffmpeg=ffmpeg, source=source, destination=destination, duration=duration, cancel=cancel)
        return {
            "kind": "proxy",
            "url": f"/api/media/{clip_id}/proxy-file",
            "duration_limit_seconds": limit,
            "source_duration": duration,
            "limited": bool(duration and duration > PROXY_LIMIT_SECONDS),
            "note": (
                f"Viewing proxy is H.264, about 640px wide, and at most {int(PROXY_LIMIT_SECONDS)} seconds. "
                "It is not full-clip playback when the source is longer. The source file was not modified. "
                "Adjustments are on the still preview. Vidstab and deshake run only during export."
            ),
            "proxy_path": str(destination),
        }

    def proxy_file(self, clip_id: str) -> Path:
        with self.lock:
            clip = self._clip(clip_id)
            digest = clip["sha256"]
        path = self.data_dir / f"proxy-{digest}.mp4"
        if not path.is_file():
            raise CraftsError("Proxy has not been created.", code="not_found", status=404)
        return path

    def enqueue_export(self, body: dict) -> dict:
        with self.lock:
            clip = self._clip(body["clip_id"])
            preset = PRESETS.get(body["preset_id"])
            if preset is None:
                raise CraftsError("Unknown preset.", code="bad_preset")
            if float(body["out_seconds"]) <= float(body["in_seconds"]):
                raise CraftsError("Trim in/out must be a positive interval.", code="bad_trim")
            duration = float(clip["probe"].get("duration") or 0)
            video = (clip["probe"].get("video") or [None])[0]
            if video and video.get("frame_rate_num") and video.get("frame_rate_den"):
                frame = float(video["frame_rate_den"]) / float(video["frame_rate_num"])
            else:
                frame = 0.04
            if duration and float(body["out_seconds"]) > duration + frame:
                raise CraftsError("Trim out is past the end of the clip.", code="bad_trim")
            note = conversion_note(video.get("pix_fmt") if video else None, video.get("bit_depth") if video else None, preset)
            if note["bit_depth_conversion"] and not body.get("acknowledge_bit_depth_conversion"):
                raise CraftsError(note["message"], code="bit_depth", status=409)
            spec = spec_from_body(body)
            flags = self._encoder_flags()
            if not any(flags.get(name) for name in preset["encoder_aliases"]):
                raise CraftsError(
                    f"{preset['label']} is unavailable because this ffmpeg build has no {preset['encoder']} encoder.",
                    code="encoder_missing",
                    status=424,
                )
            if body.get("audio", True) and not flags.get(preset["audio_encoder_required"]):
                raise CraftsError(
                    f"Audio encoder {preset['audio_encoder_required']} is missing. Turn audio off or install it.",
                    code="encoder_missing",
                    status=424,
                )
            if spec.stabilization == "vidstab" and not flags.get("_vidstab"):
                raise CraftsError(
                    "Visual vidstab is not available in this ffmpeg build. It was not queued as gyro stabilization.",
                    code="filter_missing",
                )
            if spec.stabilization == "deshake" and not flags.get("_deshake"):
                raise CraftsError(
                    "deshake is not available in this ffmpeg build. It is a visual fallback, not gyro stabilization.",
                    code="filter_missing",
                )
            stem = _stem(clip["name"])
            filename = output_filename(stem, body.get("suffix") or "", preset["container"])
            destination = resolve_existing(body["destination_dir"])
            exclusive_output(destination / filename, Path(clip["path"]))
            job = {
                "id": str(uuid.uuid4()),
                "kind": "export",
                "status": "queued",
                "progress": 0.0,
                "error": None,
                "code": None,
                "result": None,
                "attempt": 1,
                "created_at": utcnow(),
                "finished_at": None,
                "request": {
                    **body,
                    "filename": filename,
                    "source_path": clip["path"],
                    "source_sha256": clip["sha256"],
                    "resolved_stabilization": {
                        "mode": spec.stabilization,
                        "smoothing": spec.stab_smoothing,
                        "crop": spec.stab_crop,
                        "zoom": spec.stab_zoom,
                        "optzoom": spec.stab_optzoom,
                        "shakiness": body.get("stab_shakiness", 5),
                        "accuracy": body.get("stab_accuracy", 15),
                        "deshake_rx": spec.deshake_rx,
                        "deshake_ry": spec.deshake_ry,
                    },
                    "conversion": note,
                },
            }
            self.project["jobs"].append(job)
            self.cancel_flags[job["id"]] = threading.Event()
            try:
                self._persist()
            except CraftsError:
                self.project["jobs"].pop()
                self.cancel_flags.pop(job["id"], None)
                raise
            self.wake.set()
            return job

    def enqueue_gyro(self, body: dict) -> dict:
        job = {
            "id": str(uuid.uuid4()),
            "kind": "gyroflow",
            "status": "queued",
            "progress": 0.0,
            "error": None,
            "code": None,
            "result": None,
            "attempt": 1,
            "created_at": utcnow(),
            "finished_at": None,
            "request": body,
        }
        with self.lock:
            self.project["jobs"].append(job)
            self.cancel_flags[job["id"]] = threading.Event()
            try:
                self._persist()
            except CraftsError:
                self.project["jobs"].pop()
                self.cancel_flags.pop(job["id"], None)
                raise
            self.wake.set()
        return job

    def enqueue_batch(self, body: dict) -> dict:
        """Queue every selected clip, or queue none and list every problem."""
        operation = body.get("operation")
        clip_ids = body.get("clip_ids")
        if operation not in {"export", "gyro", "prepare"}:
            raise CraftsError("Batch operation must be export, gyro, or prepare.", code="bad_batch")
        if not isinstance(clip_ids, list) or not clip_ids or len(clip_ids) > 40:
            raise CraftsError("A batch needs 1 to 40 clips.", code="batch_limit")
        if not all(isinstance(item, str) and item.strip() for item in clip_ids):
            raise CraftsError("Batch clip ids must be text.", code="bad_batch")
        suffix = safe_suffix(body.get("suffix") or "")
        destination = resolve_existing(body["destination_dir"])
        if not destination.is_dir():
            raise CraftsError("Destination is not a directory.", code="bad_path")
        problems: list[dict] = []
        plans: list[dict] = []
        seen: dict[str, int] = {}
        claimed: dict[str, str] = {}
        with self.lock:
            if len(self.project["jobs"]) + len(clip_ids) > 500:
                raise CraftsError("The queue cannot hold this batch.", code="batch_limit")
            reserved = self._reserved_outputs()
            if operation == "export":
                shared = body.get("export") or {}
                self._validate_shared_export(shared)
            elif operation == "gyro":
                shared = body.get("gyro") or {}
                self._validate_shared_gyro(shared)
            else:
                shared = body.get("prepare") or {}
                self._validate_shared_prepare(shared)
            for clip_id in clip_ids:
                if clip_id in seen:
                    problems.append(
                        {
                            "clip_id": clip_id,
                            "code": "duplicate",
                            "message": "This clip id is listed more than once. Nothing was queued.",
                        }
                    )
                    continue
                seen[clip_id] = len(seen)
                clip = next((item for item in self.project["clips"] if item["id"] == clip_id), None)
                if clip is None:
                    problems.append(
                        {
                            "clip_id": clip_id,
                            "code": "missing_clip",
                            "message": "This clip is not in the open project. Nothing was queued.",
                        }
                    )
                    continue
                try:
                    if operation == "export":
                        plan = self._plan_export_clip(clip, shared, destination, suffix)
                    elif operation == "gyro":
                        plan = self._plan_gyro_clip(clip, shared, destination, suffix)
                    else:
                        plan = plan_prepare_clip(clip, shared, destination, suffix)
                        plan["request"]["recipe"] = freeze_lut_paths(
                            plan["request"]["recipe"],
                            self.data_dir / "lut-snapshots",
                        )
                        sidecar = clip.get("sidecar") if isinstance(clip.get("sidecar"), dict) else {}
                        plan["request"]["sidecar_sha256"] = str(sidecar.get("sha256") or "")
                    key = str(Path(plan["output"]).resolve(strict=False))
                    if key in claimed:
                        raise CraftsError(
                            f"Output {plan['filename']} collides with another clip in this batch.",
                            code="collision",
                            status=409,
                        )
                    if key in reserved:
                        raise CraftsError(
                            f"Output {plan['filename']} is already used by a queued or running job.",
                            code="collision",
                            status=409,
                        )
                    claimed[key] = clip_id
                    plans.append(plan)
                except CraftsError as exc:
                    problems.append({"clip_id": clip_id, "code": exc.code, "message": exc.message})
            if problems:
                raise CraftsError(
                    "Nothing was queued. Every invalid, duplicate, or colliding clip is listed.",
                    code="batch_rejected",
                    status=409,
                    details=problems,
                )
            batch_id = uuid.uuid4().hex
            jobs = []
            for plan in plans:
                job = {
                    "id": str(uuid.uuid4()),
                    "batch_id": batch_id,
                    "kind": plan["kind"],
                    "status": "queued",
                    "progress": 0.0,
                    "error": None,
                    "code": None,
                    "result": None,
                    "attempt": 1,
                    "created_at": utcnow(),
                    "finished_at": None,
                    "request": plan["request"],
                }
                self.project["jobs"].append(job)
                self.cancel_flags[job["id"]] = threading.Event()
                jobs.append(job)
            try:
                self._persist()
            except CraftsError:
                queued_ids = {job["id"] for job in jobs}
                self.project["jobs"] = [job for job in self.project["jobs"] if job["id"] not in queued_ids]
                for job in jobs:
                    self.cancel_flags.pop(job["id"], None)
                raise
            self.wake.set()
            return {"batch_id": batch_id, "queued": jobs, "count": len(jobs)}

    def batch_status(self, batch_id: str) -> dict:
        _batch_id(batch_id)
        with self.lock:
            jobs = [job for job in self.project["jobs"] if job.get("batch_id") == batch_id]
        if not jobs:
            raise CraftsError("No jobs belong to that batch.", code="not_found", status=404)
        counts = {name: 0 for name in ("queued", "running", "succeeded", "failed", "cancelled", "paused", "interrupted")}
        for job in jobs:
            counts[job["status"]] = counts.get(job["status"], 0) + 1
        return {
            "batch_id": batch_id,
            "total": len(jobs),
            "queued": counts["queued"],
            "running": counts["running"],
            "succeeded": counts["succeeded"],
            "failed": counts["failed"],
            "cancelled": counts["cancelled"],
            "paused": counts["paused"],
            "interrupted": counts["interrupted"],
            "jobs": [
                {
                    "id": job["id"],
                    "clip_id": (job.get("request") or {}).get("clip_id"),
                    "status": job["status"],
                    "code": job.get("code"),
                    "error": job.get("error"),
                    "output_path": (job.get("result") or {}).get("output_path") if isinstance(job.get("result"), dict) else None,
                    "filename": (job.get("request") or {}).get("filename"),
                }
                for job in jobs
            ],
        }

    def cancel_batch(self, batch_id: str) -> dict:
        _batch_id(batch_id)
        cancelled = []
        signalled = []
        with self.lock:
            found = False
            snapshots: list[tuple[dict, dict]] = []
            for job in self.project["jobs"]:
                if job.get("batch_id") != batch_id:
                    continue
                found = True
                if job["status"] == "queued":
                    snapshots.append((job, _job_view(job)))
                    job["status"] = "cancelled"
                    job["error"] = "Cancelled before it started."
                    job["code"] = "cancelled"
                    job["finished_at"] = utcnow()
                    cancelled.append(job["id"])
                elif job["status"] == "running":
                    self.cancel_flags.setdefault(job["id"], threading.Event()).set()
                    signalled.append(job["id"])
            if not found:
                raise CraftsError("No jobs belong to that batch.", code="not_found", status=404)
            try:
                self._persist()
            except CraftsError:
                for job, snap in snapshots:
                    job.update(snap)
                raise
        return {"batch_id": batch_id, "cancelled": cancelled, "signalled": signalled}

    def retry_batch_failures(self, batch_id: str) -> dict:
        """Requeue failed jobs in this batch. Succeeded jobs stay finished and their files stay put."""
        _batch_id(batch_id)
        retried = []
        succeeded = []
        with self.lock:
            found = False
            snapshots: list[tuple[dict, dict]] = []
            for job in self.project["jobs"]:
                if job.get("batch_id") != batch_id:
                    continue
                found = True
                if job["status"] == "succeeded":
                    succeeded.append(job["id"])
                    continue
                if job["status"] != "failed":
                    continue
                snapshots.append((job, _job_view(job)))
                job["status"] = "queued"
                job["progress"] = 0.0
                job["error"] = None
                job["code"] = None
                job["result"] = None
                job["finished_at"] = None
                job["attempt"] = int(job.get("attempt") or 1) + 1
                self.cancel_flags[job["id"]] = threading.Event()
                retried.append(job["id"])
            if not found:
                raise CraftsError("No jobs belong to that batch.", code="not_found", status=404)
            if retried:
                try:
                    self._persist()
                except CraftsError:
                    for job, snap in snapshots:
                        job.update(snap)
                    raise
                self.wake.set()
        return {"batch_id": batch_id, "retried": retried, "succeeded_left_unchanged": succeeded}

    def jobs(self) -> list[dict]:
        with self.lock:
            return list(self.project["jobs"])

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self._job(job_id)
            if job["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                raise CraftsError("This job is already finished.", code="bad_state")
            flag = self.cancel_flags.setdefault(job_id, threading.Event())
            flag.set()
            snap = None
            if job["status"] == "queued":
                snap = _job_view(job)
                job["status"] = "cancelled"
                job["error"] = "Cancelled before it started."
                job["code"] = "cancelled"
                job["finished_at"] = utcnow()
            try:
                self._persist()
            except CraftsError:
                if snap is not None:
                    job.update(snap)
                raise
            return job

    def retry(self, job_id: str) -> dict:
        with self.lock:
            job = self._job(job_id)
            if job["status"] not in {"failed", "cancelled", "interrupted", "paused"}:
                raise CraftsError(
                    "Only failed, cancelled, interrupted, or paused jobs can be retried.",
                    code="bad_state",
                )
            snap = _job_view(job)
            job["status"] = "queued"
            job["progress"] = 0.0
            job["error"] = None
            job["code"] = None
            job["result"] = None
            job["finished_at"] = None
            job["attempt"] = int(job.get("attempt") or 1) + 1
            self.cancel_flags[job_id] = threading.Event()
            try:
                self._persist()
            except CraftsError:
                job.update(snap)
                raise
            self.wake.set()
            return job

    def drain(self, limit: int = 20) -> None:
        for _ in range(limit):
            job_id = self._claim()
            if job_id is None:
                return
            self._execute(job_id)

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                if self._claims_paused:
                    try:
                        with self.lock:
                            self._persist(attempts=1)
                    except CraftsError as exc:
                        self._pause_claims(exc)
                        self.wake.wait(1.0)
                        self.wake.clear()
                        continue
                job_id = self._claim()
            except Exception as exc:  # noqa: BLE001 — the queue thread must stay alive
                log.exception("Queue paused after an unexpected error")
                self._claims_paused = True
                self.persist_error = self.persist_error or str(exc)
                self.wake.wait(1.0)
                self.wake.clear()
                continue
            if job_id is None:
                self.wake.wait(0.25)
                self.wake.clear()
                continue
            self._execute(job_id)

    def _claim(self) -> str | None:
        with self.lock:
            if self._claims_paused:
                return None
            for job in self.project["jobs"]:
                if job["status"] != "queued":
                    continue
                job["status"] = "running"
                job["progress"] = 0.0
                self.cancel_flags.setdefault(job["id"], threading.Event())
                try:
                    self._persist()
                except CraftsError as exc:
                    job["status"] = "queued"
                    job["progress"] = 0.0
                    self._pause_claims(exc)
                    return None
                return job["id"]
        return None

    def _pause_claims(self, exc: CraftsError) -> None:
        if not self._claims_paused:
            log.error("Project save failed: %s", exc.message)
        self._claims_paused = True
        self.persist_error = exc.message

    def _execute(self, job_id: str) -> None:
        with self.lock:
            job = self._job(job_id)
            kind = job["kind"]
            request = dict(job["request"])
            flag = self.cancel_flags.setdefault(job_id, threading.Event())
        try:
            if flag.is_set():
                raise Cancelled("Cancelled before it started.")
            if kind == "export":
                result = self._run_export_job(request, flag, job_id)
            elif kind == "gyroflow":
                result = self._run_gyro_job(request, flag, job_id)
            elif kind == "prepare":
                result = self._run_prepare_job(request, flag, job_id)
            else:
                raise CraftsError("Unknown job kind.", code="bad_job")
        except Cancelled as exc:
            self._finish(job_id, "cancelled", error=exc.message, code="cancelled")
            return
        except CraftsError as exc:
            self._finish(job_id, "failed", error=exc.message, code=exc.code)
            return
        except Exception as exc:  # noqa: BLE001 — a job must never look successful after an unexpected error
            self._finish(job_id, "failed", error=str(exc), code="failed")
            return
        self._finish(job_id, "succeeded", result=result)

    def _run_export_job(self, request: dict, cancel: threading.Event, job_id: str) -> dict:
        ffmpeg, ffprobe = self._tools()
        with self.lock:
            clip = self._clip(request["clip_id"])
            probe = clip["probe"]
            source = Path(clip["path"])
            before = clip["sha256"]
        if source.stat().st_size != clip["size"]:
            raise CraftsError("Source size changed since import. The source was not modified by Crafts Prepare.", code="source_changed")
        spec = spec_from_body(request)
        destination = resolve_existing(request["destination_dir"])
        if not destination.is_dir():
            raise CraftsError("Destination is not a directory.", code="bad_path")

        def on_progress(value: float) -> None:
            self._progress(job_id, value)

        result = run_export(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            source=source,
            destination_dir=destination,
            filename=request["filename"],
            job_id=job_id,
            probe=probe,
            in_seconds=float(request["in_seconds"]),
            out_seconds=float(request["out_seconds"]),
            preset_id=request["preset_id"],
            spec=spec,
            audio=bool(request.get("audio", True)),
            crf=request.get("crf"),
            acknowledge_bit_depth=bool(request.get("acknowledge_bit_depth_conversion")),
            shakiness=int(request.get("stab_shakiness") or 5),
            accuracy=int(request.get("stab_accuracy") or 15),
            cancel=cancel,
            on_progress=on_progress,
            encoders=self._encoder_flags(),
        )
        from .importer import sha256_file

        after = sha256_file(source)
        if after != before:
            output = Path(result.get("output_path") or "")
            discard_owned_output(output, result.get("published_identity"), source)
            raise CraftsError(
                "Source checksum changed during export. The published file was removed only if it was still the file this job created.",
                code="source_changed",
            )
        result["source_sha256_unchanged"] = True
        return result

    def _run_prepare_job(self, request: dict, cancel: threading.Event, job_id: str) -> dict:
        ffmpeg, ffprobe = self._tools()
        with self.lock:
            clip = self._clip(request["clip_id"])
            probe = clip["probe"]
            source = Path(clip["path"])
            sidecar = clip.get("sidecar") if isinstance(clip.get("sidecar"), dict) else None
            before = clip["sha256"]
        if source.stat().st_size != clip["size"]:
            raise CraftsError("Source size changed since import. The source was not modified by Crafts Prepare.", code="source_changed")

        def on_progress(value: float) -> None:
            self._progress(job_id, value)

        return run_prepare(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            settings=self.settings,
            source=source,
            source_sha256=before,
            probe=probe,
            sidecar=sidecar,
            request=request,
            job_id=job_id,
            cancel=cancel,
            on_progress=on_progress,
            encoders=self._encoder_flags(),
        )

    def _validate_shared_prepare(self, shared: dict) -> None:
        if not isinstance(shared, dict):
            raise CraftsError("Prepare settings must be an object.", code="bad_batch")
        preset = PRESETS.get(shared.get("preset_id") or "")
        if preset is None:
            raise CraftsError("Unknown preset.", code="bad_preset")
        if "audio" in shared and not isinstance(shared.get("audio"), bool):
            raise CraftsError("Audio flag is invalid.", code="bad_batch")
        if "use_trim" in shared and not isinstance(shared.get("use_trim"), bool):
            raise CraftsError("Trim flag is invalid.", code="bad_batch")
        if shared.get("crf") is not None and (
            isinstance(shared.get("crf"), bool) or not isinstance(shared.get("crf"), int) or not 0 <= shared["crf"] <= 51
        ):
            raise CraftsError("CRF must be an integer from 0 to 51.", code="bad_adjust")
        flags = self._encoder_flags()
        if not any(flags.get(name) for name in preset["encoder_aliases"]):
            raise CraftsError(
                f"{preset['label']} is unavailable because this ffmpeg build has no {preset['encoder']} encoder.",
                code="encoder_missing",
                status=424,
            )

    def _run_gyro_job(self, request: dict, cancel: threading.Event, job_id: str) -> dict:
        _ffmpeg, ffprobe = self._tools()
        media = resolve_existing(request["media_path"]) if request.get("media_path") else None
        project = resolve_existing(request["project_path"]) if request.get("project_path") else None
        gyro = resolve_existing(request["gyro_path"]) if request.get("gyro_path") else None
        preset = resolve_existing(request["preset_path"]) if request.get("preset_path") else None
        output = Path(request["output_path"])
        if media is None and request.get("clip_id"):
            with self.lock:
                media = Path(self._clip(request["clip_id"])["path"])
        return run_gyroflow(
            settings=self.settings,
            media=media,
            project=project,
            gyro_file=gyro,
            preset=preset,
            output=output,
            ffprobe=ffprobe,
            cancel=cancel,
            on_progress=lambda value: self._progress(job_id, value),
            automatic=bool(request.get("automatic")),
            output_settings=request.get("output_settings") if request.get("automatic") else None,
        )

    def _progress(self, job_id: str, value: float) -> None:
        with self.lock:
            job = self._job(job_id)
            if job["status"] != "running":
                return
            job["progress"] = round(float(value), 4)
            now = time.monotonic()
            if now - self._last_save > 1:
                self._last_save = now
                try:
                    self._persist()
                except CraftsError as exc:
                    self.persist_error = exc.message

    def _finish(self, job_id: str, status: str, error: str | None = None, code: str | None = None, result: dict | None = None) -> None:
        with self.lock:
            try:
                job = self._job(job_id)
            except CraftsError:
                # The project changed out from under a finished worker. Do not
                # kill the queue thread, and do not invent a success.
                return
            job["status"] = status
            job["error"] = error
            job["code"] = code
            job["result"] = result
            job["finished_at"] = utcnow()
            if status == "succeeded":
                job["progress"] = 1.0
            try:
                self._persist()
            except CraftsError as exc:
                # The render already finished. Keep that result and do not run it again.
                self._pause_claims(exc)

    def _job(self, job_id: str) -> dict:
        for job in self.project["jobs"]:
            if job["id"] == job_id:
                return job
        raise CraftsError("Job not found.", code="not_found", status=404)

    def open_project(self, raw: str) -> dict:
        path = resolve_existing(raw)
        self._assert_project_path(path)
        size = path.stat().st_size
        if size == 0:
            raise CraftsError("Project file is empty.", code="bad_project")
        if size > MAX_PROJECT_BYTES:
            raise CraftsError("Project file is too large.", code="too_large")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CraftsError("Project file is not UTF-8.", code="bad_project") from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CraftsError("Project file is not JSON.", code="bad_project") from exc
        document = validate_project_document(payload)
        for job in document["jobs"]:
            if job.get("status") == "queued":
                job["status"] = "paused"
                job["error"] = "Saved jobs stay paused. Use Retry to run this one."
                job["code"] = "paused"
            elif job.get("status") == "running":
                job["status"] = "interrupted"
                job["error"] = "The app stopped while this job was running. It was not marked successful. Use Retry to run it again."
                job["code"] = "interrupted"
                job["finished_at"] = job.get("finished_at") or utcnow()
        with self.lock:
            if any(job["status"] in {"queued", "running"} for job in self.project.get("jobs") or []):
                raise CraftsError(
                    "Finish or cancel the current queue before opening another project. Nothing was switched.",
                    code="busy_project",
                    status=409,
                )
            self.project = document
            self.project_path = path
            self.cancel_flags = {job["id"]: threading.Event() for job in document["jobs"]}
        return self.project_summary()

    def save_project(self, raw: str | None) -> dict:
        with self.lock:
            previous_path = self.project_path
            if raw:
                path = Path(raw).expanduser()
                self._assert_project_path(path)
                if path.exists():
                    existing = path.resolve(strict=True)
                    self._assert_project_path(existing)
                    size = existing.stat().st_size
                    if size == 0:
                        raise CraftsError(
                            "Refusing to overwrite an empty file. Choose a new .crafts.json path.",
                            code="bad_project",
                        )
                    if size > MAX_PROJECT_BYTES:
                        raise CraftsError(
                            "Refusing to overwrite a file larger than a Crafts Prepare project.",
                            code="too_large",
                        )
                    try:
                        previous = json.loads(existing.read_text(encoding="utf-8"))
                    except json.JSONDecodeError as exc:
                        raise CraftsError(
                            "Refusing to overwrite a file that is not a Crafts Prepare project.",
                            code="bad_project",
                        ) from exc
                    validate_project_document(previous)
                else:
                    if not path.parent.exists():
                        raise CraftsError("Project directory does not exist.", code="bad_path", status=404)
                self.project_path = path
            if self.project_path is None:
                raise CraftsError("Save needs a project path ending in .crafts.json.", code="bad_project")
            try:
                self._write_project(self.project_path)
            except CraftsError:
                self.project_path = previous_path
                raise
            return self.project_summary()

    def project_summary(self) -> dict:
        with self.lock:
            return {
                "path": str(self.project_path) if self.project_path else None,
                "name": self.project.get("name"),
                "clip_count": len(self.project.get("clips") or []),
                "job_count": len(self.project.get("jobs") or []),
                "jobs": list(self.project.get("jobs") or []),
                "clips": [public_clip(clip) for clip in self.project.get("clips") or []],
            }

    def rename_project(self, name: str) -> dict:
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise CraftsError("Project name must be 1–120 characters.", code="bad_name")
        with self.lock:
            previous = self.project["name"]
            self.project["name"] = name.strip()
            try:
                self._persist()
            except CraftsError:
                self.project["name"] = previous
                raise
        return self.project_summary()

    def parse_gcsv(self, raw: str, cancel=None) -> dict:
        from .errors import Cancelled

        path = resolve_existing(raw)
        if path.stat().st_size > 64 * 1024 * 1024:
            raise CraftsError("GCSV file exceeds the parse limit.", code="bad_gcsv")
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        return parse_gcsv_file(path, cancel=cancel).summary()

    def inspect_telemetry(self, clip_id: str, cancel=None) -> dict:
        binary = discover_executable(self.settings.get("telemetry_parser") or "")
        if not binary:
            raise CraftsError(
                "The optional crafts-telemetry helper is not configured. Build tools/telemetry-inspect "
                "and set its path in Settings. Missing telemetry is not reported as a successful gyro read.",
                code="telemetry_missing",
                status=424,
            )
        with self.lock:
            source = Path(self._clip(clip_id)["path"])
        code, stdout, stderr = run_captured([binary, str(source)], timeout=180, cancel=cancel)
        if stdout.strip().startswith("{"):
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise CraftsError("Telemetry helper returned invalid JSON.", code="telemetry_failed") from exc
            payload["exit_code"] = code
            return payload
        if code != 0:
            return {
                "parsed": False,
                "exit_code": code,
                "detail": (stderr or stdout)[-1500:],
                "disclaimer": (
                    "telemetry-parser did not accept this file. That is not proof the file lacks motion metadata, "
                    "and it is not a camera-support claim."
                ),
            }
        raise CraftsError("Telemetry helper returned no JSON.", code="telemetry_failed")

    def _persist(self, *, attempts: int | None = None) -> None:
        if self.project_path is not None:
            self._write_project(self.project_path, attempts=attempts)

    def _write_project(self, path: Path, *, attempts: int | None = None) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(self.project, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            _replace_durable(temporary, path, _REPLACE_ATTEMPTS if attempts is None else attempts)
        except CraftsError:
            _discard_owned_temp(temporary)
            raise
        except OSError as exc:
            _discard_owned_temp(temporary)
            raise CraftsError(_PROJECT_LOCK_MESSAGE, code="project_locked") from exc
        self._note_project_saved()

    def _note_project_saved(self) -> None:
        paused = self._claims_paused
        self._claims_paused = False
        self.persist_error = None
        if paused:
            self.wake.set()

    def _assert_project_path(self, path: Path) -> None:
        if not path.name.endswith(".crafts.json"):
            raise CraftsError("Project files must use the .crafts.json extension.", code="bad_project")
        for clip in self.project.get("clips") or []:
            if Path(clip["path"]).resolve(strict=False) == path.resolve(strict=False):
                raise CraftsError("Refusing to use a source clip as the project file.", code="source_overwrite")


def _finite_number(label: str, value, *, code: str = "bad_trim") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CraftsError(f"{label} must be a number.", code=code)
    number = float(value)
    if not math.isfinite(number):
        raise CraftsError(f"{label} must be a finite number.", code=code)
    return number


def validate_project_document(payload: object) -> dict:
    """Return a checked project. Raise before any live state is replaced."""
    if not isinstance(payload, dict):
        raise CraftsError("Project file must be a JSON object.", code="bad_project")
    if payload.get("schema") != SCHEMA:
        raise CraftsError("This file is not a Crafts Prepare project.", code="bad_project")
    if payload.get("version") != VERSION:
        raise CraftsError("Unsupported project version.", code="bad_project")
    name = payload.get("name", "Untitled")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise CraftsError("Project name is invalid.", code="bad_project")
    clips = payload.get("clips")
    jobs = payload.get("jobs")
    if not isinstance(clips, list) or not isinstance(jobs, list):
        raise CraftsError("Project clips and jobs must be lists.", code="bad_project")
    if len(clips) > 500 or len(jobs) > 500:
        raise CraftsError("Project has too many clips or jobs.", code="bad_project")
    seen_clips: set[str] = set()
    seen_jobs: set[str] = set()
    cleaned_clips = [_validate_stored_clip(clip, seen_clips) for clip in clips]
    cleaned_jobs = [_validate_stored_job(job, seen_jobs, seen_clips) for job in jobs]
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "name": name.strip(),
        "clips": cleaned_clips,
        "jobs": cleaned_jobs,
    }


def _validate_stored_clip(clip: object, seen: set[str]) -> dict:
    if not isinstance(clip, dict):
        raise CraftsError("Each clip must be an object.", code="bad_project")
    clip_id = clip.get("id")
    if not isinstance(clip_id, str) or not clip_id or len(clip_id) > 80 or clip_id in seen:
        raise CraftsError("Clip id is missing or duplicated.", code="bad_project")
    seen.add(clip_id)
    path = clip.get("path")
    if not isinstance(path, str) or not path or len(path) > 4096 or "\x00" in path:
        raise CraftsError("Clip path is invalid.", code="bad_project")
    digest = clip.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise CraftsError("Clip identity is invalid.", code="bad_project")
    if not isinstance(clip.get("name"), str) or not clip["name"]:
        raise CraftsError("Clip name is invalid.", code="bad_project")
    size = clip.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise CraftsError("Clip size is invalid.", code="bad_project")
    probe = clip.get("probe")
    if not isinstance(probe, dict):
        raise CraftsError("Clip probe data is invalid.", code="bad_project")
    _validate_probe(probe)
    if "flag" in clip and not isinstance(clip.get("flag"), str):
        raise CraftsError("Clip flag is invalid.", code="bad_project")
    flag = clip.get("flag") or ""
    if flag not in {"", "OK", "KEEP", "NG"}:
        raise CraftsError("Clip flag is invalid.", code="bad_project")
    if "notes" in clip and not isinstance(clip.get("notes"), str):
        raise CraftsError("Clip notes are invalid.", code="bad_project")
    notes = clip.get("notes") or ""
    if len(notes) > 8000:
        raise CraftsError("Clip notes are invalid.", code="bad_project")
    markers = _validate_markers(clip.get("markers") or [], probe.get("duration"))
    trim_in = _finite_number("Trim in", clip.get("trim_in", 0), code="bad_project")
    trim_out = _finite_number("Trim out", clip.get("trim_out", 0), code="bad_project")
    _validate_trim(trim_in, trim_out, probe)
    checked = dict(clip)
    checked["flag"] = flag
    checked["notes"] = notes
    checked["markers"] = markers
    checked["trim_in"] = trim_in
    checked["trim_out"] = trim_out
    if isinstance(clip.get("recipe"), dict):
        from .recipe import assert_stored_recipe

        checked["recipe"] = assert_stored_recipe(clip["recipe"])
    return checked


def _validate_probe(probe: dict) -> None:
    duration = probe.get("duration")
    if duration is not None:
        _finite_number("Duration", duration, code="bad_project")
        if not 0 <= float(duration) <= 86400 * 48:
            raise CraftsError("Clip duration is out of range.", code="bad_project")
    for key in ("video", "audio", "data"):
        if key not in probe or probe[key] is None or not isinstance(probe[key], list):
            raise CraftsError(f"Clip probe is missing a {key} stream list.", code="bad_project")
        for entry in probe[key]:
            if not isinstance(entry, dict):
                raise CraftsError(f"Clip {key} stream entries must be objects.", code="bad_project")
            _validate_stream(entry, kind=key)
    if not probe["video"]:
        raise CraftsError("Clip probe has no video stream.", code="bad_project")
    if "tags" in probe and probe["tags"] is not None and not isinstance(probe["tags"], dict):
        raise CraftsError("Clip tags must be an object.", code="bad_project")
    timecode = probe.get("timecode")
    if timecode is not None and (not isinstance(timecode, str) or len(timecode) > 64):
        raise CraftsError("Clip timecode is invalid.", code="bad_project")


def _validate_stream(entry: dict, *, kind: str) -> None:
    for text_key in ("codec", "profile", "pix_fmt", "frame_rate", "codec_tag", "color_space", "color_transfer", "color_primaries", "color_range"):
        if text_key not in entry or entry[text_key] is None:
            continue
        if not isinstance(entry[text_key], str) or len(entry[text_key]) > 80:
            raise CraftsError("Stream text fields must be text.", code="bad_project")
    if kind == "video":
        for axis in ("width", "height"):
            value = entry.get(axis)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16384:
                raise CraftsError("A video stream needs integer width and height.", code="bad_project")
    for axis in ("width", "height"):
        if axis not in entry or entry[axis] is None:
            continue
        value = entry[axis]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16384:
            raise CraftsError("Stream dimensions are invalid.", code="bad_project")
    depth = entry.get("bit_depth")
    if depth is not None and (isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 32):
        raise CraftsError("Stream bit depth is invalid.", code="bad_project")
    rate = entry.get("frame_rate")
    if rate is not None:
        if not isinstance(rate, str) or not re.fullmatch(r"[0-9]+/[0-9]+", rate):
            raise CraftsError("Stream frame rate is invalid.", code="bad_project")
        num_s, den_s = rate.split("/", 1)
        if int(num_s) <= 0 or int(den_s) <= 0:
            raise CraftsError("Stream frame rate is invalid.", code="bad_project")
    for key in ("frame_rate_num", "frame_rate_den"):
        if entry.get(key) is None:
            continue
        value = entry[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 1_000_000_000:
            raise CraftsError("Stream frame rate is invalid.", code="bad_project")
    if kind == "audio":
        for key, limit in (("sample_rate", 384000), ("channels", 64)):
            if entry.get(key) is None:
                continue
            value = entry[key]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= limit:
                raise CraftsError("Audio stream fields are invalid.", code="bad_project")


def _validate_markers(markers: object, duration) -> list:
    if not isinstance(markers, list) or len(markers) > 200:
        raise CraftsError("Clip markers are invalid.", code="bad_project")
    cleaned = []
    for item in markers:
        if not isinstance(item, dict):
            raise CraftsError("Each marker must be an object with seconds and a label.", code="bad_project")
        if "seconds" not in item:
            raise CraftsError("Each marker needs seconds.", code="bad_project")
        seconds = _finite_number("Marker time", item.get("seconds"), code="bad_project")
        if seconds < 0 or seconds > 86400 * 12:
            raise CraftsError("Marker time is out of range.", code="bad_project")
        if duration is not None and seconds > float(duration) + 0.05:
            raise CraftsError("Marker time is past the end of the clip.", code="bad_project")
        label = item.get("label", "")
        if not isinstance(label, str) or len(label) > 200:
            raise CraftsError("Marker labels must be text of at most 200 characters.", code="bad_project")
        marker_id = item.get("id")
        if marker_id is not None and (not isinstance(marker_id, str) or not marker_id or len(marker_id) > 80):
            raise CraftsError("Marker id is invalid.", code="bad_project")
        cleaned.append({"id": marker_id or str(uuid.uuid4()), "seconds": seconds, "label": label})
    return cleaned


def _validate_trim(trim_in: float, trim_out: float, probe: dict) -> None:
    if trim_in < 0 or trim_out < 0 or trim_out < trim_in:
        raise CraftsError("Clip trim is invalid.", code="bad_project")
    duration = probe.get("duration")
    if duration is None:
        if trim_out == trim_in and trim_in != 0:
            raise CraftsError("Clip trim is invalid.", code="bad_project")
        return
    duration = float(duration)
    if trim_out == trim_in and not (trim_in == 0 and duration == 0):
        raise CraftsError("Clip trim is invalid.", code="bad_project")
    slack = 0.05
    video = (probe.get("video") or [None])[0]
    if isinstance(video, dict) and video.get("frame_rate_num") and video.get("frame_rate_den"):
        slack = float(video["frame_rate_den"]) / float(video["frame_rate_num"])
    if trim_out > duration + slack:
        raise CraftsError("Clip trim is past the end of the clip.", code="bad_project")


def _validate_stored_job(job: object, seen: set[str], clip_ids: set[str]) -> dict:
    if not isinstance(job, dict):
        raise CraftsError("Each job must be an object.", code="bad_project")
    job_id = job.get("id")
    if not isinstance(job_id, str) or not job_id or len(job_id) > 80 or job_id in seen:
        raise CraftsError("Job id is missing or duplicated.", code="bad_project")
    seen.add(job_id)
    kind = job.get("kind")
    if not isinstance(kind, str) or kind not in {"export", "gyroflow", "prepare"}:
        raise CraftsError("Job kind is invalid.", code="bad_project")
    status = job.get("status")
    if not isinstance(status, str) or status not in {"queued", "running", "paused", "succeeded", "failed", "cancelled", "interrupted"}:
        raise CraftsError("Job status is invalid. It was not queued.", code="bad_project")
    request = job.get("request")
    if not isinstance(request, dict):
        raise CraftsError("Job request is invalid.", code="bad_project")
    if kind == "export":
        _validate_export_request(request, clip_ids)
    elif kind == "gyroflow":
        _validate_gyro_request(request, clip_ids)
    elif kind == "prepare":
        _validate_prepare_request(request, clip_ids)
    else:
        raise CraftsError("Job kind is invalid.", code="bad_project")
    if "attempt" in job and job["attempt"] is not None:
        attempt = job["attempt"]
        if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 1000:
            raise CraftsError("Job attempt is invalid.", code="bad_project")
    progress = _finite_number("Job progress", job.get("progress", 0), code="bad_project")
    if not 0 <= progress <= 1:
        raise CraftsError("Job progress is out of range.", code="bad_project")
    for key in ("error", "code"):
        if job.get(key) is not None and not isinstance(job.get(key), str):
            raise CraftsError("Job error text is invalid.", code="bad_project")
    if job.get("batch_id") is not None:
        _batch_id(job.get("batch_id"))
    if job.get("result") is not None:
        _validate_job_result(job.get("result"))
    filename = request.get("filename")
    if filename is not None and not isinstance(filename, str):
        raise CraftsError("Job filename must be text.", code="bad_project")
    return dict(job)


def _validate_job_result(result: object) -> None:
    if not isinstance(result, dict):
        raise CraftsError("Job result is invalid.", code="bad_project")
    output_path = result.get("output_path")
    if output_path is not None and not isinstance(output_path, str):
        raise CraftsError("Job output path must be text.", code="bad_project")
    conversion = result.get("conversion")
    if conversion is not None:
        if not isinstance(conversion, dict) or not isinstance(conversion.get("message"), str):
            raise CraftsError("Job conversion message must be text.", code="bad_project")
    stabilization = result.get("stabilization")
    if stabilization is not None:
        if not isinstance(stabilization, dict):
            raise CraftsError("Job stabilization record is invalid.", code="bad_project")
        label = stabilization.get("label")
        if label is not None and not isinstance(label, str):
            raise CraftsError("Job stabilization label must be text.", code="bad_project")
        for key in ("mode", "kind"):
            if key in stabilization and not isinstance(stabilization.get(key), str):
                raise CraftsError("Job stabilization fields must be text.", code="bad_project")


def _validate_export_request(request: dict, clip_ids: set[str]) -> None:
    if not request:
        raise CraftsError("Export job request is empty and cannot be resumed.", code="bad_project")
    clip_id = request.get("clip_id")
    if not isinstance(clip_id, str) or clip_id not in clip_ids:
        raise CraftsError("Export job does not reference a clip in this project.", code="bad_project")
    preset_id = request.get("preset_id")
    if not isinstance(preset_id, str) or preset_id not in PRESETS:
        raise CraftsError("Export job preset is invalid.", code="bad_project")
    trim_in = _finite_number("Export in", request.get("in_seconds"), code="bad_project")
    trim_out = _finite_number("Export out", request.get("out_seconds"), code="bad_project")
    if trim_in < 0 or trim_out <= trim_in or trim_out > 86400 * 48:
        raise CraftsError("Export trim is invalid.", code="bad_project")
    destination = request.get("destination_dir")
    if not isinstance(destination, str) or not destination.strip() or len(destination) > 4096 or "\x00" in destination or "://" in destination:
        raise CraftsError("Export destination is invalid.", code="bad_project")
    filename = request.get("filename")
    if not isinstance(filename, str):
        raise CraftsError("Export filename is missing.", code="bad_project")
    safe_filename(filename)
    if "suffix" in request and request["suffix"] is not None:
        safe_suffix(request["suffix"])
    if not isinstance(request.get("audio"), bool):
        raise CraftsError("Export audio flag is invalid.", code="bad_project")
    for key in ("acknowledge_bit_depth_conversion", "use_vidstab_auto"):
        if key in request and not isinstance(request.get(key), bool):
            raise CraftsError("Export request flags are invalid.", code="bad_project")
    if "exposure" in request and request["exposure"] is not None:
        exposure = _finite_number("Exposure", request["exposure"], code="bad_project")
        if not -1 <= exposure <= 1:
            raise CraftsError("Export exposure is out of range.", code="bad_project")
    if "saturation" in request and request["saturation"] is not None:
        saturation = _finite_number("Saturation", request["saturation"], code="bad_project")
        if not 0 <= saturation <= 3:
            raise CraftsError("Export saturation is out of range.", code="bad_project")
    if request.get("crf") is not None and (
        isinstance(request.get("crf"), bool) or not isinstance(request.get("crf"), int) or not 0 <= request["crf"] <= 51
    ):
        raise CraftsError("Export CRF is invalid.", code="bad_project")


def _validate_prepare_request(request: dict, clip_ids: set[str]) -> None:
    if not request:
        raise CraftsError("Prepare job request is empty and cannot be resumed.", code="bad_project")
    clip_id = request.get("clip_id")
    if not isinstance(clip_id, str) or clip_id not in clip_ids:
        raise CraftsError("Prepare job does not reference a clip in this project.", code="bad_project")
    preset_id = request.get("preset_id")
    if not isinstance(preset_id, str) or preset_id not in PRESETS:
        raise CraftsError("Prepare job preset is invalid.", code="bad_project")
    trim_in = _finite_number("Prepare in", request.get("in_seconds"), code="bad_project")
    trim_out = _finite_number("Prepare out", request.get("out_seconds"), code="bad_project")
    if trim_in < 0 or trim_out <= trim_in or trim_out > 86400 * 48:
        raise CraftsError("Prepare trim is invalid.", code="bad_project")
    destination = request.get("destination_dir")
    if not isinstance(destination, str) or not destination.strip() or len(destination) > 4096 or "\x00" in destination or "://" in destination:
        raise CraftsError("Prepare destination is invalid.", code="bad_project")
    filename = request.get("filename")
    if not isinstance(filename, str):
        raise CraftsError("Prepare filename is missing.", code="bad_project")
    safe_filename(filename)
    if not isinstance(request.get("audio"), bool):
        raise CraftsError("Prepare audio flag is invalid.", code="bad_project")
    if not isinstance(request.get("gyro_enabled"), bool):
        raise CraftsError("Prepare gyro flag is invalid.", code="bad_project")
    recipe = request.get("recipe")
    if not isinstance(recipe, dict):
        raise CraftsError("Prepare recipe snapshot is missing.", code="bad_project")
    if recipe.get("profile") not in {"auto", "slog3_sgamut3cine", "hlg_bt2020", "preserve"}:
        raise CraftsError("Prepare recipe profile is invalid.", code="bad_project")
    profile = request.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("pipeline"), str):
        raise CraftsError("Prepare profile snapshot is missing.", code="bad_project")
    for key in ("input_lut", "look_lut"):
        if recipe.get(key) not in (None, "") and not isinstance(recipe.get(key), str):
            raise CraftsError("Prepare LUT path is invalid.", code="bad_project")
    from .recipe import assert_stored_recipe

    assert_stored_recipe(recipe)


def _validate_gyro_request(request: dict, clip_ids: set[str]) -> None:
    output = request.get("output_path")
    if not isinstance(output, str) or not output.strip() or len(output) > 4096 or "\x00" in output or "://" in output:
        raise CraftsError("Gyroflow output path is missing.", code="bad_project")
    if Path(output).suffix.lower() not in {".mp4", ".mov", ".mxf", ".mkv", ".m2ts", ".mts"}:
        raise CraftsError("Gyroflow output path needs a media extension.", code="bad_project")
    clip_id = request.get("clip_id")
    if clip_id is not None and (not isinstance(clip_id, str) or clip_id not in clip_ids):
        raise CraftsError("Gyroflow job does not reference a clip in this project.", code="bad_project")
    for key in ("media_path", "project_path", "gyro_path", "preset_path"):
        value = request.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 4096 or "\x00" in value or "://" in value:
            raise CraftsError("Gyroflow input path is invalid.", code="bad_project")
    if "automatic" in request and not isinstance(request.get("automatic"), bool):
        raise CraftsError("Automatic gyro flag is invalid.", code="bad_project")
    if request.get("automatic") is True:
        settings = request.get("output_settings")
        if not isinstance(settings, dict):
            raise CraftsError("Automatic gyro output settings are missing.", code="bad_project")
        gyro_format(str(settings.get("format") or ""))
        if not isinstance(request.get("media_path"), str):
            raise CraftsError("Automatic gyro job is missing its media path.", code="bad_project")
        if not isinstance(settings.get("audio", True), bool):
            raise CraftsError("Automatic gyro audio flag is invalid.", code="bad_project")


def _batch_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise CraftsError("Batch id is invalid.", code="bad_batch")
    return value


def new_project() -> dict:
    return {"schema": SCHEMA, "version": VERSION, "name": "Untitled", "clips": [], "jobs": []}


def public_clip(clip: dict) -> dict:
    probe = clip.get("probe") if isinstance(clip.get("probe"), dict) else {}
    videos = probe.get("video") if isinstance(probe.get("video"), list) else []
    video = videos[0] if videos and isinstance(videos[0], dict) else {}
    audios = probe.get("audio") if isinstance(probe.get("audio"), list) else []
    data = probe.get("data") if isinstance(probe.get("data"), list) else []
    return {
        "id": clip["id"],
        "path": clip["path"],
        "name": clip["name"],
        "sha256": clip["sha256"],
        "size": clip["size"],
        "flag": clip.get("flag") or "",
        "notes": clip.get("notes") or "",
        "markers": clip.get("markers") or [],
        "trim_in": clip.get("trim_in") or 0,
        "trim_out": clip.get("trim_out") or 0,
        "browser_playable": browser_playable(probe),
        "probe": probe,
        "recipe": clip.get("recipe") if isinstance(clip.get("recipe"), dict) else None,
        "sidecar": clip.get("sidecar") if isinstance(clip.get("sidecar"), dict) else {"status": "missing", "message": "No sidecar was stored."},
        "summary": {
            "codec": video.get("codec") if isinstance(video.get("codec"), str) else None,
            "width": video.get("width") if isinstance(video.get("width"), int) else None,
            "height": video.get("height") if isinstance(video.get("height"), int) else None,
            "pix_fmt": video.get("pix_fmt") if isinstance(video.get("pix_fmt"), str) else None,
            "bit_depth": video.get("bit_depth") if isinstance(video.get("bit_depth"), int) else None,
            "frame_rate": video.get("frame_rate") if isinstance(video.get("frame_rate"), str) else None,
            "duration": probe.get("duration") if isinstance(probe.get("duration"), (int, float)) else None,
            "timecode": probe.get("timecode") if isinstance(probe.get("timecode"), str) else None,
            "audio_codecs": [item.get("codec") if isinstance(item, dict) and isinstance(item.get("codec"), str) else None for item in audios],
            "data_streams": len(data),
        },
    }


def spec_from_body(body: dict) -> FilterSpec:
    stabilization = body.get("stabilization") or "off"
    smoothing = int(body.get("stab_smoothing") if body.get("stab_smoothing") is not None else 10)
    crop = body.get("stab_crop") or "black"
    zoom = int(body.get("stab_zoom") or 0)
    optzoom = int(body.get("stab_optzoom") or 0)
    shakiness = int(body.get("stab_shakiness") if body.get("stab_shakiness") is not None else 5)
    accuracy = int(body.get("stab_accuracy") if body.get("stab_accuracy") is not None else 15)
    if body.get("use_vidstab_auto") and stabilization == "vidstab":
        smoothing = VIDSTAB_AUTO["smoothing"]
        crop = VIDSTAB_AUTO["crop"]
        zoom = VIDSTAB_AUTO["zoom"]
        optzoom = VIDSTAB_AUTO["optzoom"]
        body["stab_shakiness"] = VIDSTAB_AUTO["shakiness"]
        body["stab_accuracy"] = VIDSTAB_AUTO["accuracy"]
        body["stab_smoothing"] = smoothing
        body["stab_crop"] = crop
        body["stab_zoom"] = zoom
        body["stab_optzoom"] = optzoom
    else:
        body["stab_shakiness"] = shakiness
        body["stab_accuracy"] = accuracy
    crf = body.get("crf")
    if crf is not None and (not isinstance(crf, int) or isinstance(crf, bool) or crf < 0 or crf > 51):
        raise CraftsError("CRF must be an integer from 0 to 51.", code="bad_adjust")
    spec = FilterSpec(
        exposure=float(body.get("exposure") or 0),
        saturation=float(body.get("saturation") if body.get("saturation") is not None else 1),
        lut_path=body.get("lut_path") or None,
        rotate=str(body.get("rotate") or "0"),
        fit=str(body.get("fit") or "none"),
        width=body.get("width"),
        height=body.get("height"),
        stabilization=stabilization,
        stab_smoothing=smoothing,
        stab_crop=crop,
        stab_zoom=zoom,
        stab_optzoom=optzoom,
        deshake_rx=int(body.get("deshake_rx") if body.get("deshake_rx") is not None else 16),
        deshake_ry=int(body.get("deshake_ry") if body.get("deshake_ry") is not None else 16),
    )
    validate_adjustment(spec)
    return spec


def _markers(value) -> list[dict]:
    if not isinstance(value, list) or len(value) > 200:
        raise CraftsError("Markers must be a list of at most 200 items.", code="bad_notes")
    cleaned = []
    for item in value:
        if not isinstance(item, dict):
            raise CraftsError("Each marker needs seconds and a label.", code="bad_notes")
        seconds = float(item.get("seconds"))
        label = item.get("label") or ""
        if seconds < 0 or seconds > 86400 * 12:
            raise CraftsError("Marker time is out of range.", code="bad_notes")
        if not isinstance(label, str) or len(label) > 200:
            raise CraftsError("Marker labels must be 200 characters or fewer.", code="bad_notes")
        cleaned.append({"id": str(item.get("id") or uuid.uuid4()), "seconds": seconds, "label": label})
    return cleaned


def _stem(name: str) -> str:
    stem = Path(name).stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._-")
    cleaned = cleaned[:80] or "clip"
    return safe_filename(cleaned)
