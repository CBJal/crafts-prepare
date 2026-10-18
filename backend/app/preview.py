"""Frame thumbnails and browser-oriented proxies. Sources are never rewritten."""

from __future__ import annotations

from pathlib import Path

from .errors import CraftsError
from .filters import FilterSpec, write_filter_script
from .process import run_captured


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
    script_path = destination.with_suffix(".filter")
    # Stills do not include temporal stabilizers. The export path does.
    still = FilterSpec(**{**spec.__dict__, "include_temporal": False, "stabilization": "off"})
    script = write_filter_script(still, script_path)
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
    if script is not None:
        args.extend(["-filter_script:v", str(script)])
    args.extend(["-q:v", "3", str(destination)])
    try:
        code, _stdout, stderr = run_captured(args, timeout=120)
    finally:
        script_path.unlink(missing_ok=True)
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
    limit = min(duration or 30, 60)
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
