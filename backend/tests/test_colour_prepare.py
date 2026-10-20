"""Sidecar pairing, documented colour, and one combined preparation job."""

import json
import os
from pathlib import Path

import pytest

from app.colour import (
    BT2020_TO_REC709,
    build_plan,
    ensure_plan_cubes,
    file_sha256,
    hlg_diffuse_display,
    hlg_pixel,
    slog3_decode,
    slog3_encode,
    slog3_pixel,
)
from app.errors import CraftsError
from app.filters import FilterSpec
from app.prepare import plan_prepare_clip
from app.preview import extract_thumbnail, preview_seconds
from app.recipe import default_recipe, normalize_recipe
from app.service import Service, validate_project_document
from app.sony_xml import pair_sidecar, parse_sidecar_text, resolve_profile
from tests.test_gyroflow_adapter import _write_fake


def _xml(gamma: str, primaries: str, coding: str, *, gyro: bool = True) -> str:
    gyro_group = ""
    if gyro:
        gyro_group = """
    <Group name="GyroscopeAcquisitionSet">
      <Item name="Gyroscope" value="true"/>
      <Item name="Accelerometor" value="true"/>
    </Group>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.00">
  <Duration value="120"/>
  <TargetMaterial umidRef="060A2B340101010501010D4313000000SYNTHETIC0001">
    <Duration value="120"/>
  </TargetMaterial>
  <VideoFormat>
    <VideoFrame captureFps="50p" formatFps="50p" videoCodec="AVC_3840_2160"/>
    <VideoLayout pixel="3840" numOfVerticalLine="2160" aspectRatio="16:9"/>
  </VideoFormat>
  <Device manufacturer="Sony" modelName="ILCE-6700" serialNo="SYNTHETIC0001"/>
  <AcquisitionRecord>
    <Group name="CameraUnitMetadataSet">
      <Item name="CaptureGammaEquation" value="{gamma}"/>
      <Item name="CaptureColorPrimaries" value="{primaries}"/>
      <Item name="CodingEquations" value="{coding}"/>
    </Group>{gyro_group}
  </AcquisitionRecord>
</NonRealTimeMeta>
"""


def test_slog3_reference_codes_and_white_map():
    assert slog3_decode(420 / 1023) == pytest.approx(0.18, abs=1e-6)
    assert slog3_decode(598 / 1023) == pytest.approx(0.90, abs=1e-2)
    assert slog3_encode(0.18) * 1023 == pytest.approx(420, abs=1e-6)
    encoded = slog3_pixel((598 / 1023, 598 / 1023, 598 / 1023), exposure_ev=0, contrast=1, saturation=1)
    assert encoded == pytest.approx((1, 1, 1), abs=1e-4)
    grey = slog3_pixel((420 / 1023, 420 / 1023, 420 / 1023), exposure_ev=1, contrast=1, saturation=1)
    base = slog3_pixel((420 / 1023, 420 / 1023, 420 / 1023), exposure_ev=0, contrast=1, saturation=1)
    assert grey[0] > base[0]
    white = tuple(sum(BT2020_TO_REC709[row][col] for col in range(3)) for row in range(3))
    assert white == pytest.approx((1, 1, 1), abs=1e-6)
    assert 0.18 < hlg_diffuse_display() < 0.25
    hlg_white = hlg_pixel((0.75, 0.75, 0.75), exposure_ev=0, contrast=1, saturation=1)
    assert hlg_white == pytest.approx((1, 1, 1), abs=1e-3)


def test_sidecar_pairing_cases(tmp_path: Path):
    media = tmp_path / "C0001.MP4"
    media.write_bytes(b"synthetic")
    (tmp_path / "c0001m01.xml").write_text(_xml("s-log3-cine", "s-gamut3-cine", "rec709"), encoding="utf-8")
    paired = pair_sidecar(media)
    assert paired["status"] == "ok"
    assert paired["gamma"] == "slog3"
    assert paired["primaries"] == "sgamut3cine"
    assert paired["duration_frames"] == 120
    assert paired["gyro_announced"] is True
    assert paired["gyro_samples"] is False
    profile = resolve_profile(paired, {"video": [{"color_range": "tv"}]}, "auto")
    assert profile["pipeline"] == "slog3_sgamut3cine"
    assert profile["input_range"] == "pc"


