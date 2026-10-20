"""Correction-2 regressions: selection, geometry identity, range, orientation, metadata, thumbnails."""

import os
import subprocess
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.colour import build_plan, ensure_plan_cubes
from app.desktop.thumbs import ThumbnailHub
from app.errors import CraftsError
from app.export import run_export
from app.filters import FilterSpec
from app.gyroflow_cli import _write_video_rotation
from app.prepare import colour_input_range, gyro_geometry_key
from app.probe import display_dimensions, probe_file
from app.recipe import assert_stored_recipe
from app.service import Service, validate_project_document
from app.sony_xml import assess_sidecar, parse_sidecar_text


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def _raw_rgb(path: Path) -> bytes:
    destination = path.with_suffix(".rgb")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(destination)],
        check=True,
    )
    return destination.read_bytes()


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application


def _window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CRAFTS_LOG_PATH", str(tmp_path / "app.log"))
    from app.desktop.window import MainWindow

    window = MainWindow(Service(), state_path=tmp_path / "state.json")
    window.show()
    qapp.processEvents()
    return window


def test_card_click_keeps_exposure_on_the_edited_clip(qapp, tmp_path, media_dir, monkeypatch):
    window = _window(qapp, tmp_path, monkeypatch)
    window.import_paths([str(media_dir / "h264_8.mp4"), str(media_dir / "hevc_10.mp4")])
    assert window.wait_until_idle(60000)
    first, second = window.clips[0]["id"], window.clips[1]["id"]
    window.focus_clip(first)
    qapp.processEvents()
    window.exposure.setValue(1.0)
    qapp.processEvents()
    window.grid._on_pressed(second, 0)
    qapp.processEvents()
    stored = {clip["id"]: clip for clip in window.service.clips()}
    assert stored[first]["recipe"]["exposure_ev"] == pytest.approx(1.0)
    assert stored[second]["recipe"]["exposure_ev"] == pytest.approx(0.0)
    window.focus_clip(second)
    qapp.processEvents()
    window.exposure.setValue(1.0)
    window.grid._on_pressed(first, 1)
    qapp.processEvents()
    window.focus_clip(second)
    qapp.processEvents()
    window.exposure.setValue(0.0)
    window.grid._on_pressed(first, 0)
    qapp.processEvents()
    stored = {clip["id"]: clip for clip in window.service.clips()}
    assert stored[second]["recipe"]["exposure_ev"] == pytest.approx(0.0)
    window.force_close = True
    window.close()


def test_failed_recipe_commit_does_not_move_to_the_other_clip(qapp, tmp_path, media_dir, monkeypatch):
    window = _window(qapp, tmp_path, monkeypatch)
    window.import_paths([str(media_dir / "h264_8.mp4"), str(media_dir / "hevc_10.mp4")])
    assert window.wait_until_idle(60000)
    first, second = window.clips[0]["id"], window.clips[1]["id"]
    window.focus_clip(first)
    qapp.processEvents()
    window.input_lut.setText(str(tmp_path / "missing.cube"))
    window.input_lut.editingFinished.emit()
    window.grid._on_pressed(second, 0)
    qapp.processEvents()
    assert window.focused_id() == first
    assert window._pending_edit is not None and window._pending_edit[0] == first
    stored = {clip["id"]: clip for clip in window.service.clips()}
    assert not stored[second]["recipe"].get("input_lut")
    window.force_close = True
    window.close()


def test_stale_gyro_geometry_is_not_called_current(qapp, tmp_path, media_dir, monkeypatch):
    window = _window(qapp, tmp_path, monkeypatch)
    window.import_paths([str(media_dir / "h264_8.mp4")])
    assert window.wait_until_idle(60000)
    window.focus_clip(window.clips[0]["id"])
    qapp.processEvents()
    window.gyro_enabled.setChecked(True)
    window.gyro_strength.setValue(25)
    assert window._flush_pending_edit()
    clip = window.focused_clip()
    dummy = tmp_path / "old-geometry.mov"
    dummy.write_bytes(b"owned-preview")
    window._gyro_geometry[clip["id"]] = {"key": "not-the-current-key", "path": str(dummy)}
    window._show_gyro_state(clip)
    assert "stale" in window.gyro_status.text().lower()
    assert window._current_geometry(clip) is None
    window.gyro_strength.setValue(75)
    assert window._flush_pending_edit()
    clip = window.focused_clip()
    assert gyro_geometry_key(clip, clip["recipe"]) != "not-the-current-key"
    assert window._current_geometry(clip) is None
    assert "stale" in window.gyro_status.text().lower()
    window.force_close = True
    window.close()


