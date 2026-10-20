"""Import local files and folders without following symlinks outside the root."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from .errors import Cancelled, CraftsError
from .probe import probe_file
from .recipe import default_recipe
from .security import MAX_HASH_BYTES, is_within, resolve_existing
from .sony_xml import pair_sidecar

CANDIDATE_EXTENSIONS = {".mp4", ".mov", ".mxf", ".mts", ".m2ts"}
MAX_FOLDER_FILES = 500
MAX_DEPTH = 8


def _cancelled(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def sha256_file(path: Path, cancel=None) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            _cancelled(cancel)
            block = handle.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_HASH_BYTES:
                raise CraftsError("File exceeds the hash size limit.", code="too_large")
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path, ffprobe: str, cancel=None) -> dict:
    _cancelled(cancel)
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise CraftsError("Path is not a file.", code="bad_path")
    digest = sha256_file(resolved, cancel=cancel)
    stat = resolved.stat()
    probed = probe_file(ffprobe, resolved, cancel=cancel)
    if not probed.get("video"):
        raise CraftsError("ffprobe found no video stream.", code="unreadable_media")
    return {
        "id": str(uuid.uuid4()),
        "path": str(resolved),
        "name": resolved.name,
        "sha256": digest,
        "size": stat.st_size,
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "probe": probed,
        "flag": "",
        "notes": "",
        "markers": [],
        "trim_in": 0.0,
        "trim_out": probed.get("duration") or 0.0,
        "duplicate": False,
        "recipe": default_recipe(),
        "sidecar": pair_sidecar(resolved),
    }


def import_path(raw: str, ffprobe: str, cancel=None) -> dict:
    resolved = resolve_existing(raw)
    if resolved.is_dir():
        return import_folder(resolved, ffprobe, cancel=cancel)
    if resolved.is_file():
        return {"clips": [file_record(resolved, ffprobe, cancel=cancel)], "skipped": [], "root": str(resolved.parent)}
    raise CraftsError("Path is neither a file nor a folder.", code="bad_path")


def list_directory_media(root: Path) -> list[Path]:
    """Media files in this directory only, sorted by name. Subfolders are not walked."""
    root = root.resolve(strict=True)
    found: list[Path] = []
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise CraftsError(f"The folder could not be listed. {exc}", code="bad_path") from exc
    for candidate in entries:
        if not candidate.is_file() or candidate.suffix.lower() not in CANDIDATE_EXTENSIONS:
            continue
        if candidate.is_symlink():
            try:
                target = candidate.resolve(strict=True)
            except OSError:
                continue
            if not is_within(target, root):
                continue
            found.append(target)
        else:
            resolved = candidate.resolve(strict=True)
            if is_within(resolved, root):
                found.append(resolved)
    found.sort(key=lambda path: path.name.casefold())
    return found


def list_media_paths(root: Path) -> list[Path]:
    """List candidate media without hashing or probing."""
    root = root.resolve(strict=True)
    found: list[Path] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        depth = len(current.relative_to(root).parts)
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        kept = []
        for name in dirnames:
            candidate = current / name
            if candidate.is_symlink():
                try:
                    target = candidate.resolve(strict=True)
                except OSError:
                    continue
                if not is_within(target, root):
                    continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            candidate = current / name
            if candidate.suffix.lower() not in CANDIDATE_EXTENSIONS:
                continue
            seen += 1
            if seen > MAX_FOLDER_FILES:
                return found
            if candidate.is_symlink():
                try:
                    target = candidate.resolve(strict=True)
                except OSError:
                    continue
                if not is_within(target, root):
                    continue
                found.append(target)
            else:
                resolved = candidate.resolve(strict=True)
                if is_within(resolved, root):
                    found.append(resolved)
    return found


def import_folder(root: Path, ffprobe: str, cancel=None) -> dict:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise CraftsError("Folder import needs a directory.", code="bad_path")
    clips = []
    skipped = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        depth = len(current.relative_to(root).parts)
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        kept = []
        for name in dirnames:
            candidate = current / name
            if candidate.is_symlink():
                try:
                    target = candidate.resolve(strict=True)
                except OSError:
                    skipped.append({"path": str(candidate), "reason": "symlink could not be resolved"})
                    continue
                if not is_within(target, root):
                    skipped.append({"path": str(candidate), "reason": "symlink escapes the selected folder"})
                    continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            candidate = current / name
            if candidate.suffix.lower() not in CANDIDATE_EXTENSIONS:
                continue
            seen += 1
            if seen > MAX_FOLDER_FILES:
                skipped.append({"path": str(candidate), "reason": "folder file limit reached"})
                return {"clips": clips, "skipped": skipped, "root": str(root)}
            if candidate.is_symlink():
                try:
                    target = candidate.resolve(strict=True)
                except OSError:
                    skipped.append({"path": str(candidate), "reason": "symlink could not be resolved"})
                    continue
                if not is_within(target, root):
                    skipped.append({"path": str(candidate), "reason": "symlink escapes the selected folder"})
                    continue
                probe_path = target
            else:
                probe_path = candidate.resolve(strict=True)
                if not is_within(probe_path, root):
                    skipped.append({"path": str(candidate), "reason": "path escapes the selected folder"})
                    continue
            try:
                _cancelled(cancel)
                clips.append(file_record(probe_path, ffprobe, cancel=cancel))
            except Cancelled:
                raise
            except CraftsError as exc:
                skipped.append({"path": str(probe_path), "reason": exc.message})
    return {"clips": clips, "skipped": skipped, "root": str(root)}
