"""In-memory project, persisted JSON, and the sequential job queue."""

from __future__ import annotations

import json
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
from .filters import VIDSTAB_AUTO, FilterSpec, validate_adjustment
from .gcsv import parse_gcsv_file
from .gyroflow_cli import run_gyroflow
from .importer import import_path
from .presets import PRESETS, conversion_note, preset_availability
from .preview import browser_playable, extract_thumbnail, make_proxy
from .probe import discover_executable
from .process import run_captured
from .security import MAX_PROJECT_BYTES, exclusive_output, resolve_existing, safe_filename, same_file

SCHEMA = "crafts-prepare.project"
VERSION = 1


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_settings_path() -> Path:
    override = os.environ.get("CRAFTS_SETTINGS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".crafts-prepare" / "settings.json"


def default_data_dir() -> Path:
    override = os.environ.get("CRAFTS_DATA_DIR")
    if override:
        return Path(override)
    return Path.home() / ".crafts-prepare" / "cache"


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

    def start(self) -> None:
        if os.environ.get("CRAFTS_DISABLE_WORKER") == "1":
            return
        if self.worker and self.worker.is_alive():
            return
        self.stop.clear()
        self.worker = threading.Thread(target=self._loop, name="crafts-queue", daemon=True)
        self.worker.start()

    def shutdown(self) -> None:
        self.stop.set()
        self.wake.set()
        for flag in self.cancel_flags.values():
            flag.set()

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

    def capabilities(self) -> dict:
        if self._caps_cache is not None:
            return self._caps_cache
        report = inspect_tools(self.settings)
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

    def import_media(self, raw: str) -> dict:
        _ffmpeg, ffprobe = self._tools()
        result = import_path(raw, ffprobe)
        added = []
        duplicates = []
        with self.lock:
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

    def update_clip(self, clip_id: str, patch: dict) -> dict:
        with self.lock:
            clip = self._clip(clip_id)
            if "flag" in patch and patch["flag"] is not None:
                if patch["flag"] not in {"", "OK", "KEEP", "NG"}:
                    raise CraftsError("Flag must be OK, KEEP, NG, or empty.", code="bad_flag")
                clip["flag"] = patch["flag"]
            if "notes" in patch and patch["notes"] is not None:
                notes = patch["notes"]
                if not isinstance(notes, str) or len(notes) > 8000:
                    raise CraftsError("Notes must be text up to 8000 characters.", code="bad_notes")
                clip["notes"] = notes
            if "markers" in patch and patch["markers"] is not None:
                clip["markers"] = _markers(patch["markers"])
            duration = float(clip["probe"].get("duration") or 0)
            if "trim_in" in patch and patch["trim_in"] is not None:
                clip["trim_in"] = float(patch["trim_in"])
            if "trim_out" in patch and patch["trim_out"] is not None:
                clip["trim_out"] = float(patch["trim_out"])
            if clip["trim_in"] < 0 or clip["trim_out"] <= clip["trim_in"]:
                raise CraftsError("Trim in/out must be a positive interval.", code="bad_trim")
            if duration and clip["trim_out"] > duration + 0.05:
                raise CraftsError("Trim out is past the end of the clip.", code="bad_trim")
            self._persist()
            return public_clip(clip)

    def thumbnail(self, clip_id: str, seconds: float, spec: FilterSpec) -> Path:
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
        )

    def stream_path(self, clip_id: str) -> Path:
        with self.lock:
            clip = self._clip(clip_id)
            return Path(clip["path"])

    def proxy(self, clip_id: str) -> dict:
        ffmpeg, _ffprobe = self._tools()
        with self.lock:
            clip = self._clip(clip_id)
            source = Path(clip["path"])
            digest = clip["sha256"]
            duration = float(clip["probe"].get("duration") or 0)
            playable = browser_playable(clip["probe"])
        if playable:
            return {
                "kind": "source",
                "url": f"/api/media/{clip_id}/file",
                "note": "Browser playback uses the source file. Adjustments are not baked into this player.",
            }
        destination = self.data_dir / f"proxy-{digest}.mp4"
        if not destination.exists():
            make_proxy(ffmpeg=ffmpeg, source=source, destination=destination, duration=duration)
        return {
            "kind": "proxy",
            "url": f"/api/media/{clip_id}/proxy-file",
            "note": (
                "This is a 640px H.264 viewing proxy. The source file was not modified. "
                "Adjustments are shown on the still preview, which uses the same filter graph as export, "
                "except temporal stabilization (vidstab/deshake) which is applied only during export."
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
            self._persist()
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
            self._persist()
            self.wake.set()
        return job

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
            if job["status"] == "queued":
                job["status"] = "cancelled"
                job["error"] = "Cancelled before it started."
                job["code"] = "cancelled"
                job["finished_at"] = utcnow()
                self._persist()
            return job

    def retry(self, job_id: str) -> dict:
        with self.lock:
            job = self._job(job_id)
            if job["status"] not in {"failed", "cancelled", "interrupted"}:
                raise CraftsError("Only failed, cancelled, or interrupted jobs can be retried.", code="bad_state")
            job["status"] = "queued"
            job["progress"] = 0.0
            job["error"] = None
            job["code"] = None
            job["result"] = None
            job["finished_at"] = None
            job["attempt"] = int(job.get("attempt") or 1) + 1
            self.cancel_flags[job_id] = threading.Event()
            self._persist()
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
            job_id = self._claim()
            if job_id is None:
                self.wake.wait(0.25)
                self.wake.clear()
                continue
            self._execute(job_id)

    def _claim(self) -> str | None:
        with self.lock:
            for job in self.project["jobs"]:
                if job["status"] == "queued":
                    job["status"] = "running"
                    job["progress"] = 0.0
                    self.cancel_flags.setdefault(job["id"], threading.Event())
                    self._persist()
                    return job["id"]
        return None

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
            if output.exists() and not same_file(output, source):
                output.unlink()
            raise CraftsError(
                "Source checksum changed during export. Any published output was removed.",
                code="source_changed",
            )
        result["source_sha256_unchanged"] = True
        return result

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
                self._persist()

    def _finish(self, job_id: str, status: str, error: str | None = None, code: str | None = None, result: dict | None = None) -> None:
        with self.lock:
            job = self._job(job_id)
            job["status"] = status
            job["error"] = error
            job["code"] = code
            job["result"] = result
            job["finished_at"] = utcnow()
            if status == "succeeded":
                job["progress"] = 1.0
            self._persist()

    def _job(self, job_id: str) -> dict:
        for job in self.project["jobs"]:
            if job["id"] == job_id:
                return job
        raise CraftsError("Job not found.", code="not_found", status=404)

    def open_project(self, raw: str) -> dict:
        path = resolve_existing(raw)
        self._assert_project_path(path)
        if path.stat().st_size > MAX_PROJECT_BYTES:
            raise CraftsError("Project file is too large.", code="too_large")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CraftsError("Project file is not JSON.", code="bad_project") from exc
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise CraftsError("This file is not a Crafts Prepare project.", code="bad_project")
        if payload.get("version") != VERSION:
            raise CraftsError("Unsupported project version.", code="bad_project")
        for job in payload.get("jobs") or []:
            if job.get("status") == "running":
                job["status"] = "interrupted"
                job["error"] = "The app stopped while this job was running. It was not marked successful."
                job["code"] = "interrupted"
                job["finished_at"] = job.get("finished_at") or utcnow()
        with self.lock:
            self.project = payload
            self.project_path = path
            self.cancel_flags = {job["id"]: threading.Event() for job in self.project.get("jobs") or []}
        return self.project_summary()

    def save_project(self, raw: str | None) -> dict:
        with self.lock:
            if raw:
                path = Path(raw).expanduser()
                self._assert_project_path(path)
                if path.exists():
                    existing = path.resolve(strict=True)
                    self._assert_project_path(existing)
                    if existing.stat().st_size and existing.stat().st_size < MAX_PROJECT_BYTES:
                        try:
                            previous = json.loads(existing.read_text(encoding="utf-8"))
                        except json.JSONDecodeError as exc:
                            raise CraftsError(
                                "Refusing to overwrite a file that is not a Crafts Prepare project.",
                                code="bad_project",
                            ) from exc
                        if previous.get("schema") != SCHEMA:
                            raise CraftsError(
                                "Refusing to overwrite a file that is not a Crafts Prepare project.",
                                code="bad_project",
                            )
                else:
                    if not path.parent.exists():
                        raise CraftsError("Project directory does not exist.", code="bad_path", status=404)
                self.project_path = path
            if self.project_path is None:
                raise CraftsError("Save needs a project path ending in .crafts.json.", code="bad_project")
            self._write_project(self.project_path)
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
            self.project["name"] = name.strip()
            self._persist()
        return self.project_summary()

    def parse_gcsv(self, raw: str) -> dict:
        path = resolve_existing(raw)
        if path.stat().st_size > 64 * 1024 * 1024:
            raise CraftsError("GCSV file exceeds the parse limit.", code="bad_gcsv")
        return parse_gcsv_file(path).summary()

    def inspect_telemetry(self, clip_id: str) -> dict:
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
        code, stdout, stderr = run_captured([binary, str(source)], timeout=180)
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

    def _persist(self) -> None:
        if self.project_path is not None:
            self._write_project(self.project_path)

    def _write_project(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=False) if False else None
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(self.project, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def _assert_project_path(self, path: Path) -> None:
        if not path.name.endswith(".crafts.json"):
            raise CraftsError("Project files must use the .crafts.json extension.", code="bad_project")
        for clip in self.project.get("clips") or []:
            if Path(clip["path"]).resolve(strict=False) == path.resolve(strict=False):
                raise CraftsError("Refusing to use a source clip as the project file.", code="source_overwrite")


def new_project() -> dict:
    return {"schema": SCHEMA, "version": VERSION, "name": "Untitled", "clips": [], "jobs": []}


def public_clip(clip: dict) -> dict:
    video = (clip["probe"].get("video") or [{}])[0]
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
        "browser_playable": browser_playable(clip["probe"]),
        "probe": clip["probe"],
        "summary": {
            "codec": video.get("codec"),
            "width": video.get("width"),
            "height": video.get("height"),
            "pix_fmt": video.get("pix_fmt"),
            "bit_depth": video.get("bit_depth"),
            "frame_rate": video.get("frame_rate"),
            "duration": clip["probe"].get("duration"),
            "timecode": clip["probe"].get("timecode"),
            "audio_codecs": [item.get("codec") for item in clip["probe"].get("audio") or []],
            "data_streams": len(clip["probe"].get("data") or []),
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
