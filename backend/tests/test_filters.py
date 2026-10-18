from pathlib import Path

from app.security import quote_ffmpeg_filter_path
from tests.conftest import require_encoder
from tests.test_prepare import decode_ok, drain_job, import_clip


def test_filter_path_quotes_drive_space_and_apostrophe():
    assert quote_ffmpeg_filter_path(r"C:/Users/O'Brien/My LUT.cube") == "'C\\:/Users/O'\\''Brien/My LUT.cube'"
    assert quote_ffmpeg_filter_path("C:/My LUT/a.cube") == "'C\\:/My LUT/a.cube'"
    assert quote_ffmpeg_filter_path("D:/footage/clip.mp4") == "'D\\:/footage/clip.mp4'"


def test_removed_filter_script_option_is_gone():
    root = Path(__file__).resolve().parents[1] / "app"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "filter_script" not in text, path.name


def test_lut_in_spaced_apostrophe_directory_uses_vf(client, media_dir: Path, tmp_path: Path):
    require_encoder(client, "libx264")
    folder = tmp_path / "O'Brien LUT"
    folder.mkdir()
    lut = folder / "look.cube"
    lut.write_text(
        "TITLE look\nLUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n",
        encoding="utf-8",
    )
    clip = import_clip(client, media_dir / "h264_8.mp4")
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
            "suffix": "_lut",
            "audio": False,
            "lut_path": str(lut),
        },
    )
    assert queued.status_code == 200, queued.text
    job = drain_job(client, queued.json()["id"])
    assert job["status"] == "succeeded", job
    preview = job["result"]["command_preview"]
    assert "-vf" in preview
    assert "filter_script" not in " ".join(preview)
    assert job["result"]["filter_chain"] == "lut3d=file=grade.cube"
    assert "O'Brien" not in job["result"]["filter_chain"]
    assert "Brien" not in " ".join(preview)
    decode_ok(Path(job["result"]["output_path"]))
