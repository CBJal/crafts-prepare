"""Export presets declare the encoder, container, pixel format, and bit depth."""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "h264_mp4": {
        "id": "h264_mp4",
        "label": "H.264 MP4",
        "container": "mp4",
        "encoder": "libx264",
        "encoder_aliases": ["libx264"],
        "pix_fmt": "yuv420p",
        "bit_depth": 8,
        "audio_encoder": "aac",
        "audio_encoder_required": "aac",
        "profile": None,
        "extra": ["-preset", "veryfast"],
        "crf_default": 18,
    },
    "prores_422": {
        "id": "prores_422",
        "label": "ProRes 422 MOV",
        "container": "mov",
        "encoder": "prores_ks",
        "encoder_aliases": ["prores_ks", "prores"],
        "pix_fmt": "yuv422p10le",
        "bit_depth": 10,
        "audio_encoder": "pcm_s16le",
        "audio_encoder_required": "pcm_s16le",
        "profile": "2",
        "extra": [],
        "crf_default": None,
    },
    "hevc_main": {
        "id": "hevc_main",
        "label": "HEVC Main MP4 (8-bit)",
        "container": "mp4",
        "encoder": "libx265",
        "encoder_aliases": ["libx265"],
        "pix_fmt": "yuv420p",
        "bit_depth": 8,
        "audio_encoder": "aac",
        "audio_encoder_required": "aac",
        "profile": None,
        "extra": ["-tag:v", "hvc1"],
        "crf_default": 24,
    },
    "hevc_main10": {
        "id": "hevc_main10",
        "label": "HEVC Main10 MP4 (10-bit)",
        "container": "mp4",
        "encoder": "libx265",
        "encoder_aliases": ["libx265"],
        "pix_fmt": "yuv420p10le",
        "bit_depth": 10,
        "audio_encoder": "aac",
        "audio_encoder_required": "aac",
        "profile": None,
        "extra": ["-tag:v", "hvc1"],
        "crf_default": 24,
    },
    "dnxhr_hq": {
        "id": "dnxhr_hq",
        "label": "DNxHR HQ MXF (8-bit)",
        "container": "mxf",
        "encoder": "dnxhd",
        "encoder_aliases": ["dnxhd"],
        "pix_fmt": "yuv422p",
        "bit_depth": 8,
        "audio_encoder": "pcm_s16le",
        "audio_encoder_required": "pcm_s16le",
        "profile": "dnxhr_hq",
        "extra": [],
        "crf_default": None,
    },
    "dnxhr_hqx": {
        "id": "dnxhr_hqx",
        "label": "DNxHR HQX MXF (10-bit)",
        "container": "mxf",
        "encoder": "dnxhd",
        "encoder_aliases": ["dnxhd"],
        "pix_fmt": "yuv422p10le",
        "bit_depth": 10,
        "audio_encoder": "pcm_s16le",
        "audio_encoder_required": "pcm_s16le",
        "profile": "dnxhr_hqx",
        "extra": [],
        "crf_default": None,
    },
}


def preset_availability(preset: dict, encoders: dict) -> dict:
    encoder = next((name for name in preset["encoder_aliases"] if encoders.get(name)), None)
    audio_ok = encoders.get(preset["audio_encoder_required"], False)
    available = encoder is not None
    reason = None
    if encoder is None:
        reason = (
            f"This ffmpeg build has no {preset['encoder']} encoder. "
            f"{preset['label']} stays unavailable until that encoder is installed."
        )
    elif not audio_ok:
        reason = f"Audio encoder {preset['audio_encoder_required']} is missing. Video-only exports can still be queued with audio off."
    return {
        **{key: preset[key] for key in ("id", "label", "container", "pix_fmt", "bit_depth")},
        "encoder": encoder or preset["encoder"],
        "available": available,
        "audio_encoder_available": audio_ok,
        "reason": reason,
    }


def conversion_note(source_pix: str | None, source_depth: int | None, preset: dict) -> dict:
    out_depth = preset["bit_depth"]
    out_pix = preset["pix_fmt"]
    depth_changes = source_depth is not None and source_depth != out_depth
    chroma_changes = bool(source_pix) and source_pix != out_pix
    if depth_changes:
        message = (
            f"Source {source_pix or 'unknown'} ({source_depth}-bit) will be converted to "
            f"{out_pix} ({out_depth}-bit) because the {preset['label']} preset declares {out_depth}-bit output. "
            "This conversion is explicit and must be acknowledged."
        )
    elif chroma_changes:
        message = (
            f"Bit depth stays {out_depth}-bit. Pixel format changes from {source_pix} to {out_pix} "
            f"as declared by the {preset['label']} preset."
        )
    else:
        message = (
            f"Preset output matches the source bit depth ({out_depth}-bit, {out_pix}). "
            "No bit-depth conversion."
        )
    return {
        "bit_depth_conversion": depth_changes,
        "source_pix_fmt": source_pix,
        "source_bit_depth": source_depth,
        "output_pix_fmt": out_pix,
        "output_bit_depth": out_depth,
        "message": message,
    }
