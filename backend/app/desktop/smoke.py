"""Installed-app exercise. It is off unless --smoke is passed.

The window is the real main window. Screenshots come from QWidget.grab.
Jobs are the real queue. A missing tool is recorded as a failure, not success.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..version import APP_NAME, APP_VERSION

_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "paused"}


def run_smoke(*, sample: Path, output: Path, report: Path, screenshots: Path) -> int:
    from PySide6.QtWidgets import QApplication

    from ..service import Service
    from .window import MainWindow

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

    def grab(name: str) -> None:
        app.processEvents()
        image = window.grab()
        path = screenshots / f"{name}.png"
        image.save(str(path))
        shots.append({"name": name, "path": str(path), "width": image.width(), "height": image.height()})

    proof: dict = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "sample": str(sample),
        "output": str(output),
        "http_server_started": False,
        "browser_opened": False,
        "screenshots": shots,
        "steps": [],
    }
    code = 0
    try:
        if not sample.is_file():
            proof["steps"].append({"step": "import", "ok": False, "detail": "sample file is missing"})
            code = 2
            return code
        window.import_paths([str(sample)])
        if not window.wait_until_idle(60000):
            proof["steps"].append({"step": "import", "ok": False, "detail": window.last_error or "timed out"})
            code = 2
            return code
        imported = bool(window.clips)
        proof["steps"].append({"step": "import", "ok": imported, "clips": len(window.clips), "detail": window.last_error})
        if not imported:
            code = 2
            return code
        grab("imported")
        window.set_theme("dark")
        grab("theme-dark")
        window.set_theme("light")
        grab("theme-light")
        proof["steps"].append({"step": "theme", "ok": window.property("theme") == "light", "choice": "light"})
        project = output / "smoke.crafts.json"
        saved = window.save_project_to(str(project))
        window.new_project(discard=True)
        opened = window.open_project_path(str(project)) if saved else False
        proof["steps"].append(
            {
                "step": "project",
                "ok": saved and opened and project.is_file(),
                "path": str(project),
                "detail": window.last_error,
            }
        )
        window.request_playback()
        window.wait_until_idle(90000)
        window.request_still(False)
        window.wait_until_idle(90000)
        window.request_still(True)
        window.wait_until_idle(90000)
        grab("viewer")
        proof["steps"].append(
            {
                "step": "viewer",
                "ok": bool(window.playback_result) and bool(window.still_path),
                "playback": window.playback_result,
                "still": window.still_path,
                "detail": window.last_error,
            }
        )
        window.destination.setText(str(output))
        window.select_all_batch()
        window.suffix.setText("_export")
        window.queue_checked_batch("export")
        window.suffix.setText("_gyro")
        window.queue_checked_batch("gyro")
        window.wait_until_idle(5000)
        if service.worker is None or not service.worker.is_alive():
            service.drain(20)
        else:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                app.processEvents()
                jobs_now = service.jobs()
                if jobs_now and all(job["status"] in _TERMINAL for job in jobs_now):
                    break
                time.sleep(0.05)
            else:
                for job in service.jobs():
                    if job["status"] in {"queued", "running"}:
                        try:
                            service.cancel(job["id"])
                        except Exception:
                            pass
                service.shutdown(timeout=8)
        jobs = []
        for job in service.jobs():
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            output_path = result.get("output_path")
            honest = True
            if job["status"] == "succeeded":
                honest = bool(output_path) and Path(str(output_path)).is_file()
            jobs.append(
                {
                    "id": job["id"],
                    "kind": job.get("kind"),
                    "status": job.get("status"),
                    "code": job.get("code"),
                    "error": job.get("error"),
                    "output_path": output_path,
                    "honest": honest,
                }
            )
        grab("queue")
        proof["steps"].append({"step": "queue", "ok": all(item["honest"] for item in jobs), "jobs": jobs})
        if not all(item["honest"] for item in jobs):
            code = 2
        window.force_close = True
        window.close()
        proof["steps"].append({"step": "close", "ok": True})
    finally:
        proof["screenshots"] = shots
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        if window is not None and window.isVisible():
            window.force_close = True
            window.close()
    return code
