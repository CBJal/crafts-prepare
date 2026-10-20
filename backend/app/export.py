"""Build and run ffmpeg exports. Final files are published only after ffprobe."""

from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path

from .errors import Cancelled, CraftsError
from .filters import FilterSpec, chain_for, detect_chain
from .presets import PRESETS, conversion_note
from .probe import Rational, display_dimensions, normalize_rotation, parse_rational, probe_file
from .process import run_captured
from .security import exclusive_output, partial_path, publish_exclusive, safe_filename, safe_suffix

PRORES_PROFILES = {"0", "1", "2", "3"}


def expected_dimensions(source_w: int, source_h: int, spec: FilterSpec) -> tuple[int, int]:
    width, height = source_w, source_h
    if spec.rotate in {"90", "270"}:
        width, height = height, width
    if spec.fit != "none" and spec.width and spec.height:
        return spec.width, spec.height
    return width, height


def build_export_args(
    *,
    ffmpeg: str,
    source: Path,
    partial: Path,
    in_seconds: float,
    duration: float,
    frames: int,
    rate: Rational,
    preset: dict,
    encoder: str,
    audio: bool,
    crf: int | None,
    filter_chain: str | None,
    output_color: dict | None = None,
    noautorotate: bool = False,
) -> list[str]:
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
    ]
    if noautorotate:
        # Pixels are already upright. A second autorotate would turn them again.
        args.append("-noautorotate")
    args.extend(
        [
        "-i",
        str(source),
        "-ss",
        f"{in_seconds:.6f}",
        "-t",
        f"{duration:.6f}",
        "-frames:v",
        str(frames),
        ]
    )
    if filter_chain:
        # One argv value. A removed file-option form is not used.
        args.extend(["-vf", filter_chain])
    args.extend(["-fps_mode", "cfr", "-r", rate.text, "-c:v", encoder])
    profile = preset.get("profile")
    if encoder.startswith("prores") and profile in PRORES_PROFILES:
        args.extend(["-profile:v", profile])
    elif profile and encoder == "dnxhd":
        args.extend(["-profile:v", str(profile)])
    args.extend(["-pix_fmt", preset["pix_fmt"]])
    if crf is not None and encoder in {"libx264", "libx265"}:
        args.extend(["-crf", str(crf)])
    args.extend(preset.get("extra") or [])
    args.extend(["-map", "0:v:0"])
    if audio:
        args.extend(["-map", "0:a:0", "-c:a", preset["audio_encoder"]])
        if preset["audio_encoder"] == "aac":
            args.extend(["-b:a", "192k"])
        args.append("-shortest")
    else:
        args.append("-an")
    if preset["container"] == "mp4":
        args.extend(["-movflags", "+faststart"])
    if output_color:
        for key, flag in (
            ("colorspace", "-colorspace"),
            ("primaries", "-color_primaries"),
            ("trc", "-color_trc"),
            ("range", "-color_range"),
        ):
            if output_color.get(key):
                args.extend([flag, str(output_color[key])])
    # Rotation is applied once, on decode. Do not leave a display matrix that
    # would rotate the finished pixels a second time.
    args.extend(["-metadata:s:v:0", "rotate=0"])
    args.extend(["-f", preset["container"], str(partial)])
    return args


def parse_ffmpeg_progress(line: str, duration: float) -> float | None:
    if "out_time_us=" in line:
        raw = line.split("out_time_us=", 1)[1].strip()
        try:
            seconds = int(raw) / 1_000_000
        except ValueError:
            return None
    elif "out_time_ms=" in line:
        raw = line.split("out_time_ms=", 1)[1].strip()
        try:
            seconds = int(raw) / 1_000_000
        except ValueError:
            return None
    else:
        return None
    if duration <= 0:
        return None
    return max(0.0, min(0.99, seconds / duration))


