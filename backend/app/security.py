"""Loopback access checks and filesystem path guards.

Folder imports refuse symlinks whose targets resolve outside the selected
root. Output names are a restricted character set so user text cannot become
an FFmpeg filter expression or a path segment.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from .errors import CraftsError

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_SUFFIX = re.compile(r"^[A-Za-z0-9._-]{0,48}$")

MAX_JSON_BYTES = 1_000_000
MAX_GCSV_BYTES = 64 * 1024 * 1024
MAX_LUT_BYTES = 5 * 1024 * 1024
MAX_HASH_BYTES = 64 * 1024 * 1024 * 1024
MAX_PROJECT_BYTES = 20 * 1024 * 1024


def host_hostname(host_header: str) -> str:
    host_header = host_header.strip()
    if host_header.startswith("["):
        end = host_header.find("]")
        if end == -1:
            return host_header
        return host_header[1:end]
    return host_header.split(":")[0]


def origin_allowed(origin: str | None, host_header: str) -> bool:
    if origin is None or origin == "":
        return True
    parsed = urlparse(origin)
    if parsed.scheme != "http":
        return False
    if parsed.hostname not in LOOPBACK_HOSTS:
        return False
    if parsed.port is None:
        return False
    host = host_hostname(host_header)
    if host not in LOOPBACK_HOSTS:
        return False
    # Same-origin: hostname class is loopback and the port matches the request.
    request_port = host_header
    if host_header.startswith("["):
        port_part = host_header.rsplit(":", 1)[-1]
    else:
        parts = host_header.split(":")
        port_part = parts[1] if len(parts) == 2 else ""
    if not port_part.isdigit():
        return False
    return parsed.port == int(port_part)


def assert_loopback_request(host_header: str | None, origin: str | None) -> None:
    if not host_header:
        raise CraftsError("Host header is required.", code="bad_host", status=403)
    if host_hostname(host_header) not in LOOPBACK_HOSTS:
        raise CraftsError("Crafts Prepare accepts loopback hosts only.", code="bad_host", status=403)
    if not origin_allowed(origin, host_header):
        raise CraftsError("Origin is not this local app.", code="bad_origin", status=403)


def user_path(raw: str) -> Path:
    if not isinstance(raw, str) or raw.strip() == "":
        raise CraftsError("A filesystem path is required.", code="bad_path")
    if "\x00" in raw:
        raise CraftsError("Path contains a null byte.", code="bad_path")
    # Reject schemes and obvious remote forms. Local absolute or relative paths only.
    if "://" in raw:
        raise CraftsError("Remote paths are not accepted.", code="bad_path")
    return Path(raw).expanduser()


def resolve_existing(raw: str) -> Path:
    path = user_path(raw)
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise CraftsError(f"Path does not exist: {path}", code="not_found", status=404) from exc
    except OSError as exc:
        raise CraftsError(f"Path cannot be resolved: {path}", code="bad_path") from exc
    return resolved


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def same_file(left: Path, right: Path) -> bool:
    try:
        left_stat = left.stat()
        right_stat = right.stat()
    except OSError:
        return False
    return (left_stat.st_dev, left_stat.st_ino) == (right_stat.st_dev, right_stat.st_ino)


def canonical_alias(left: Path, right: Path) -> bool:
    """True when two paths name the same file, including symlink and hardlink aliases."""
    if same_file(left, right):
        return True
    try:
        return left.resolve(strict=False) == right.resolve(strict=False) and left.exists() and right.exists()
    except OSError:
        return False


def safe_filename(name: str) -> str:
    if not isinstance(name, str) or not _SAFE_NAME.match(name):
        raise CraftsError(
            "Filename must use letters, numbers, dots, underscores, or hyphens.",
            code="bad_name",
        )
    if name in {".", ".."} or ".." in name.split("."):
        # ".." as a path segment is already excluded by the character class except dots.
        pass
    pure = PurePosixPath(name)
    if pure.name != name or pure.is_absolute():
        raise CraftsError("Filename cannot include a directory.", code="bad_name")
    return name


def safe_suffix(suffix: str) -> str:
    if suffix is None:
        return ""
    if not isinstance(suffix, str) or not _SUFFIX.match(suffix):
        raise CraftsError(
            "Suffix may contain only letters, numbers, dots, underscores, and hyphens.",
            code="bad_name",
        )
    if "/" in suffix or "\\" in suffix or suffix.startswith("."):
        raise CraftsError("Suffix cannot change the directory or the extension.", code="bad_name")
    return suffix


def assert_regular_file(path: Path) -> None:
    if not path.is_file():
        raise CraftsError(f"Not a file: {path}", code="bad_path")
    if path.is_symlink():
        # Callers that need the target should pass the resolved path.
        return


def reject_source_alias(source: Path, destination: Path) -> None:
    source_real = source.resolve(strict=True)
    if destination.exists():
        dest_real = destination.resolve(strict=True)
        if dest_real == source_real or same_file(destination, source) or same_file(dest_real, source_real):
            raise CraftsError(
                "Refusing to overwrite the source clip or an alias of it.",
                code="source_overwrite",
            )
    else:
        parent = destination.parent.resolve(strict=True)
        dest_real = parent / destination.name
        if dest_real == source_real:
            raise CraftsError(
                "Refusing to write the export onto the source path.",
                code="source_overwrite",
            )


def exclusive_output(destination: Path, source: Path) -> Path:
    """Return a new output path that does not exist and is not the source."""
    if not destination.parent.exists():
        raise CraftsError("Destination directory does not exist.", code="bad_path", status=404)
    parent = destination.parent.resolve(strict=True)
    if not parent.is_dir():
        raise CraftsError("Destination is not a directory.", code="bad_path")
    final = parent / safe_filename(destination.name)
    reject_source_alias(source, final)
    if final.exists() or final.is_symlink():
        raise CraftsError(
            f"Output already exists and will not be overwritten: {final}",
            code="collision",
            status=409,
        )
    # Compare lexical canonical form against odd spellings of the same path.
    if final.resolve(strict=False) == source.resolve(strict=True):
        raise CraftsError(
            "Refusing to write the export onto the source path.",
            code="source_overwrite",
        )
    return final


def partial_path(final: Path, job_id: str) -> Path:
    token = re.sub(r"[^A-Za-z0-9]", "", job_id)[:16] or "job"
    # The partial name does not end in a media extension, so a crash cannot
    # leave a file that looks like a finished export.
    return final.parent / f".{final.name}.{token}.crafts-partial"


def publish_exclusive(partial: Path, final: Path, source: Path) -> None:
    reject_source_alias(source, final)
    if final.exists() or final.is_symlink():
        raise CraftsError(
            f"Output appeared during the job and was left untouched: {final}",
            code="collision",
            status=409,
        )
    try:
        os.link(partial, final)
    except FileExistsError as exc:
        raise CraftsError(
            f"Output already exists and will not be overwritten: {final}",
            code="collision",
            status=409,
        ) from exc
    except OSError:
        # Fall back to an exclusive create plus replace of that empty inode only.
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(final, flags)
        except FileExistsError as exc:
            raise CraftsError(
                f"Output already exists and will not be overwritten: {final}",
                code="collision",
                status=409,
            ) from exc
        os.close(fd)
        os.replace(partial, final)
        return
    partial.unlink(missing_ok=True)


def quote_ffmpeg_filter_path(posix_path: str) -> str:
    """Return one filter-graph token for a path.

    The token is single-quoted. Colons, including a Windows drive letter, are
    escaped. An apostrophe ends the quote, inserts \\', and reopens it. Spaces
    stay inside the quotes. This is the filter parser's quoting, not a shell
    command string: the result is one argv element.
    """
    if any(char in posix_path for char in ("\x00", "\n", "\r")):
        raise CraftsError("Filter path contains a control character.", code="bad_path")
    escaped = posix_path.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"'\''")
    return f"'{escaped}'"


def ffmpeg_filter_path(path: Path) -> str:
    """Quote a real path for an FFmpeg filter graph value."""
    try:
        text = path.resolve(strict=False).as_posix()
    except OSError:
        text = path.as_posix()
    return quote_ffmpeg_filter_path(text)
