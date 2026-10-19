"""Desktop entry. The installed app does not start Uvicorn or a browser."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ..runtime import is_frozen, log_path, profile_name, strip_developer_overrides, user_root
from ..version import APP_NAME


def apply_profile_argument(argv: list[str]) -> None:
    if "--profile" not in argv:
        return
    index = argv.index("--profile")
    if index + 1 >= len(argv) or argv[index + 1].startswith("-"):
        raise SystemExit("Use --profile with a short token such as qa.")
    os.environ["CRAFTS_PROFILE"] = argv[index + 1]


def prepare_standard_streams(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is not None and sys.stderr is not None:
        return
    handle = open(path, "a", encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle if sys.stdout is handle else open(path, "a", encoding="utf-8", errors="replace")


def configure_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(path),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def server_name() -> str:
    return f"CraftsPrepare-{profile_name()}"


def notify_running(name: str | None = None, timeout_ms: int = 2000) -> bool:
    """Ask the running instance to focus. Uses the Qt event loop, not a sleep."""
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtNetwork import QLocalSocket

    chosen = name or server_name()
    socket = QLocalSocket()
    loop = QEventLoop()
    result = {"ok": False, "buffer": bytearray()}

    def finish(ok: bool) -> None:
        result["ok"] = ok
        if loop.isRunning():
            loop.quit()

    def send() -> None:
        socket.write(b"raise\n")
        socket.flush()

    def read() -> None:
        result["buffer"].extend(bytes(socket.readAll()))
        if b"ok\n" in result["buffer"]:
            finish(True)

    socket.connected.connect(send)
    socket.readyRead.connect(read)
    socket.errorOccurred.connect(lambda *_args: finish(False))
    QTimer.singleShot(timeout_ms, lambda: finish(False))
    socket.connectToServer(chosen)
    loop.exec()
    if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        socket.disconnectFromServer()
    socket.deleteLater()
    return result["ok"]


def attach_server(window, name: str | None = None):
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    chosen = name or server_name()
    QLocalServer.removeServer(chosen)
    server = QLocalServer(window)
    if not server.listen(chosen):
        logging.error("Local server failed: %s", server.errorString())
        return None
    peers: list[QLocalSocket] = []

    def take(socket: QLocalSocket) -> None:
        buffer = bytearray(bytes(socket.readAll()))

        def finish() -> None:
            if socket in peers:
                peers.remove(socket)
            socket.deleteLater()

        def read() -> None:
            buffer.extend(bytes(socket.readAll()))
            if b"\n" not in buffer:
                return
            line = bytes(buffer).split(b"\n", 1)[0].strip()
            if line == b"raise":
                window.handle_peer(b"raise\n")
                socket.write(b"ok\n")
                socket.flush()
            socket.disconnectFromServer()

        socket.readyRead.connect(read)
        socket.disconnected.connect(finish)
        socket.errorOccurred.connect(lambda *_args: finish())
        peers.append(socket)
        if buffer:
            read()

    def accept() -> None:
        while True:
            socket = server.nextPendingConnection()
            if socket is None:
                return
            take(socket)

    server.newConnection.connect(accept)
    server._peers = peers
    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crafts-prepare", add_help=True)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sample", action="append", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--screenshots", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    apply_profile_argument(sys.argv[1:] if argv is None else argv)
    if is_frozen():
        strip_developer_overrides()
    os.environ["CRAFTS_HOST"] = "127.0.0.1"
    log_file = log_path()
    user_root().mkdir(parents=True, exist_ok=True)
    configure_logging(log_file)
    prepare_standard_streams(log_file)
    logging.info("Starting %s desktop profile %s", APP_NAME, profile_name())

    from PySide6.QtWidgets import QApplication

    from ..runtime import InstanceLock
    from .smoke import run_smoke

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(True)
    lock = InstanceLock()
    if not lock.acquire():
        if args.smoke:
            logging.error("Smoke did not start because this profile is already running.")
            return 2
        notify_running()
        return 0
    try:
        if args.smoke:
            if not args.output or not args.report or not args.screenshots or not (args.sample or args.config):
                logging.error("Smoke needs --output, --report, --screenshots, and --sample or --config.")
                return 2
            return run_smoke(
                samples=[Path(item) for item in (args.sample or [])],
                output=Path(args.output),
                report=Path(args.report),
                screenshots=Path(args.screenshots),
                config_path=Path(args.config) if args.config else None,
            )
        from ..service import Service
        from .window import MainWindow

        service = Service()
        service.start()
        window = MainWindow(service)
        attach_server(window)
        window.show()
        return app.exec()
    finally:
        lock.release()