def run_export(
    *,
    ffmpeg: str,
    ffprobe: str,
    source: Path,
    destination_dir: Path,
    filename: str,
    job_id: str,
    probe: dict,
    in_seconds: float,
    out_seconds: float,
    preset_id: str,
    spec: FilterSpec,
    audio: bool,
    crf: int | None,
    acknowledge_bit_depth: bool,
    shakiness: int,
    accuracy: int,
    cancel: threading.Event,
    on_progress,
    encoders: dict,
    extra_filters: str | None = None,
    stage_files: dict[str, Path] | None = None,
    output_color: dict | None = None,
    noautorotate: bool = False,
) -> dict:
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise CraftsError("Unknown preset.", code="bad_preset")
    encoder = next((name for name in preset["encoder_aliases"] if encoders.get(name)), None)
    if encoder is None:
        raise CraftsError(
            f"{preset['label']} is unavailable because {preset['encoder']} is not in this ffmpeg build.",
            code="encoder_missing",
        )
    if audio and not encoders.get(preset["audio_encoder_required"]):
        raise CraftsError(
            f"Audio encoder {preset['audio_encoder_required']} is missing. Turn audio off or install it.",
            code="encoder_missing",
        )
    video = (probe.get("video") or [None])[0]
    if not video:
        raise CraftsError("The clip has no video stream.", code="unreadable_media")
    if "rotation" not in video:
        fresh = probe_file(ffprobe, source, cancel=cancel)
        if fresh.get("video"):
            probe = fresh
            video = fresh["video"][0]
    rate = Rational(int(video["frame_rate_num"]), int(video["frame_rate_den"]))
    if rate.num <= 0 or rate.den <= 0:
        raise CraftsError("The source frame rate is not a usable rational.", code="bad_rate")
    duration_src = float(probe.get("duration") or 0)
    if in_seconds < 0 or out_seconds <= in_seconds:
        raise CraftsError("Trim in/out must be a positive interval.", code="bad_trim")
    if duration_src and out_seconds > duration_src + rate.frame_duration():
        raise CraftsError("Trim out is past the end of the clip.", code="bad_trim")
    note = conversion_note(video.get("pix_fmt"), video.get("bit_depth"), preset)
    if note["bit_depth_conversion"] and not acknowledge_bit_depth:
        raise CraftsError(note["message"], code="bit_depth", status=409)
    if audio and not probe.get("audio"):
        raise CraftsError("Audio was requested, but the source has no audio stream.", code="bad_audio")
    if spec.stabilization == "vidstab" and not encoders.get("_vidstab"):
        raise CraftsError(
            "Visual stabilization (vidstab) is unavailable because this ffmpeg build has no vidstab filters. "
            "This is not a gyro stabilizer.",
            code="filter_missing",
        )
    if spec.stabilization == "deshake" and not encoders.get("_deshake"):
        raise CraftsError(
            "The deshake filter is not in this ffmpeg build. Deshake is a visual fallback, not gyro stabilization.",
            code="filter_missing",
        )
    if (spec.exposure != 0 or spec.saturation != 1) and not encoders.get("_eq"):
        raise CraftsError("The eq filter is not in this ffmpeg build.", code="filter_missing")
    if spec.lut_path and not encoders.get("_lut3d"):
        raise CraftsError("The lut3d filter is not in this ffmpeg build.", code="filter_missing")

    span = out_seconds - in_seconds
    frames = max(1, int(round(span * rate.num / rate.den)))
    suffix_name = safe_filename(filename)
    final = exclusive_output(destination_dir / suffix_name, source)
    partial = partial_path(final, job_id)
    token = "".join(char for char in job_id if char.isalnum())[:12] or "job"
    work = final.parent / f".crafts-work-{token}"
    work.mkdir()
    try:
        if spec.lut_path:
            from .filters import validate_lut

            shutil.copyfile(validate_lut(spec.lut_path), work / "grade.cube")
            spec.lut_name = "grade.cube"
        for name, staged in (stage_files or {}).items():
            if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
                raise CraftsError("Internal colour filename is not safe.", code="bad_lut")
            shutil.copyfile(staged, work / name)
        if spec.stabilization == "vidstab":
            detect = detect_chain(shakiness, accuracy, "transforms.trf")
            detect_args = [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-ss",
                f"{in_seconds:.6f}",
                "-t",
                f"{span:.6f}",
                "-vf",
                detect,
                "-f",
                "null",
                "-",
            ]
            code, _stdout, stderr = run_captured(
                detect_args,
                timeout=60 * 60,
                cancel=cancel,
                on_stdout=lambda line: on_progress(0.05),
                cwd=str(work),
            )
            trf = work / "transforms.trf"
            if code != 0 or not trf.exists() or trf.stat().st_size == 0:
                raise CraftsError(
                    "vidstabdetect failed. " + (stderr or "")[-800:],
                    code="ffmpeg_failed",
                )
            spec.trf_name = "transforms.trf"
        filter_chain = chain_for(spec)
        if extra_filters:
            filter_chain = extra_filters if not filter_chain else f"{extra_filters},{filter_chain}"
        if not noautorotate and normalize_rotation(video.get("rotation")) in {90, 270} and not filter_chain:
            # An empty graph can skip display rotation. One no-op filter makes
            # FFmpeg apply the stored rotation exactly once before encode.
            filter_chain = "setsar=1"
        chosen_crf = crf if crf is not None else preset.get("crf_default")
        args = build_export_args(
            ffmpeg=ffmpeg,
            source=source,
            partial=partial,
            in_seconds=in_seconds,
            duration=span,
            frames=frames,
            rate=rate,
            preset=preset,
            encoder=encoder,
            audio=audio,
            crf=chosen_crf,
            filter_chain=filter_chain or None,
            output_color=output_color,
            noautorotate=noautorotate,
        )

        def on_line(line: str) -> None:
            progress = parse_ffmpeg_progress(line, span)
            if progress is not None:
                on_progress(progress)

        code, _stdout, stderr = run_captured(
            args, timeout=60 * 60 * 6, cancel=cancel, on_stdout=on_line, cwd=str(work)
        )
        if code != 0 or not partial.exists() or partial.stat().st_size == 0:
            raise CraftsError("ffmpeg failed. " + (stderr or "no output")[-1200:], code="ffmpeg_failed")
        checked = _validate_output(
            ffprobe,
            partial,
            rate=rate,
            frames=frames,
            duration=span,
            spec=spec,
            source_w=(int(video["width"]) if noautorotate else display_dimensions(video)[0]),
            source_h=(int(video["height"]) if noautorotate else display_dimensions(video)[1]),
            pix_fmt=preset["pix_fmt"],
            audio=audio,
            output_color=output_color,
        )
        published = publish_exclusive(partial, final, source)
        return {
            "output_path": str(final),
            "published_identity": published,
            "validation": checked,
            "conversion": note,
            "stabilization": _stab_label(spec),
            "command_preview": _redacted_preview(args),
            "filter_chain": filter_chain,
        }
    except Cancelled:
        _cleanup(partial)
        raise
    except Exception:
        _cleanup(partial)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
        partial.unlink(missing_ok=True)


