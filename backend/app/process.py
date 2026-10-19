"""Child-process helpers. Commands are argument lists, never a shell string."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager

from .errors import Cancelled, CraftsError

# Win32 CREATE_NO_WINDOW. subprocess does not define it outside Windows.
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

# Tests replace this with an object that records get/set. Production uses kernel32.
_dll_controller = None
_dll_lock = threading.Lock()


def windows_no_window_flag() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW))


def windows_creation_flags() -> int:
    new_group = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", CREATE_NEW_PROCESS_GROUP))
    return new_group | windows_no_window_flag()


class _Kernel32DllDirectory:
    """The DLL search directory the PyInstaller bootloader sets for this process."""

    def get(self) -> str | None:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetDllDirectoryW(len(buffer), buffer)
        if not length:
            return None
        return buffer.value

    def set(self, value: str | None) -> None:
        import ctypes

        if not ctypes.windll.kernel32.SetDllDirectoryW(value):
            raise OSError("SetDllDirectoryW failed")


def _dll_reset_active() -> bool:
    if _dll_controller is not None:
        return True
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


@contextmanager
def isolate_external_dll_search():
    """Clear the inherited PyInstaller DLL directory for one child, then restore it.

    The Windows bootloader calls SetDllDirectoryW on its extraction directory.
    Child processes inherit that search path, so PATH edits are not enough.
    https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html
    """
    if not _dll_reset_active():
        yield
        return
    controller = _dll_controller or _Kernel32DllDirectory()
    with _dll_lock:
        previous = controller.get()
        controller.set(None)
        try:
            yield
        finally:
            controller.set(previous if previous else None)


def spawn(args: list[str], cwd: str | None = None) -> subprocess.Popen[str]:
    if not args or not isinstance(args[0], str):
        raise CraftsError("Missing executable.", code="missing_tool")
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "cwd": cwd,
    }
    if os.name == "nt":
        # CREATE_NO_WINDOW keeps ffmpeg and Gyroflow from opening a console.
        kwargs["creationflags"] = windows_creation_flags()
    else:
        kwargs["start_new_session"] = True
    with isolate_external_dll_search():
        if getattr(sys, "frozen", False):
            from .runtime import external_tool_environ

            kwargs["env"] = external_tool_environ()
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
            creationflags=windows_no_window_flag(),
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
    cwd: str | None = None,
) -> tuple[int, str, str]:
    proc = spawn(args, cwd=cwd)
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
