import json
import os
import sys
import threading
from pathlib import Path

from app.gyroflow_cli import FORBIDDEN, build_argv, launch_argv, parse_progress


def test_argv_uses_public_flags_and_blocks_watch_shutdown_overwrite(tmp_path: Path):
    output = tmp_path / "out" / "clip_stabilized.mp4"
    output.parent.mkdir()
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"not-read-here")
    gyro = tmp_path / "clip.gcsv"
    gyro.write_text("gcsv")
    preset = tmp_path / "look.gyroflow"
    preset.write_text("{}")
    args = build_argv(
        "/opt/Gyroflow",
        media=media,
        project=None,
        gyro_file=gyro,
        preset=preset,
        output=output,
    )
    assert args[0] == "/opt/Gyroflow"
    assert str(media) in args
    assert "--gyro-file" in args
    assert "--preset" in args
    assert "--stdout-progress" in args
    assert "--out-params" in args
    params = json.loads(args[args.index("--out-params") + 1])
    assert params["output_path"] == str(output.resolve())
    assert params["output_filename"] == "clip_stabilized.mp4"
    for flag in FORBIDDEN:
        assert flag not in args
    assert "-d" not in args


def test_progress_parser_reads_stdout_progress_lines():
    line = "[0000abcd] Rendering progress: 12/40 frames (30.0%) ETA 1.5s"
    assert parse_progress(line) == 12 / 40
    assert parse_progress("[0000abcd] Synchronization progress: 40.0%") == 0.2
    assert parse_progress("[0000abcd] Rendering completed: out.mp4") == 0.99
    assert parse_progress("unrelated") is None


def _write_fake(path: Path) -> None:
    path.write_text(
        """import json, os, shutil, sys, time
from pathlib import Path
if "--version" in sys.argv:
    print("Gyroflow v9.9.9-test-double")
    raise SystemExit(0)
log = Path(os.environ["GYRO_LOG"])
log.write_text("\\n".join(sys.argv))
blocked = {"--watch", "--when-done", "--overwrite", "-f", "--open"}
if blocked.intersection(sys.argv):
    print("blocked flag")
    raise SystemExit(3)
raw = sys.argv[sys.argv.index("--out-params") + 1]
out = Path(json.loads(raw)["output_path"])
print("[0000000a] Rendering progress: 1/2 frames (50.0%) ETA 1.0s", flush=True)
mode = os.environ.get("GYRO_MODE", "ok")
if mode == "sleep":
    time.sleep(30)
if mode == "wait":
    Path(os.environ["GYRO_READY"]).write_text("ready")
    deadline = time.time() + 15
    go = Path(os.environ["GYRO_GO"])
    while not go.exists():
        if time.time() > deadline:
            raise SystemExit(2)
        time.sleep(0.05)
if mode == "fail":
    print("[0000000a] Rendering failed: synthetic", flush=True)
    raise SystemExit(2)
if mode == "empty":
    out.write_bytes(b"")
    raise SystemExit(0)
shutil.copyfile(os.environ["GYRO_MEDIA"], out)
print("[0000000a] Rendering completed: ok", flush=True)
"""
    )


def test_python_test_double_uses_sys_executable(tmp_path: Path):
    script = tmp_path / "gyroflow_fake.py"
    script.write_text("print('no')\n")
    assert launch_argv(str(script)) == [sys.executable, str(script)]
    assert launch_argv(r"C:\Tools\Gyroflow.exe") == [r"C:\Tools\Gyroflow.exe"]


def test_missing_gyroflow_and_missing_motion_are_not_success(client, media_dir: Path, tmp_path: Path):
    media = media_dir / "h264_8.mp4"
    imported = client.post("/api/media/import", json={"path": str(media)}).json()
    clip_id = imported["added"][0]["id"]
    output = tmp_path / "missing.mp4"
    client.put("/api/settings", json={"gyroflow": str(tmp_path / "no-gyroflow-binary")})
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"clip_id": clip_id, "gyro_path": str(media_dir / "none.gcsv"), "output_path": str(output)},
    )
    # The gyro file itself does not exist, which is an input error before a fake success.
    assert queued.status_code == 200
    drained = client.post("/api/jobs/drain").json()["jobs"][-1]
    assert drained["status"] == "failed"
    assert drained["code"] in {"not_found", "gyro_missing", "gyro_motion_missing"}
    assert not output.exists()

    local_media = tmp_path / "clip.mp4"
    local_media.write_bytes(media.read_bytes())
    (local_media.with_suffix(".gcsv")).write_text("not a real log")
    imported = client.post("/api/media/import", json={"path": str(local_media)}).json()
    clip_id = imported["added"][0]["id"] if imported["added"] else imported["duplicates"][0]["id"]
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"clip_id": clip_id, "output_path": str(tmp_path / "side.mp4")},
    ).json()
    result = client.post("/api/jobs/drain").json()["jobs"]
    job = next(item for item in result if item["id"] == queued["id"])
    assert job["status"] == "failed"
    assert job["code"] == "gyro_missing"
    assert not (tmp_path / "side.mp4").exists()


