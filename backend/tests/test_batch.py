"""Batch enqueue is all-or-nothing, and batch controls do not rerun successes."""

import json
import os
import threading
from pathlib import Path

import pytest

from tests.gyro_sync import wait_for_fake_render
from tests.test_gyroflow_adapter import _write_fake
from tests.test_prepare import drain_job, import_clip


@pytest.fixture(autouse=True)
def _clear_encode_mode():
    yield
    os.environ.pop("GYRO_ENCODE", None)


def _clips(client, media_dir):
    first = import_clip(client, media_dir / "h264_8.mp4")
    second = import_clip(client, media_dir / "ntsc.mp4")
    return first, second


def _export_body(clip_ids, destination, **extra):
    body = {
        "operation": "export",
        "clip_ids": clip_ids,
        "destination_dir": str(destination),
        "suffix": "_batch",
        "export": {
            "preset_id": "h264_mp4",
            "audio": True,
            "acknowledge_bit_depth_conversion": True,
            "stabilization": "off",
            "fit": "none",
        },
    }
    body.update(extra)
    return body


def test_batch_export_uses_saved_trim_and_one_batch_id(client, media_dir, tmp_path):
    first, second = _clips(client, media_dir)
    client.patch(f"/api/media/{first['id']}", json={"trim_in": 0.0, "trim_out": 0.4})
    client.patch(f"/api/media/{second['id']}", json={"trim_in": 0.0, "trim_out": 0.2})
    destination = tmp_path / "out"
    destination.mkdir()
    response = client.post("/api/jobs/batch", json=_export_body([first["id"], second["id"]], destination))
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["queued"]) == 2
    assert body["batch_id"]
    trims = {job["request"]["clip_id"]: (job["request"]["in_seconds"], job["request"]["out_seconds"]) for job in body["queued"]}
    assert trims[first["id"]] == (0.0, 0.4)
    assert trims[second["id"]] == (0.0, 0.2)
    assert {job["batch_id"] for job in body["queued"]} == {body["batch_id"]}
    status = client.get(f"/api/jobs/batch/{body['batch_id']}").json()
    assert status["total"] == 2
    assert status["queued"] == 2
    assert status["running"] == 0


def test_invalid_duplicate_and_collision_queue_nothing(client, media_dir, tmp_path):
    first, second = _clips(client, media_dir)
    destination = tmp_path / "out"
    destination.mkdir()
    before = client.get("/api/jobs").json()["jobs"]
    duplicate = client.post("/api/jobs/batch", json=_export_body([first["id"], first["id"]], destination))
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "batch_rejected"
    assert any(item["code"] == "duplicate" for item in duplicate.json()["problems"])
    assert client.get("/api/jobs").json()["jobs"] == before

    missing = client.post(
        "/api/jobs/batch",
        json=_export_body([first["id"], "not-a-clip"], destination),
    )
    assert missing.status_code == 409
    assert {item["code"] for item in missing.json()["problems"]} == {"missing_clip"}
    assert client.get("/api/jobs").json()["jobs"] == before

    # Different bytes from the ntsc fixture, same stem, so the two outputs would share a name.
    twin = tmp_path / "ntsc.mp4"
    twin.write_bytes((media_dir / "hevc_10.mp4").read_bytes())
    imported = client.post("/api/media/import", json={"path": str(twin)}).json()["added"][0]
    clash = client.post(
        "/api/jobs/batch",
        json=_export_body([second["id"], imported["id"]], destination),
    )
    assert clash.status_code == 409, clash.text
    assert any(item["code"] == "collision" for item in clash.json()["problems"])
    assert client.get("/api/jobs").json()["jobs"] == before

    taken = destination / "h264_8_batch.mp4"
    taken.write_bytes(b"keep")
    exists = client.post("/api/jobs/batch", json=_export_body([first["id"]], destination))
    assert exists.status_code == 409
    assert exists.json()["problems"][0]["code"] == "collision"
    assert taken.read_bytes() == b"keep"
    assert client.get("/api/jobs").json()["jobs"] == before


