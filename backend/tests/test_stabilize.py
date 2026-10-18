"""Visual stabilization on a synthetic moving crop. Not a gyro test."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.conftest import require_encoder
from tests.test_prepare import decode_ok, drain_job, import_clip, sha256


def bright_centroid_std(path: Path, width: int, height: int) -> tuple[float, float]:
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ]
    )
    frame = width * height
    count = len(raw) // frame
    xs: list[float] = []
    ys: list[float] = []
    for index in range(count):
        buf = raw[index * frame : (index + 1) * frame]
        total = 0
        sx = 0
        sy = 0
        for y in range(height):
            row = buf[y * width : (y + 1) * width]
            for x, value in enumerate(row):
                if value > 200:
                    total += 1
                    sx += x
                    sy += y
        if total:
            xs.append(sx / total)
            ys.append(sy / total)
    return _std(xs), _std(ys)


def _std(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5


def test_vidstab_reduces_synthetic_camera_shake(client, media_dir: Path, tmp_path: Path):
    caps = client.get("/api/capabilities").json()
    filters = (caps.get("ffmpeg") or {}).get("filters") or {}
    if not (filters.get("vidstabdetect") and filters.get("vidstabtransform")):
        import pytest

        pytest.skip("vidstab filters are not installed; visual stabilization was not tested")
    require_encoder(client, "libx264")
    source = media_dir / "shake.mp4"
    before = sha256(source)
    clip = import_clip(client, source)
    dest = tmp_path / "stab"
    dest.mkdir()
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": clip["summary"]["duration"],
            "destination_dir": str(dest),
            "suffix": "_stab",
            "audio": False,
            "stabilization": "vidstab",
            "use_vidstab_auto": True,
        },
    )
    assert queued.status_code == 200, queued.text
    job = drain_job(client, queued.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["stabilization"]["kind"] == "visual"
    assert "gyro" not in job["result"]["stabilization"]["label"].lower() or "not gyro" in job["result"]["stabilization"]["label"].lower()
    assert "vidstabtransform" in job["result"]["filter_chain"]
    assert "smoothing=10" in job["result"]["filter_chain"]
    assert "optzoom=2" in job["result"]["filter_chain"]
    output = Path(job["result"]["output_path"])
    decode_ok(output)
    before_x, before_y = bright_centroid_std(source, 320, 180)
    after_x, after_y = bright_centroid_std(output, 320, 180)
    # Numeric comparison of the white square's position. A lower spread means
    # the crop window's motion was reduced. This is not a quality grade.
    assert before_x > 5 and before_y > 3
    assert after_x < before_x * 0.75
    assert after_y < before_y * 0.75
    assert sha256(source) == before

    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.8,
            "destination_dir": str(dest),
            "suffix": "_deshake",
            "audio": False,
            "stabilization": "deshake",
            "deshake_rx": 32,
            "deshake_ry": 24,
        },
    )
    assert queued.status_code == 200, queued.text
    job = drain_job(client, queued.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["stabilization"]["mode"] == "deshake"
    assert job["result"]["stabilization"]["kind"] == "visual-fallback"
    assert "deshake=rx=32:ry=24" in job["result"]["filter_chain"]
    decode_ok(Path(job["result"]["output_path"]))
