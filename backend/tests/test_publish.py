"""No-clobber publication shared by export and gyroflow."""

import errno
import os
from pathlib import Path

import pytest

from app.errors import CraftsError
from app.security import discard_owned_output, publish_exclusive, rename_noreplace
from tests.conftest import require_encoder
from tests.test_gyroflow_adapter import _write_fake
from tests.test_prepare import drain_job, import_clip, sha256


def _pair(tmp_path: Path, name: str = "out.mp4") -> tuple[Path, Path, Path]:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source-bytes")
    partial = tmp_path / f".{name}.job.crafts-partial"
    partial.write_bytes(b"finished-bytes")
    final = tmp_path / name
    return source, partial, final


def test_normal_hardlink_publication(tmp_path: Path):
    source, partial, final = _pair(tmp_path)
    identity = publish_exclusive(partial, final, source)
    assert final.read_bytes() == b"finished-bytes"
    assert not partial.exists()
    st = final.lstat()
    assert identity == {"device": st.st_dev, "inode": st.st_ino}


def test_existing_destination_bytes_are_preserved(tmp_path: Path):
    source, partial, final = _pair(tmp_path)
    final.write_bytes(b"keep-me")
    with pytest.raises(CraftsError) as caught:
        publish_exclusive(partial, final, source)
    assert caught.value.code == "collision"
    assert final.read_bytes() == b"keep-me"
    assert partial.read_bytes() == b"finished-bytes"


def test_hardlink_failure_does_not_clobber_a_racing_file(tmp_path: Path, monkeypatch):
    source, partial, final = _pair(tmp_path)

    def no_hardlinks(_src, _dst):
        raise OSError(errno.EPERM, "hard links are not available")

    def plant_then_rename(src, dst):
        Path(dst).write_bytes(b"racer-bytes")
        return rename_noreplace(src, dst)

    monkeypatch.setattr(os, "link", no_hardlinks)
    monkeypatch.setattr("app.security.rename_noreplace", plant_then_rename)
    with pytest.raises(CraftsError) as caught:
        publish_exclusive(partial, final, source)
    assert caught.value.code == "collision"
    assert final.read_bytes() == b"racer-bytes"
    assert partial.read_bytes() == b"finished-bytes"
    assert final.stat().st_size != 0


def test_hardlink_failure_still_publishes_when_destination_is_free(tmp_path: Path, monkeypatch):
    source, partial, final = _pair(tmp_path)

    def no_hardlinks(_src, _dst):
        raise OSError(errno.EPERM, "hard links are not available")

    monkeypatch.setattr(os, "link", no_hardlinks)
    publish_exclusive(partial, final, source)
    assert final.read_bytes() == b"finished-bytes"
    assert not partial.exists()


def test_hash_mismatch_cleanup_keeps_a_replacement_inode(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    final = tmp_path / "out.mp4"
    final.write_bytes(b"ours")
    identity = {"device": final.lstat().st_dev, "inode": final.lstat().st_ino}
    other = tmp_path / "replacement.mp4"
    other.write_bytes(b"racer-bytes")
    final.unlink()
    other.rename(final)
    assert discard_owned_output(final, identity, source) is False
    assert final.read_bytes() == b"racer-bytes"
    owned = {"device": final.lstat().st_dev, "inode": final.lstat().st_ino}
    assert discard_owned_output(final, owned, source) is True
    assert not final.exists()


def test_export_publication_survives_forced_hardlink_failure(client, media_dir: Path, tmp_path: Path, monkeypatch):
    require_encoder(client, "libx264")
    clip = import_clip(client, media_dir / "h264_8.mp4")
    before = sha256(media_dir / "h264_8.mp4")

    def no_hardlinks(_src, _dst):
        raise OSError(errno.EPERM, "hard links are not available")

    monkeypatch.setattr(os, "link", no_hardlinks)
    dest = tmp_path / "out"
    dest.mkdir()
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.4,
            "destination_dir": str(dest),
            "suffix": "_nolink",
            "audio": False,
        },
    )
    assert queued.status_code == 200, queued.text
    job = drain_job(client, queued.json()["id"])
    assert job["status"] == "succeeded", job
    output = Path(job["result"]["output_path"])
    assert output.stat().st_size > 0
    assert sha256(media_dir / "h264_8.mp4") == before
    assert job["result"]["published_identity"]["inode"] == output.lstat().st_ino


def test_export_and_gyro_leave_a_racing_destination(client, media_dir: Path, tmp_path: Path, monkeypatch):
    require_encoder(client, "libx264")
    clip = import_clip(client, media_dir / "h264_8.mp4")

    def no_hardlinks(_src, _dst):
        raise OSError(errno.EPERM, "hard links are not available")

    def plant_then_rename(src, dst):
        Path(dst).write_bytes(b"export-racer")
        return rename_noreplace(src, dst)

    monkeypatch.setattr(os, "link", no_hardlinks)
    monkeypatch.setattr("app.security.rename_noreplace", plant_then_rename)
    dest = tmp_path / "exports"
    dest.mkdir()
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.4,
            "destination_dir": str(dest),
            "suffix": "_race",
            "audio": False,
        },
    )
    assert queued.status_code == 200, queued.text
    job = drain_job(client, queued.json()["id"])
    assert job["status"] == "failed"
    assert job["code"] == "collision"
    raced = dest / "h264_8_race.mp4"
    assert raced.read_bytes() == b"export-racer"

    def plant_gyro(src, dst):
        Path(dst).write_bytes(b"gyro-racer")
        return rename_noreplace(src, dst)

    monkeypatch.setattr("app.security.rename_noreplace", plant_gyro)
    fake = tmp_path / "gyroflow_fake.py"
    _write_fake(fake)
    os.environ["GYRO_LOG"] = str(tmp_path / "argv.txt")
    os.environ["GYRO_MEDIA"] = str(media_dir / "h264_8.mp4")
    os.environ["GYRO_MODE"] = "ok"
    client.put("/api/settings", json={"gyroflow": str(fake)})
    gyro_out = tmp_path / "gyro_out.mp4"
    gyro = tmp_path / "motion.gcsv"
    gyro.write_text("log")
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"media_path": str(media_dir / "h264_8.mp4"), "gyro_path": str(gyro), "output_path": str(gyro_out)},
    ).json()
    job = drain_job(client, queued["id"])
    assert job["status"] == "failed"
    assert job["code"] == "collision"
    assert gyro_out.read_bytes() == b"gyro-racer"
