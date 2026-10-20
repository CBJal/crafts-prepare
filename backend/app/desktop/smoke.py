"""Installed-app exercise. It is off unless --smoke is passed.

The window, queue, and player are the production objects. The JSON report
records what those objects actually did. A missing required export fails the run.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from pathlib import Path

from ..version import APP_NAME, APP_VERSION

_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "paused"}


def run_smoke(
    *,
    output: Path,
    report: Path,
    screenshots: Path,
    samples: list[Path] | None = None,
    sample: Path | None = None,
    config_path: Path | None = None,
    config: dict | None = None,
) -> int:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from ..service import Service
    from .window import MainWindow

    settings = dict(config or {})
    if config_path is not None:
        settings.update(json.loads(config_path.read_text(encoding="utf-8")))
    paths = [Path(item) for item in settings.get("samples") or []]
    paths.extend(samples or [])
    if sample is not None:
        paths.append(Path(sample))
    output.mkdir(parents=True, exist_ok=True)
    screenshots.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    service = Service()
    service.start()
    window = MainWindow(service)
    window.resize(1280, 800)
    window.show()
    app.processEvents()
    shots: list[dict] = []
    before = {str(path): _sha256(path) for path in paths if path.is_file()}

    def grab(name: str) -> None:
        app.processEvents()
        image = window.grab()
        destination = screenshots / f"{name}.png"
        image.save(str(destination))
        shots.append(
            {
                "name": name,
                "path": str(destination),
                "width": image.width(),
                "height": image.height(),
                "bytes": destination.stat().st_size if destination.is_file() else 0,
            }
        )

    def wait_signal(predicate, timeout_ms: int) -> bool:
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)

        def poll() -> None:
            if predicate():
                loop.quit()
            else:
                QTimer.singleShot(30, poll)

        QTimer.singleShot(0, poll)
        loop.exec()
        return bool(predicate())

    proof: dict = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "samples": [str(path) for path in paths],
        "output": str(output),
        "config": settings,
        "threads": [],
        "http_server_started": False,
        "webbrowser_loaded": False,
        "screenshots": shots,
        "steps": [],
    }
    code = 0
    try:
        missing = [str(path) for path in paths if not path.is_file()]
        if not paths or missing:
            proof["steps"].append({"step": "import", "ok": False, "detail": f"missing samples: {missing}"})
            code = 2
            return code
        window.import_paths([str(path) for path in paths])
        if not window.wait_until_idle(120000) or len(window.clips) != len(paths):
            proof["steps"].append(
                {"step": "import", "ok": False, "clips": len(window.clips), "detail": window.last_error}
            )
            code = 2
            return code
        grab("imported")
        proof["steps"].append({"step": "import", "ok": True, "clips": len(window.clips)})
        window.set_theme("dark")
        grab("theme-dark")
        window.set_theme("light")
        grab("theme-light")
        proof["steps"].append({"step": "theme", "ok": window.property("theme") == "light"})
        project = output / "smoke.crafts.json"
        saved = window.save_project_to(str(project))
        window.new_project(discard=True)
        opened = window.open_project_path(str(project)) if saved else False
        proof["steps"].append(
            {
                "step": "project",
                "ok": bool(saved and opened and project.is_file() and len(window.clips) == len(paths)),
                "path": str(project),
                "detail": window.last_error,
            }
        )
        if not proof["steps"][-1]["ok"]:
            code = 2
            return code
        def painted() -> int:
            count = 0
            for key, pix in window.grid._pixmaps.items():
                if str(key).startswith(("error", "pending")):
                    continue
                if pix is not None and not pix.isNull():
                    count += 1
            return count

        wait_signal(lambda: painted() >= len(window.clips) and len(window.clips) > 0, 30000)
        proof["steps"].append(
            {
                "step": "thumbnails",
                "ok": painted() == len(window.clips) and len(window.clips) > 0,
                "count": len(window.clips),
                "painted": painted(),
            }
        )
        recipe = dict(settings.get("recipe") or {})
        if settings.get("lut"):
            recipe["look_lut"] = settings["lut"]
        if recipe and window.clips:
            for clip in list(window.clips):
                window.service.update_clip(clip["id"], {"recipe": recipe})
            window.clips = window.service.clips()
        profiles = []
        for clip in window.clips:
            from .grade import describe_clip

            described = describe_clip(clip)
            profiles.append(
                {
                    "name": clip.get("name"),
                    "sidecar": (described["sidecar"] or {}).get("status"),
                    "pipeline": (described["profile"] or {}).get("pipeline"),
                    "source": (described["profile"] or {}).get("source"),
                    "message": (described["profile"] or {}).get("message"),
                }
            )
        unresolved = [item for item in profiles if item.get("pipeline") in {None, "", "unknown"} or item.get("sidecar") in {"conflict", "ambiguous", "unsafe"}]
        proof["steps"].append({"step": "profiles", "ok": bool(profiles) and not unresolved, "clips": profiles})
        if window.clips:
            window.focus_clip(window.clips[0]["id"])
        window._show_mode(1)
        window.request_comparison()
        window.wait_until_idle(120000)
        start_frames = window.compare.frames_presented
        start_seconds = window.compare.seconds
        window.play()

        def comparison_moved() -> bool:
            return (
                window.compare.same_time()
                and window.compare.frames_presented > start_frames
                and window.compare.seconds > start_seconds + 0.02
                and not window.compare._left.isNull()
                and not window.compare._right.isNull()
            )

        wait_signal(comparison_moved, 8000)
        window.pause()
        playback = _observe_playback(window, wait_signal)
        comparison_ok = window.compare.same_time() and comparison_moved()
        grab("viewer")
        proof["steps"].append(
            {
                "step": "viewer",
                "ok": bool(window.still_path) and playback["advanced"],
                "playback": playback,
                "still": window.still_path,
                "detail": window.last_error,
            }
        )
        proof["steps"].append(
            {
                "step": "comparison",
                "ok": comparison_ok,
                "seconds": window.compare.seconds,
                "mode": window.compare.mode,
                "detail": window.preview_status.text(),
            }
        )
        window.destination.setText(str(output))
        window.select_all_batch()
        _apply_export(window, settings.get("export") or {})
        gyro_settings = settings.get("gyro") or {}
        gyro_expect = gyro_settings.get("expect")
        if gyro_settings:
            for clip in list(window.clips):
                recipe = {"gyro_enabled": True}
                if "smoothness" in gyro_settings:
                    recipe["gyro_smoothness"] = float(gyro_settings["smoothness"])
                if "fov" in gyro_settings:
                    recipe["gyro_fov"] = float(gyro_settings["fov"])
                if "horizon" in gyro_settings:
                    recipe["gyro_horizon"] = float(gyro_settings["horizon"])
                window.service.update_clip(clip["id"], {"recipe": recipe})
            window.clips = window.service.clips()
        gyro_compare_ok = True
        if gyro_expect == "succeeded" and window.clips:
            window.focus_clip(window.clips[0]["id"])
            window._show_mode(1)
            window.gyro_enabled.setChecked(True)
            window.update_stabilization_preview()
            gyro_compare_ok = wait_signal(
                lambda: bool(window.comparison_used_gyro) and window.compare.frames_presented > 0,
                int(gyro_settings.get("timeout_ms") or 180000),
            )
            proof["steps"].append(
                {
                    "step": "gyro-comparison",
                    "ok": gyro_compare_ok,
                    "detail": window.gyro_status.text(),
                    "preview": window.preview_status.text(),
                    "geometry": window.playback_result,
                }
            )
            if not gyro_compare_ok:
                code = 2
        # Focusing the representative gyro clip above (and trim controls) uses
        # select_only. The export contract is still every imported sample.
        window.select_all_batch()
        proof["selected_for_export"] = len(window.selected_ids())
        window.queue_checked_batch("prepare")
        _wait_jobs(app, service, window)
        jobs = _job_rows(service)
        expectations = settings.get("expect") or {}
        prepare_rows = [item for item in jobs if item["kind"] == "prepare"]
        prepare_ok = [item for item in prepare_rows if item["status"] == "succeeded" and item["honest"]]
        gyro_expect = gyro_settings.get("expect")
        min_export = int(expectations.get("min_export_succeeded", len(paths)))
        if gyro_expect == "succeeded":
            queue_ok = len(prepare_ok) >= int(expectations.get("min_gyro_succeeded", len(paths)))
        elif gyro_expect == "failed":
            queue_ok = bool(prepare_rows) and all(item["status"] == "failed" for item in prepare_rows)
            queue_ok = queue_ok and not any(Path(str(item.get("output_path") or "")).is_file() for item in prepare_rows)
        else:
            queue_ok = bool(prepare_rows) and len(prepare_ok) >= min_export and all(item["honest"] for item in jobs)
        grab("queue")
        proof["steps"].append({"step": "queue", "ok": queue_ok, "jobs": jobs, "export_succeeded": len(prepare_ok)})
        after = {str(path): _sha256(path) for path in paths if path.is_file()}
        proof["source_sha256_before"] = before
        proof["source_sha256_after"] = after
        proof["sources_unchanged"] = before == after
        proof["steps"].append({"step": "sources", "ok": before == after})
        if not queue_ok or not playback["advanced"] or before != after:
            code = 2
        window.force_close = True
        window.close()
        proof["steps"].append({"step": "close", "ok": not window._runner.isRunning()})
        proof["threads"] = [thread.name for thread in threading.enumerate()]
        proof["http_server_started"] = "crafts-http" in proof["threads"]
        proof["webbrowser_loaded"] = "webbrowser" in sys.modules
        if proof["http_server_started"] or any(not step.get("ok") for step in proof["steps"]):
            code = 2
    finally:
        proof["webbrowser_loaded"] = "webbrowser" in sys.modules
        proof["screenshots"] = shots
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        if window is not None and window.isVisible():
            window.force_close = True
            window.close()
    return code


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _apply_export(window, settings: dict) -> None:
    preset = settings.get("preset")
    if preset:
        index = window.preset.findData(preset)
        if index >= 0:
            window.preset.setCurrentIndex(index)
    if "audio" in settings:
        window.audio.setChecked(bool(settings["audio"]))
    if settings.get("acknowledge"):
        window.ack.setChecked(True)
    if settings.get("fit"):
        index = window.fit.findData(settings["fit"])
        if index >= 0:
            window.fit.setCurrentIndex(index)
    if settings.get("width"):
        window.out_width.setValue(int(settings["width"]))
    if settings.get("height"):
        window.out_height.setValue(int(settings["height"]))
    window.suffix.setText(str(settings.get("suffix") or "_export"))
    trim_in = settings.get("trim_in")
    trim_out = settings.get("trim_out")
    if trim_in is None and trim_out is None:
        return
    for clip in list(window.clips):
        window.focus_clip(clip["id"])
        if trim_in is not None:
            window.trim_in.setValue(float(trim_in))
        if trim_out is not None:
            window.trim_out.setValue(float(trim_out))
        window.save_trim()
        updated = next(item for item in window.service.clips() if item["id"] == clip["id"])
        if trim_in is not None and abs(float(updated["trim_in"]) - float(trim_in)) > 0.05:
            raise RuntimeError(f"Trim in was not stored for {clip['name']}.")
        if trim_out is not None and abs(float(updated["trim_out"]) - float(trim_out)) > 0.05:
            raise RuntimeError(f"Trim out was not stored for {clip['name']}.")


def _apply_gyro(window, settings: dict) -> None:
    if settings.get("format"):
        index = window.gyro_format.findData(settings["format"])
        if index >= 0:
            window.gyro_format.setCurrentIndex(index)
    if "audio" in settings:
        window.gyro_audio.setChecked(bool(settings["audio"]))
    if settings.get("width") and settings.get("height"):
        window.gyro_source_size.setChecked(False)
        window.gyro_width.setValue(int(settings["width"]))
        window.gyro_height.setValue(int(settings["height"]))
    else:
        window.gyro_source_size.setChecked(True)
    if settings.get("preset"):
        window.gyro_preset.setText(str(settings["preset"]))
    window.suffix.setText(str(settings.get("suffix") or "_gyro"))


def _observe_playback(window, wait_signal) -> dict:
    from PySide6.QtMultimedia import QVideoFrame, QVideoSink

    observed = {"position_ms": 0, "frames": 0, "error": "", "advanced": False}
    if window.player is None:
        observed["error"] = window.player_error or "no player"
        return observed
    frames: list[bool] = []

    def on_frame(frame: QVideoFrame) -> None:
        if frame.isValid():
            frames.append(True)
            observed["frames"] = len(frames)

    def on_position(position: int) -> None:
        if position > observed["position_ms"]:
            observed["position_ms"] = position

    sink = QVideoSink()
    sink.videoFrameChanged.connect(on_frame)
    window.player.setVideoSink(sink)
    window.player.positionChanged.connect(on_position)
    window.play()

    def ready() -> bool:
        return observed["position_ms"] > 0 and observed["frames"] > 0

    wait_signal(ready, 8000)
    window.pause()
    if window.video is not None:
        window.player.setVideoOutput(window.video)
    observed["error"] = window.player.errorString()
    observed["advanced"] = observed["position_ms"] > 0 and observed["frames"] > 0
    return observed


def _wait_jobs(app, service, window) -> None:
    window.wait_until_idle(5000)
    if service.worker is None or not service.worker.is_alive():
        service.drain(40)
        return
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        app.processEvents()
        jobs = service.jobs()
        if jobs and all(job["status"] in _TERMINAL for job in jobs):
            return
        time.sleep(0.05)
    for job in service.jobs():
        if job["status"] in {"queued", "running"}:
            try:
                service.cancel(job["id"])
            except Exception:
                pass
    service.shutdown(timeout=8)


def _job_rows(service) -> list[dict]:
    rows = []
    for job in service.jobs():
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        output_path = result.get("output_path")
        honest = True
        if job["status"] == "succeeded":
            honest = bool(output_path) and Path(str(output_path)).is_file() and Path(str(output_path)).stat().st_size > 0
        rows.append(
            {
                "id": job["id"],
                "kind": job.get("kind"),
                "status": job.get("status"),
                "code": job.get("code"),
                "error": job.get("error"),
                "output_path": output_path,
                "honest": honest,
                "request": {
                    "preset_id": (job.get("request") or {}).get("preset_id"),
                    "automatic": (job.get("request") or {}).get("automatic"),
                    "output_settings": (job.get("request") or {}).get("output_settings"),
                    "audio": (job.get("request") or {}).get("audio"),
                },
            }
        )
    return rows
