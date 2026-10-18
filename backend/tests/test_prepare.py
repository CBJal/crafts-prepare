"""Import, trim, export, collision, and queue behavior against synthetic fixtures."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from app.errors import Cancelled
from tests.conftest import require_encoder


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def probe(path: Path) -> dict:
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-hide_banner",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    return json.loads(raw)


def decode_ok(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-i", str(path), "-f", "null", "-"],
        check=True,
    )


def import_clip(client, path: Path) -> dict:
    response = client.post("/api/media/import", json={"path": str(path)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["added"], body
    return body["added"][0]


def drain_job(client, job_id: str) -> dict:
    jobs = client.post("/api/jobs/drain").json()["jobs"]
    return next(job for job in jobs if job["id"] == job_id)


def test_loopback_rejects_other_hosts(client):
    response = client.get("/api/health", headers={"host": "example.com"})
    assert response.status_code == 403
    response = client.get(
        "/api/health",
        headers={"origin": "http://example.com:8765", "host": "127.0.0.1:8765"},
    )
    assert response.status_code == 403
    ok = client.get("/api/health", headers={"origin": "http://127.0.0.1:8765"})
    assert ok.status_code == 200


def test_import_probe_dedup_and_rejects_bad_paths(client, media_dir: Path, tmp_path: Path):
    before = sha256(media_dir / "h264_8.mp4")
    clip = import_clip(client, media_dir / "h264_8.mp4")
    assert sha256(Path(clip["path"])) == before == clip["sha256"]
    summary = clip["summary"]
    assert summary["codec"] == "h264"
    assert summary["width"] == 640
    assert summary["height"] == 360
    assert summary["pix_fmt"] == "yuv420p"
    assert summary["bit_depth"] == 8
    assert summary["frame_rate"] == "25/1"
    assert summary["audio_codecs"] == ["aac"]
    assert clip["probe"]["motion_metadata"]["status"] == "not_interpreted"
    again = client.post("/api/media/import", json={"path": str(media_dir / "h264_8.mp4")}).json()
    assert again["added"] == []
    assert again["duplicates"][0]["id"] == clip["id"]
    missing = client.post("/api/media/import", json={"path": str(tmp_path / "nope.mp4")})
    assert missing.status_code == 404
    junk = tmp_path / "notes.mp4"
    junk.write_text("not media")
    bad = client.post("/api/media/import", json={"path": str(junk)})
    assert bad.status_code == 400
    assert bad.json()["code"] == "unreadable_media"
    assert sha256(media_dir / "h264_8.mp4") == before


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        if winerror == 1314 or exc.errno in {errno.EPERM, errno.EACCES, errno.EOPNOTSUPP}:
            pytest.skip(
                "This assertion needs a symlink and the process cannot create one "
                f"(winerror={winerror}, errno={exc.errno}). Format import and hardlink alias tests still run."
            )
        raise


def test_folder_symlink_escape_is_skipped(client, media_dir: Path, tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.mp4"
    secret.write_bytes((media_dir / "h264_8.mp4").read_bytes())
    _symlink(library / "escape.mp4", secret)
    _symlink(library / "inside.mov", media_dir / "prores_422_10.mov")
    response = client.post("/api/media/import", json={"path": str(library)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["added"] == []
    reasons = {item["reason"] for item in body["skipped"]}
    assert any("escapes" in reason for reason in reasons)


def test_folder_import_probes_real_codecs(client, media_dir: Path, tmp_path: Path):
    held = tmp_path / "held"
    held.mkdir()
    for name in ("h264_8.mp4", "hevc_10.mp4", "prores_422_10.mov", "dnxhr_hq.mxf"):
        os.link(media_dir / name, held / name) if False else (held / name).write_bytes((media_dir / name).read_bytes())
    body = client.post("/api/media/import", json={"path": str(held)}).json()
    codecs = sorted(item["summary"]["codec"] for item in body["added"])
    assert codecs == ["dnxhd", "h264", "hevc", "prores"]
    by_name = {item["name"]: item for item in body["added"]}
    assert by_name["hevc_10.mp4"]["summary"]["bit_depth"] == 10
    assert by_name["prores_422_10.mov"]["summary"]["bit_depth"] == 10
    assert by_name["prores_422_10.mov"]["summary"]["pix_fmt"] == "yuv422p10le"
    assert by_name["dnxhr_hq.mxf"]["summary"]["pix_fmt"] == "yuv422p"
    assert by_name["dnxhr_hq.mxf"]["summary"]["bit_depth"] == 8


def test_flags_notes_markers_and_project_roundtrip(client, media_dir: Path, tmp_path: Path):
    clip = import_clip(client, media_dir / "h264_8.mp4")
    patched = client.patch(
        f"/api/media/{clip['id']}",
        json={
            "flag": "KEEP",
            "notes": "hero take",
            "markers": [{"seconds": 0.4, "label": "look"}],
            "trim_in": 0.0,
            "trim_out": 1.0,
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["flag"] == "KEEP"
    bad = client.patch(f"/api/media/{clip['id']}", json={"flag": "MAYBE"})
    assert bad.status_code == 400
    project = tmp_path / "bench.crafts.json"
    saved = client.post("/api/project/save", json={"path": str(project)})
    assert saved.status_code == 200, saved.text
    from app.service import Service

    restored = Service().open_project(str(project))
    assert restored["clips"][0]["flag"] == "KEEP"
    assert restored["clips"][0]["notes"] == "hero take"
    assert restored["clips"][0]["markers"][0]["label"] == "look"
    require_encoder(client, "libx264")
    dest = tmp_path / "later"
    dest.mkdir()
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.4,
            "destination_dir": str(dest),
            "suffix": "_later",
            "audio": False,
        },
    )
    assert queued.status_code == 200, queued.text
    assert client.post("/api/project/save", json={"path": str(project)}).status_code == 200
    payload = json.loads(project.read_text())
    payload["jobs"][0]["status"] = "running"
    project.write_text(json.dumps(payload))
    interrupted = Service().open_project(str(project))
    assert interrupted["jobs"][0]["status"] == "interrupted"
    assert interrupted["jobs"][0]["code"] == "interrupted"


def test_trimmed_resized_h264_and_prores_exports(client, media_dir: Path, tmp_path: Path):
    require_encoder(client, "libx264")
    require_encoder(client, "prores_ks")
    require_encoder(client, "aac")
    require_encoder(client, "pcm_s16le")
    h264 = import_clip(client, media_dir / "h264_8.mp4")
    before = sha256(media_dir / "h264_8.mp4")
    dest = tmp_path / "out"
    dest.mkdir()
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": h264["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0.0,
            "out_seconds": 1.0,
            "destination_dir": str(dest),
            "suffix": "_web",
            "audio": True,
            "fit": "resize",
            "width": 320,
            "height": 180,
            "exposure": 0.1,
            "saturation": 0.5,
        },
    )
    assert queued.status_code == 200, queued.text
    job = drain_job(client, queued.json()["id"])
    assert job["status"] == "succeeded", job
    output = Path(job["result"]["output_path"])
    assert output.name == "h264_8_web.mp4"
    assert "eq=brightness=0.1000:saturation=0.5000" in job["result"]["filter_chain"]
    assert "scale=320:180" in job["result"]["filter_chain"]
    assert "-vf" in job["result"]["command_preview"]
    assert "-filter_script:v" not in job["result"]["command_preview"]
    assert "filter_script" not in " ".join(job["result"]["command_preview"])
    decode_ok(output)
    info = probe(output)
    video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
    assert video["codec_name"] == "h264"
    assert video["width"] == 320 and video["height"] == 180
    assert video["pix_fmt"] == "yuv420p"
    assert video["r_frame_rate"] == "25/1"
    duration = float(info["format"]["duration"])
    assert abs(duration - 1.0) <= (1 / 25) + 0.002
    assert audio["codec_name"] == "aac"
    assert sha256(media_dir / "h264_8.mp4") == before
    assert not list(dest.glob("*.crafts-partial"))

    prores = import_clip(client, media_dir / "prores_422_10.mov")
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": prores["id"],
            "preset_id": "prores_422",
            "in_seconds": 0.4,
            "out_seconds": 1.4,
            "destination_dir": str(dest),
            "suffix": "_master",
            "audio": True,
        },
    )
    assert queued.status_code == 200, queued.text
    job = drain_job(client, queued.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["conversion"]["bit_depth_conversion"] is False
    master = Path(job["result"]["output_path"])
    decode_ok(master)
    info = probe(master)
    video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
    assert video["codec_name"] == "prores"
    assert video["pix_fmt"] == "yuv422p10le"
    assert video["width"] == 640 and video["height"] == 360
    assert video["r_frame_rate"] == "25/1"
    duration = float(info["format"]["duration"])
    assert abs(duration - 1.0) <= (1 / 25) + 0.002
    assert audio["codec_name"] == "pcm_s16le"
    assert sha256(media_dir / "prores_422_10.mov") == sha256(Path(prores["path"]))


def test_bit_depth_is_explicit_and_rational_rate_is_preserved(client, media_dir: Path, tmp_path: Path):
    require_encoder(client, "libx264")
    require_encoder(client, "libx265")
    hevc = import_clip(client, media_dir / "hevc_10.mp4")
    dest = tmp_path / "out"
    dest.mkdir()
    silent = client.post(
        "/api/jobs/export",
        json={
            "clip_id": hevc["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 1,
            "destination_dir": str(dest),
            "suffix": "_8bit",
            "audio": True,
        },
    )
    assert silent.status_code == 409
    assert silent.json()["code"] == "bit_depth"
    assert "8-bit" in silent.json()["detail"]
    acked = client.post(
        "/api/jobs/export",
        json={
            "clip_id": hevc["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 1,
            "destination_dir": str(dest),
            "suffix": "_8bit",
            "audio": True,
            "acknowledge_bit_depth_conversion": True,
        },
    )
    assert acked.status_code == 200, acked.text
    job = drain_job(client, acked.json()["id"])
    assert job["status"] == "succeeded", job
    info = probe(Path(job["result"]["output_path"]))
    video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
    assert video["pix_fmt"] == "yuv420p"
    assert "converted" in job["result"]["conversion"]["message"].lower() or "conversion" in job["result"]["conversion"]["message"].lower()

    ntsc = import_clip(client, media_dir / "ntsc.mp4")
    duration = ntsc["summary"]["duration"]
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": ntsc["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": duration,
            "destination_dir": str(dest),
            "suffix": "_ntsc",
            "audio": True,
        },
    )
    assert queued.status_code == 200, queued.text
    job = drain_job(client, queued.json()["id"])
    assert job["status"] == "succeeded", job
    info = probe(Path(job["result"]["output_path"]))
    video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
    assert video["r_frame_rate"] == "30000/1001"


def test_collision_source_alias_and_failed_ffmpeg_leave_no_finished_file(client, media_dir: Path, tmp_path: Path):
    require_encoder(client, "libx264")
    source = tmp_path / "takes" / "h264_8.mp4"
    source.parent.mkdir()
    source.write_bytes((media_dir / "h264_8.mp4").read_bytes())
    before = sha256(source)
    clip = import_clip(client, source)
    dest = source.parent
    overwrite = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 1,
            "destination_dir": str(dest),
            "suffix": "",
            "audio": True,
        },
    )
    assert overwrite.status_code == 400
    assert overwrite.json()["code"] == "source_overwrite"
    assert sha256(source) == before

    alias = tmp_path / "alias"
    alias.mkdir()
    alias_file = alias / "h264_8_cut.mp4"
    try:
        alias_file.symlink_to(source)
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        if winerror == 1314 or exc.errno in {errno.EPERM, errno.EACCES, errno.EOPNOTSUPP}:
            os.link(source, alias_file)
        else:
            raise
    aliased = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 1,
            "destination_dir": str(alias),
            "suffix": "_cut",
            "audio": True,
        },
    )
    assert aliased.status_code == 400
    assert aliased.json()["code"] == "source_overwrite"

    safe = tmp_path / "safe"
    nested = safe / "nested"
    nested.mkdir(parents=True)
    first = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.5,
            "destination_dir": str(nested / ".."),
            "suffix": "_once",
            "audio": False,
        },
    )
    assert first.status_code == 200, first.text
    job = drain_job(client, first.json()["id"])
    assert job["status"] == "succeeded", job
    second = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.5,
            "destination_dir": str(safe),
            "suffix": "_once",
            "audio": False,
        },
    )
    assert second.status_code == 409
    assert second.json()["code"] == "collision"
    assert sha256(source) == before

    broken = tmp_path / "broken" / "damaged.mp4"
    broken.parent.mkdir()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(media_dir / "h264_8.mp4"),
            "-c",
            "copy",
            "-metadata",
            "comment=crafts-damage-fixture",
            str(broken),
        ],
        check=True,
    )
    damaged = import_clip(client, broken)
    payload = broken.read_bytes()
    broken.write_bytes(os.urandom(len(payload)))
    out = tmp_path / "broken-out"
    out.mkdir()
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": damaged["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.5,
            "destination_dir": str(out),
            "suffix": "_bad",
            "audio": False,
        },
    )
    assert queued.status_code == 200, queued.text
    job = drain_job(client, queued.json()["id"])
    assert job["status"] == "failed"
    assert job["status"] != "succeeded"
    assert list(out.glob("*.mp4")) == []
    assert list(out.glob("*.crafts-partial")) == []


def test_invalid_clip_edit_does_not_change_saved_state(client, media_dir: Path):
    clip = import_clip(client, media_dir / "h264_8.mp4")
    before = client.get(f"/api/media/{clip['id']}").json()
    bad_trim = client.patch(f"/api/media/{clip['id']}", json={"trim_in": 1.5, "trim_out": 0.2})
    assert bad_trim.status_code == 400
    bad_flag = client.patch(f"/api/media/{clip['id']}", json={"flag": "MAYBE", "trim_out": 0.4})
    assert bad_flag.status_code == 400
    again = client.get(f"/api/media/{clip['id']}").json()
    assert again["trim_in"] == before["trim_in"]
    assert again["trim_out"] == before["trim_out"]
    assert again["flag"] == before["flag"]
    assert again["notes"] == before["notes"]


def test_invalid_trim_suffix_and_queue_cancel_retry(client, media_dir: Path, tmp_path: Path, monkeypatch):
    require_encoder(client, "libx264")
    clip = import_clip(client, media_dir / "h264_8.mp4")
    dest = tmp_path / "out"
    dest.mkdir()
    bad_trim = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 1.5,
            "out_seconds": 0.2,
            "destination_dir": str(dest),
            "audio": True,
        },
    )
    assert bad_trim.status_code == 400
    assert bad_trim.json()["code"] == "bad_trim"
    injected = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.4,
            "destination_dir": str(dest),
            "suffix": ",scale=320:180",
            "audio": True,
        },
    )
    assert injected.status_code == 400
    past = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 30,
            "destination_dir": str(dest),
            "audio": True,
        },
    )
    assert past.status_code == 400

    service = client.app.state.service
    started = threading.Event()
    calls = {"n": 0}

    def slow(request, cancel, job_id):
        calls["n"] += 1
        if calls["n"] == 1:
            started.set()
            for _ in range(200):
                if cancel.is_set():
                    raise Cancelled()
                import time

                time.sleep(0.02)
            raise AssertionError("cancel did not arrive")
        return {"output_path": None, "validation": {"retried": True}}

    monkeypatch.setattr(service, "_run_export_job", slow)
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.4,
            "destination_dir": str(dest),
            "suffix": "_retry",
            "audio": True,
        },
    )
    assert queued.status_code == 200, queued.text
    job_id = queued.json()["id"]
    worker = threading.Thread(target=service.drain)
    worker.start()
    assert started.wait(3)
    cancelled = client.post(f"/api/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    worker.join(5)
    job = next(item for item in client.get("/api/jobs").json()["jobs"] if item["id"] == job_id)
    assert job["status"] == "cancelled"
    assert job["status"] != "succeeded"
    retried = client.post(f"/api/jobs/{job_id}/retry")
    assert retried.status_code == 200
    job = drain_job(client, job_id)
    assert job["status"] == "succeeded"
    assert job["attempt"] == 2
    assert job["result"]["validation"]["retried"] is True

    project = tmp_path / "queue.crafts.json"
    client.post("/api/project/save", json={"path": str(project)})
    from app.service import Service

    restored = Service().open_project(str(project))
    assert any(item["id"] == job_id and item["status"] == "succeeded" for item in restored["jobs"])


def test_capabilities_report_missing_preset_without_success(client, media_dir: Path, tmp_path: Path):
    clip = import_clip(client, media_dir / "h264_8.mp4")
    caps = client.get("/api/capabilities").json()
    assert caps["ffmpeg"]["available"] is True
    assert caps["limits"]["sony_color_transforms"] is False
    assert caps["limits"]["hdr_display_matching"] is False
    assert caps["gyroflow"]["available"] is False
    client.put("/api/settings", json={"ffmpeg": str(tmp_path / "missing-ffmpeg")})
    caps = client.get("/api/capabilities").json()
    assert caps["ffmpeg"]["available"] is False
    dest = tmp_path / "out"
    dest.mkdir()
    denied = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.2,
            "destination_dir": str(dest),
            "audio": False,
        },
    )
    assert denied.status_code == 424
    assert denied.json()["code"] == "encoder_missing"
    assert client.get("/api/jobs").json()["jobs"] == []