def test_fake_cli_safe_success_failure_and_empty_output(client, media_dir: Path, tmp_path: Path):
    fake = tmp_path / "gyroflow_fake.py"
    log = tmp_path / "argv.txt"
    _write_fake(fake)
    os.environ["GYRO_LOG"] = str(log)
    os.environ["GYRO_MEDIA"] = str(media_dir / "h264_8.mp4")
    os.environ["GYRO_MODE"] = "ok"
    client.put("/api/settings", json={"gyroflow": str(fake)})
    media = media_dir / "h264_8.mp4"
    gyro = tmp_path / "motion.gcsv"
    gyro.write_text("sidecar")
    output = tmp_path / "rendered.mp4"
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"media_path": str(media), "gyro_path": str(gyro), "output_path": str(output)},
    ).json()
    job = next(item for item in client.post("/api/jobs/drain").json()["jobs"] if item["id"] == queued["id"])
    assert job["status"] == "succeeded"
    argv = log.read_text()
    assert "--stdout-progress" in argv
    assert "--overwrite" not in argv.splitlines()[0] and "--watch" not in argv and "--when-done" not in argv
    assert output.stat().st_size > 0
    assert "probed with ffprobe" in job["result"]["note"]
    assert "decoded" not in job["result"]["note"]
    assert list(output.parent.glob(".crafts-gyro-*")) == []
    # This success is a test double that copies a fixture. It is not a Gyroflow render.

    os.environ["GYRO_MODE"] = "fail"
    failed_out = tmp_path / "failed.mp4"
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"media_path": str(media), "gyro_path": str(gyro), "output_path": str(failed_out)},
    ).json()
    job = next(item for item in client.post("/api/jobs/drain").json()["jobs"] if item["id"] == queued["id"])
    assert job["status"] == "failed"
    assert job["code"] == "gyro_failed"
    assert not failed_out.exists()

    os.environ["GYRO_MODE"] = "empty"
    empty_out = tmp_path / "empty.mp4"
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"media_path": str(media), "gyro_path": str(gyro), "output_path": str(empty_out)},
    ).json()
    job = next(item for item in client.post("/api/jobs/drain").json()["jobs"] if item["id"] == queued["id"])
    assert job["status"] == "failed"
    assert job["code"] == "gyro_unverified"
    assert not empty_out.exists()

    os.environ["GYRO_MODE"] = "ok"
    output.write_bytes(b"already")
    log.unlink(missing_ok=True)
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"media_path": str(media), "gyro_path": str(gyro), "output_path": str(output)},
    ).json()
    job = next(item for item in client.post("/api/jobs/drain").json()["jobs"] if item["id"] == queued["id"])
    assert job["status"] == "failed"
    assert job["code"] == "collision"
    assert not log.exists()
    assert output.read_bytes() == b"already"


def test_file_appearing_at_final_path_is_preserved(client, media_dir: Path, tmp_path: Path):
    fake = tmp_path / "gyroflow_wait.py"
    log = tmp_path / "argv.txt"
    ready = tmp_path / "ready"
    go = tmp_path / "go"
    _write_fake(fake)
    os.environ["GYRO_LOG"] = str(log)
    os.environ["GYRO_MEDIA"] = str(media_dir / "h264_8.mp4")
    os.environ["GYRO_MODE"] = "wait"
    os.environ["GYRO_READY"] = str(ready)
    os.environ["GYRO_GO"] = str(go)
    client.put("/api/settings", json={"gyroflow": str(fake)})
    media = media_dir / "h264_8.mp4"
    gyro = tmp_path / "motion.gcsv"
    gyro.write_text("sidecar")
    output = tmp_path / "race.mp4"
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"media_path": str(media), "gyro_path": str(gyro), "output_path": str(output)},
    ).json()
    worker = threading.Thread(target=client.app.state.service.drain)
    worker.start()
    for _ in range(50):
        if ready.exists():
            break
        threading.Event().wait(0.05)
    assert ready.exists()
    output.write_bytes(b"preserve-me")
    go.write_text("go")
    worker.join(10)
    job = next(item for item in client.get("/api/jobs").json()["jobs"] if item["id"] == queued["id"])
    assert job["status"] == "failed"
    assert job["code"] == "collision"
    assert output.read_bytes() == b"preserve-me"
    assert list(tmp_path.glob(".crafts-gyro-*")) == []

    os.environ["GYRO_MODE"] = "sleep"
    ready.unlink(missing_ok=True)
    log.unlink(missing_ok=True)
    cancel_out = tmp_path / "cancel.mp4"
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"media_path": str(media), "gyro_path": str(gyro), "output_path": str(cancel_out)},
    ).json()
    worker = threading.Thread(target=client.app.state.service.drain)
    worker.start()
    for _ in range(50):
        if log.exists():
            break
        threading.Event().wait(0.05)
    assert log.exists()
    cancel_out.write_bytes(b"preserve-cancel")
    client.post(f"/api/jobs/{queued['id']}/cancel")
    worker.join(10)
    job = next(item for item in client.get("/api/jobs").json()["jobs"] if item["id"] == queued["id"])
    assert job["status"] == "cancelled"
    assert cancel_out.read_bytes() == b"preserve-cancel"
    assert list(tmp_path.glob(".crafts-gyro-*")) == []
