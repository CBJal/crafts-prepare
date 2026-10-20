"""Documented camera-to-Rec.709 normalization.

This is not a Sony look LUT and it does not include Sony binaries. The S-Log3
curve is the public Sony formula (10-bit code 95 = 0%, 420 = 18%, 598 = 90%).
S-Gamut3.Cine and BT.2020 are converted with the published primaries and D65.
HLG uses the BT.2100 inverse OETF and the reference OOTF (system gamma 1.2).
Rec.709 uses the BT.709 OETF.

Exposure is stops of scene-linear light (multiply by 2^EV) after the camera
decode. It is not FFmpeg's eq brightness. Contrast is a power about 18%
scene reflectance. Saturation is a Rec.709-luma scale in linear light after
the gamut matrix. Output of a normalizing plan is full-range Rec.709 code in
0..1, which the filter then writes as limited-range video with bt709 tags.

An input LUT replaces that normalization. A look LUT runs after it. When
``look_includes_camera`` is set, automatic normalization is skipped so a look
that already contains the camera transform is not applied twice.
"""

from __future__ import annotations

import hashlib
import math
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from .errors import CraftsError

SLOG3_KNEE = 171.2102946929
SLOG3_LINEAR_KNEE = 0.01125000
REC709_OETF_KNEE = 0.018
REC709_EOTF_KNEE = 4.5 * REC709_OETF_KNEE
HLG_A = 0.17883277
HLG_B = 1.0 - 4.0 * HLG_A
HLG_C = 0.5 - HLG_A * math.log(4.0 * HLG_A)
HLG_DIFFUSE_SIGNAL = 0.75
# BT.2100 HLG OOTF system gamma for a 1000 cd/m^2 reference display.
HLG_SYSTEM_GAMMA = 1.2
# Sony publishes 90% reflectance as S-Log3 nominal white (code 598).
SLOG3_WHITE_REFLECTANCE = 0.9
D65 = (0.3127, 0.3290)
REC709_PRIMARIES = ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06))
BT2020_PRIMARIES = ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046))
# Sony Technical Summary for S-Gamut3.Cine / S-Log3.
SGAMUT3CINE_PRIMARIES = ((0.766, 0.275), (0.225, 0.800), (0.089, -0.087))
REC709_LUMA = (0.2126, 0.7152, 0.0722)
BT2020_LUMA = (0.2627, 0.6780, 0.0593)
# HLG still uses a 65³ lattice because its OOTF depends on luma. S-Log3 does
# not: it is a per-channel curve, an exact matrix, and a per-channel encode.
# A 65³ S-Log lattice was about 0.017 off near white on Windows, outside the
# 0.02 patch bound. The 1D tables below are what the S-Log path ships.
LUT_SIZE = 65
LUT1D_SIZE = 4096
# S-Log3 code 1.0 is about 38.4 reflectance. The gamut matrix can raise one
# channel to roughly twice that. Dividing by 128 keeps the linear sample in
# 0..1 so the next 1D table is not clipped before the white map.
SCENE_SCALE = 128.0


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def slog3_decode(normalized_code: float) -> float:
    """Inverse S-Log3. Input is code/1023 (full range). Output is reflectance."""
    if normalized_code >= SLOG3_KNEE / 1023.0:
        return (10 ** ((normalized_code * 1023.0 - 420.0) / 261.5)) * (0.18 + 0.01) - 0.01
    return (normalized_code * 1023.0 - 95.0) * SLOG3_LINEAR_KNEE / (SLOG3_KNEE - 95.0)


def slog3_encode(reflectance: float) -> float:
    """Forward S-Log3. Output is code/1023."""
    if reflectance >= SLOG3_LINEAR_KNEE:
        return (420.0 + math.log10((reflectance + 0.01) / (0.18 + 0.01)) * 261.5) / 1023.0
    return (reflectance * (SLOG3_KNEE - 95.0) / SLOG3_LINEAR_KNEE + 95.0) / 1023.0


def hlg_inverse_oetf(signal: float) -> float:
    """BT.2100 inverse HLG OETF. Scene light is 0 at black and 1 at nominal peak."""
    if signal <= 0.5:
        return (signal * signal) / 3.0
    return (math.exp((signal - HLG_C) / HLG_A) + HLG_B) / 12.0


def rec709_oetf(linear: float) -> float:
    if linear < REC709_OETF_KNEE:
        return 4.5 * linear
    return 1.099 * (linear ** 0.45) - 0.099


