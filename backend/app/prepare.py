"""One preparation job per clip: optional gyro stage, then colour and encode.

The gyro stage, when requested, renders the original clip into an owned
high-quality intermediate. Colour, LUT, and the final preset run after that.
The intermediate is never published. A failed or cancelled stage removes owned
temporary files and does not publish the requested output.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from .colour import build_plan, ensure_plan_cubes, output_tags_for
from .errors import Cancelled, CraftsError
from .export import output_filename, run_export
from .filters import FilterSpec
from .gyroflow_cli import run_gyroflow
from .presets import PRESETS, conversion_note
from .probe import probe_file
from .recipe import normalize_recipe, snapshot_lut
from .security import exclusive_output, resolve_existing, safe_suffix
from .sony_xml import resolve_profile


def _check(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise Cancelled("Cancelled before the next stage.")


def plan_prepare_clip(clip: dict, shared: dict, destination: Path, suffix: str) -> dict:
    recipe = normalize_recipe(clip.get("recipe"))
    if isinstance(shared.get("recipe_overrides"), dict):
        # Shared controls from Apply-to-selected are already stored per clip.
        # A queue-time override is still snapshotted below and does not change AUTO resolution.
        recipe = normalize_recipe(shared["recipe_overrides"], previous=recipe)
    preset = PRESETS.get(shared.get("preset_id") or "")
    if preset is None:
        raise CraftsError("Unknown preset.", code="bad_preset")
    use_trim = bool(shared.get("use_trim"))
    duration = float(clip["probe"].get("duration") or 0)
    if use_trim:
        start = float(clip.get("trim_in") or 0)
        end = float(clip.get("trim_out") or 0)
    else:
        start = 0.0
        end = duration
    if end <= start:
        raise CraftsError("This clip has no positive duration to export.", code="bad_trim")
    video = (clip["probe"].get("video") or [None])[0] or {}
    note = conversion_note(video.get("pix_fmt"), video.get("bit_depth"), preset)
    if note["bit_depth_conversion"] and not shared.get("acknowledge_bit_depth_conversion"):
        raise CraftsError(note["message"], code="bit_depth", status=409)
    profile = resolve_profile(clip.get("sidecar"), clip.get("probe"), recipe["profile"])
    gyro = bool(shared["gyro_enabled"]) if "gyro_enabled" in shared else bool(recipe["gyro_enabled"])
    if gyro:
        recipe = dict(recipe)
        recipe["gyro_enabled"] = True
    filename = output_filename(_stem(clip["name"]), safe_suffix(suffix), preset["container"])
    final = exclusive_output(destination / filename, Path(clip["path"]))
    fit = str(shared.get("fit") or "none")
    width = shared.get("width")
    height = shared.get("height")
    request = {
        "clip_id": clip["id"],
        "source_path": clip["path"],
        "source_sha256": clip["sha256"],
        "preset_id": preset["id"],
        "in_seconds": start,
        "out_seconds": end,
        "use_trim": use_trim,
        "destination_dir": str(destination),
        "filename": filename,
        "suffix": safe_suffix(suffix),
        "audio": bool(shared.get("audio", True)),
        "crf": shared.get("crf"),
        "acknowledge_bit_depth_conversion": bool(shared.get("acknowledge_bit_depth_conversion")),
        "fit": fit,
        "width": width,
        "height": height,
        "rotate": str(shared.get("rotate") or "0"),
        "recipe": recipe,
        "profile": profile,
        "conversion": note,
        "gyro_enabled": gyro,
    }
    return {"kind": "prepare", "filename": filename, "output": str(final), "request": request}


def _stem(name: str) -> str:
    from .service import _stem as service_stem

    return service_stem(name)


def run_prepare(
    *,
    ffmpeg: str,
    ffprobe: str,
    settings: dict,
    source: Path,
    source_sha256: str,
    probe: dict,
    sidecar: dict | None,
    request: dict,
    job_id: str,
    cancel: threading.Event,
    on_progress,
    encoders: dict,
) -> dict:
    from .importer import sha256_file
    from .security import discard_owned_output

    if sha256_file(source, cancel=cancel) != source_sha256:
        raise CraftsError("Source checksum changed before preparation. Nothing was published.", code="source_changed")
    recipe = normalize_recipe(request.get("recipe"))
    profile = request.get("profile") if isinstance(request.get("profile"), dict) else resolve_profile(sidecar, probe, recipe["profile"])
    if profile.get("pipeline") == "unknown" and not recipe.get("input_lut") and not recipe.get("look_includes_camera"):
        raise CraftsError(profile.get("message") or "The colour profile is unresolved.", code="colour_unresolved")
    destination = resolve_existing(request["destination_dir"])
    filename = request["filename"]
    final = exclusive_output(destination / filename, source)
    token = "".join(char for char in job_id if char.isalnum())[:12] or "job"
    work = destination / f".crafts-prepare-{token}"
    if work.exists():
        raise CraftsError("A preparation folder for this job already exists. Nothing was published.", code="collision")
    work.mkdir()
    gyro_result = None
    try:
        _check(cancel)
        colour_source = source
        colour_probe = probe
        if request.get("gyro_enabled"):
            on_progress(0.02)
            intermediate = work / "gyro-intermediate.mov"
            gyro_result = run_gyroflow(
                settings=settings,
                media=source,
                project=None,
                gyro_file=None,
                preset=None,
                output=intermediate,
                ffprobe=ffprobe,
                cancel=cancel,
                on_progress=lambda value: on_progress(min(0.45, 0.05 + 0.4 * float(value))),
                automatic=True,
                output_settings={
                    "format": "prores_422",
                    "audio": bool(request.get("audio", True)) and bool(probe.get("audio")),
                },
                stabilization={
                    "smoothness": recipe["gyro_smoothness"],
                    "fov": recipe["gyro_fov"],
                    "horizon_lock_amount": recipe["gyro_horizon"],
                },
            )
            produced = Path(str((gyro_result or {}).get("output_path") or intermediate))
            if produced.resolve().parent != work.resolve() or not produced.is_file():
                raise CraftsError(
                    "Gyro stage did not leave its intermediate inside the job folder. Nothing was published.",
                    code="gyro_unverified",
                )
            colour_source = produced
            colour_probe = probe_file(ffprobe, colour_source, cancel=cancel)
            on_progress(0.5)
        _check(cancel)
        plan = build_plan(
            pipeline=str(profile.get("pipeline") or "unknown"),
            input_range=str(profile.get("input_range") or "tv"),
            exposure_ev=float(recipe["exposure_ev"]),
            contrast=float(recipe["contrast"]),
            saturation=float(recipe["saturation"]),
            has_input_lut=bool(recipe.get("input_lut")),
            has_look_lut=bool(recipe.get("look_lut")),
            look_includes_camera=bool(recipe.get("look_includes_camera")),
        )
        cube_dir = work / "cubes"
        cube_dir.mkdir()
        snapshots: dict[str, dict] = {}
        if recipe.get("input_lut"):
            snapshots["input"] = snapshot_lut(recipe["input_lut"], cube_dir, "input.cube")
        if recipe.get("look_lut"):
            snapshots["look"] = snapshot_lut(recipe["look_lut"], cube_dir, "look.cube")
        fragments = ensure_plan_cubes(plan, cube_dir)
        stage_files = {path.name: path for path in cube_dir.iterdir() if path.is_file() and path.suffix == ".cube"}
        start, end = _colour_span(request, colour_probe, same_file=colour_source == source)
        spec = FilterSpec(
            rotate=str(request.get("rotate") or "0"),
            fit=str(request.get("fit") or "none"),
            width=request.get("width"),
            height=request.get("height"),
            stabilization="off",
        )
        result = run_export(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            source=colour_source,
            destination_dir=destination,
            filename=filename,
            job_id=job_id,
            probe=colour_probe,
            in_seconds=start,
            out_seconds=end,
            preset_id=request["preset_id"],
            spec=spec,
            audio=bool(request.get("audio", True)),
            crf=request.get("crf"),
            acknowledge_bit_depth=bool(request.get("acknowledge_bit_depth_conversion")) or colour_source != source,
            shakiness=5,
            accuracy=15,
            cancel=cancel,
            on_progress=lambda value: on_progress(0.5 + 0.5 * float(value)),
            encoders=encoders,
            extra_filters=",".join(fragments) if fragments else None,
            stage_files=stage_files,
            output_color=output_tags_for(plan),
        )
        published = Path(result["output_path"])
        if work.resolve() in published.resolve().parents or published.resolve() == work.resolve():
            discard_owned_output(published, result.get("published_identity"), source)
            raise CraftsError("Refusing to treat a temporary file as the finished clip.", code="invalid_output")
        if sha256_file(source) != source_sha256:
            discard_owned_output(published, result.get("published_identity"), source)
            raise CraftsError(
                "Source checksum changed during preparation. The published file was removed if this job still owned it.",
                code="source_changed",
            )
        if final.exists() and published.resolve() != final.resolve():
            raise CraftsError("Preparation published an unexpected path.", code="invalid_output")
        result["colour"] = {
            "pipeline": plan.pipeline,
            "input_range": plan.input_range,
            "exposure_ev": plan.exposure_ev,
            "contrast": plan.contrast,
            "saturation": plan.saturation,
            "explanation": plan.explanation,
            "profile_status": profile.get("status"),
            "profile_source": profile.get("source"),
            "profile_message": profile.get("message"),
            "input_lut_sha256": (snapshots.get("input") or {}).get("sha256") or "",
            "look_lut_sha256": (snapshots.get("look") or {}).get("sha256") or "",
            "look_includes_camera": bool(recipe.get("look_includes_camera")),
            "output_tags": output_tags_for(plan),
        }
        result["gyro"] = (
            {
                "applied": True,
                "samples": gyro_result.get("motion_samples") if gyro_result else None,
                "stabilization_written": (gyro_result or {}).get("stabilization_written") or {},
                "intermediate_published": False,
                "note": "Embedded gyro was rendered by Gyroflow before colour. The intermediate file was not published.",
            }
            if request.get("gyro_enabled")
            else {"applied": False, "reason": "Gyro was off for this clip."}
        )
        result["source_sha256_unchanged"] = True
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _colour_span(request: dict, colour_probe: dict, *, same_file: bool) -> tuple[float, float]:
    if request.get("use_trim"):
        return float(request["in_seconds"]), float(request["out_seconds"])
    if same_file:
        return float(request["in_seconds"]), float(request["out_seconds"])
    duration = float(colour_probe.get("duration") or 0)
    if duration <= 0:
        raise CraftsError("The stabilized intermediate has no duration. It was not published.", code="gyro_unverified")
    return 0.0, duration