def test_unsafe_malformed_ambiguous_and_conflict(tmp_path: Path):
    unsafe = parse_sidecar_text('<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><NonRealTimeMeta>&xxe;</NonRealTimeMeta>')
    assert unsafe["status"] == "unsafe"
    malformed = parse_sidecar_text("<NonRealTimeMeta><Duration")
    assert malformed["status"] == "malformed"
    media = tmp_path / "C0002.mp4"
    media.write_bytes(b"clip")
    (tmp_path / "C0002M01.XML").write_text(_xml("s-log3-cine", "s-gamut3-cine", "rec709"), encoding="utf-8")
    (tmp_path / "C0002.XML").write_text(_xml("hlg-slog3ootf", "rec2020", "rec2020ncl"), encoding="utf-8")
    ambiguous = pair_sidecar(media)
    assert ambiguous["status"] == "ambiguous"
    resolved = resolve_profile(ambiguous, {"video": [{}]}, "auto")
    assert resolved["pipeline"] == "unknown"
    conflict = resolve_profile(
        pair_sidecar_single(tmp_path, "C0003.mp4", "s-log3-cine", "s-gamut3-cine", "rec709"),
        {"video": [{"color_transfer": "arib-std-b67", "color_primaries": "bt2020", "color_range": "tv"}]},
        "auto",
    )
    assert conflict["status"] == "conflict"
    assert conflict["pipeline"] == "unknown"


def pair_sidecar_single(folder: Path, name: str, gamma: str, primaries: str, coding: str) -> dict:
    media = folder / name
    media.write_bytes(b"clip")
    stem = media.stem
    (folder / f"{stem}M01.XML").write_text(_xml(gamma, primaries, coding), encoding="utf-8")
    return pair_sidecar(media)


def test_mixed_profiles_do_not_copy_and_projects_roundtrip(tmp_path: Path, media_dir: Path):
    folder = tmp_path / "roll"
    folder.mkdir()
    slog = folder / "C0001.MP4"
    hlg = folder / "C0002.MP4"
    slog.write_bytes((media_dir / "h264_8.mp4").read_bytes())
    hlg.write_bytes((media_dir / "hevc_10.mp4").read_bytes())
    (folder / "C0001M01.XML").write_text(_xml("s-log3-cine", "s-gamut3-cine", "rec709"), encoding="utf-8")
    (folder / "C0002M01.XML").write_text(_xml("hlg-slog3ootf", "rec2020", "rec2020ncl"), encoding="utf-8")
    service = Service()
    service.import_media(str(folder))
    clips = service.clips()
    by_name = {clip["name"].casefold(): clip for clip in clips}
    assert resolve_profile(by_name["c0001.mp4"]["sidecar"], by_name["c0001.mp4"]["probe"], "auto")["pipeline"] == "slog3_sgamut3cine"
    # The HEVC fixture is not tagged HLG, so the sidecar is the authority and must not inherit C0001.
    hlg_profile = resolve_profile(by_name["c0002.mp4"]["sidecar"], by_name["c0002.mp4"]["probe"], "auto")
    assert hlg_profile["pipeline"] == "hlg_bt2020"
    service.update_clip(by_name["c0001.mp4"]["id"], {"recipe": {"exposure_ev": 0.5, "profile": "auto"}})
    project = tmp_path / "roll.crafts.json"
    service.save_project(str(project))
    other = Service()
    other.open_project(str(project))
    reopened = {clip["name"].casefold(): clip for clip in other.clips()}
    assert reopened["c0001.mp4"]["recipe"]["exposure_ev"] == pytest.approx(0.5)
    assert reopened["c0002.mp4"]["recipe"]["exposure_ev"] == pytest.approx(0)
    assert reopened["c0001.mp4"]["sidecar"]["gamma"] == "slog3"
    assert reopened["c0002.mp4"]["sidecar"]["gamma"] == "hlg"
    legacy = json.loads(project.read_text(encoding="utf-8"))
    legacy["clips"][0].pop("recipe", None)
    legacy["clips"][0].pop("sidecar", None)
    legacy_path = tmp_path / "legacy.crafts.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    validate_project_document(json.loads(legacy_path.read_text(encoding="utf-8")))
    third = Service()
    third.open_project(str(legacy_path))
    assert third.clips()


def test_preview_is_not_capped_at_60_seconds():
    assert preview_seconds(90, None) is None
    assert preview_seconds(90, 60) == 60


