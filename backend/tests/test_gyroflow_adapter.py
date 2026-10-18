import json
import os
import stat
from pathlib import Path

from app.gyroflow_cli import FORBIDDEN, build_argv, parse_progress


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
        """#!/usr/bin/env python3
import json, os, shutil, sys, time
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
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


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
    fake = tmp_path / "gyroflow"
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
