"""Frame thumbnails and browser-oriented proxies. Sources are never rewritten."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .errors import CraftsError
from .filters import FilterSpec, chain_for, validate_lut
from .process import run_captured

PROXY_LIMIT_SECONDS = 60


def extract_thumbnail(
    *,
    ffmpeg: str,
    source: Path,
    seconds: float,
    destination: Path,
    spec: FilterSpec,
    duration: float,
) -> Path:
    if seconds < 0 or (duration and seconds > duration + 0.05):
        raise CraftsError("Thumbnail time is outside the clip.", code="bad_trim")
    # Stills do not include temporal stabilizers. The export path does.
    still = FilterSpec(**{**spec.__dict__, "include_temporal": False, "stabilization": "off"})
    with tempfile.TemporaryDirectory(prefix="crafts-still-") as temp:
        work = Path(temp)
        if still.lut_path:
            shutil.copyfile(validate_lut(still.lut_path), work / "grade.cube")
            still.lut_name = "grade.cube"
        chain = chain_for(still)
        args = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-ss",
            f"{seconds:.6f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
        ]
        if chain:
            args.extend(["-vf", chain])
        args.extend(["-q:v", "3", str(destination)])
        code, _stdout, stderr = run_captured(args, timeout=120, cwd=str(work))
    if code != 0 or not destination.exists():
        raise CraftsError("Thumbnail failed. " + (stderr or "")[-500:], code="ffmpeg_failed")
    return destination


def browser_playable(probe: dict) -> bool:
    video = (probe.get("video") or [None])[0]
    if not video:
        return False
    codec = (video.get("codec") or "").lower()
    pix = video.get("pix_fmt") or ""
    if codec != "h264":
        return False
    if pix not in {"yuv420p", "yuvj420p"}:
        return False
    return True


def make_proxy(
    *,
    ffmpeg: str,
    source: Path,
    destination: Path,
    duration: float,
) -> Path:
    limit = min(duration, PROXY_LIMIT_SECONDS) if duration and duration > 0 else PROXY_LIMIT_SECONDS
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-t",
        f"{limit:.3f}",
        "-vf",
        "scale=640:-2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    code, _stdout, stderr = run_captured(args, timeout=60 * 30)
    if code != 0 or not destination.exists():
        raise CraftsError("Proxy failed. " + (stderr or "")[-500:], code="ffmpeg_failed")
    return destination
