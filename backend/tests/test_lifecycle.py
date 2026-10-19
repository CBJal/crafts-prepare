"""Frozen paths, tool lookup, and loopback port choice. No Windows installer is built here."""

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.runtime import (
    can_bind_loopback,
    choose_port,
    common_tool_candidates,
    discover_tool,
    external_tool_environ,
    frontend_dist,
    settings_path,
    user_root,
)
from app.launcher import health_is_ours, wait_until_ready
from app.version import APP_VERSION


def test_dev_paths_stay_under_the_home_directory(monkeypatch):
    monkeypatch.delenv("CRAFTS_SETTINGS_PATH", raising=False)
    monkeypatch.delenv("CRAFTS_DATA_DIR", raising=False)
    monkeypatch.delenv("CRAFTS_LOG_PATH", raising=False)
    monkeypatch.setattr("app.runtime.is_frozen", lambda: False)
    assert user_root().name == ".crafts-prepare"
    assert settings_path() == user_root() / "settings.json"
    assert frontend_dist().name == "dist"
    assert frontend_dist().parent.name == "frontend"


def test_frozen_frontend_and_user_data_are_outside_the_install_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("app.runtime.is_frozen", lambda: True)
    monkeypatch.setattr("app.runtime.bundle_dir", lambda: tmp_path / "bundle")
    (tmp_path / "bundle" / "frontend" / "dist").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.delenv("CRAFTS_SETTINGS_PATH", raising=False)
    monkeypatch.delenv("CRAFTS_DATA_DIR", raising=False)
    assert frontend_dist() == tmp_path / "bundle" / "frontend" / "dist"
    assert user_root() == tmp_path / "Local" / "CraftsPrepare"
    assert settings_path().parent == user_root()
    assert "Programs" not in settings_path().parts
    assert settings_path() != frontend_dist()


def test_explicit_missing_tool_is_not_replaced(tmp_path: Path):
    missing = tmp_path / "missing" / "ffmpeg.exe"
    assert discover_tool(str(missing), which=lambda _name: str(tmp_path / "other" / "ffmpeg.exe")) is None


def test_common_location_is_used_when_path_has_no_match(tmp_path: Path, monkeypatch):
    program = tmp_path / "Program Files"
    binary = program / "ffmpeg" / "bin" / "ffmpeg.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"not-a-real-binary")
    monkeypatch.setenv("ProgramFiles", str(program))
    found = discover_tool("ffmpeg", which=lambda _name: None)
    assert found == str(binary.resolve())
    assert any(item.name == "ffmpeg.exe" for item in common_tool_candidates("ffmpeg"))


def test_frozen_tool_path_drops_the_bundle_directory(monkeypatch, tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr("app.runtime.is_frozen", lambda: True)
    monkeypatch.setattr("app.runtime.bundle_path_entries", lambda: [bundle])
    env = external_tool_environ({"PATH": os.pathsep.join([str(bundle), str(tmp_path / "ffmpeg" / "bin")])})
    assert str(bundle) not in env["PATH"].split(os.pathsep)
    assert str(tmp_path / "ffmpeg" / "bin") in env["PATH"].split(os.pathsep)


def test_unrelated_listener_is_left_open_and_not_chosen():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    occupied = sock.getsockname()[1]
    sock.listen(1)
    try:
        assert can_bind_loopback(occupied) is False
        action, chosen = choose_port(occupied, can_bind=can_bind_loopback, is_ours=lambda _port: False, attempts=8)
        assert action == "bind"
        assert chosen != occupied
        assert sock.getsockname()[1] == occupied
    finally:
        sock.close()


def test_existing_crafts_prepare_port_is_reused_not_rebound():
    action, port = choose_port(
        8765,
        can_bind=lambda _port: False,
        is_ours=lambda port: port == 8765,
        attempts=3,
    )
    assert action == "reuse"
    assert port == 8765


def test_readiness_accepts_our_health_and_ignores_another_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": True, "name": "Crafts Prepare", "version": APP_VERSION}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert health_is_ours(port) is True
        ready, detail = wait_until_ready(port, timeout=2)
        assert ready, detail
        assert health_is_ours(port + 1) is False
    finally:
        server.shutdown()
        server.server_close()


def test_app_version_is_reported(client):
    health = client.get("/api/health").json()
    assert health["version"] == APP_VERSION
    assert health["name"] == "Crafts Prepare"
