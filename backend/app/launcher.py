"""Windowed loopback launcher for the installed Windows app.

`python -m app` stays a console server. This module is the frozen entry point:
no console, one instance, and a small Open/Quit window.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from .runtime import (
    HEALTH_NAME,
    can_bind_loopback,
    choose_port,
    instance_path,
    log_path,
    user_root,
)
from .version import APP_NAME, APP_VERSION


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def health_is_ours(port: int, timeout: float = 0.4) -> bool:
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError):
        return False
    return bool(body.get("ok")) and body.get("name") == HEALTH_NAME


def wait_until_ready(port: int, timeout: float = 20.0) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last = "the server did not answer"
    while time.monotonic() < deadline:
        if health_is_ours(port, timeout=0.5):
            return True, ""
        last = f"no Crafts Prepare health response on 127.0.0.1:{port}"
        time.sleep(0.1)
    return False, last


def read_instance(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def running_instance(path: Path | None = None) -> tuple[int, int] | None:
    """Return pid and port when a live Crafts Prepare instance recorded itself."""
    record = read_instance(path or instance_path())
    if not record:
        return None
    try:
        pid = int(record.get("pid"))
        port = int(record.get("port"))
    except (TypeError, ValueError):
        return None
    if not pid_alive(pid) or not health_is_ours(port):
        return None
    return pid, port


def write_instance(port: int, path: Path | None = None) -> None:
    destination = path or instance_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "port": port, "version": APP_VERSION}
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, destination)


def clear_instance(path: Path | None = None) -> None:
    destination = path or instance_path()
    record = read_instance(destination)
    if record and record.get("pid") not in {os.getpid(), None}:
        try:
            if int(record.get("pid")) != os.getpid():
                return
        except (TypeError, ValueError):
            return
    try:
        destination.unlink()
    except OSError:
        pass


def configure_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(path),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def tool_warning() -> str:
    try:
        from .service import Service

        report = Service().capabilities()
    except Exception as exc:  # noqa: BLE001 — a missing tool must not stop the window
        logging.exception("Tool check failed")
        return f"Tool check failed: {exc}"
    missing = []
    for name in ("ffmpeg", "ffprobe", "gyroflow"):
        item = report.get(name) or {}
        if not item.get("available"):
            missing.append(name)
    if not missing:
        return "FFmpeg, ffprobe, and Gyroflow were found."
    return (
        "Not bundled, and not found yet: "
        + ", ".join(missing)
        + ". Install them yourself and set the paths in Capabilities. Crafts Prepare does not download them."
    )


def show_message(title: str, message: str) -> None:
    logging.error("%s: %s", title, message)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
            return
        except Exception:
            logging.exception("MessageBox failed")
    print(f"{title}\n{message}", file=sys.stderr)


def _open_browser(port: int) -> None:
    webbrowser.open(f"http://127.0.0.1:{port}/")


def _run_window(port: int, server, log_file: Path, warning: str) -> None:
    import tkinter as tk
    from tkinter import font as tkfont

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("440x220")
    root.minsize(380, 200)
    root.configure(bg="#171d25")
    title_font = tkfont.Font(family="Segoe UI", size=14, weight="bold")
    body_font = tkfont.Font(family="Segoe UI", size=10)

    def paint(parent, text, *, size_font, fg: str) -> None:
        label = tk.Label(parent, text=text, bg="#171d25", fg=fg, font=size_font, justify="left", wraplength=400)
        label.pack(anchor="w", padx=16, pady=(8, 0))

    paint(root, APP_NAME, size_font=title_font, fg="#e7edf4")
    paint(root, f"Running at http://127.0.0.1:{port}/", size_font=body_font, fg="#9aa8b8")
    paint(root, warning, size_font=body_font, fg="#e7edf4")
    paint(root, f"Log: {log_file}", size_font=body_font, fg="#9aa8b8")

    buttons = tk.Frame(root, bg="#171d25")
    buttons.pack(anchor="w", padx=16, pady=16)

    def open_ui() -> None:
        _open_browser(port)

    def quit_app() -> None:
        logging.info("Quit requested")
        try:
            root.destroy()
        except tk.TclError:
            pass

    open_button = tk.Button(buttons, text="Open", command=open_ui, bg="#4c8dff", fg="#061018", relief="flat", padx=14, pady=6)
    open_button.pack(side="left", padx=(0, 8))
    quit_button = tk.Button(buttons, text="Quit", command=quit_app, bg="#1e2631", fg="#e7edf4", relief="flat", padx=14, pady=6)
    quit_button.pack(side="left")
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.mainloop()
    try:
        server.app.state.service.shutdown()
    except Exception:
        logging.exception("Queue shutdown failed")
    server.should_exit = True


def serve(port: int):
    import uvicorn

    from .main import create_app

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    # Signal handlers can only be installed on the main thread, which owns the window.
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, name="crafts-http", daemon=True)
    thread.start()
    return server, thread


def main() -> None:
    os.environ["CRAFTS_HOST"] = "127.0.0.1"
    log_file = log_path()
    try:
        user_root().mkdir(parents=True, exist_ok=True)
        configure_logging(log_file)
        logging.info("Starting %s %s", APP_NAME, APP_VERSION)
        existing = running_instance()
        if existing:
            _open_browser(existing[1])
            logging.info("Already running on port %s", existing[1])
            return
        preferred = int(os.environ.get("CRAFTS_PORT", "8765"))
        action, port = choose_port(preferred, can_bind=can_bind_loopback, is_ours=health_is_ours)
        if action == "reuse":
            _open_browser(port)
            logging.info("Reusing instance on port %s", port)
            return
        server, thread = serve(port)
        ready, detail = wait_until_ready(port)
        if not ready:
            server.should_exit = True
            thread.join(timeout=5)
            show_message(APP_NAME, f"The local server did not become ready. {detail}\n\nLog:\n{log_file}")
            return
        write_instance(port)
        warning = tool_warning()
        logging.info(warning)
        _open_browser(port)
        try:
            _run_window(port, server, log_file, warning)
        finally:
            clear_instance()
            thread.join(timeout=8)
        logging.info("Stopped")
    except Exception as exc:
        try:
            configure_logging(log_file)
        except Exception:
            pass
        logging.exception("Launcher failed")
        show_message(APP_NAME, f"{exc}\n\nLog:\n{log_file}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
