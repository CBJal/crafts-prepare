"""Windowed loopback launcher for the installed Windows app.

`python -m app` stays a console server. This module is the frozen entry point:
no console, one instance per user profile, and a small Open/Quit window.
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
from pathlib import Path

from .runtime import (
    HEALTH_NAME,
    InstanceLock,
    claim_listen_socket,
    instance_path,
    log_path,
    preferred_port,
    profile_name,
    strip_developer_overrides,
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


def health_is_ours(port: int, timeout: float = 0.4, expected_profile: str | None = None) -> bool:
    profile = profile_name() if expected_profile is None else expected_profile
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError):
        return False
    return bool(body.get("ok")) and body.get("name") == HEALTH_NAME and body.get("profile") == profile


def wait_until_ready(port: int, timeout: float = 20.0, expected_profile: str | None = None) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    profile = profile_name() if expected_profile is None else expected_profile
    last = "the server did not answer"
    while time.monotonic() < deadline:
        if health_is_ours(port, timeout=0.5, expected_profile=profile):
            return True, ""
        last = f"no Crafts Prepare health response for profile {profile} on 127.0.0.1:{port}"
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
    """Return pid and port when this profile recorded a live instance."""
    record = read_instance(path or instance_path())
    if not record:
        return None
    if str(record.get("profile") or "") != profile_name():
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
    payload = {"pid": os.getpid(), "port": port, "version": APP_VERSION, "profile": profile_name()}
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


def wait_for_recorded_instance(path: Path | None = None, timeout: float = 20.0) -> tuple[int, int] | None:
    deadline = time.monotonic() + timeout
    while True:
        found = running_instance(path)
        if found:
            return found
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.1)


def bind_owned_port(preferred: int, attempts: int = 16) -> tuple[int, object]:
    """Reserve a listening socket. A busy port is left alone, including our own health."""
    profile = profile_name()
    if not 1 <= preferred <= 65535:
        raise ValueError("preferred port is invalid")
    for offset in range(max(1, attempts)):
        port = preferred + offset
        if port > 65535:
            break
        sock = claim_listen_socket(port)
        if sock is not None:
            return port, sock
        if health_is_ours(port, expected_profile=profile):
            logging.warning(
                "Port %s answered as profile %s while this process holds the lock. Leaving it.",
                port,
                profile,
            )
        else:
            logging.info("Port %s is in use by another program. Leaving it.", port)
    raise RuntimeError(f"No free loopback port from {preferred}.")


def apply_profile_argument(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--profile" not in args:
        return
    index = args.index("--profile")
    if index + 1 >= len(args) or args[index + 1].startswith("-"):
        raise SystemExit("Use --profile with a short token such as qa.")
    os.environ["CRAFTS_PROFILE"] = args[index + 1]


def configure_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(path),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def prepare_standard_streams(path: Path) -> None:
    """Give a windowed process real streams. Windowed Python sets stdout and stderr to None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is not None and sys.stderr is not None:
        return
    handle = open(path, "a", encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle if sys.stdout is handle else open(path, "a", encoding="utf-8", errors="replace")


def file_only_log_config(path: Path) -> dict:
    """Uvicorn log config that never touches a console or color formatter."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
            "access": {"format": "%(asctime)s %(message)s"},
        },
        "handlers": {
            "default": {
                "class": "logging.FileHandler",
                "formatter": "default",
                "filename": str(path),
                "encoding": "utf-8",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": ["default"], "level": "INFO", "propagate": False},
        },
    }


def fitted_window_size(required_width: int, required_height: int, screen_width: int, screen_height: int) -> tuple[int, int]:
    screen_width = max(int(screen_width), 1)
    screen_height = max(int(screen_height), 1)
    width = min(max(int(required_width), 320), max(320, screen_width - 24))
    height = min(max(int(required_height), 160), max(160, screen_height - 48))
    return width, height


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
    stream = sys.stderr if sys.stderr is not None else sys.stdout
    if stream is not None:
        print(f"{title}\n{message}", file=stream)


def _open_browser(port: int) -> None:
    webbrowser.open(f"http://127.0.0.1:{port}/")


def _stop_owned_server(server, thread: threading.Thread | None) -> None:
    try:
        server.app.state.service.shutdown(timeout=8)
    except Exception:
        logging.exception("Queue shutdown failed")
    server.should_exit = True
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=8)


def _run_window(port: int, log_file: Path, warning: str) -> None:
    import tkinter as tk
    from tkinter import font as tkfont

    root = tk.Tk()
    root.title(APP_NAME)
    root.configure(bg="#171d25")
    root.minsize(320, 160)
    screen_w = max(root.winfo_screenwidth(), 320)
    screen_h = max(root.winfo_screenheight(), 240)
    try:
        scaling = float(root.tk.call("tk", "scaling"))
    except (tk.TclError, ValueError):
        scaling = 1.0
    wrap = int(min(max(420 * max(scaling, 1.0), 280), screen_w - 48))

    def quit_app() -> None:
        logging.info("Quit requested")
        try:
            root.destroy()
        except tk.TclError:
            pass

    buttons = tk.Frame(root, bg="#171d25")
    buttons.pack(side="bottom", fill="x", padx=16, pady=12)
    open_button = tk.Button(
        buttons,
        text="Open",
        command=lambda: _open_browser(port),
        bg="#4c8dff",
        fg="#061018",
        relief="flat",
        padx=14,
        pady=6,
    )
    open_button.pack(side="left", padx=(0, 8))
    quit_button = tk.Button(
        buttons,
        text="Quit",
        command=quit_app,
        bg="#1e2631",
        fg="#e7edf4",
        relief="flat",
        padx=14,
        pady=6,
    )
    quit_button.pack(side="left")

    body = tk.Frame(root, bg="#171d25")
    body.pack(side="top", fill="both", expand=True, padx=16, pady=(12, 0))
    title_font = tkfont.Font(family="Segoe UI", size=14, weight="bold")
    body_font = tkfont.Font(family="Segoe UI", size=10)
    tk.Label(body, text=APP_NAME, bg="#171d25", fg="#e7edf4", font=title_font, anchor="w").pack(anchor="w")
    content = f"Running at http://127.0.0.1:{port}/\n\n{warning}\n\nLog: {log_file}"
    text = tk.Text(
        body,
        wrap="word",
        width=max(24, wrap // 8),
        height=4,
        bg="#171d25",
        fg="#d5dee8",
        font=body_font,
        relief="flat",
        highlightthickness=0,
        borderwidth=0,
    )
    text.insert("1.0", content)
    text.configure(state="disabled")
    column = max(1, wrap // 7)
    wrapped = 0
    for line in content.splitlines() or [""]:
        wrapped += max(1, (len(line) + column - 1) // column)
    text.configure(height=min(max(wrapped, 4), 14))
    text.pack(fill="both", expand=True, pady=(8, 0))
    root.update_idletasks()
    width, height = fitted_window_size(root.winfo_reqwidth(), root.winfo_reqheight(), screen_w, screen_h)
    root.geometry(f"{width}x{height}")
    if screen_w >= width and screen_h >= height:
        root.maxsize(screen_w, screen_h)
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.mainloop()


def serve(port: int, sock, log_file: Path):
    import uvicorn

    from .main import create_app

    prepare_standard_streams(log_file)
    app = create_app()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
        use_colors=False,
        log_config=file_only_log_config(log_file),
    )
    server = uvicorn.Server(config)
    # Signal handlers can only be installed on the main thread, which owns the window.
    server.install_signal_handlers = lambda: None

    def run() -> None:
        server.run(sockets=[sock])

    thread = threading.Thread(target=run, name="crafts-http", daemon=True)
    thread.start()
    return server, thread


def main() -> None:
    apply_profile_argument()
    strip_developer_overrides()
    os.environ["CRAFTS_HOST"] = "127.0.0.1"
    log_file = log_path()
    lock = InstanceLock()
    try:
        user_root().mkdir(parents=True, exist_ok=True)
        configure_logging(log_file)
        prepare_standard_streams(log_file)
        logging.info("Starting %s %s profile %s", APP_NAME, APP_VERSION, profile_name())
        if not lock.acquire():
            existing = wait_for_recorded_instance(timeout=20)
            if existing:
                _open_browser(existing[1])
                logging.info("Already running on port %s", existing[1])
                return
            show_message(
                APP_NAME,
                "Another Crafts Prepare launch for this profile is still starting, "
                f"or it did not become ready.\n\nLog:\n{log_file}",
            )
            return
        try:
            preferred = preferred_port()
        except ValueError as exc:
            show_message(APP_NAME, f"{exc}\n\nLog:\n{log_file}")
            raise SystemExit(1) from exc
        server = None
        thread = None
        sock = None
        handed = False
        try:
            port, sock = bind_owned_port(preferred)
            server, thread = serve(port, sock, log_file)
            handed = True
            ready, detail = wait_until_ready(port)
            if not ready:
                show_message(APP_NAME, f"The local server did not become ready. {detail}\n\nLog:\n{log_file}")
                return
            write_instance(port)
            warning = tool_warning()
            logging.info(warning)
            _open_browser(port)
            _run_window(port, log_file, warning)
            logging.info("Stopped")
        finally:
            if server is not None:
                _stop_owned_server(server, thread)
            if sock is not None and not handed:
                try:
                    sock.close()
                except OSError:
                    pass
            clear_instance()
    except SystemExit:
        raise
    except Exception as exc:
        try:
            configure_logging(log_file)
            prepare_standard_streams(log_file)
        except Exception:
            pass
        logging.exception("Launcher failed")
        show_message(APP_NAME, f"{exc}\n\nLog:\n{log_file}")
        raise SystemExit(1) from exc
    finally:
        lock.release()


if __name__ == "__main__":
    main()
