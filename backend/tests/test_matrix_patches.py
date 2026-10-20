"""Exported patch pixels versus an independent copy of the public formulas."""

import math
import subprocess
import threading
from pathlib import Path

import pytest

_KNEE = 171.2102946929


def _slog3_encode(value: float) -> float:
    if value >= 0.01125:
        return (420.0 + math.log10((value + 0.01) / 0.19) * 261.5) / 1023.0
    return (value * (_KNEE - 95.0) / 0.01125 + 95.0) / 1023.0


def _rec709(linear: float) -> float:
    if linear < 0.018:
        return 4.5 * linear
    return 1.099 * (linear ** 0.45) - 0.099


PATCHES = (
    (0.0, 0.0, 0.0),
    (0.01125, 0.01125, 0.01125),
    (0.18, 0.18, 0.18),
    (0.9, 0.9, 0.9),
    (0.2, 0.18, 0.16),
    (0.1, 0.16, 0.2),
)


def _write_patches(path: Path, codes: list[tuple[float, float, float]]) -> None:
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    chains = []
    for index, rgb in enumerate(codes):
        args += ["-f", "lavfi", "-i", "color=c=black:s=64x64:r=25:d=0.08"]
        red, green, blue = (int(round(max(0.0, min(1.0, channel)) * 65535)) for channel in rgb)
        chains.append(
            f"[{index}:v]format=rgb48le,geq=r={red}:g={green}:b={blue},"
            f"scale=in_range=pc:out_range=pc:out_color_matrix=bt2020:flags=accurate_rnd,format=yuv444p10le[p{index}]"
        )
    graph = ";".join(chains) + ";" + "".join(f"[p{index}]" for index in range(len(codes))) + f"hstack=inputs={len(codes)}"
    args += [
        "-filter_complex", graph,
        "-color_range", "pc", "-colorspace", "bt2020nc", "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
        "-frames:v", "2", str(path),
    ]
    subprocess.run(args, check=True)


def test_half_red_white_is_not_a_bt2020_decode(tmp_path: Path):
    if not __import__("shutil").which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    codes = [tuple(_slog3_encode(channel) for channel in rgb) for rgb in PATCHES]
    source = tmp_path / "slog.mov"
    _write_patches(source, codes)
    from app.colour import build_plan, ensure_plan_cubes
    from app.export import run_export
    from app.filters import FilterSpec
    from app.probe import probe_file

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
    (work / "look.cube").write_text(
        "LUT_3D_SIZE 2\n0 0 0\n0.5 0 0\n0 1 0\n0.5 1 0\n0 0 1\n0.5 0 1\n0 1 1\n0.5 1 1\n",
        encoding="utf-8",
    )
    probe = probe_file("ffprobe", source)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = run_export(
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        source=source,
        destination_dir=out_dir,
        filename="patches.mov",
        job_id="patches",
        probe=probe,
        in_seconds=0,
        out_seconds=float(probe["duration"] or 0.08),
        preset_id="prores_422",
        spec=FilterSpec(),
        audio=False,
        crf=None,
        acknowledge_bit_depth=True,
        shakiness=5,
        accuracy=15,
        cancel=threading.Event(),
        on_progress=lambda _value: None,
        encoders={"prores_ks": True, "pcm_s16le": True, "_lut3d": True},
        extra_filters=",".join(fragments),
        stage_files={path.name: path for path in work.glob("*.cube")},
        output_color={"colorspace": "bt709", "primaries": "bt709", "trc": "bt709", "range": "tv"},
    )
    decoded = tmp_path / "decoded.rgb"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", result["output_path"],
            "-vf", "scale=in_color_matrix=bt709:in_range=tv:out_range=pc:out_color_matrix=bt709,format=rgb24",
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(decoded),
        ],
        check=True,
    )
    raw = decoded.read_bytes()
    assert len(raw) == 384 * 64 * 3

    def pixel(index: int) -> tuple[float, float, float]:
        start = (index * 64 + 32) * 3
        return tuple(raw[start + offset] / 255 for offset in range(3))

    white = pixel(3)
    assert white == pytest.approx((0.5, 1.0, 1.0), abs=0.02), white
    grey = pixel(2)
    expected_grey = _rec709(0.18 / 0.9)
    # The half-red look scales red only, so the grey patch is (0.5 y, y, y).
    assert grey[1] == pytest.approx(expected_grey, abs=0.02), grey
    assert grey[2] == pytest.approx(expected_grey, abs=0.02), grey
    assert grey[0] == pytest.approx(0.5 * expected_grey, abs=0.02), grey