def test_tv_tag_does_not_change_slog_code_reading(tmp_path: Path):
    if not __import__("shutil").which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    code = 598 / 1023
    value = int(round(code * 65535))
    source = tmp_path / "slog-pc.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:s=32x32:r=25:d=0.08",
        "-vf", f"format=rgb48le,geq=r={value}:g={value}:b={value},format=yuv444p10le",
        "-color_range", "pc", "-colorspace", "bt2020nc", "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
        "-frames:v", "2", str(source),
    )
    tagged = tmp_path / "slog-tv-tag.mp4"
    _ffmpeg("-i", str(source), "-c", "copy", "-bsf:v", "h264_metadata=video_full_range_flag=0", str(tagged))
    probed = probe_file("ffprobe", tagged)
    profile = {"pipeline": "slog3_sgamut3cine", "input_range": "pc"}
    assert colour_input_range(profile) == "pc"
    assert (probed["video"][0].get("color_range") or "").casefold() in {"tv", "mpeg", ""}
    plan = build_plan(
        pipeline="slog3_sgamut3cine",
        input_range=colour_input_range(profile),
        exposure_ev=0,
        contrast=1,
        saturation=1,
        has_input_lut=False,
        has_look_lut=False,
        look_includes_camera=False,
    )
    work = tmp_path / "cubes"
    chain = ",".join(ensure_plan_cubes(plan, work))
    rendered = tmp_path / "pc-read.rgb"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(tagged), "-vf", chain, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(rendered)],
        check=True,
        cwd=work,
    )
    same = tmp_path / "pc-file.rgb"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vf", chain, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(same)],
        check=True,
        cwd=work,
    )
    # The TV tag must not move the samples when the profile range is full.
    assert rendered.read_bytes()[:3] == same.read_bytes()[:3]
    forced = build_plan(
        pipeline="slog3_sgamut3cine",
        input_range="tv",
        exposure_ev=0,
        contrast=1,
        saturation=1,
        has_input_lut=False,
        has_look_lut=False,
        look_includes_camera=False,
    )
    forced_dir = tmp_path / "forced"
    forced_chain = ",".join(ensure_plan_cubes(forced, forced_dir))
    forced_rgb = tmp_path / "tv-read.rgb"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(tagged), "-vf", forced_chain, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(forced_rgb)],
        check=True,
        cwd=forced_dir,
    )
    pc_bytes = rendered.read_bytes()[:3]
    tv_bytes = forced_rgb.read_bytes()[:3]
    shift = max(abs(tv_bytes[index] - pc_bytes[index]) / 255 for index in range(3))
    assert shift > 0.04


def test_source_rotation_is_applied_once(tmp_path: Path):
    if not __import__("shutil").which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    coded = tmp_path / "coded.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:s=160x90:r=25:d=0.2",
        "-vf", "format=rgb24,geq=r='if(lt(Y,12),255,0)':g='if(gt(X,148),255,0)':b=0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "8",
        str(coded),
    )
    source = tmp_path / "turned.mp4"
    _ffmpeg("-display_rotation", "90", "-i", str(coded), "-c", "copy", str(source))
    probed = probe_file("ffprobe", source)
    video = probed["video"][0]
    assert video["rotation"] == 90
    assert display_dimensions(video) == (90, 160)
    reference = tmp_path / "reference.rgb"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(reference)],
        check=True,
    )
    reference_bytes = reference.read_bytes()
    assert len(reference_bytes) == 90 * 160 * 3
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    run_export(
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        source=source,
        destination_dir=output_dir,
        filename="upright.mp4",
        job_id="orient",
        probe=probed,
        in_seconds=0,
        out_seconds=0.2,
        preset_id="h264_mp4",
        spec=FilterSpec(),
        audio=False,
        crf=18,
        acknowledge_bit_depth=True,
        shakiness=5,
        accuracy=15,
        cancel=threading.Event(),
        on_progress=lambda _value: None,
        encoders={"libx264": True, "aac": True},
    )
    exported = probe_file("ffprobe", output_dir / "upright.mp4")
    exported_video = exported["video"][0]
    assert (exported_video["width"], exported_video["height"]) == (90, 160)
    assert exported_video["rotation"] == 0
    got = _raw_rgb(output_dir / "upright.mp4")
    assert len(got) == len(reference_bytes) == 90 * 160 * 3
    # One autorotate. A second rotation would not match this frame, and leaving
    # the stored landscape raster would be 160×90.
    mean = sum(abs(left - right) for left, right in zip(got, reference_bytes)) / len(got)
    assert mean < 12
    stored = tmp_path / "stored.rgb"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-noautorotate", "-i", str(source), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(stored)],
        check=True,
    )
    assert len(stored.read_bytes()) == 160 * 90 * 3


def test_gyro_project_records_upright_rotation(tmp_path: Path):
    project = tmp_path / "prepared.gyroflow"
    project.write_text('{"videofile":"clip.mp4","stabilization":{"fov":1},"output":{}}', encoding="utf-8")
    written = _write_video_rotation(project, 90)
    assert written["video_rotation"] == 90
    import json

    payload = json.loads(project.read_text(encoding="utf-8"))
    assert payload["video_info"]["rotation"] == 90.0
    assert "width" not in payload["video_info"]


