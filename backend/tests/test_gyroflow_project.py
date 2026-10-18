"""Project files must render into owned staging, not their stored output folder."""

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from app.gyroflow_cli import resolve_reference, stage_gyroflow_document
from tests.test_gyroflow_adapter import _write_fake
from tests.test_prepare import drain_job

_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def _job_view(client, job_id: str) -> dict:
    service = client.app.state.service
    with service.lock:
        job = next(item for item in service.project["jobs"] if item["id"] == job_id)
        error = job.get("error")
        if isinstance(error, str) and len(error) > 500:
            error = error[:500]
        return {"status": job.get("status"), "code": job.get("code"), "error": error}


def _wait_for_fake_render(client, job_id: str, ready: Path, *, timeout: float = 20.0) -> dict:
    """Wait until the fake render process has created `ready`, or the job has finished.

    A short blind sleep treats a slow Windows interpreter start the same as a
    job that already failed before the render. Terminal state fails immediately
    with the job's status, code, and error. The bound is long enough for a
    cold Python 3.14 process start and still returns as soon as the file exists.
    """
    started = time.monotonic()
    last = {"status": None, "code": None, "error": None}
    while True:
        elapsed = time.monotonic() - started
        if ready.is_file():
            last = _job_view(client, job_id)
            if last["status"] in _TERMINAL:
                raise AssertionError(_render_diag("fake render file appeared after the job finished", last, ready, elapsed))
            return last
        last = _job_view(client, job_id)
        if last["status"] in _TERMINAL:
            raise AssertionError(_render_diag("job finished before the fake render started", last, ready, elapsed))
        if elapsed >= timeout:
            raise AssertionError(_render_diag(f"fake render did not start within {timeout:.0f}s", last, ready, elapsed))
        threading.Event().wait(0.05)


