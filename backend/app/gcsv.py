"""Original .gcsv reader.

The field rules follow the public Gyroflow GCSV description:
https://docs.gyroflow.xyz/app/technical-details/gcsv-format

This module does not claim that a file came from a particular camera.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .errors import CraftsError
from .security import MAX_GCSV_BYTES

MAGIC = {"GYROFLOW IMU LOG", "CAMERA IMU LOG"}
COLUMN_ORDERS = (
    ("t", "gx", "gy", "gz"),
    ("t", "gx", "gy", "gz", "ax", "ay", "az"),
    ("t", "gx", "gy", "gz", "ax", "ay", "az", "mx", "my", "mz"),
)
_AXIS = set("XYZxyz")
MAX_SAMPLES = 5_000_000


@dataclass
class GcsvSample:
    t_raw: float
    t_seconds: float
    gyro_raw: tuple[float, float, float]
    gyro_rad_s: tuple[float, float, float]
    accel_raw: tuple[float, float, float] | None = None
    accel_g: tuple[float, float, float] | None = None
    mag_raw: tuple[float, float, float] | None = None
    mag_gauss: tuple[float, float, float] | None = None


@dataclass
class GcsvGap:
    index: int
    dt_seconds: float
    median_dt_seconds: float


@dataclass
class GcsvLog:
    magic: str
    version: str
    logger_id: str
    orientation: str
    tscale: float
    gscale: float
    ascale: float | None
    mscale: float | None
    columns: list[str]
    metadata: dict[str, str]
    samples: list[GcsvSample]
    sample_rate_hz: float | None
    median_dt_seconds: float | None
    duration_seconds: float
    gaps: list[GcsvGap] = field(default_factory=list)
    lensprofile: str | None = None
    lens_info: str | None = None
    frame_readout_time_ms: float | None = None
    frame_readout_direction: int | None = None

    def summary(self) -> dict:
        return {
            "magic": self.magic,
            "version": self.version,
            "logger_id": self.logger_id,
            "orientation": self.orientation,
            "tscale": self.tscale,
            "gscale": self.gscale,
            "ascale": self.ascale,
            "mscale": self.mscale,
            "columns": self.columns,
            "sample_count": len(self.samples),
            "sample_rate_hz": self.sample_rate_hz,
            "median_dt_seconds": self.median_dt_seconds,
            "duration_seconds": self.duration_seconds,
            "gap_count": len(self.gaps),
            "gaps": [
                {
                    "index": gap.index,
                    "dt_seconds": gap.dt_seconds,
                    "median_dt_seconds": gap.median_dt_seconds,
                }
                for gap in self.gaps[:50]
            ],
            "lensprofile": self.lensprofile,
            "lens_info": self.lens_info,
            "frame_readout_time_ms": self.frame_readout_time_ms,
            "frame_readout_direction": self.frame_readout_direction,
            "metadata": self.metadata,
            "units": {
                "time": "seconds = raw t * tscale",
                "gyro": "rad/s = raw gx/gy/gz * gscale",
                "accelerometer": "g = raw ax/ay/az * ascale" if self.ascale is not None else None,
                "magnetometer": "gauss = raw mx/my/mz * mscale" if self.mscale is not None else None,
            },
            "disclaimer": (
                "This is a text log parse. It does not identify a camera model "
                "and it is not a Gyroflow stabilization result."
            ),
        }


def _fail(message: str) -> None:
    raise CraftsError(message, code="bad_gcsv")


def _orientation(value: str) -> str:
    if len(value) != 3 or any(ch not in _AXIS for ch in value):
        _fail("orientation must be three axes such as YxZ (uppercase normal, lowercase inverted).")
    axes = [ch.upper() for ch in value]
    if sorted(axes) != ["X", "Y", "Z"]:
        _fail("orientation must use each of X, Y, and Z once.")
    return value


def _positive_float(name: str, raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        _fail(f"{name} is not a number.")
    if value <= 0 or value != value or value in (float("inf"), float("-inf")):
        _fail(f"{name} must be a positive finite scale.")
    return value


def _optional_float(name: str, raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        _fail(f"{name} is not a number.")
    if value != value or value in (float("inf"), float("-inf")):
        _fail(f"{name} must be finite.")
    return value


def parse_gcsv_text(text: str) -> GcsvLog:
    if len(text.encode("utf-8")) > MAX_GCSV_BYTES:
        _fail("GCSV file exceeds the parse limit.")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[0].startswith("\ufeff"):
        lines[0] = lines[0].lstrip("\ufeff")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        _fail("GCSV file is empty.")
    magic = lines[0].strip()
    if magic not in MAGIC:
        _fail("First line must be GYROFLOW IMU LOG or CAMERA IMU LOG.")

    metadata: dict[str, str] = {}
    columns: list[str] | None = None
    data_start = None
    for index, line in enumerate(lines[1:], start=1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if parts and parts[0] == "t":
            columns = parts
            data_start = index + 1
            break
        if len(parts) < 2 or any(part == "" for part in parts[:2]):
            _fail(f"Header line {index + 1} is not key,value metadata.")
        key = parts[0]
        value = ",".join(parts[1:]).strip()
        if key in metadata:
            _fail(f"Duplicate header field: {key}")
        metadata[key] = value
    if columns is None or data_start is None:
        _fail("Column header is missing. Expected t,gx,gy,gz with optional accel and mag.")
    if tuple(columns) not in COLUMN_ORDERS:
        _fail(
            "Column order must be t,gx,gy,gz or t,gx,gy,gz,ax,ay,az "
            "or t,gx,gy,gz,ax,ay,az,mx,my,mz."
        )

    version = metadata.get("version", "")
    logger_id = metadata.get("id", "")
    orientation = metadata.get("orientation", "")
    if not version:
        _fail("version is required.")
    if not logger_id:
        _fail("id is required.")
    if not orientation:
        _fail("orientation is required.")
    orientation = _orientation(orientation)
    if "tscale" not in metadata or "gscale" not in metadata:
        _fail("tscale and gscale are required.")
    tscale = _positive_float("tscale", metadata["tscale"])
    gscale = _positive_float("gscale", metadata["gscale"])
    has_accel = "ax" in columns
    has_mag = "mx" in columns
    if has_accel and "ascale" not in metadata:
        _fail("ascale is required when accelerometer columns are present.")
    if has_mag and "mscale" not in metadata:
        _fail("mscale is required when magnetometer columns are present.")
    ascale = _positive_float("ascale", metadata["ascale"]) if has_accel else _optional_float("ascale", metadata.get("ascale"))
    mscale = _positive_float("mscale", metadata["mscale"]) if has_mag else _optional_float("mscale", metadata.get("mscale"))

    readout = _optional_float("frame_readout_time", metadata.get("frame_readout_time"))
    direction_raw = metadata.get("frame_readout_direction")
    direction = None
    if direction_raw not in (None, ""):
        try:
            direction = int(direction_raw)
        except ValueError:
            _fail("frame_readout_direction must be an integer.")
        if direction not in {0, 1, 2, 3}:
            _fail("frame_readout_direction must be 0, 1, 2, or 3.")

    samples: list[GcsvSample] = []
    previous_t: float | None = None
    for line_number, line in enumerate(lines[data_start:], start=data_start + 1):
        if not line.strip():
            continue
        if len(samples) >= MAX_SAMPLES:
            _fail("GCSV sample limit exceeded.")
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != len(columns):
            _fail(f"Line {line_number} does not match the column count.")
        try:
            numbers = [float(part) for part in parts]
        except ValueError:
            _fail(f"Line {line_number} contains a non-numeric sample.")
        if any(value != value or value in (float("inf"), float("-inf")) for value in numbers):
            _fail(f"Line {line_number} contains a non-finite sample.")
        t_raw = numbers[0]
        if previous_t is not None and t_raw <= previous_t:
            _fail(f"Timestamps must be strictly increasing (line {line_number}).")
        previous_t = t_raw
        gyro_raw = (numbers[1], numbers[2], numbers[3])
        sample = GcsvSample(
            t_raw=t_raw,
            t_seconds=t_raw * tscale,
            gyro_raw=gyro_raw,
            gyro_rad_s=tuple(value * gscale for value in gyro_raw),  # type: ignore[arg-type]
        )
        if has_accel and ascale is not None:
            accel_raw = (numbers[4], numbers[5], numbers[6])
            sample.accel_raw = accel_raw
            sample.accel_g = tuple(value * ascale for value in accel_raw)  # type: ignore[assignment]
        if has_mag and mscale is not None:
            mag_raw = (numbers[7], numbers[8], numbers[9])
            sample.mag_raw = mag_raw
            sample.mag_gauss = tuple(value * mscale for value in mag_raw)  # type: ignore[assignment]
        samples.append(sample)
    if len(samples) < 2:
        _fail("At least two samples are required to estimate rate and gaps.")

    deltas = [
        samples[index].t_seconds - samples[index - 1].t_seconds
        for index in range(1, len(samples))
    ]
    median_dt = statistics.median(deltas)
    gaps: list[GcsvGap] = []
    if median_dt > 0:
        for index, dt in enumerate(deltas, start=1):
            if dt > median_dt * 1.5 and (dt - median_dt) > (median_dt * 0.25):
                gaps.append(GcsvGap(index=index, dt_seconds=dt, median_dt_seconds=median_dt))
        sample_rate = 1.0 / median_dt
    else:
        sample_rate = None
    duration = samples[-1].t_seconds - samples[0].t_seconds
    return GcsvLog(
        magic=magic,
        version=version,
        logger_id=logger_id,
        orientation=orientation,
        tscale=tscale,
        gscale=gscale,
        ascale=ascale,
        mscale=mscale,
        columns=columns,
        metadata=metadata,
        samples=samples,
        sample_rate_hz=sample_rate,
        median_dt_seconds=median_dt,
        duration_seconds=duration,
        gaps=gaps,
        lensprofile=metadata.get("lensprofile"),
        lens_info=metadata.get("lens_info"),
        frame_readout_time_ms=readout,
        frame_readout_direction=direction,
    )


def parse_gcsv_file(path: Path) -> GcsvLog:
    size = path.stat().st_size
    if size > MAX_GCSV_BYTES:
        _fail("GCSV file exceeds the parse limit.")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise CraftsError("GCSV file is not UTF-8 text.", code="bad_gcsv") from exc
    return parse_gcsv_text(text)
