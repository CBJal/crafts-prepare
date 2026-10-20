"""Background thumbnails. Results from an old folder are ignored."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..filters import FilterSpec
from ..preview import extract_thumbnail
from .grade import safe_filters


class ThumbnailHub(QObject):
    ready = Signal(int, str, str, str)

    def __init__(self, service) -> None:
        super().__init__()
        self.service = service
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="crafts-thumb")
        self.generation = 0

    def cancel(self) -> int:
        self.generation += 1
        return self.generation

    def submit(self, clip: dict, *, adjusted: bool) -> None:
        generation = self.generation
        clip_id = clip["id"]

        def work() -> None:
            if generation != self.generation:
                return
            try:
                path, error = self._render(clip, adjusted=adjusted)
            except Exception as exc:  # noqa: BLE001 — one thumbnail must not kill the grid
                self.ready.emit(generation, clip_id, "", str(exc)[:240])
                return
            if generation != self.generation:
                return
            self.ready.emit(generation, clip_id, path, error)

        self.pool.submit(work)

    def shutdown(self) -> None:
        self.generation += 1
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _render(self, clip: dict, *, adjusted: bool) -> tuple[str, str]:
        ffmpeg, _ffprobe = self.service._tools()
        cache = self.service.data_dir / "colour-cache"
        work = self.service.data_dir / "thumb-src" / clip["id"]
        filters, files, message = safe_filters(clip, work, adjusted=adjusted, cache=cache)
        destination = self.service.data_dir / "thumbs" / f"{clip['id']}.jpg"
        destination.parent.mkdir(parents=True, exist_ok=True)
        duration = float((clip.get("probe") or {}).get("duration") or 0)
        seconds = min(0.2, max(0.0, duration / 2 if duration else 0.0))
        extract_thumbnail(
            ffmpeg=ffmpeg,
            source=Path(clip["path"]),
            seconds=seconds,
            destination=destination,
            spec=FilterSpec(),
            duration=duration,
            extra_filters=filters or None,
            stage_files=files or None,
        )
        if not destination.is_file() or destination.stat().st_size == 0:
            return "", "Thumbnail failed"
        return str(destination), message
