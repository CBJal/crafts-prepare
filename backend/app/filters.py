"""Video filter graphs shared by still previews and exports.

Every numeric option is validated before it is formatted. Paths inserted into
the graph are files this process created, or a .cube file that passed the LUT
checks, and they are escaped for the filter parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import CraftsError
from .security import MAX_LUT_BYTES, ffmpeg_filter_path, resolve_existing


@dataclass
class FilterSpec:
    exposure: float = 0.0
    saturation: float = 1.0
    lut_path: str | None = None
    rotate: str = "0"
    fit: str = "none"
    width: int | None = None
    height: int | None = None
    stabilization: str = "off"
    stab_smoothing: int = 10
    stab_crop: str = "black"
    stab_zoom: int = 0
    stab_optzoom: int = 0
    deshake_rx: int = 16
    deshake_ry: int = 16
    vidstab_trf: Path | None = None
    include_temporal: bool = True


def validate_adjustment(spec: FilterSpec) -> None:
    if spec.exposure < -1 or spec.exposure > 1:
        raise CraftsError("Exposure (eq brightness) must be between -1 and 1.", code="bad_adjust")
    if spec.saturation < 0 or spec.saturation > 3:
        raise CraftsError("Saturation must be between 0 and 3.", code="bad_adjust")
    if spec.rotate not in {"0", "90", "180", "270"}:
        raise CraftsError("Rotate must be 0, 90, 180, or 270 degrees clockwise.", code="bad_adjust")
    if spec.fit not in {"none", "resize", "crop", "letterbox"}:
        raise CraftsError("Fit must be none, resize, crop, or letterbox.", code="bad_adjust")
    if spec.fit != "none":
        if spec.width is None or spec.height is None:
            raise CraftsError("Resize, crop, and letterbox need width and height.", code="bad_adjust")
        if spec.width < 2 or spec.height < 2 or spec.width > 7680 or spec.height > 4320:
            raise CraftsError("Output dimensions must be between 2 and 7680×4320.", code="bad_adjust")
        if spec.width % 2 or spec.height % 2:
            raise CraftsError("Output width and height must be even.", code="bad_adjust")
    if spec.stabilization not in {"off", "vidstab", "deshake"}:
        raise CraftsError("Stabilization must be off, vidstab, or deshake.", code="bad_adjust")
    if not 0 <= spec.stab_smoothing <= 1000:
        raise CraftsError("vidstab smoothing must be between 0 and 1000.", code="bad_adjust")
    if spec.stab_crop not in {"keep", "black"}:
        raise CraftsError("vidstab crop must be keep or black.", code="bad_adjust")
    if not -100 <= spec.stab_zoom <= 100:
        raise CraftsError("vidstab zoom must be between -100 and 100.", code="bad_adjust")
    if spec.stab_optzoom not in {0, 1, 2}:
        raise CraftsError("vidstab optzoom must be 0 (off), 1 (static), or 2 (adaptive).", code="bad_adjust")
    if not 0 <= spec.deshake_rx <= 64 or not 0 <= spec.deshake_ry <= 64:
        raise CraftsError("deshake rx and ry must be between 0 and 64.", code="bad_adjust")
    if spec.lut_path:
        validate_lut(spec.lut_path)


def validate_lut(raw: str) -> Path:
    path = resolve_existing(raw)
    if path.suffix.lower() != ".cube":
        raise CraftsError("LUT files must use the .cube extension.", code="bad_lut")
    if path.stat().st_size > MAX_LUT_BYTES:
        raise CraftsError("LUT file exceeds the size limit.", code="bad_lut")
    head = path.read_bytes()[:8192]
    if b"LUT_3D_SIZE" not in head and b"LUT_1D_SIZE" not in head:
        raise CraftsError("File does not look like a .cube LUT (missing LUT_3D_SIZE).", code="bad_lut")
    return path


def chain_for(spec: FilterSpec) -> str:
    validate_adjustment(spec)
    parts: list[str] = []
    if spec.include_temporal and spec.stabilization == "vidstab":
        if spec.vidstab_trf is None:
            raise CraftsError("vidstab transform file is missing.", code="bad_adjust")
        trf = ffmpeg_filter_path(spec.vidstab_trf)
        parts.append(
            "vidstabtransform="
            f"input='{trf}':smoothing={int(spec.stab_smoothing)}:"
            f"crop={spec.stab_crop}:zoom={int(spec.stab_zoom)}:optzoom={int(spec.stab_optzoom)}"
        )
    elif spec.include_temporal and spec.stabilization == "deshake":
        parts.append(
            f"deshake=rx={int(spec.deshake_rx)}:ry={int(spec.deshake_ry)}:edge=0"
        )
    if spec.exposure != 0 or spec.saturation != 1:
        parts.append(f"eq=brightness={spec.exposure:.4f}:saturation={spec.saturation:.4f}")
    if spec.lut_path:
        lut = validate_lut(spec.lut_path)
        parts.append(f"lut3d=file='{ffmpeg_filter_path(lut)}'")
    if spec.rotate == "90":
        parts.append("transpose=1")
    elif spec.rotate == "180":
        parts.append("transpose=1,transpose=1")
    elif spec.rotate == "270":
        parts.append("transpose=2")
    if spec.fit == "resize" and spec.width and spec.height:
        parts.append(f"scale={int(spec.width)}:{int(spec.height)}:flags=lanczos")
    elif spec.fit == "crop" and spec.width and spec.height:
        parts.append(
            f"scale={int(spec.width)}:{int(spec.height)}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={int(spec.width)}:{int(spec.height)}"
        )
    elif spec.fit == "letterbox" and spec.width and spec.height:
        parts.append(
            f"scale={int(spec.width)}:{int(spec.height)}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={int(spec.width)}:{int(spec.height)}:(ow-iw)/2:(oh-ih)/2"
        )
    return ",".join(parts)


def write_filter_script(spec: FilterSpec, destination: Path) -> Path | None:
    chain = chain_for(spec)
    if not chain:
        return None
    destination.write_text(chain + "\n", encoding="utf-8")
    return destination


def detect_chain(shakiness: int, accuracy: int, trf: Path) -> str:
    if not 1 <= shakiness <= 10:
        raise CraftsError("vidstab shakiness must be between 1 and 10.", code="bad_adjust")
    if not 1 <= accuracy <= 15:
        raise CraftsError("vidstab accuracy must be between 1 and 15.", code="bad_adjust")
    return (
        f"vidstabdetect=shakiness={int(shakiness)}:accuracy={int(accuracy)}:"
        f"result='{ffmpeg_filter_path(trf)}'"
    )


# Documented auto values. The UI sends these numbers; they are not a hidden mode.
VIDSTAB_AUTO = {
    "shakiness": 5,
    "accuracy": 15,
    "smoothing": 10,
    "crop": "black",
    "zoom": 0,
    "optzoom": 2,
}