def test_batch_reserves_pending_output_names_and_limits_size(client, media_dir, tmp_path):
    first, _second = _clips(client, media_dir)
    destination = tmp_path / "out"
    destination.mkdir()
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": first["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.2,
            "destination_dir": str(destination),
            "suffix": "_batch",
            "audio": False,
            "acknowledge_bit_depth_conversion": True,
        },
    )
    assert queued.status_code == 200, queued.text
    clash = client.post("/api/jobs/batch", json=_export_body([first["id"]], destination, export={
        "preset_id": "h264_mp4",
        "audio": False,
        "acknowledge_bit_depth_conversion": True,
        "stabilization": "off",
        "fit": "none",
    }))
    assert clash.status_code == 409
    assert clash.json()["problems"][0]["code"] == "collision"
    assert len(client.get("/api/jobs").json()["jobs"]) == 1

    service = client.app.state.service
    service.project["jobs"].extend({"id": f"pad-{index}", "status": "paused"} for index in range(499))
    limited = client.post("/api/jobs/batch", json=_export_body([first["id"]], destination, suffix="_other"))
    assert limited.status_code == 400
    assert limited.json()["code"] == "batch_limit"


def test_cancel_and_retry_batch_leave_successes_untouched(client, media_dir, tmp_path):
    first, second = _clips(client, media_dir)
    destination = tmp_path / "out"
    destination.mkdir()
    created = client.post("/api/jobs/batch", json=_export_body([first["id"], second["id"]], destination)).json()
    cancelled = client.post(f"/api/jobs/batch/{created['batch_id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert len(cancelled.json()["cancelled"]) == 2
    status = client.get(f"/api/jobs/batch/{created['batch_id']}").json()
    assert status["cancelled"] == 2
    assert status["queued"] == 0
    retry = client.post(f"/api/jobs/batch/{created['batch_id']}/retry")
    assert retry.json()["retried"] == []

    # A failed gyro job can be retried. A succeeded export in the same batch cannot.
    fake = tmp_path / "gyroflow_fake.py"
    _write_fake(fake)
    os.environ["GYRO_LOG"] = str(tmp_path / "argv.txt")
    os.environ["GYRO_MEDIA"] = str(media_dir / "h264_8.mp4")
    os.environ["GYRO_MODE"] = "ok"
    os.environ["GYRO_META"] = "samples"
    os.environ["GYRO_META_EMPTY"] = "ntsc.mp4"
    client.put("/api/settings", json={"gyroflow": str(fake)})
    gyro_dest = tmp_path / "gyro"
    gyro_dest.mkdir()
    gyro = client.post(
        "/api/jobs/batch",
        json={
            "operation": "gyro",
            "clip_ids": [first["id"], second["id"]],
            "destination_dir": str(gyro_dest),
            "suffix": "_gyro",
            "gyro": {"format": "h264_mp4", "audio": True},
        },
    )
    assert gyro.status_code == 200, gyro.text
    drain_job(client, gyro.json()["queued"][0]["id"])
    drain_job(client, gyro.json()["queued"][1]["id"])
    report = client.get(f"/api/jobs/batch/{gyro.json()['batch_id']}").json()
    by_name = {item["filename"]: item for item in report["jobs"]}
    assert by_name["h264_8_gyro.mp4"]["status"] == "succeeded"
    assert by_name["ntsc_gyro.mp4"]["status"] == "failed"
    assert by_name["ntsc_gyro.mp4"]["code"] == "gyro_motion_missing"
    finished = gyro_dest / "h264_8_gyro.mp4"
    digest = finished.read_bytes()
    retried = client.post(f"/api/jobs/batch/{gyro.json()['batch_id']}/retry").json()
    assert by_name["h264_8_gyro.mp4"]["id"] not in retried["retried"]
    assert by_name["h264_8_gyro.mp4"]["id"] in retried["succeeded_left_unchanged"]
    assert by_name["ntsc_gyro.mp4"]["id"] in retried["retried"]
    assert finished.read_bytes() == digest
    again = client.get(f"/api/jobs/batch/{gyro.json()['batch_id']}").json()
    assert next(item for item in again["jobs"] if item["id"] == by_name["h264_8_gyro.mp4"]["id"])["status"] == "succeeded"


def test_batch_ids_survive_save_and_old_projects_still_open(client, media_dir, tmp_path):
    first, _second = _clips(client, media_dir)
    destination = tmp_path / "out"
    destination.mkdir()
    created = client.post("/api/jobs/batch", json=_export_body([first["id"]], destination)).json()
    path = tmp_path / "bench.crafts.json"
    assert client.post("/api/project/save", json={"path": str(path)}).status_code == 200
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["jobs"][0]["batch_id"] == created["batch_id"]
    assert saved["jobs"][0]["status"] == "queued"
    snapshot = tmp_path / "snapshot.crafts.json"
    snapshot.write_bytes(path.read_bytes())
    assert client.post(f"/api/jobs/batch/{created['batch_id']}/cancel").status_code == 200
    opened = client.post("/api/project/open", json={"path": str(snapshot)})
    assert opened.status_code == 200, opened.text
    restored = client.get("/api/jobs").json()["jobs"][0]
    assert restored["batch_id"] == created["batch_id"]
    assert restored["status"] == "paused"

    legacy = json.loads(path.read_text(encoding="utf-8"))
    legacy["jobs"][0].pop("batch_id")
    legacy["jobs"][0]["status"] = "paused"
    old = tmp_path / "old.crafts.json"
    old.write_text(json.dumps(legacy), encoding="utf-8")
    assert client.post("/api/project/open", json={"path": str(old)}).status_code == 200
    assert "batch_id" not in client.get("/api/jobs").json()["jobs"][0]


def test_preflight_cancel_preset_precedence_and_empty_output(client, media_dir, tmp_path):
    clip = import_clip(client, media_dir / "h264_8.mp4")
    fake = tmp_path / "gyroflow_fake.py"
    _write_fake(fake)
    ready = tmp_path / "meta-ready"
    os.environ["GYRO_LOG"] = str(tmp_path / "argv.txt")
    os.environ["GYRO_MEDIA"] = str(media_dir / "h264_8.mp4")
    os.environ["GYRO_MODE"] = "ok"
    os.environ["GYRO_META"] = "sleep"
    os.environ["GYRO_META_READY"] = str(ready)
    client.put("/api/settings", json={"gyroflow": str(fake)})
    destination = tmp_path / "out"
    destination.mkdir()
    preset = tmp_path / "look.gyroflow"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    preset.write_text(
        json.dumps(
            {
                "stabilization": {"fov": 1.2},
                "output": {
                    "output_folder": foreign.resolve().as_uri() + "/",
                    "output_filename": "preset.mp4",
                    "pixel_format": "yuv420p",
                },
            }
        ),
        encoding="utf-8",
    )
    preset_hash = preset.read_bytes()
    queued = client.post(
        "/api/jobs/batch",
        json={
            "operation": "gyro",
            "clip_ids": [clip["id"]],
            "destination_dir": str(destination),
            "suffix": "_gyro",
            "gyro": {"format": "h264_mp4", "audio": True, "preset_path": str(preset)},
        },
    ).json()
    worker = threading.Thread(target=client.app.state.service.drain)
    worker.start()
    running = wait_for_fake_render(client, queued["queued"][0]["id"], ready)
    assert running["status"] == "running"
    assert worker.is_alive()
    client.post(f"/api/jobs/{queued['queued'][0]['id']}/cancel")
    worker.join(15)
    assert not worker.is_alive()
    job = client.get("/api/jobs").json()["jobs"][-1]
    assert job["status"] == "cancelled"
    assert not (destination / "h264_8_gyro.mp4").exists()
    assert list(foreign.iterdir()) == []
    assert preset.read_bytes() == preset_hash
    assert list(destination.glob(".crafts-gyro-*")) == []

    os.environ["GYRO_META"] = "invalid"
    os.environ["GYRO_MODE"] = "ok"
    bad = client.post(
        "/api/jobs/batch",
        json={
            "operation": "gyro",
            "clip_ids": [clip["id"]],
            "destination_dir": str(destination),
            "suffix": "_bad",
            "gyro": {"format": "prores_422", "audio": False, "width": 640, "height": 360},
        },
    ).json()
    failed = drain_job(client, bad["queued"][0]["id"])
    assert failed["status"] == "failed"
    assert failed["code"] == "gyro_motion_missing"
    assert not (destination / "h264_8_bad.mov").exists()

    os.environ["GYRO_META"] = "samples"
    os.environ["GYRO_MODE"] = "empty"
    empty = client.post(
        "/api/jobs/batch",
        json={
            "operation": "gyro",
            "clip_ids": [clip["id"]],
            "destination_dir": str(destination),
            "suffix": "_empty",
            "gyro": {"format": "h264_mp4", "audio": True, "preset_path": str(preset)},
        },
    ).json()
    unverified = drain_job(client, empty["queued"][0]["id"])
    assert unverified["status"] == "failed"
    assert unverified["code"] == "gyro_unverified"
    assert not (destination / "h264_8_empty.mp4").exists()
    assert list(foreign.iterdir()) == []
    assert preset.read_bytes() == preset_hash


def test_automatic_output_overrides_preset_and_rejects_a_different_encode(client, media_dir, tmp_path):
    clip = import_clip(client, media_dir / "h264_8.mp4")
    fake = tmp_path / "gyroflow_fake.py"
    _write_fake(fake)
    os.environ["GYRO_LOG"] = str(tmp_path / "argv.txt")
    os.environ["GYRO_MEDIA"] = str(media_dir / "h264_8.mp4")
    os.environ["GYRO_MODE"] = "ok"
    os.environ["GYRO_META"] = "samples"
    os.environ["GYRO_ENCODE"] = "1"
    client.put("/api/settings", json={"gyroflow": str(fake)})
    destination = tmp_path / "out"
    destination.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    preset = tmp_path / "h264.gyroflow"
    preset.write_text(
        json.dumps(
            {
                "stabilization": {"fov": 1.25},
                "lens_profile": {"name": "kept"},
                "output": {
                    "output_folder": foreign.resolve().as_uri() + "/",
                    "output_filename": "preset.mp4",
                    "codec": "H.264/AVC",
                    "codec_options": "",
                    "pixel_format": "yuv420p",
                    "output_width": 1280,
                    "output_height": 720,
                    "audio": True,
                },
            }
        ),
        encoding="utf-8",
    )
    original = preset.read_bytes()
    queued = client.post(
        "/api/jobs/batch",
        json={
            "operation": "gyro",
            "clip_ids": [clip["id"]],
            "destination_dir": str(destination),
            "suffix": "_prores",
            "gyro": {
                "format": "prores_422",
                "audio": False,
                "width": 640,
                "height": 360,
                "preset_path": str(preset),
            },
        },
    )
    assert queued.status_code == 200, queued.text
    job = drain_job(client, queued.json()["queued"][0]["id"])
    assert job["status"] == "succeeded", job
    output = destination / "h264_8_prores.mov"
    assert output.is_file()
    probe = client.app.state.service._tools()
    from app.probe import probe_file

    probed = probe_file(probe[1], output)
    video = probed["video"][0]
    assert video["codec"] == "prores"
    assert video["pix_fmt"] == "yuv422p10le"
    assert video["width"] == 640
    assert video["height"] == 360
    assert probed["audio"] == []
    assert list(foreign.iterdir()) == []
    assert preset.read_bytes() == original

    source_sized = client.post(
        "/api/jobs/batch",
        json={
            "operation": "gyro",
            "clip_ids": [clip["id"]],
            "destination_dir": str(destination),
            "suffix": "_source",
            "gyro": {"format": "h264_mp4", "audio": True, "preset_path": str(preset)},
        },
    ).json()
    sized = drain_job(client, source_sized["queued"][0]["id"])
    assert sized["status"] == "succeeded", sized
    sized_probe = probe_file(probe[1], destination / "h264_8_source.mp4")
    assert sized_probe["video"][0]["width"] == 640
    assert sized_probe["video"][0]["height"] == 360
    assert sized_probe["video"][0]["codec"] == "h264"
    assert sized_probe["audio"]
    assert preset.read_bytes() == original

    os.environ["GYRO_ENCODE"] = "force-h264"
    forced = client.post(
        "/api/jobs/batch",
        json={
            "operation": "gyro",
            "clip_ids": [clip["id"]],
            "destination_dir": str(destination),
            "suffix": "_wrong",
            "gyro": {"format": "prores_422", "audio": False, "width": 640, "height": 360, "preset_path": str(preset)},
        },
    ).json()
    rejected = drain_job(client, forced["queued"][0]["id"])
    assert rejected["status"] == "failed"
    assert rejected["code"] == "gyro_output_mismatch"
    assert not (destination / "h264_8_wrong.mov").exists()
    assert preset.read_bytes() == original
    os.environ.pop("GYRO_ENCODE", None)


def test_motion_metadata_must_be_a_progressing_sequence_under_the_size_cap(tmp_path):
    from app.gyroflow_cli import MAX_MOTION_BYTES, _motion_sample_count

    staging = tmp_path / "stage"
    staging.mkdir()
    valid = {
        "raw_imu": [
            {"timestamp_ms": 0, "gyro": [0.0, 0.1, -0.2], "accl": [0.0, 0.0, 1.0], "magn": None},
            {"timestamp_ms": 0.5, "gyro": [0.2, 0.0, 0.0], "accl": [0.0, 0.0, 1.0], "magn": None},
        ]
    }
    path = staging / "motion.json"
    text = json.dumps(valid).encode()
    path.write_bytes(text + b" " * (161_423_762 - len(text)))
    assert path.stat().st_size == 161_423_762
    assert MAX_MOTION_BYTES >= 161_423_762
    assert _motion_sample_count(path, staging) == 2

    huge = staging / "huge.json"
    huge.write_bytes(b"{")
    os.truncate(huge, MAX_MOTION_BYTES + 1)
    try:
        _motion_sample_count(huge, staging)
    except Exception as exc:
        assert getattr(exc, "code", "") == "gyro_motion_invalid"
        assert "not decoded" in str(exc)
    else:
        raise AssertionError("oversized metadata was decoded")

    for payload in (
        {"raw_imu": [{"unexpected": 1}]},
        {"raw_imu": []},
        {"raw_imu": [{"timestamp_ms": 0, "gyro": [0, 0, 0]}]},
        {"raw_imu": [{"timestamp_ms": float("nan"), "gyro": [0, 0, 0]}, {"timestamp_ms": 1, "gyro": [0, 0, 0]}]},
        {"raw_imu": [{"timestamp_ms": 2, "gyro": [0, 0, 0]}, {"timestamp_ms": 1, "gyro": [0, 1, 0]}]},
        {"detected_source": "rtmd"},
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            _motion_sample_count(path, staging)
        except Exception as exc:
            assert getattr(exc, "code", "") == "gyro_motion_missing"
        else:
            raise AssertionError(payload)


def test_running_batch_cancel_stops_only_that_batch(client, media_dir, tmp_path):
    first, second = _clips(client, media_dir)
    fake = tmp_path / "gyroflow_fake.py"
    _write_fake(fake)
    ready = tmp_path / "render-ready"
    cancel_log = tmp_path / "cancel-argv.txt"
    os.environ["GYRO_LOG"] = str(cancel_log)
    os.environ["GYRO_MEDIA"] = str(media_dir / "h264_8.mp4")
    os.environ["GYRO_MODE"] = "sleep"
    os.environ["GYRO_META"] = "samples"
    client.put("/api/settings", json={"gyroflow": str(fake)})
    destination = tmp_path / "out"
    destination.mkdir()
    other = client.post(
        "/api/jobs/export",
        json={
            "clip_id": second["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.2,
            "destination_dir": str(destination),
            "suffix": "_keep",
            "audio": False,
            "acknowledge_bit_depth_conversion": True,
        },
    ).json()
    gyro = client.post(
        "/api/jobs/batch",
        json={
            "operation": "gyro",
            "clip_ids": [first["id"]],
            "destination_dir": str(destination),
            "suffix": "_gyro",
            "gyro": {"format": "h264_mp4", "audio": False},
        },
    ).json()
    # Run only the gyro job: cancel the unrelated export first so drain reaches gyro.
    client.post(f"/api/jobs/{other['id']}/cancel")
    worker = threading.Thread(target=client.app.state.service.drain)
    worker.start()
    running = wait_for_fake_render(client, gyro["queued"][0]["id"], cancel_log)
    assert running["status"] == "running"
    assert worker.is_alive()
    client.post(f"/api/jobs/batch/{gyro['batch_id']}/cancel")
    worker.join(15)
    assert not worker.is_alive()
    report = client.get(f"/api/jobs/batch/{gyro['batch_id']}").json()
    assert report["cancelled"] == 1
    assert not (destination / "h264_8_gyro.mp4").exists()
    assert list(destination.glob(".crafts-gyro-*")) == []
    _ = ready
