"""Child-process helpers. Commands are argument lists, never a shell string."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable

from .errors import Cancelled, CraftsError


def spawn(args: list[str]) -> subprocess.Popen[str]:
    if not args or not isinstance(args[0], str):
        raise CraftsError("Missing executable.", code="missing_tool")
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill this process and descendants we started. Do not signal unrelated pids."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass


def run_captured(
    args: list[str],
    *,
    timeout: float,
    cancel: threading.Event | None = None,
    on_stdout: Callable[[str], None] | None = None,
) -> tuple[int, str, str]:
    proc = spawn(args)
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    stdout_lock = threading.Lock()

    def read_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            with stdout_lock:
                stdout_parts.append(line)
                if len(stdout_parts) > 4000:
                    del stdout_parts[:1000]
            if on_stdout:
                on_stdout(line)

    def read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_parts.append(line)
            if len(stderr_parts) > 4000:
                del stderr_parts[:1000]

    out_thread = threading.Thread(target=read_stdout, daemon=True)
    err_thread = threading.Thread(target=read_stderr, daemon=True)
    out_thread.start()
    err_thread.start()
    deadline = time.monotonic() + timeout
    cancelled = False
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                cancelled = True
                kill_tree(proc)
                break
            if time.monotonic() > deadline:
                kill_tree(proc)
                raise CraftsError("The tool exceeded its time limit.", code="timeout", status=504)
            time.sleep(0.05)
    finally:
        out_thread.join(timeout=2)
        err_thread.join(timeout=2)
    code = proc.wait(timeout=5)
    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    if cancelled:
        raise Cancelled()
    return code, stdout, stderr


def tool_version(args: list[str], timeout: float = 15) -> tuple[bool, str]:
    try:
        code, stdout, stderr = run_captured(args, timeout=timeout)
    except (OSError, CraftsError) as exc:
        return False, str(exc)
    text = (stdout or stderr).strip()
    return code == 0, text[:2000]