def _render_diag(reason: str, job: dict, ready: Path, elapsed: float) -> str:
    return (
        f"{reason} after {elapsed:.2f}s: status={job.get('status')} code={job.get('code')} "
        f"error={job.get('error')!r} mode={os.environ.get('GYRO_MODE')!r} "
        f"ready={ready} exists={ready.exists()}"
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(folder: Path, media: Path, *, relative: str, extra: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "videofile": relative,
        "gyro_source": {"filepath": "motion.gcsv", "sample_rate": 2000},
        "stabilization": {"fov": 1.5, "smoothing": 0.5},
        "output": {
            "output_folder": (folder / "project-output").resolve().as_uri() + "/",
            "output_filename": "C0218_stabilized.mp4",
            "output_width": 1280,
            "output_height": 720,
            "codec": "H.264/AVC",
            "pixel_format": "yuv422p10le",
            "use_gpu": False,
            "audio": True,
        },
    }
    if extra:
        payload.update(extra)
    (folder / "motion.gcsv").write_text("gyro", encoding="utf-8")
    path = folder / "clip.gyroflow"
    path.write_text(json.dumps(payload), encoding="utf-8")
    (folder / "project-output").mkdir(exist_ok=True)
    return path


def test_reference_resolution_keeps_absolute_forms(tmp_path: Path):
    base = tmp_path / "proj"
    base.mkdir()
    assert resolve_reference("sub/clip.mp4", base) == str((base / "sub/clip.mp4").resolve())
    assert resolve_reference("file:///data/My%20clip.mp4", base) == "file:///data/My%20clip.mp4"
    assert resolve_reference("file:/data/My%20clip.mp4", base) == "file:/data/My%20clip.mp4"
    assert resolve_reference(r"C:\footage\clíp.mp4", base) == r"C:\footage\clíp.mp4"
    assert resolve_reference("D:/footage/my clip.mp4", base) == "D:/footage/my clip.mp4"
    spaced = base / "My clip.mp4"
    spaced.write_bytes(b"x")
    assert resolve_reference("My clip.mp4", base) == str(spaced.resolve())
    assert resolve_reference("file:clip.mp4", base) == str((base / "clip.mp4").resolve())


def test_project_output_is_staged_and_originals_stay(client, media_dir: Path, tmp_path: Path):
    media_dir_name = tmp_path / "clíp dir"
    media_dir_name.mkdir()
    media = media_dir_name / "my clip.mp4"
    media.write_bytes((media_dir / "h264_8.mp4").read_bytes())
    project_dir = tmp_path / "project"
    project = _project(project_dir, media, relative="my clip.mp4")
    # The relative file must sit next to the project for resolution.
    project_media = project_dir / "my clip.mp4"
    project_media.write_bytes(media.read_bytes())
    preset = project_dir / "look.gyroflow"
    preset.write_text(
        json.dumps(
            {
                "stabilization": {"fov": 1.25},
                "output": {
                    "output_folder": (project_dir / "preset-output").resolve().as_uri() + "/",
                    "output_filename": "preset.mp4",
                    "pixel_format": "yuv422p10le",
                },
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "preset-output").mkdir()
    project_hash = _sha(project)
    preset_hash = _sha(preset)
    media_hash = _sha(project_media)

    fake = tmp_path / "gyroflow_fake.py"
    _write_fake(fake)
    os.environ["GYRO_LOG"] = str(tmp_path / "argv.txt")
    os.environ["GYRO_MEDIA"] = str(project_media)
    os.environ["GYRO_MODE"] = "ok"
    client.put("/api/settings", json={"gyroflow": str(fake)})
    requested = tmp_path / "requested.mp4"
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"project_path": str(project), "preset_path": str(preset), "output_path": str(requested)},
    )
    assert queued.status_code == 200, queued.text
    job = drain_job(client, queued.json()["id"])
    assert job["status"] == "succeeded", job
    assert requested.stat().st_size == project_media.stat().st_size
    assert list((project_dir / "project-output").iterdir()) == []
    assert list((project_dir / "preset-output").iterdir()) == []
    assert _sha(project) == project_hash
    assert _sha(preset) == preset_hash
    assert _sha(project_media) == media_hash
    log_text = Path(os.environ["GYRO_LOG"]).read_text()
    assert str(project) not in log_text
    assert str(preset) not in log_text
    assert "--overwrite" not in log_text
    assert "--watch" not in Path(os.environ["GYRO_LOG"]).read_text()
    assert "--when-done" not in Path(os.environ["GYRO_LOG"]).read_text()
    assert list(tmp_path.glob(".crafts-gyro-*")) == []


def test_staged_project_preserves_render_settings_and_absolute_uris(tmp_path: Path, media_dir: Path):
    folder = tmp_path / "proj"
    folder.mkdir()
    media = folder / "clip.mp4"
    media.write_bytes((media_dir / "h264_8.mp4").read_bytes())
    (folder / "motion.gcsv").write_text("gyro", encoding="utf-8")
    encoded = (folder / "My clip.mp4")
    encoded.write_bytes(media.read_bytes())
    uri = encoded.resolve().as_uri()
    project = folder / "clip.gyroflow"
    project.write_text(
        json.dumps(
            {
                "videofile": uri,
                "gyro_source": {"filepath": "motion.gcsv"},
                "stabilization": {"fov": 1.5},
                "output": {
                    "output_folder": "file:///not-used/",
                    "output_filename": "old.mp4",
                    "output_folder_bookmark": "bookmark-must-not-survive",
                    "pixel_format": "yuv422p10le",
                    "codec": "H.264/AVC",
                    "output_width": 1280,
                    "output_height": 720,
                    "use_gpu": False,
                    "audio": True,
                },
            }
        ),
        encoding="utf-8",
    )
    before = project.read_bytes()
    staging = tmp_path / "stage"
    staging.mkdir()
    target = staging / "staged.mp4"
    staged = stage_gyroflow_document(project, staging, target, role="project")
    body = json.loads(staged.read_text(encoding="utf-8"))
    assert body["videofile"] == uri
    assert body["gyro_source"]["filepath"] == str((folder / "motion.gcsv").resolve())
    assert body["stabilization"]["fov"] == 1.5
    assert body["output"]["pixel_format"] == "yuv422p10le"
    assert body["output"]["codec"] == "H.264/AVC"
    assert body["output"]["output_width"] == 1280
    assert body["output"]["output_height"] == 720
    assert body["output"]["use_gpu"] is False
    assert body["output"]["audio"] is True
    assert body["output"]["output_filename"] == "staged.mp4"
    assert body["output"]["output_path"].endswith("staged.mp4")
    assert "output_folder_bookmark" not in body["output"]
    assert body["output"]["output_folder"].startswith("file:///")
    assert body["output"]["output_folder"].endswith("/")
    assert "stage" in body["output"]["output_folder"]
    assert project.read_bytes() == before
    bare = folder / "preset-bare.gyroflow"
    bare.write_text(json.dumps({"stabilization": {"fov": 1.1}}), encoding="utf-8")
    staged_preset = stage_gyroflow_document(bare, staging, target, role="preset")
    preset_body = json.loads(staged_preset.read_text(encoding="utf-8"))
    assert "output" not in preset_body
    assert preset_body["stabilization"]["fov"] == 1.1
    no_out = folder / "no-output.gyroflow"
    no_out.write_text(json.dumps({"videofile": uri, "stabilization": {"fov": 1.2}}), encoding="utf-8")
    staged_plain = stage_gyroflow_document(no_out, staging, target, role="project")
    plain = json.loads(staged_plain.read_text(encoding="utf-8"))
    assert "output" not in plain
    assert plain["videofile"] == uri
    assert plain["stabilization"]["fov"] == 1.2


def test_missing_relative_media_fails_before_launch(client, tmp_path: Path):
    folder = tmp_path / "proj"
    folder.mkdir()
    project = folder / "clip.gyroflow"
    project.write_text(
        json.dumps(
            {
                "videofile": "missing.mp4",
                "output": {"output_folder": "file:///tmp/", "output_filename": "old.mp4"},
            }
        ),
        encoding="utf-8",
    )
    fake = tmp_path / "gyroflow_fake.py"
    log = tmp_path / "argv.txt"
    _write_fake(fake)
    os.environ["GYRO_LOG"] = str(log)
    os.environ["GYRO_MEDIA"] = str(tmp_path / "unused.mp4")
    os.environ["GYRO_MODE"] = "ok"
    client.put("/api/settings", json={"gyroflow": str(fake)})
    requested = tmp_path / "out.mp4"
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"project_path": str(project), "output_path": str(requested)},
    ).json()
    job = drain_job(client, queued["id"])
    assert job["status"] == "failed"
    assert job["code"] == "gyro_stage"
    assert not requested.exists()
    assert not log.exists()
    assert project.read_text(encoding="utf-8").count("missing.mp4") == 1