def test_sidecar_duration_identity_refresh_and_missing_source(tmp_path: Path, media_dir: Path, monkeypatch):
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    folder = tmp_path / "roll"
    folder.mkdir()
    media = folder / "C0001.MP4"
    media.write_bytes((media_dir / "h264_8.mp4").read_bytes())
    xml = folder / "C0001M01.XML"
    xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta>
  <Duration value="999"/>
  <TargetMaterial umidRef="AAA">
    <Duration value="999"/>
  </TargetMaterial>
  <Device manufacturer="Sony" modelName="ILCE-7M4" serialNo="XMLSERIAL"/>
  <Item name="CaptureGammaEquation" value="s-log3-cine"/>
  <Item name="CaptureColorPrimaries" value="s-gamut3-cine"/>
</NonRealTimeMeta>
""",
        encoding="utf-8",
    )
    service = Service()
    added = service.import_media(str(media))["added"][0]
    assessment = added["sidecar"]["assessment"]
    assert assessment["duration"] == "mismatch"
    assert assessment["camera"] == "unknown"
    assert assessment["target"] == "unknown"
    with pytest.raises(CraftsError) as exc:
        from app.prepare import _check_sidecar_agreement

        _check_sidecar_agreement(added["sidecar"], added["probe"])
    assert exc.value.code == "sidecar_mismatch"
    xml.write_text(xml.read_text(encoding="utf-8").replace("999", "50"), encoding="utf-8")
    refreshed = service.refresh_metadata([added["id"]])
    assert refreshed["clips"][0]["status"] == "refreshed"
    assert refreshed["clips"][0]["recipe_kept"] is True
    assert service.clip(added["id"])["recipe"]["exposure_ev"] == pytest.approx(0)
    assert service.clip(added["id"])["sidecar"]["assessment"]["duration"] == "match"
    media.unlink()
    missing = service.refresh_metadata([added["id"]])
    assert missing["clips"][0]["status"] == "missing"
    assert service.clip(added["id"])["recipe"]["profile"] == "auto"
    tagged = tmp_path / "tagged.mp4"
    _ffmpeg(
        "-i", str(media_dir / "h264_8.mp4"),
        "-c", "copy",
        "-movflags", "use_metadata_tags",
        "-metadata", "serial_number=FILESERIAL",
        str(tagged),
    )
    parsed = parse_sidecar_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta>
  <Duration value="50"/>
  <Device serialNo="XMLSERIAL"/>
  <TargetMaterial umidRef="MATERIAL"/>
</NonRealTimeMeta>
"""
    )
    probed = probe_file("ffprobe", tagged)
    disagree = assess_sidecar(parsed, probed)
    assert disagree["camera"] == "mismatch"
    parsed["device"]["serialNo"] = "FILESERIAL"
    assert assess_sidecar(parsed, probed)["camera"] == "match"


def test_loaded_recipe_rejects_non_finite_snapshot():
    with pytest.raises(CraftsError):
        assert_stored_recipe({"profile": "auto", "exposure_ev": float("nan")})
    document = {
        "schema": "crafts-prepare.project",
        "version": 1,
        "name": "Old",
        "clips": [],
        "jobs": [],
    }
    assert validate_project_document(document)["name"] == "Old"


def test_thumbnail_demand_is_not_dropped_and_old_generation_cannot_clobber(qapp, tmp_path, monkeypatch):
    class _Service:
        def __init__(self, root: Path) -> None:
            self.data_dir = root

        def _tools(self):
            return "ffmpeg", "ffprobe"

    service = _Service(tmp_path / "data")
    hub = ThumbnailHub(service)
    ready: list[tuple[int, str]] = []
    hub.ready.connect(lambda generation, clip_id, _path, _error: ready.append((generation, clip_id)))
    started = threading.Event()
    release = threading.Event()

    def fake_extract(**kwargs):
        destination = kwargs["destination"]
        if not started.is_set():
            started.set()
            release.wait(5)
        destination.write_bytes(b"\xff\xd8\xff\xd9")
        return destination

    monkeypatch.setattr("app.desktop.thumbs.extract_thumbnail", fake_extract)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")
    clips = []
    for index in range(64):
        clips.append(
            {
                "id": f"clip-{index}",
                "path": str(source),
                "sha256": f"{index:064x}",
                "size": index + 1,
                "probe": {"duration": 0.2, "video": [{"width": 16, "height": 16, "rotation": 0}]},
                "recipe": {"profile": "preserve"},
                "sidecar": {},
            }
        )
    hub.request(clips[:1])
    assert started.wait(5)
    first_generation = hub.generation
    hub.cancel()
    hub.request(clips[:1])
    second_generation = hub.generation
    assert second_generation != first_generation
    release.set()
    deadline = threading.Event()
    for _ in range(50):
        qapp.processEvents()
        if any(item[0] == second_generation for item in ready):
            break
        deadline.wait(0.05)
    assert any(item[0] == second_generation for item in ready)
    assert not any(item[0] == first_generation and item[1] == "clip-0" for item in ready)
    hub.cancel()
    ready.clear()
    release.set()
    hub.request(clips)
    for _ in range(200):
        qapp.processEvents()
        if len({item[1] for item in ready if item[0] == hub.generation}) >= 64:
            break
        threading.Event().wait(0.02)
    assert len({item[1] for item in ready if item[0] == hub.generation}) == 64
    hub.shutdown()
