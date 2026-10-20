"""Build the canonical colour filter for one clip."""

from __future__ import annotations

from pathlib import Path

from ..colour import build_plan, ensure_plan_cubes
from ..errors import CraftsError
from ..recipe import default_recipe
from ..sony_xml import resolve_profile


def clip_recipe(clip: dict) -> dict:
    recipe = clip.get("recipe")
    if isinstance(recipe, dict):
        return {**default_recipe(), **recipe}
    return default_recipe()


def describe_clip(clip: dict) -> dict:
    recipe = clip_recipe(clip)
    sidecar = clip.get("sidecar") if isinstance(clip.get("sidecar"), dict) else {"status": "missing", "message": "No sidecar."}
    profile = resolve_profile(sidecar, clip.get("probe"), str(recipe.get("profile") or "auto"))
    return {"recipe": recipe, "sidecar": sidecar, "profile": profile}


def filters_for_clip(clip: dict, directory: Path, *, adjusted: bool, cache: Path | None = None) -> tuple[str, dict[str, Path], dict]:
    """Return a filter string, cube files, and the profile record.

    ``adjusted`` false is the original image: no camera transform and no LUT.
    """
    described = describe_clip(clip)
    if not adjusted:
        return "", {}, described["profile"]
    recipe = described["recipe"]
    profile = described["profile"]
    plan = build_plan(
        pipeline=str(profile.get("pipeline") or "unknown"),
        input_range=str(profile.get("input_range") or "tv"),
        exposure_ev=float(recipe.get("exposure_ev") or 0),
        contrast=float(recipe.get("contrast") if recipe.get("contrast") is not None else 1),
        saturation=float(recipe.get("saturation") if recipe.get("saturation") is not None else 1),
        has_input_lut=bool(recipe.get("input_lut")),
        has_look_lut=bool(recipe.get("look_lut")),
        look_includes_camera=bool(recipe.get("look_includes_camera")),
    )
    directory.mkdir(parents=True, exist_ok=True)
    if recipe.get("input_lut"):
        from ..recipe import snapshot_lut

        snapshot_lut(recipe["input_lut"], directory, "input.cube")
    if recipe.get("look_lut"):
        from ..recipe import snapshot_lut

        snapshot_lut(recipe["look_lut"], directory, "look.cube")
    fragments = ensure_plan_cubes(plan, directory, cache=cache)
    files = {path.name: path for path in directory.iterdir() if path.is_file() and path.suffix == ".cube"}
    return ",".join(fragments), files, profile


def safe_filters(clip: dict, directory: Path, *, adjusted: bool, cache: Path | None = None) -> tuple[str, dict[str, Path], str]:
    try:
        filters, files, profile = filters_for_clip(clip, directory, adjusted=adjusted, cache=cache)
    except CraftsError as exc:
        return "", {}, exc.message
    return filters, files, str(profile.get("message") or "")
