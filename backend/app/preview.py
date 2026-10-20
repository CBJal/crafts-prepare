"""Frame thumbnails and browser-oriented proxies. Sources are never rewritten."""

from __future__ import annotations

import os
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
    cancel=None,
    extra_filters: str | None = None,
    stage_files: dict | None = None,
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
        if stage_files:
            for name, staged in stage_files.items():
                if not isinstance(name, str) or "/" in name or "\\" in name or name.startswith("."):
                    raise CraftsError("Internal preview filename is not safe.", code="bad_lut")
                shutil.copyfile(staged, work / name)
        chain = chain_for(still)
        if extra_filters:
            chain = extra_filters if not chain else f"{extra_filters},{chain}"
        if chain:
            chain = f"{chain},scale=320:-2"
        else:
            chain = "scale=320:-2"
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
        code, _stdout, stderr = run_captured(args, timeout=120, cwd=str(work), cancel=cancel)
    if code != 0 or not destination.exists():
        raise CraftsError("Thumbnail failed. " + (stderr or "")[-500:], code="ffmpeg_failed")
    return destination


def browser_playable(probe: dict) -> bool:
    videos = probe.get("video") if isinstance(probe, dict) else None
    if not isinstance(videos, list) or not videos or not isinstance(videos[0], dict):
        return False
    video = videos[0]
    if not video:
        return False
    codec = (video.get("codec") or "").lower()
    pix = video.get("pix_fmt") or ""
    if codec != "h264":
        return False
    if pix not in {"yuv420p", "yuvj420p"}:
        return False
    return True


def preview_seconds(duration: float, max_seconds: float | None) -> float | None:
    """None means the whole clip. The native viewer passes None so nothing is cut at 60s."""
    if max_seconds is None:
        return None
    if duration and duration > 0:
        return min(duration, max_seconds)
    return max_seconds


def make_proxy(
    *,
    ffmpeg: str,
    source: Path,
    destination: Path,
    duration: float,
    cancel=None,
    on_start=None,
    max_seconds: float | None = PROXY_LIMIT_SECONDS,
    video_filter: str | None = None,
    stage_files: dict | None = None,
) -> Path:
    limit = preview_seconds(duration, max_seconds)
    # The staged file must end in .mp4. A suffix of ".mp4.partial" makes FFmpeg
    # guess a muxer named "partial" and fail before any frames are written.
    partial = destination.with_name(destination.stem + ".writing.mp4")
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
    ]
    if limit is not None:
        args.extend(["-t", f"{limit:.3f}"])
    args.extend(
        [
        "-vf",
        video_filter or "scale=640:-2",
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
        "-f",
        "mp4",
        str(partial),
        ]
    )
    work = None
    if stage_files:
        work = destination.parent / f".proxy-cubes-{destination.stem}"
        work.mkdir(parents=True, exist_ok=True)
        for name, staged in stage_files.items():
            if not isinstance(name, str) or "/" in name or "\\" in name or name.startswith("."):
                raise CraftsError("Internal preview filename is not safe.", code="bad_lut")
            shutil.copyfile(staged, work / name)
    try:
        try:
            code, _stdout, stderr = run_captured(
                args,
                timeout=60 * 30,
                cancel=cancel,
                on_start=on_start,
                cwd=str(work) if work is not None else None,
            )
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        if cancel is not None and cancel.is_set():
            partial.unlink(missing_ok=True)
            from .errors import Cancelled

            raise Cancelled()
        if code != 0 or not partial.is_file() or partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
            raise CraftsError("Proxy failed. " + (stderr or "")[-500:], code="ffmpeg_failed")
        os.replace(partial, destination)
        return destination
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
