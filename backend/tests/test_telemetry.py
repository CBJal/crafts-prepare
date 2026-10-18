import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "tools" / "telemetry-inspect" / "target" / "release" / "crafts-telemetry"


@pytest.mark.skipif(not BINARY.is_file() and shutil.which("crafts-telemetry") is None, reason="crafts-telemetry binary is not built")
def test_synthetic_mp4_is_not_reported_as_sony_gyro(client, media_dir: Path):
    binary = BINARY if BINARY.is_file() else shutil.which("crafts-telemetry")
    client.put("/api/settings", json={"telemetry_parser": str(binary)})
    clip = client.post("/api/media/import", json={"path": str(media_dir / "h264_8.mp4")}).json()["added"][0]
    report = client.post(f"/api/media/{clip['id']}/telemetry")
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["parsed"] is False
    assert "camera support" in body["disclaimer"].lower() or "not a camera-support" in body["disclaimer"].lower()
    assert "sony" not in (body.get("camera_type") or "").lower()
