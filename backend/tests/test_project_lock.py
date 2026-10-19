"""Project replacement locks must not drop the queue or the last good file."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app.errors import CraftsError
from app.service import _REPLACE_ATTEMPTS, Service, validate_project_document


def _service(tmp_path: Path, monkeypatch) -> Service:
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CRAFTS_LOG_PATH", str(tmp_path / "app.log"))
    monkeypatch.delenv("CRAFTS_DISABLE_WORKER", raising=False)
    return Service()


def _wait(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_transient_replace_keeps_the_previous_project_until_it_succeeds(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    path = tmp_path / "bench.crafts.json"
    service.project["name"] = "Original"
    service.save_project(str(path))
    original = path.read_bytes()
    real = os.replace
    calls = {"n": 0}

    def flaky(source, dest):
        calls["n"] += 1
        if calls["n"] == 1:
            assert Path(dest).read_bytes() == original
            raise PermissionError(13, "Access is denied")
        return real(source, dest)

    monkeypatch.setattr("app.service.os.replace", flaky)
    service.project["name"] = "Updated"
    service.save_project(None)
    assert calls["n"] == 2
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "Updated"
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_persistent_replace_keeps_the_valid_project_and_owned_temp(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    path = tmp_path / "bench.crafts.json"
    service.project["name"] = "Original"
    service.save_project(str(path))
    original = path.read_bytes()
    calls = {"n": 0}

    def locked(source, dest):
        calls["n"] += 1
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("app.service.os.replace", locked)
    service.project["name"] = "Not written"
    started = time.monotonic()
    with pytest.raises(CraftsError) as caught:
        service.save_project(None)
    assert caught.value.code == "project_locked"
    assert calls["n"] == _REPLACE_ATTEMPTS
    assert time.monotonic() - started < 5
    assert path.read_bytes() == original
    assert service.project["name"] == "Not written"
    assert service.project_path == path
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []
    validate_project_document(json.loads(original))


def test_non_permission_replace_is_not_retried(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    path = tmp_path / "bench.crafts.json"
    service.save_project(str(path))
    original = path.read_bytes()
    calls = {"n": 0}

    def missing(source, dest):
        calls["n"] += 1
        raise FileNotFoundError(2, "No such file")

    monkeypatch.setattr("app.service.os.replace", missing)
    started = time.monotonic()
    with pytest.raises(CraftsError) as caught:
        service.project["name"] = "Nope"
        service.save_project(None)
    assert caught.value.code == "project_locked"
    assert calls["n"] == 1
    assert time.monotonic() - started < 0.4
    assert path.read_bytes() == original


def test_save_as_writes_a_free_path_and_leaves_the_locked_file(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    path = tmp_path / "locked.crafts.json"
    service.project["name"] = "Original"
    service.save_project(str(path))
    original = path.read_bytes()
    real = os.replace

    def selective(source, dest):
        if Path(dest) == path:
            raise PermissionError(13, "Access is denied")
        return real(source, dest)

    monkeypatch.setattr("app.service.os.replace", selective)
    service.project["name"] = "Moved"
    other = tmp_path / "local.crafts.json"
    service.save_project(str(other))
    assert path.read_bytes() == original
    assert json.loads(other.read_text(encoding="utf-8"))["name"] == "Moved"
    assert service.project_path == other
    assert list(tmp_path.glob(".*.tmp")) == []


def test_locked_claim_does_not_kill_the_queue_and_recovers_once(tmp_path: Path, monkeypatch, media_dir: Path):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg and ffprobe are required")
    encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False)
    if encoders.returncode != 0 or "libx264" not in encoders.stdout:
        pytest.skip("libx264 is required")
    service = _service(tmp_path, monkeypatch)
    project = tmp_path / "queue.crafts.json"
    dest = tmp_path / "out"
    dest.mkdir()
    try:
        imported = service.import_media(str(media_dir / "h264_8.mp4"))
        clip_id = imported["added"][0]["id"]
        service.save_project(str(project))
        for suffix in ("_one", "_two"):
            service.enqueue_export(
                {
                    "clip_id": clip_id,
                    "preset_id": "h264_mp4",
                    "in_seconds": 0.0,
                    "out_seconds": 0.4,
                    "destination_dir": str(dest),
                    "suffix": suffix,
                    "audio": False,
                }
            )
        snapshot = project.read_bytes()
        document = validate_project_document(json.loads(snapshot))
        assert [job["status"] for job in document["jobs"]] == ["queued", "queued"]
        real = os.replace

        def locked(source, dest_path):
            raise PermissionError(13, "Access is denied")

        monkeypatch.setattr("app.service.os.replace", locked)
        service.start()
        assert _wait(lambda: service.persist_error is not None, 8)
        assert service.worker is not None and service.worker.is_alive()
        assert [job["status"] for job in service.jobs()] == ["queued", "queued"]
        assert project.read_bytes() == snapshot
        assert list(dest.iterdir()) == []
        monkeypatch.setattr("app.service.os.replace", real)
        assert _wait(lambda: all(job["status"] == "succeeded" for job in service.jobs()), 40)
        jobs = service.jobs()
        assert [job["attempt"] for job in jobs] == [1, 1]
        outputs = sorted(path for path in dest.iterdir() if path.is_file())
        assert len(outputs) == 2
        for output in outputs:
            probe = subprocess.run(
                [ffprobe, "-hide_banner", "-v", "error", "-show_streams", "-of", "json", str(output)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert probe.returncode == 0, probe.stderr
            assert "h264" in probe.stdout
        saved = json.loads(project.read_text(encoding="utf-8"))
        assert [job["status"] for job in saved["jobs"]] == ["succeeded", "succeeded"]
        assert service.persist_error is None
        assert service.worker.is_alive()
    finally:
        service.shutdown()


def test_cancel_and_retry_restore_the_job_when_replace_fails(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    path = tmp_path / "edit.crafts.json"
    service.save_project(str(path))
    service.project["jobs"].append(
        {
            "id": "job-1",
            "kind": "export",
            "status": "queued",
            "progress": 0.0,
            "error": None,
            "code": None,
            "result": None,
            "attempt": 1,
            "created_at": "t",
            "finished_at": None,
            "request": {},
        }
    )
    service._persist()
    snapshot = path.read_bytes()

    def locked(source, dest):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("app.service.os.replace", locked)
    with pytest.raises(CraftsError) as caught:
        service.cancel("job-1")
    assert caught.value.code == "project_locked"
    assert service.jobs()[0]["status"] == "queued"
    assert path.read_bytes() == snapshot
    service.project["jobs"][0]["status"] = "failed"
    with pytest.raises(CraftsError):
        service.retry("job-1")
    assert service.jobs()[0]["status"] == "failed"
    assert service.jobs()[0]["attempt"] == 1
    assert path.read_bytes() == snapshot


def test_finish_persist_failure_keeps_the_result_and_the_worker_alive(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    path = tmp_path / "finish.crafts.json"
    service.save_project(str(path))
    service.project["jobs"].append(
        {
            "id": "job-1",
            "kind": "export",
            "status": "running",
            "progress": 0.4,
            "error": None,
            "code": None,
            "result": None,
            "attempt": 1,
            "created_at": "t",
            "finished_at": None,
            "request": {},
        }
    )
    service._persist()
    original = path.read_bytes()

    def locked(source, dest):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("app.service.os.replace", locked)
    service._progress("job-1", 0.8)
    assert service.jobs()[0]["status"] == "running"
    assert path.read_bytes() == original
    service._finish("job-1", "succeeded", result={"output_path": "done.mp4"})
    assert service.jobs()[0]["status"] == "succeeded"
    assert service.jobs()[0]["result"]["output_path"] == "done.mp4"
    assert service._claims_paused is True
    assert service.persist_error
    assert path.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="A Win32 share lock without delete sharing is only available on Windows")
def test_windows_share_lock_pauses_the_queue_until_the_handle_closes(tmp_path: Path, monkeypatch, media_dir: Path):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg and ffprobe are required")
    encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False)
    if encoders.returncode != 0 or "libx264" not in encoders.stdout:
        pytest.skip("libx264 is required")
    import ctypes
    from ctypes import wintypes

    service = _service(tmp_path, monkeypatch)
    project = tmp_path / "share.crafts.json"
    dest = tmp_path / "out"
    dest.mkdir()
    handle = None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        imported = service.import_media(str(media_dir / "h264_8.mp4"))
        service.save_project(str(project))
        service.enqueue_export(
            {
                "clip_id": imported["added"][0]["id"],
                "preset_id": "h264_mp4",
                "in_seconds": 0.0,
                "out_seconds": 0.4,
                "destination_dir": str(dest),
                "suffix": "_share",
                "audio": False,
            }
        )
        snapshot = project.read_bytes()
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.CreateFileW(
            str(project),
            0x80000000,
            0x00000001 | 0x00000002,
            None,
            3,
            0x80,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle is None or ctypes.cast(handle, ctypes.c_void_p).value == invalid:
            raise OSError(ctypes.get_last_error(), "CreateFileW failed")
        service.start()
        assert _wait(lambda: bool(service.persist_error), 8)
        assert service.worker is not None and service.worker.is_alive()
        assert service.jobs()[0]["status"] == "queued"
        assert project.read_bytes() == snapshot
        assert list(dest.iterdir()) == []
    finally:
        if handle is not None:
            kernel32.CloseHandle(handle)
    try:
        assert _wait(lambda: service.jobs()[0]["status"] == "succeeded", 40)
        assert service.jobs()[0]["attempt"] == 1
        outputs = list(dest.glob("*.mp4"))
        assert len(outputs) == 1 and outputs[0].stat().st_size > 0
        assert service.worker is not None and service.worker.is_alive()
    finally:
        service.shutdown()
