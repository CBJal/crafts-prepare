"""Bounded thumbnail renders. One job per cache key, with a cancel event for FFmpeg."""

from __future__ import annotations

import hashlib
import threading
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..filters import FilterSpec
from ..preview import extract_thumbnail
from ..recipe import recipe_fingerprint
from .grade import clip_recipe, safe_filters

MAX_PENDING = 3
MAX_QUEUED = 40


def thumbnail_key(clip: dict, *, adjusted: bool, width: int = 320) -> str:
    recipe = clip_recipe(clip)
    sidecar = clip.get("sidecar") if isinstance(clip.get("sidecar"), dict) else {}
    body = "|".join(
        (
            str(clip.get("sha256") or ""),
            str(clip.get("size") or ""),
            recipe_fingerprint(recipe) if adjusted else "original",
            str(sidecar.get("sha256") or ""),
            str(recipe.get("look_lut_sha256") or ""),
            str(recipe.get("input_lut_sha256") or ""),
            str(width),
        )
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


class ThumbnailHub(QObject):
    ready = Signal(int, str, str, str)

    def __init__(self, service) -> None:
        super().__init__()
        self.service = service
        self.pool = ThreadPoolExecutor(max_workers=MAX_PENDING, thread_name_prefix="crafts-thumb")
        self.generation = 0
        self._lock = threading.Lock()
        self._inflight: dict[str, tuple[int, threading.Event, Future]] = {}
        self._waiting: deque[tuple[int, str, dict, bool]] = deque()

    def cancel(self) -> int:
        with self._lock:
            self.generation += 1
            generation = self.generation
            for _key, (_gen, event, _future) in self._inflight.items():
                event.set()
            self._inflight.clear()
            self._waiting.clear()
        return generation

    def request(self, clips: list[dict], *, adjusted: bool = True) -> None:
        """Queue at most one render per key. Cached files are emitted without FFmpeg."""
        generation = self.generation
        for clip in clips:
            if not isinstance(clip, dict) or not clip.get("id") or not clip.get("path"):
                continue
            key = thumbnail_key(clip, adjusted=adjusted)
            cached = self._cache_path(key)
            if cached.is_file() and cached.stat().st_size > 0:
                self.ready.emit(generation, str(clip["id"]), str(cached), "")
                continue
            with self._lock:
                if key in self._inflight or any(item[1] == key for item in self._waiting):
                    continue
                if len(self._inflight) >= MAX_PENDING:
                    if len(self._waiting) < MAX_QUEUED:
                        self._waiting.append((generation, key, clip, adjusted))
                    continue
                self._start_locked(generation, key, clip, adjusted)

    def shutdown(self) -> None:
        self.cancel()
        self.pool.shutdown(wait=True, cancel_futures=True)

    def _cache_path(self, key: str) -> Path:
        return self.service.data_dir / "thumbs" / f"{key}.jpg"

    def _start_locked(self, generation: int, key: str, clip: dict, adjusted: bool) -> None:
        event = threading.Event()
        future = self.pool.submit(self._render, generation, key, clip, adjusted, event)
        self._inflight[key] = (generation, event, future)

    def _pump(self) -> None:
        with self._lock:
            while self._waiting and len(self._inflight) < MAX_PENDING:
                generation, key, clip, adjusted = self._waiting.popleft()
                if generation != self.generation or key in self._inflight:
                    continue
                self._start_locked(generation, key, clip, adjusted)

    def _render(self, generation: int, key: str, clip: dict, adjusted: bool, cancel: threading.Event) -> None:
        clip_id = str(clip["id"])
        try:
            if generation != self.generation or cancel.is_set():
                return
            path, error = self._encode(clip, adjusted=adjusted, key=key, cancel=cancel)
        except Exception as exc:  # noqa: BLE001
            if generation == self.generation:
                self.ready.emit(generation, clip_id, "", str(exc)[:180])
            return
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            self._pump()
        if generation != self.generation or cancel.is_set():
            return
        self.ready.emit(generation, clip_id, path, error)

    def _encode(self, clip: dict, *, adjusted: bool, key: str, cancel: threading.Event) -> tuple[str, str]:
        destination = self._cache_path(key)
        if destination.is_file() and destination.stat().st_size > 0:
            return str(destination), ""
        ffmpeg, _ffprobe = self.service._tools()
        cache = self.service.data_dir / "colour-cache"
        work = self.service.data_dir / "thumb-src" / key
        filters, files, message = safe_filters(clip, work, adjusted=adjusted, cache=cache)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + f".{key}.partial.jpg")
        duration = float((clip.get("probe") or {}).get("duration") or 0)
        seconds = min(0.2, max(0.0, duration / 2 if duration else 0.0))
        extract_thumbnail(
            ffmpeg=ffmpeg,
            source=Path(clip["path"]),
            seconds=seconds,
            destination=partial,
            spec=FilterSpec(),
            duration=duration,
            cancel=cancel,
            extra_filters=filters or None,
            stage_files=files or None,
        )
        if cancel.is_set():
            partial.unlink(missing_ok=True)
            return "", "cancelled"
        if not partial.is_file() or partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
            return "", "Thumbnail failed"
        partial.replace(destination)
        return str(destination), message
