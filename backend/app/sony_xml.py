"""Pair and read Sony NonRealTimeMeta sidecars.

Only the clip's own ``<stem>M01.XML`` and ``<stem>.XML`` in the same directory
are considered, case-insensitively. The parser rejects DTDs and entity
declarations, limits size and depth, and does not follow paths stored in the
XML. Gyroscope or Accelerometor group names are acquisition announcements.
They are not numeric motion samples, and ChangeTable names are ignored.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from pathlib import Path

MAX_XML_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 40
MAX_NODES = 20000

_GAMMA = {
    "s-log3-cine": "slog3",
    "s-log3": "slog3",
    "slog3": "slog3",
    "hlg-slog3ootf": "hlg",
    "hlg": "hlg",
    "rec709": "rec709",
    "rec709-800": "rec709",
}
_PRIMARIES = {
    "s-gamut3-cine": "sgamut3cine",
    "s-gamut3": "sgamut3",
    "rec2020": "rec2020",
    "bt2020": "rec2020",
    "rec709": "rec709",
}
_CODING = {
    "rec709": "rec709",
    "rec2020": "rec2020",
    "rec2020ncl": "rec2020",
    "bt2020nc": "rec2020",
    "bt2020ncl": "rec2020",
}


def _local(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def candidate_names(media: Path) -> set[str]:
    stem = media.stem.casefold()
    return {f"{stem}m01.xml", f"{stem}.xml"}


def _unsafe(text: str) -> str | None:
    folded = text.casefold()
    if "<!doctype" in folded or "<!entity" in folded:
        return "The sidecar contains a DTD or entity declaration and was not parsed."
    if folded.count("<") > MAX_NODES:
        return "The sidecar has too many elements and was not parsed."
    return None


def _walk(element: ElementTree.Element, depth: int, count: list[int]) -> None:
    count[0] += 1
    if depth > MAX_DEPTH or count[0] > MAX_NODES:
        raise ValueError("The sidecar exceeds the nesting or size limit.")
    for child in list(element):
        _walk(child, depth + 1, count)


def parse_sidecar_text(text: str) -> dict:
    reason = _unsafe(text)
    if reason:
        return {"status": "unsafe", "message": reason}
    if len(text.encode("utf-8")) > MAX_XML_BYTES:
        return {"status": "malformed", "message": "The sidecar is larger than the 2 MiB limit."}
    try:
        root = ElementTree.fromstring(text)
        _walk(root, 1, [0])
    except (ElementTree.ParseError, ValueError) as exc:
        return {"status": "malformed", "message": f"The sidecar is not usable XML. {exc}"}
    items: dict[str, str] = {}
    conflicts: list[str] = []
    duration = None
    target_duration = None
    video: dict[str, str] = {}
    device: dict[str, str] = {}
    gyro_announced = False
    for element in root.iter():
        name = _local(element.tag)
        if name == "Duration" and element.get("value"):
            if duration is None:
                duration = element.get("value")
        if name == "TargetMaterial":
            for child in list(element):
                if _local(child.tag) == "Duration" and child.get("value"):
                    target_duration = child.get("value")
        if name in {"VideoFrame", "VideoLayout"}:
            for key, value in element.attrib.items():
                video[_local(key)] = value
        if name == "Device":
            for key, value in element.attrib.items():
                device[_local(key)] = value
        if name == "Group":
            group = (element.get("name") or "").casefold()
            if "gyro" in group or "acceleromet" in group:
                gyro_announced = True
        if name == "Item":
            key = element.get("name") or ""
            value = element.get("value")
            if not key or value is None:
                continue
            folded = key.casefold()
            if folded in {"gyroscope", "accelerometor", "accelerometer"}:
                gyro_announced = True
            if key in items and items[key] != value:
                conflicts.append(key)
            else:
                items[key] = value
    if conflicts:
        return {
            "status": "conflict",
            "message": "The sidecar repeats metadata with different values: " + ", ".join(conflicts) + ".",
            "items": items,
        }
    gamma = items.get("CaptureGammaEquation", "")
    primaries = items.get("CaptureColorPrimaries", "")
    coding = items.get("CodingEquations", "")
    return {
        "status": "ok",
        "message": "Sidecar parsed.",
        "duration_frames": _frame_count(duration),
        "target_duration_frames": _frame_count(target_duration),
        "video": video,
        "device": {key: device.get(key) for key in ("manufacturer", "modelName", "serialNo") if device.get(key)},
        "gamma_raw": gamma,
        "primaries_raw": primaries,
        "coding_raw": coding,
        "gamma": _GAMMA.get(gamma.strip().casefold()),
        "primaries": _PRIMARIES.get(primaries.strip().casefold()),
        "coding": _CODING.get(coding.strip().casefold()),
        "gyro_announced": gyro_announced,
        "gyro_samples": False,
        "items_present": sorted(items),
    }


def _frame_count(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None
    number = int(value)
    if number < 0 or number > 100_000_000:
        return None
    return number


def _read_limited(path: Path) -> dict:
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"status": "malformed", "message": f"The sidecar could not be read. {exc}"}
    if size <= 0 or size > MAX_XML_BYTES:
        return {"status": "malformed", "message": "The sidecar is empty or larger than the 2 MiB limit."}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"status": "malformed", "message": "The sidecar is not UTF-8."}
    except OSError as exc:
        return {"status": "malformed", "message": f"The sidecar could not be read. {exc}"}
    parsed = parse_sidecar_text(text)
    parsed["path"] = str(path)
    return parsed


def _inside(parent: Path, candidate: Path) -> bool:
    try:
        resolved = candidate.resolve(strict=True)
        return resolved.parent == parent.resolve(strict=True) and resolved.is_file()
    except OSError:
        return False


def pair_sidecar(media: Path) -> dict:
    """Return a sidecar record for this clip. Never reads an unrelated file."""
    parent = media.parent
    wanted = candidate_names(media)
    found: list[Path] = []
    try:
        names = list(parent.iterdir())
    except OSError as exc:
        return {"status": "missing", "message": f"The clip folder could not be listed. {exc}", "paths": []}
    for entry in names:
        if not entry.is_file():
            continue
        if entry.name.casefold() not in wanted:
            continue
        if not _inside(parent, entry):
            return {
                "status": "unsafe",
                "message": f"{entry.name} does not stay in the clip folder, so it was not read.",
                "paths": [str(entry)],
            }
        found.append(entry.resolve())
    if not found:
        return {
            "status": "missing",
            "message": "No C0001M01.XML or C0001.XML sidecar was found beside this clip.",
            "paths": [],
            "gyro_announced": False,
            "gyro_samples": False,
        }
    parsed = [_read_limited(path) for path in found]
    usable = [item for item in parsed if item.get("status") == "ok"]
    if len(found) > 1:
        signatures = {
            (item.get("gamma_raw"), item.get("primaries_raw"), item.get("coding_raw"), item.get("status"))
            for item in parsed
        }
        if len(signatures) > 1:
            return {
                "status": "ambiguous",
                "message": "More than one sidecar was found and they do not agree. No profile was copied from either file.",
                "paths": [str(path) for path in found],
                "candidates": parsed,
                "gyro_announced": False,
                "gyro_samples": False,
            }
    if not usable:
        first = parsed[0]
        first["paths"] = [str(path) for path in found]
        return first
    chosen = usable[0]
    chosen["paths"] = [str(path) for path in found]
    chosen["paired_name"] = Path(chosen["path"]).name
    return chosen


def pipeline_from_sidecar(sidecar: dict) -> str | None:
    if sidecar.get("status") != "ok":
        return None
    gamma = sidecar.get("gamma")
    primaries = sidecar.get("primaries")
    if gamma == "slog3" and primaries in {"sgamut3cine", "sgamut3"}:
        if primaries != "sgamut3cine":
            return None
        return "slog3_sgamut3cine"
    if gamma == "hlg" and primaries == "rec2020":
        return "hlg_bt2020"
    if gamma == "rec709" and primaries == "rec709":
        return "preserve"
    if gamma or primaries:
        return "unknown"
    return None


def pipeline_from_tags(video: dict | None) -> str | None:
    if not isinstance(video, dict):
        return None
    transfer = (video.get("color_transfer") or "").casefold()
    primaries = (video.get("color_primaries") or "").casefold()
    if transfer in {"arib-std-b67", "bt2020-10"} and primaries in {"bt2020", "bt2020nc"}:
        if transfer == "arib-std-b67":
            return "hlg_bt2020"
    if transfer in {"slog3", "s-log3", "sony-slog3"}:
        return "slog3_sgamut3cine"
    return None


def resolve_profile(sidecar: dict | None, probe: dict | None, override: str) -> dict:
    """Pick one clip's profile. A missing or conflicting profile is not guessed."""
    sidecar = sidecar or {"status": "missing", "message": "No sidecar was stored."}
    videos = (probe or {}).get("video") if isinstance(probe, dict) else None
    video = videos[0] if isinstance(videos, list) and videos and isinstance(videos[0], dict) else {}
    tag_pipeline = pipeline_from_tags(video)
    xml_pipeline = pipeline_from_sidecar(sidecar) if sidecar.get("status") == "ok" else None
    range_name = "tv"
    raw_range = (video.get("color_range") or "").casefold()
    if xml_pipeline == "slog3_sgamut3cine" or override == "slog3_sgamut3cine":
        # The public S-Log3 formula is defined on full 10-bit code values.
        range_name = "pc"
    elif raw_range in {"pc", "jpeg", "full"}:
        range_name = "pc"
    if override not in {"auto", "slog3_sgamut3cine", "hlg_bt2020", "preserve"}:
        return {
            "pipeline": "unknown",
            "source": "invalid",
            "input_range": range_name,
            "status": "invalid",
            "message": "The profile override is not one of the supported choices.",
        }
    if override != "auto":
        return {
            "pipeline": override if override != "preserve" else "preserve",
            "source": "override",
            "input_range": "pc" if override == "slog3_sgamut3cine" else range_name,
            "status": "override",
            "message": f"Using the profile selected for this clip ({override}). Automatic detection was not used.",
            "xml_pipeline": xml_pipeline,
            "tag_pipeline": tag_pipeline,
        }
    if sidecar.get("status") in {"unsafe", "malformed", "ambiguous", "conflict"}:
        return {
            "pipeline": "unknown",
            "source": sidecar["status"],
            "input_range": range_name,
            "status": sidecar["status"],
            "message": sidecar.get("message") or "The sidecar cannot be used.",
            "tag_pipeline": tag_pipeline,
        }
    if xml_pipeline == "unknown":
        return {
            "pipeline": "unknown",
            "source": "xml",
            "input_range": range_name,
            "status": "unknown",
            "message": (
                f"Sidecar gamma {sidecar.get('gamma_raw') or 'missing'} and primaries "
                f"{sidecar.get('primaries_raw') or 'missing'} are not a supported automatic profile."
            ),
            "tag_pipeline": tag_pipeline,
        }
    if xml_pipeline and tag_pipeline and xml_pipeline != tag_pipeline:
        return {
            "pipeline": "unknown",
            "source": "conflict",
            "input_range": range_name,
            "status": "conflict",
            "message": (
                f"The sidecar says {xml_pipeline.replace('_', ' ')} but the video tags say "
                f"{tag_pipeline.replace('_', ' ')}. Choose a profile for this clip. Neither was applied."
            ),
            "xml_pipeline": xml_pipeline,
            "tag_pipeline": tag_pipeline,
        }
    if xml_pipeline:
        return {
            "pipeline": xml_pipeline,
            "source": "xml",
            "input_range": "pc" if xml_pipeline == "slog3_sgamut3cine" else range_name,
            "status": "ok",
            "message": f"Profile {xml_pipeline} came from this clip's sidecar.",
            "tag_pipeline": tag_pipeline,
        }
    if tag_pipeline:
        return {
            "pipeline": tag_pipeline,
            "source": "tags",
            "input_range": "pc" if tag_pipeline == "slog3_sgamut3cine" else range_name,
            "status": "ok",
            "message": f"No usable sidecar. Profile {tag_pipeline} came from this clip's video tags.",
            "xml_pipeline": None,
        }
    if sidecar.get("status") == "missing":
        return {
            "pipeline": "preserve",
            "source": "missing",
            "input_range": range_name,
            "status": "preserve",
            "message": "No sidecar and no supported video-tag profile. Input is preserved. Choose S-Log3 or HLG to normalize this clip.",
        }
    return {
        "pipeline": "unknown",
        "source": sidecar.get("status") or "missing",
        "input_range": range_name,
        "status": sidecar.get("status") or "missing",
        "message": sidecar.get("message") or "The profile could not be resolved.",
    }