def rec709_inverse_oetf(encoded: float) -> float:
    if encoded < REC709_EOTF_KNEE:
        return encoded / 4.5
    return ((encoded + 0.099) / 1.099) ** (1.0 / 0.45)


def _xy_xyz(xy: tuple[float, float]) -> tuple[float, float, float]:
    x, y = xy
    if y == 0:
        raise CraftsError("A colour primary has a zero y coordinate.", code="colour")
    return (x / y, 1.0, (1.0 - x - y) / y)


def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _mul_vec(matrix: tuple[tuple[float, float, float], ...], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(_dot(row, vector) for row in matrix)  # type: ignore[return-value]


def _invert(matrix: tuple[tuple[float, float, float], ...]) -> tuple[tuple[float, float, float], ...]:
    a, b, c = matrix[0]
    d, e, f = matrix[1]
    g, h, i = matrix[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise CraftsError("Colour primaries do not form a matrix.", code="colour")
    inv = 1.0 / det
    return (
        ((e * i - f * h) * inv, (c * h - b * i) * inv, (b * f - c * e) * inv),
        ((f * g - d * i) * inv, (a * i - c * g) * inv, (c * d - a * f) * inv),
        ((d * h - e * g) * inv, (b * g - a * h) * inv, (a * e - b * d) * inv),
    )


def _mul(left: tuple[tuple[float, float, float], ...], right: tuple[tuple[float, float, float], ...]) -> tuple[tuple[float, float, float], ...]:
    columns = tuple(zip(*right))
    return tuple(tuple(_dot(row, column) for column in columns) for row in left)  # type: ignore[return-value]


def rgb_to_xyz(primaries: tuple[tuple[float, float], ...], white: tuple[float, float] = D65) -> tuple[tuple[float, float, float], ...]:
    columns = [_xy_xyz(primary) for primary in primaries]
    unscaled = (
        (columns[0][0], columns[1][0], columns[2][0]),
        (columns[0][1], columns[1][1], columns[2][1]),
        (columns[0][2], columns[1][2], columns[2][2]),
    )
    scale = _mul_vec(_invert(unscaled), _xy_xyz(white))
    return (
        (unscaled[0][0] * scale[0], unscaled[0][1] * scale[1], unscaled[0][2] * scale[2]),
        (unscaled[1][0] * scale[0], unscaled[1][1] * scale[1], unscaled[1][2] * scale[2]),
        (unscaled[2][0] * scale[0], unscaled[2][1] * scale[1], unscaled[2][2] * scale[2]),
    )


def conversion_matrix(source: tuple[tuple[float, float], ...], destination: tuple[tuple[float, float], ...]) -> tuple[tuple[float, float, float], ...]:
    return _mul(_invert(rgb_to_xyz(destination)), rgb_to_xyz(source))


SGAMUT3CINE_TO_REC709 = conversion_matrix(SGAMUT3CINE_PRIMARIES, REC709_PRIMARIES)
BT2020_TO_REC709 = conversion_matrix(BT2020_PRIMARIES, REC709_PRIMARIES)


def _contrast(value: float, amount: float) -> float:
    if amount == 1.0:
        return value
    if value <= 0.0:
        return 0.0
    return 0.18 * ((value / 0.18) ** amount)


def _saturate(rgb: tuple[float, float, float], amount: float, luma: tuple[float, float, float] = REC709_LUMA) -> tuple[float, float, float]:
    if amount == 1.0:
        return rgb
    y = _dot(luma, rgb)
    return tuple(y + (channel - y) * amount for channel in rgb)  # type: ignore[return-value]


def _hlg_ootf(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    y = _dot(BT2020_LUMA, rgb)
    if y <= 1e-8:
        return (0.0, 0.0, 0.0)
    gain = y ** (HLG_SYSTEM_GAMMA - 1.0)
    return (rgb[0] * gain, rgb[1] * gain, rgb[2] * gain)


def hlg_diffuse_display() -> float:
    """Display-linear value of a 75% HLG signal after the reference OOTF."""
    scene = hlg_inverse_oetf(HLG_DIFFUSE_SIGNAL)
    return _hlg_ootf((scene, scene, scene))[0]


HLG_SDR_SCALE = 1.0 / hlg_diffuse_display()


def _map_rec709(rgb: tuple[float, float, float], saturation: float) -> tuple[float, float, float]:
    clipped = tuple(_clamp01(channel) for channel in rgb)
    saturated = _saturate(clipped, saturation)  # type: ignore[arg-type]
    return tuple(_clamp01(rec709_oetf(_clamp01(channel))) for channel in saturated)  # type: ignore[return-value]


def slog3_pixel(rgb: tuple[float, float, float], *, exposure_ev: float, contrast: float, saturation: float) -> tuple[float, float, float]:
    gain = 2.0 ** exposure_ev
    scene = []
    for channel in rgb:
        linear = max(0.0, slog3_decode(channel)) * gain
        scene.append(_contrast(linear, contrast))
    converted = _mul_vec(SGAMUT3CINE_TO_REC709, (scene[0], scene[1], scene[2]))
    # 90% reflectance becomes Rec.709 reference white. Highlights above that clip.
    mapped = tuple(channel / SLOG3_WHITE_REFLECTANCE for channel in converted)
    return _map_rec709(mapped, saturation)  # type: ignore[arg-type]


def hlg_pixel(rgb: tuple[float, float, float], *, exposure_ev: float, contrast: float, saturation: float) -> tuple[float, float, float]:
    gain = 2.0 ** exposure_ev
    scene = []
    for channel in rgb:
        linear = max(0.0, hlg_inverse_oetf(_clamp01(channel))) * gain
        scene.append(_contrast(linear, contrast))
    displayed = _hlg_ootf((scene[0], scene[1], scene[2]))
    scaled = tuple(channel * HLG_SDR_SCALE for channel in displayed)
    converted = _mul_vec(BT2020_TO_REC709, scaled)  # type: ignore[arg-type]
    return _map_rec709(converted, saturation)  # type: ignore[arg-type]


def rec709_grade_pixel(rgb: tuple[float, float, float], *, exposure_ev: float, contrast: float, saturation: float) -> tuple[float, float, float]:
    """Exposure, contrast, and saturation for an image that is already Rec.709."""
    if exposure_ev == 0.0 and contrast == 1.0 and saturation == 1.0:
        return tuple(_clamp01(channel) for channel in rgb)  # type: ignore[return-value]
    linear = tuple(max(0.0, rec709_inverse_oetf(_clamp01(channel))) for channel in rgb)
    gained = tuple(_contrast(channel * (2.0 ** exposure_ev), contrast) for channel in linear)
    return _map_rec709(gained, saturation)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ColourPlan:
    """One canonical plan shared by thumbnails, the viewer, and export."""

    pipeline: str
    input_range: str
    exposure_ev: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    technical: bool = False
    grade: bool = False
    look_name: str | None = None
    input_name: str | None = None
    explanation: str = ""

    @property
    def normalizes(self) -> bool:
        return self.pipeline in {"slog3_sgamut3cine", "hlg_bt2020"}

    def cache_key(self) -> str:
        payload = (
            f"{self.pipeline}|{self.input_range}|{self.exposure_ev:.6f}|"
            f"{self.contrast:.6f}|{self.saturation:.6f}|{self.technical}|{self.grade}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _identity(amount_exposure: float, contrast: float, saturation: float) -> bool:
    return amount_exposure == 0.0 and contrast == 1.0 and saturation == 1.0


def build_plan(
    *,
    pipeline: str,
    input_range: str,
    exposure_ev: float,
    contrast: float,
    saturation: float,
    has_input_lut: bool,
    has_look_lut: bool,
    look_includes_camera: bool,
) -> ColourPlan:
    if pipeline not in {"slog3_sgamut3cine", "hlg_bt2020", "preserve", "unknown"}:
        raise CraftsError("Unknown colour pipeline.", code="colour")
    if input_range not in {"pc", "tv"}:
        raise CraftsError("Colour input range must be full or limited.", code="colour")
    if look_includes_camera and has_input_lut:
        raise CraftsError(
            "An input LUT already replaces camera normalization. Turn off 'look includes the camera transform', or clear the input LUT.",
            code="colour",
        )
    if pipeline == "unknown" and not has_input_lut and not look_includes_camera:
        raise CraftsError(
            "The input profile is unknown. Choose S-Log3 / S-Gamut3.Cine, HLG / BT.2020, Preserve input, or an input LUT. No transform was guessed.",
            code="colour_unresolved",
        )
    if pipeline == "preserve" and not _identity(exposure_ev, contrast, saturation) and not has_input_lut:
        raise CraftsError(
            "Preserve input does not apply exposure, contrast, or saturation. Choose a profile or an input LUT before using those controls.",
            code="colour_unresolved",
        )
    technical = False
    grade = False
    explanation = ""
    chosen = pipeline
    if has_input_lut:
        chosen = "input_lut"
        grade = not _identity(exposure_ev, contrast, saturation)
        explanation = (
            "The input LUT replaces S-Log3/HLG normalization. Exposure, contrast, and saturation "
            "then run in Rec.709 display-linear light, and a look LUT can follow."
        )
    elif look_includes_camera:
        chosen = "look_includes_camera"
        grade = not _identity(exposure_ev, contrast, saturation)
        explanation = (
            "Automatic camera normalization is off because the look LUT is marked as already including it. "
            "That avoids converting twice."
        )
    elif pipeline == "slog3_sgamut3cine":
        technical = True
        explanation = (
            "S-Log3 is decoded as full-range code values, S-Gamut3.Cine is converted to Rec.709, "
            "90% reflectance is mapped to reference white, and the BT.709 OETF is applied."
        )
    elif pipeline == "hlg_bt2020":
        technical = True
        explanation = (
            "HLG is decoded with the BT.2100 inverse OETF, the reference OOTF (gamma 1.2) is applied, "
            "diffuse white at 75% signal is mapped to Rec.709 white, and BT.2020 is converted to Rec.709."
        )
    elif pipeline == "preserve":
        explanation = "Preserve input does not change transfer or gamut. Output tags follow the source."
    if not has_look_lut and chosen == "look_includes_camera":
        raise CraftsError(
            "Marking the look as including the camera transform needs a look LUT. Nothing was guessed.",
            code="colour_unresolved",
        )
    return ColourPlan(
        pipeline=chosen,
        input_range=input_range,
        exposure_ev=exposure_ev,
        contrast=contrast,
        saturation=saturation,
        technical=technical,
        grade=grade,
        look_name="look.cube" if has_look_lut else None,
        input_name="input.cube" if has_input_lut else None,
        explanation=explanation,
    )


def _technical_sample(plan: ColourPlan, rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    if plan.pipeline == "slog3_sgamut3cine":
        return slog3_pixel(rgb, exposure_ev=plan.exposure_ev, contrast=plan.contrast, saturation=plan.saturation)
    if plan.pipeline == "hlg_bt2020":
        return hlg_pixel(rgb, exposure_ev=plan.exposure_ev, contrast=plan.contrast, saturation=plan.saturation)
    raise CraftsError("This plan has no technical transform.", code="colour")


def _grade_sample(rgb: tuple[float, float, float], plan: ColourPlan) -> tuple[float, float, float]:
    return rec709_grade_pixel(rgb, exposure_ev=plan.exposure_ev, contrast=plan.contrast, saturation=plan.saturation)


def write_cube(path: Path, function, *, size: int = LUT_SIZE) -> None:
    if not 2 <= size <= 65:
        raise CraftsError("LUT size is out of range.", code="colour")
    step = 1.0 / (size - 1)
    lines = [f"LUT_3D_SIZE {size}", ""]
    for blue in range(size):
        for green in range(size):
            for red in range(size):
                sample = function((red * step, green * step, blue * step))
                lines.append(f"{sample[0]:.6f} {sample[1]:.6f} {sample[2]:.6f}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_CUBE_GUARD = threading.Lock()
_CUBE_LOCKS: dict[str, threading.Lock] = {}


def _cube_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _CUBE_GUARD:
        lock = _CUBE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CUBE_LOCKS[key] = lock
        return lock


def _header_has(path: Path, marker: str) -> bool:
    """True when the first 80 bytes contain the LUT size marker.

    Reading the whole cube just to check the header is avoided: a 65³ file
    is several megabytes, and this check runs on every cache hit.
    """
    try:
        if not path.is_file() or path.stat().st_size < 24:
            return False
        with path.open("rb") as handle:
            head = handle.read(80)
    except OSError:
        return False
    return marker.encode("ascii") in head


def _cube_ready(path: Path) -> bool:
    return _header_has(path, "LUT_3D_SIZE")


def _cached_cube(directory: Path, name: str, function) -> None:
    """One writer per cache key. The temp name is unique. Readers only see a finished cube."""
    destination = directory / name
    lock = _cube_lock(destination)
    with lock:
        if _cube_ready(destination):
            return
        temporary = directory / f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            write_cube(temporary, function)
            os.replace(temporary, destination)
        finally:
            if temporary.exists() and not _cube_ready(destination):
                temporary.unlink(missing_ok=True)
        if not _cube_ready(destination):
            raise CraftsError("The colour LUT was not published.", code="colour")


def write_lut1d(path: Path, function, *, size: int = LUT1D_SIZE) -> None:
    if not 2 <= size <= 16384:
        raise CraftsError("LUT size is out of range.", code="colour")
    step = 1.0 / (size - 1)
    lines = [f"LUT_1D_SIZE {size}", ""]
    for index in range(size):
        value = float(function(index * step))
        if not math.isfinite(value):
            value = 0.0
        value = min(1.0, max(0.0, value))
        lines.append(f"{value:.8f} {value:.8f} {value:.8f}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _lut1d_ready(path: Path) -> bool:
    return _header_has(path, "LUT_1D_SIZE")


def _cached_lut1d(directory: Path, name: str, function) -> None:
    destination = directory / name
    lock = _cube_lock(destination)
    with lock:
        if _lut1d_ready(destination):
            return
        temporary = directory / f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            write_lut1d(temporary, function)
            os.replace(temporary, destination)
        finally:
            if temporary.exists() and not _lut1d_ready(destination):
                temporary.unlink(missing_ok=True)
        if not _lut1d_ready(destination):
            raise CraftsError("The colour LUT was not published.", code="colour")


def _stage_lut1d(store: Path, directory: Path, cache_name: str, local_name: str, function) -> None:
    _cached_lut1d(store, cache_name, function)
    source = store / cache_name
    target = directory / local_name
    if source.resolve() == target.resolve():
        return
    temporary = directory / f".{local_name}.{uuid.uuid4().hex}.tmp"
    temporary.write_bytes(source.read_bytes())
    os.replace(temporary, target)


def _matrix_mixer(matrix: tuple[tuple[float, float, float], ...]) -> str:
    parts = []
    for prefix, row in zip(("r", "g", "b"), matrix):
        parts.append(f"{prefix}r={row[0]:.8f}:{prefix}g={row[1]:.8f}:{prefix}b={row[2]:.8f}:{prefix}a=0")
    return "colorchannelmixer=" + ":".join(parts)


def _saturation_mixer(amount: float) -> str:
    red, green, blue = REC709_LUMA
    keep = 1.0 - amount
    rows = (
        (amount + keep * red, keep * green, keep * blue),
        (keep * red, amount + keep * green, keep * blue),
        (keep * red, keep * green, amount + keep * blue),
    )
    return _matrix_mixer(rows)


def _slog_decode_sample(plan: ColourPlan, code: float) -> float:
    gain = 2.0 ** plan.exposure_ev
    linear = max(0.0, slog3_decode(code)) * gain
    return min(1.0, max(0.0, _contrast(linear, plan.contrast) / SCENE_SCALE))


def _slog_display_sample(scaled: float) -> float:
    return _clamp01(scaled * SCENE_SCALE / SLOG3_WHITE_REFLECTANCE)


def _slog_encode_sample(scaled: float) -> float:
    return rec709_oetf(_slog_display_sample(scaled))


def _oetf_sample(value: float) -> float:
    return rec709_oetf(_clamp01(value))


def _slog_fragments(plan: ColourPlan, store: Path, directory: Path) -> list[str]:
    """S-Log3 once: 1D decode, exact matrix, then 1D Rec.709 encode."""
    key = plan.cache_key()
    _stage_lut1d(store, directory, f"slog-decode-{key}.cube", "slog-decode.cube", lambda code: _slog_decode_sample(plan, code))
    fragments = ["lut1d=file=slog-decode.cube", _matrix_mixer(SGAMUT3CINE_TO_REC709)]
    if plan.saturation == 1.0:
        _stage_lut1d(store, directory, f"slog-encode-{key}.cube", "slog-encode.cube", _slog_encode_sample)
        fragments.append("lut1d=file=slog-encode.cube")
        return fragments
    _stage_lut1d(store, directory, f"slog-display-{key}.cube", "slog-display.cube", _slog_display_sample)
    _stage_lut1d(store, directory, "rec709-oetf.cube", "rec709-oetf.cube", _oetf_sample)
    fragments.append("lut1d=file=slog-display.cube")
    fragments.append(_saturation_mixer(plan.saturation))
    fragments.append("lut1d=file=rec709-oetf.cube")
    return fragments


def ensure_plan_cubes(plan: ColourPlan, directory: Path, *, cache: Path | None = None) -> list[str]:
    """Write owned tables. Returns filter fragments that must run in ``directory``.

    Preserve-input locks the source range and does not change transfer. S-Log3
    is a 1D curve plus the exact gamut matrix, so near-white is not a 3D
    lattice error. HLG stays a 3D lattice because the OOTF is cross-channel.
    """
    if (
        plan.pipeline == "preserve"
        and not plan.technical
        and not plan.grade
        and not plan.look_name
        and not plan.input_name
    ):
        # Same range in and out, so full-range codes are not rewritten as limited.
        return [f"scale=in_range={plan.input_range}:out_range={plan.input_range}:flags=accurate_rnd"]
    directory.mkdir(parents=True, exist_ok=True)
    store = cache or directory
    store.mkdir(parents=True, exist_ok=True)
    fragments: list[str] = []
    if plan.input_range == "tv":
        fragments.append("scale=in_range=tv:out_range=pc:flags=accurate_rnd")
    else:
        fragments.append("scale=in_range=pc:out_range=pc:flags=accurate_rnd")
    fragments.append("format=gbrpf32le")
    if plan.pipeline == "slog3_sgamut3cine" and plan.technical:
        fragments.extend(_slog_fragments(plan, store, directory))
    elif plan.technical:
        cached = store / f"technical-{plan.cache_key()}.cube"
        _cached_cube(store, cached.name, lambda rgb: _technical_sample(plan, rgb))
        if cached.resolve() != (directory / "technical.cube").resolve():
            (directory / "technical.cube").write_bytes(cached.read_bytes())
        fragments.append("lut3d=file=technical.cube")
    if plan.input_name:
        fragments.append(f"lut3d=file={plan.input_name}")
    if plan.look_name and plan.pipeline == "look_includes_camera":
        fragments.append(f"lut3d=file={plan.look_name}")
    if plan.grade:
        cached = store / f"grade-{plan.cache_key()}.cube"
        _cached_cube(store, cached.name, lambda rgb: _grade_sample(rgb, plan))
        if cached.resolve() != (directory / "grade-linear.cube").resolve():
            (directory / "grade-linear.cube").write_bytes(cached.read_bytes())
        fragments.append("lut3d=file=grade-linear.cube")
    if plan.look_name and plan.pipeline != "look_includes_camera":
        fragments.append(f"lut3d=file={plan.look_name}")
    if plan.normalizes or plan.pipeline in {"input_lut", "look_includes_camera"}:
        # Tag the LUT output as full-range Rec.709 RGB, then convert to YUV with
        # the Rec.709 matrix. Doing the tag only after an automatic RGB->YUV
        # conversion lets FFmpeg keep a BT.2020 matrix and mislabel the pixels.
        fragments.append("setparams=range=pc:colorspace=bt709:color_primaries=bt709:color_trc=bt709")
        fragments.append(
            "scale=in_range=pc:out_range=tv:in_color_matrix=bt709:out_color_matrix=bt709:flags=accurate_rnd"
        )
        fragments.append("setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709")
    return fragments


def output_tags_for(plan: ColourPlan) -> dict | None:
    if plan.normalizes or plan.pipeline in {"input_lut", "look_includes_camera"}:
        return {"colorspace": "bt709", "primaries": "bt709", "trc": "bt709", "range": "tv"}
    return None


def passthrough_tags(probe: dict | None) -> dict | None:
    """Copy transfer and levels the source actually stores. Missing tags stay omitted."""
    video = (((probe or {}).get("video") or [{}])[0]) or {}
    tags: dict[str, str] = {}
    if video.get("color_space"):
        tags["colorspace"] = str(video["color_space"])
    if video.get("color_primaries"):
        tags["primaries"] = str(video["color_primaries"])
    if video.get("color_transfer"):
        tags["trc"] = str(video["color_transfer"])
    raw = str(video.get("color_range") or "").casefold()
    if raw in {"pc", "jpeg", "full"}:
        tags["range"] = "pc"
    elif raw in {"tv", "mpeg", "limited"}:
        tags["range"] = "tv"
    return tags or None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