def test_lut_changes_pixels_and_matches_the_reference(tmp_path: Path):
    ffmpeg = os.environ.get("FFMPEG", "ffmpeg")
    if not os.access(ffmpeg, os.X_OK) and not __import__("shutil").which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    plan = build_plan(
        pipeline="slog3_sgamut3cine",
        input_range="pc",
        exposure_ev=0,
        contrast=1,
        saturation=1,
        has_input_lut=False,
        has_look_lut=True,
        look_includes_camera=False,
    )
    work = tmp_path / "cubes"
    fragments = ensure_plan_cubes(plan, work)
    look = work / "look.cube"
    look.write_text(
        "LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 0 0\n1 0 0\n0 0 0\n1 0 0\n0 0 0\n1 0 0\n",
        encoding="utf-8",
    )
    code = 420
    value = int(round(code / 1023 * 65535))
    source = tmp_path / "patch.mov"
    rendered = tmp_path / "out.rgb"
    import subprocess

    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "nullsrc=s=8x8:r=1:d=1",
            "-vf", f"format=rgb48le,geq=r={value}:g={value}:b={value},format=yuv422p10le",
            "-color_range", "pc", "-frames:v", "1", str(source),
        ],
        check=True,
    )
    chain = ",".join(fragments)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-vf", chain, "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", str(rendered),
        ],
        check=True,
        cwd=work,
    )
    raw = rendered.read_bytes()
    assert len(raw) == 8 * 8 * 3
    red, green, blue = raw[0], raw[1], raw[2]
    reference = slog3_pixel((code / 1023, code / 1023, code / 1023), exposure_ev=0, contrast=1, saturation=1)
    # The look LUT sends chroma to red. A 2-point cube still interpolates with black, so the result is red-dominant.
    assert red > green + 40
    assert red > blue + 40
    assert reference[0] > 0.2
    plain = tmp_path / "plain.rgb"
    technical = [part for part in fragments if not part.startswith("lut3d=file=look")]
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-vf", ",".join(technical), "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", str(plain),
        ],
        check=True,
        cwd=work,
    )
    pixel = plain.read_bytes()[:3]
    assert pixel[0] == pytest.approx(reference[0] * 255, abs=12)
    assert abs(pixel[0] - pixel[1]) < 12


def test_combined_job_applies_colour_after_gyro_and_failures_do_not_publish(tmp_path: Path, media_dir: Path, monkeypatch):
    pytest.importorskip("shutil")
    if not __import__("shutil").which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    fake = tmp_path / "gyroflow_fake.py"
    _write_fake(fake)
    monkeypatch.setenv("GYRO_LOG", str(tmp_path / "argv.txt"))
    monkeypatch.setenv("GYRO_MEDIA", str(media_dir / "h264_8.mp4"))
    monkeypatch.setenv("GYRO_MODE", "ok")
    monkeypatch.setenv("GYRO_ENCODE", "match")
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    service = Service()
    service.update_settings({"gyroflow": str(fake)})
    folder = tmp_path / "media"
    folder.mkdir()
    media = folder / "C0008.MP4"
    media.write_bytes((media_dir / "h264_8.mp4").read_bytes())
    (folder / "C0008M01.XML").write_text(_xml("s-log3-cine", "s-gamut3-cine", "rec709"), encoding="utf-8")
    look = tmp_path / "look.cube"
    look.write_text(
        "LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 0 0\n1 0 0\n0 0 0\n1 0 0\n0 0 0\n1 0 0\n",
        encoding="utf-8",
    )
    added = service.import_media(str(media))["added"][0]
    service.update_clip(
        added["id"],
        {"recipe": {"profile": "auto", "look_lut": str(look), "gyro_enabled": True, "gyro_smoothness": 0.25, "gyro_fov": 1.2, "gyro_horizon": 0.4}},
    )
    destination = tmp_path / "out"
    destination.mkdir()
    before = file_sha256(media)
    queued = service.enqueue_batch(
        {
            "operation": "prepare",
            "clip_ids": [added["id"]],
            "destination_dir": str(destination),
            "suffix": "_prep",
            "prepare": {"preset_id": "h264_mp4", "audio": False, "acknowledge_bit_depth_conversion": True},
        }
    )
    service.drain()
    job = service.jobs()[-1]
    assert job["id"] == queued["queued"][0]["id"]
    assert job["status"] == "succeeded", job
    output = Path(job["result"]["output_path"])
    assert output.is_file() and output.stat().st_size > 0
    assert job["result"]["colour"]["pipeline"] == "slog3_sgamut3cine"
    assert job["result"]["colour"]["look_lut_sha256"] == file_sha256(look)
    assert job["result"]["gyro"]["applied"] is True
    assert job["result"]["gyro"]["intermediate_published"] is False
    assert job["result"]["gyro"]["stabilization_written"]["smoothness"] == pytest.approx(0.25)
    assert list(destination.glob(".crafts-prepare-*")) == []
    assert list(destination.glob("gyro-intermediate.*")) == []
    assert file_sha256(media) == before
    probe = __import__("json").loads(__import__("subprocess").check_output([
        "ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(output)
    ]))
    video = probe["streams"][0]
    assert video["color_transfer"] == "bt709"
    assert video["color_primaries"] == "bt709"
    assert video["color_space"] == "bt709"
    blocked = destination / output.name
    blocked.write_bytes(b"keep")
    with pytest.raises(CraftsError):
        service.enqueue_batch(
            {
                "operation": "prepare",
                "clip_ids": [added["id"]],
                "destination_dir": str(destination),
                "suffix": "_prep",
                "prepare": {"preset_id": "h264_mp4", "audio": False, "acknowledge_bit_depth_conversion": True},
            }
        )
    assert blocked.read_bytes() == b"keep"

    monkeypatch.setenv("GYRO_MODE", "fail")
    failed_dir = tmp_path / "failed"
    failed_dir.mkdir()
    service.enqueue_batch(
        {
            "operation": "prepare",
            "clip_ids": [added["id"]],
            "destination_dir": str(failed_dir),
            "suffix": "_prep",
            "prepare": {"preset_id": "h264_mp4", "audio": False, "acknowledge_bit_depth_conversion": True},
        }
    )
    service.drain()
    failed = service.jobs()[-1]
    assert failed["status"] == "failed"
    assert list(failed_dir.glob("*.mp4")) == []
    assert list(failed_dir.glob("*.mov")) == []