def _stab_label(spec: FilterSpec) -> dict:
    if spec.stabilization == "off":
        return {"mode": "off", "kind": "none"}
    if spec.stabilization == "vidstab":
        return {
            "mode": "vidstab",
            "kind": "visual",
            "label": "FFmpeg vidstabdetect + vidstabtransform (visual, not gyro)",
            "smoothing": spec.stab_smoothing,
            "crop": spec.stab_crop,
            "zoom": spec.stab_zoom,
            "optzoom": spec.stab_optzoom,
        }
    return {
        "mode": "deshake",
        "kind": "visual-fallback",
        "label": "FFmpeg deshake fallback (visual, not gyro)",
        "rx": spec.deshake_rx,
        "ry": spec.deshake_ry,
    }


def _validate_output(
    ffprobe: str,
    path: Path,
    *,
    rate: Rational,
    frames: int,
    duration: float,
    spec: FilterSpec,
    source_w: int,
    source_h: int,
    pix_fmt: str,
    audio: bool,
    output_color: dict | None = None,
) -> dict:
    probed = probe_file(ffprobe, path)
    video = (probed.get("video") or [None])[0]
    if not video:
        raise CraftsError("Export did not contain a decodable video stream.", code="invalid_output")
    got_rate = parse_rational(video.get("frame_rate"))
    if got_rate is None or got_rate.num != rate.num or got_rate.den != rate.den:
        raise CraftsError(
            f"Output frame rate {video.get('frame_rate')} does not match source {rate.text}.",
            code="invalid_output",
        )
    got_duration = float(probed.get("duration") or video.get("duration") or 0)
    tolerance = rate.frame_duration() + 0.002
    if abs(got_duration - duration) > tolerance:
        raise CraftsError(
            f"Output duration {got_duration:.4f}s is not within one frame of {duration:.4f}s.",
            code="invalid_output",
        )
    expect_w, expect_h = expected_dimensions(source_w, source_h, spec)
    if int(video["width"]) != expect_w or int(video["height"]) != expect_h:
        raise CraftsError(
            f"Output dimensions {video['width']}x{video['height']} do not match {expect_w}x{expect_h}.",
            code="invalid_output",
        )
    if video.get("pix_fmt") != pix_fmt:
        raise CraftsError(
            f"Output pixel format {video.get('pix_fmt')} does not match the preset {pix_fmt}.",
            code="invalid_output",
        )
    has_audio = bool(probed.get("audio"))
    if audio and not has_audio:
        raise CraftsError("Output is missing the requested audio stream.", code="invalid_output")
    if not audio and has_audio:
        raise CraftsError("Output contains audio even though audio was turned off.", code="invalid_output")
    if output_color:
        problems = []
        expected = {
            field: output_color[key]
            for field, key in (("color_space", "colorspace"), ("color_primaries", "primaries"),
                               ("color_transfer", "trc"), ("color_range", "range"))
            if output_color.get(key)
        }
        for key, want in expected.items():
            if not want:
                continue
            got = (video.get(key) or "").casefold()
            if key == "color_range" and not got and (video.get("codec") or "") == "prores":
                # ProRes does not store color_range. The scale filter still wrote limited-range samples.
                continue
            if got != str(want).casefold():
                problems.append(f"{key}={video.get(key) or 'missing'}")
        if problems:
            raise CraftsError(
                "Output colour tags do not match the requested plan (" + ", ".join(problems) + "). It was not published.",
                code="invalid_output",
            )
    return {
        "duration": got_duration,
        "requested_duration": duration,
        "frames_requested": frames,
        "width": video["width"],
        "height": video["height"],
        "frame_rate": got_rate.text,
        "pix_fmt": video.get("pix_fmt"),
        "bit_depth": video.get("bit_depth"),
        "audio": has_audio,
        "codec": video.get("codec"),
    }


def _cleanup(*paths: Path) -> None:
    for path in paths:
        try:
            if path.exists() and path.is_file():
                path.unlink()
        except OSError:
            pass


def _redacted_preview(args: list[str]) -> list[str]:
    return list(args)


def output_filename(stem: str, suffix: str, container: str) -> str:
    clean_stem = safe_filename(stem) if _stem_ok(stem) else "clip"
    extra = safe_suffix(suffix)
    return f"{clean_stem}{extra}.{container}"


def _stem_ok(stem: str) -> bool:
    try:
        safe_filename(stem)
        return True
    except CraftsError:
        return False