def test_project_cancel_and_racing_final_only_touch_staging(client, media_dir: Path, tmp_path: Path):
    folder = tmp_path / "proj"
    folder.mkdir()
    media = folder / "clip.mp4"
    media.write_bytes((media_dir / "h264_8.mp4").read_bytes())
    project = _project(folder, media, relative="clip.mp4")
    project_hash = _sha(project)
    fake = tmp_path / "gyroflow_fake.py"
    _write_fake(fake)
    ready = tmp_path / "ready"
    go = tmp_path / "go"
    os.environ["GYRO_LOG"] = str(tmp_path / "argv.txt")
    os.environ["GYRO_MEDIA"] = str(media)
    os.environ["GYRO_MODE"] = "wait"
    os.environ["GYRO_READY"] = str(ready)
    os.environ["GYRO_GO"] = str(go)
    client.put("/api/settings", json={"gyroflow": str(fake)})
    requested = tmp_path / "race.mp4"
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"project_path": str(project), "output_path": str(requested)},
    ).json()
    worker = threading.Thread(target=client.app.state.service.drain)
    worker.start()
    _wait_for_fake_render(client, queued["id"], ready)
    requested.write_bytes(b"preserve-me")
    go.write_text("go")
    worker.join(10)
    job = next(item for item in client.get("/api/jobs").json()["jobs"] if item["id"] == queued["id"])
    assert job["status"] == "failed"
    assert job["code"] == "collision"
    assert requested.read_bytes() == b"preserve-me"
    assert list((folder / "project-output").iterdir()) == []
    assert _sha(project) == project_hash
    assert list(tmp_path.glob(".crafts-gyro-*")) == []

    os.environ["GYRO_MODE"] = "fail"
    failed = tmp_path / "failed.mp4"
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"project_path": str(project), "output_path": str(failed)},
    ).json()
    job = drain_job(client, queued["id"])
    assert job["status"] == "failed"
    assert job["code"] == "gyro_failed"
    assert not failed.exists()
    assert list((folder / "project-output").iterdir()) == []
    assert list(tmp_path.glob(".crafts-gyro-*")) == []

    # Separate log from the race and fail phases. Those phases already wrote
    # argv.txt; this phase waits for a file that only its own render can create.
    cancel_log = tmp_path / "cancel-argv.txt"
    assert not cancel_log.exists()
    os.environ["GYRO_LOG"] = str(cancel_log)
    os.environ["GYRO_MODE"] = "sleep"
    cancel_out = tmp_path / "cancel.mp4"
    response = client.post(
        "/api/jobs/gyroflow",
        json={"project_path": str(project), "output_path": str(cancel_out)},
    )
    assert response.status_code == 200, response.text
    queued = response.json()
    worker = threading.Thread(target=client.app.state.service.drain)
    worker.start()
    running = _wait_for_fake_render(client, queued["id"], cancel_log)
    assert running["status"] == "running"
    assert worker.is_alive()
    client.post(f"/api/jobs/{queued['id']}/cancel")
    worker.join(15)
    assert not worker.is_alive()
    job = next(item for item in client.get("/api/jobs").json()["jobs"] if item["id"] == queued["id"])
    assert job["status"] == "cancelled"
    assert not cancel_out.exists()
    assert list((folder / "project-output").iterdir()) == []
    assert _sha(project) == project_hash
    assert list(tmp_path.glob(".crafts-gyro-*")) == []