def test_unknown_profile_is_not_guessed(tmp_path: Path):
    recipe = normalize_recipe({"profile": "auto", "exposure_ev": 1})
    profile = {"pipeline": "unknown", "input_range": "tv", "status": "unknown", "message": "choose"}
    with pytest.raises(CraftsError) as caught:
        build_plan(
            pipeline=profile["pipeline"],
            input_range="tv",
            exposure_ev=recipe["exposure_ev"],
            contrast=1,
            saturation=1,
            has_input_lut=False,
            has_look_lut=False,
            look_includes_camera=False,
        )
    assert caught.value.code == "colour_unresolved"
    assert default_recipe()["exposure_ev"] == 0


def test_thumbnail_uses_the_same_plan(tmp_path: Path, media_dir: Path):
    if not __import__("shutil").which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    work = tmp_path / "plan"
    plan = build_plan(
        pipeline="slog3_sgamut3cine",
        input_range="pc",
        exposure_ev=0.0,
        contrast=1,
        saturation=0,
        has_input_lut=False,
        has_look_lut=False,
        look_includes_camera=False,
    )
    fragments = ensure_plan_cubes(plan, work)
    destination = tmp_path / "thumb.jpg"
    extract_thumbnail(
        ffmpeg="ffmpeg",
        source=media_dir / "h264_8.mp4",
        seconds=0.1,
        destination=destination,
        spec=FilterSpec(),
        duration=2,
        extra_filters=",".join(fragments),
        stage_files={path.name: path for path in work.glob("*.cube")},
    )
    assert destination.is_file() and destination.stat().st_size > 0
    untouched = plan_prepare_clip(
        {
            "id": "abc",
            "name": "C0001.MP4",
            "path": str(media_dir / "h264_8.mp4"),
            "sha256": "a" * 64,
            "probe": {"duration": 2, "video": [{"pix_fmt": "yuv420p", "bit_depth": 8, "width": 640, "height": 360}]},
            "recipe": default_recipe(),
            "sidecar": {"status": "missing"},
            "trim_in": 0,
            "trim_out": 2,
        },
        {"preset_id": "h264_mp4", "audio": False, "acknowledge_bit_depth_conversion": True},
        tmp_path,
        "_prep",
    )
    assert untouched["kind"] == "prepare"
    assert untouched["request"]["profile"]["pipeline"] == "preserve"
