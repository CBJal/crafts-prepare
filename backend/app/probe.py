"""ffprobe wrapper. Support is whatever the installed ffprobe can decode."""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

from .errors import CraftsError
from .process import run_captured

PIX_DEPTH = {
    "yuv420p": 8,
    "yuvj420p": 8,
    "yuv422p": 8,
    "yuvj422p": 8,
    "yuv444p": 8,
    "yuvj444p": 8,
    "rgb24": 8,
    "yuv420p10le": 10,
    "yuv422p10le": 10,
    "yuv444p10le": 10,
    "yuv420p10be": 10,
    "yuv422p10be": 10,
    "yuv444p12le": 12,
    "yuv422p12le": 12,
    "yuv444p12le": 12,
    "yuva444p10le": 10,
}


@dataclass
class Rational:
    num: int
    den: int

    @property
    def text(self) -> str:
        return f"{self.num}/{self.den}"

    @property
    def float(self) -> float:
        if self.den == 0:
            return 0.0
        return self.num / self.den

    def frame_duration(self) -> float:
        if self.num == 0:
            return 0.0
        return self.den / self.num


def parse_rational(value: str | None) -> Rational | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    if "/" not in value:
        try:
            number = float(value)
        except ValueError:
            return None
        return Rational(int(round(number * 1000)), 1000)
    num_s, den_s = value.split("/", 1)
    try:
        num = int(num_s)
        den = int(den_s)
    except ValueError:
        return None
    if den == 0:
        return None
    divisor = math.gcd(num, den)
    return Rational(num // divisor, den // divisor)


def normalize_rotation(value) -> int:
    """Map a display angle to 0, 90, 180, or 270. Unknown values stay 0."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number):
        return 0
    quarters = int(round(number / 90.0)) % 4
    return (quarters * 90) % 360


def display_dimensions(video: dict | None) -> tuple[int, int]:
    """Pixel size after one display rotation. Missing rotation is stored size."""
    video = video or {}
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if normalize_rotation(video.get("rotation")) in {90, 270}:
        return height, width
    return width, height


def _stream_rotation(stream: dict) -> int:
    tags = stream.get("tags") or {}
    if isinstance(tags, dict):
        for key, value in tags.items():
            if str(key).casefold() in {"rotate", "rotation"}:
                return normalize_rotation(value)
    for side in stream.get("side_data_list") or []:
        if isinstance(side, dict) and "rotation" in side:
            return normalize_rotation(side.get("rotation"))
    return 0


def bit_depth_for(pix_fmt: str | None, raw_bits: int | None) -> int | None:
    if raw_bits and raw_bits > 0:
        return raw_bits
    if not pix_fmt:
        return None
    return PIX_DEPTH.get(pix_fmt)


def discover_executable(configured: str) -> str | None:
    from .runtime import discover_tool

    return discover_tool(configured, which=shutil.which)


def probe_file(ffprobe: str, path: Path, *, timeout: float = 60, cancel=None) -> dict:
    code, stdout, stderr = run_captured(
        [
            ffprobe,
            "-hide_banner",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=timeout,
        cancel=cancel,
    )
    if code != 0 or not stdout.strip():
        detail = (stderr or stdout or "ffprobe failed").strip()
        raise CraftsError(f"ffprobe could not read this file. {detail[:500]}", code="unreadable_media")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CraftsError("ffprobe returned invalid JSON.", code="unreadable_media") from exc
    return describe_probe(payload)


def describe_probe(payload: dict) -> dict:
    streams = payload.get("streams") or []
    fmt = payload.get("format") or {}
    videos = []
    audios = []
    data_streams = []
    others = []
    for stream in streams:
        kind = stream.get("codec_type") or "unknown"
        if kind == "video":
            rate = parse_rational(stream.get("r_frame_rate"))
            avg = parse_rational(stream.get("avg_frame_rate"))
            raw_bits = stream.get("bits_per_raw_sample")
            try:
                raw_bits_i = int(raw_bits) if raw_bits not in (None, "N/A", "0") else None
            except (TypeError, ValueError):
                raw_bits_i = None
            pix = stream.get("pix_fmt")
            rotation = _stream_rotation(stream)
            coded_w = _int(stream.get("width")) or 0
            coded_h = _int(stream.get("height")) or 0
            display_w, display_h = (coded_h, coded_w) if rotation in {90, 270} else (coded_w, coded_h)
            videos.append(
                {
                    "index": stream.get("index"),
                    "codec": stream.get("codec_name"),
                    "codec_long": stream.get("codec_long_name"),
                    "profile": stream.get("profile"),
                    "width": stream.get("width"),
                    "height": stream.get("height"),
                    "rotation": rotation,
                    "display_width": display_w,
                    "display_height": display_h,
                    "pix_fmt": pix,
                    "bit_depth": bit_depth_for(pix, raw_bits_i),
                    "color_space": stream.get("color_space"),
                    "color_transfer": stream.get("color_transfer"),
                    "color_primaries": stream.get("color_primaries"),
                    "color_range": stream.get("color_range"),
                    "frame_rate": rate.text if rate else None,
                    "frame_rate_num": rate.num if rate else None,
                    "frame_rate_den": rate.den if rate else None,
                    "avg_frame_rate": avg.text if avg else None,
                    "duration": _float(stream.get("duration")),
                    "nb_frames": _int(stream.get("nb_frames")),
                    "time_base": stream.get("time_base"),
                    "tags": stream.get("tags") or {},
                }
            )
        elif kind == "audio":
            audios.append(
                {
                    "index": stream.get("index"),
                    "codec": stream.get("codec_name"),
                    "sample_rate": _int(stream.get("sample_rate")),
                    "channels": stream.get("channels"),
                    "channel_layout": stream.get("channel_layout"),
                    "bit_rate": _int(stream.get("bit_rate")),
                    "duration": _float(stream.get("duration")),
                    "tags": stream.get("tags") or {},
                }
            )
        elif kind == "data":
            data_streams.append(
                {
                    "index": stream.get("index"),
                    "codec": stream.get("codec_name"),
                    "codec_tag": stream.get("codec_tag_string"),
                    "tags": stream.get("tags") or {},
                }
            )
        else:
            others.append(
                {
                    "index": stream.get("index"),
                    "codec_type": kind,
                    "codec": stream.get("codec_name"),
                    "tags": stream.get("tags") or {},
                }
            )
    duration = _float(fmt.get("duration"))
    if duration is None and videos:
        duration = videos[0].get("duration")
    timecode = None
    tags = fmt.get("tags") or {}
    for key in ("timecode", "TIMECODE", "quicktime_timecode"):
        if key in tags:
            timecode = str(tags[key])
            break
    if timecode is None:
        for video in videos:
            for key, value in (video.get("tags") or {}).items():
                if key.lower() == "timecode":
                    timecode = str(value)
                    break
    return {
        "format_name": fmt.get("format_name"),
        "format_long_name": fmt.get("format_long_name"),
        "duration": duration,
        "size": _int(fmt.get("size")),
        "bit_rate": _int(fmt.get("bit_rate")),
        "tags": tags,
        "timecode": timecode,
        "video": videos,
        "audio": audios,
        "data": data_streams,
        "other": others,
        "motion_metadata": {
            "status": "not_interpreted",
            "note": (
                "ffprobe data streams and tags are listed as-is. "
                "Missing gyro tags are not treated as proof that the file has no motion data."
            ),
        },
    }


def _float(value) -> float | None:
    if value in (None, "N/A"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _int(value) -> int | None:
    if value in (None, "N/A"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
