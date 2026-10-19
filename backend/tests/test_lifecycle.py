"""Frozen paths, tool lookup, and loopback port choice. No Windows installer is built here."""

import json
import logging
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn

from app.runtime import (
    InstanceLock,
    can_bind_loopback,
    choose_port,
    claim_listen_socket,
    common_tool_candidates,
    data_dir,
    discover_tool,
    external_tool_environ,
    frontend_dist,
    log_path,
    preferred_port,
    profile_name,
    settings_path,
    strip_developer_overrides,
    user_root,
)
from app.launcher import (
    bind_owned_port,
    file_only_log_config,
    fitted_window_size,
    health_is_ours,
    prepare_standard_streams,
    running_instance,
    wait_for_recorded_instance,
    wait_until_ready,
    write_instance,
)
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
            body = json.dumps(
                {"ok": True, "name": "Crafts Prepare", "version": APP_VERSION, "profile": "dev"}
            ).encode()
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
    assert health["profile"] == "dev"


def test_dev_blank_port_uses_8765_and_overrides_still_apply(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("app.runtime.is_frozen", lambda: False)
    monkeypatch.setenv("CRAFTS_PORT", "")
    assert preferred_port() == 8765
    monkeypatch.setenv("CRAFTS_PORT", "   ")
    assert preferred_port() == 8765
    monkeypatch.setenv("CRAFTS_PORT", "8791")
    assert preferred_port() == 8791
    monkeypatch.setenv("CRAFTS_PORT", "nope")
    with pytest.raises(ValueError):
        preferred_port()
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CRAFTS_LOG_PATH", str(tmp_path / "app.log"))
    assert settings_path() == tmp_path / "settings.json"
    assert data_dir() == tmp_path / "cache"
    assert log_path() == tmp_path / "app.log"


def test_frozen_app_ignores_developer_overrides(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("app.runtime.is_frozen", lambda: True)
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "dev-settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "dev-cache"))
    monkeypatch.setenv("CRAFTS_LOG_PATH", str(tmp_path / "dev.log"))
    monkeypatch.setenv("CRAFTS_PORT", "")
    monkeypatch.setenv("CRAFTS_HOST", "0.0.0.0")
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    monkeypatch.setenv("CRAFTS_PROFILE", r"..\secret")
    strip_developer_overrides()
    assert "CRAFTS_PORT" not in os.environ
    assert "CRAFTS_SETTINGS_PATH" not in os.environ
    assert "CRAFTS_DISABLE_WORKER" not in os.environ
    assert profile_name() == "user"
    assert user_root() == local / "CraftsPrepare"
    assert settings_path() == local / "CraftsPrepare" / "settings.json"
    assert data_dir() == local / "CraftsPrepare" / "cache"
    assert log_path() == local / "CraftsPrepare" / "logs" / "crafts-prepare.log"
    assert preferred_port() == 8765
    monkeypatch.setenv("CRAFTS_PROFILE", "qa")
    assert profile_name() == "qa"
    assert user_root() == local / "CraftsPrepare-qa"


def test_two_profiles_take_independent_locks(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("app.runtime.is_frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("CRAFTS_PROFILE", raising=False)
    user_lock = InstanceLock()
    assert user_lock.acquire()
    monkeypatch.setenv("CRAFTS_PROFILE", "qa")
    qa_lock = InstanceLock()
    assert qa_lock.path != user_lock.path
    assert qa_lock.acquire()
    same_qa = InstanceLock()
    assert same_qa.acquire() is False
    qa_lock.release()
    user_lock.release()
    assert same_qa.acquire()
    same_qa.release()


def test_concurrent_launches_only_one_lock_wins(tmp_path: Path):
    path = tmp_path / "instance.lock"
    results: list[bool] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        lock = InstanceLock(path)
        barrier.wait()
        won = lock.acquire()
        results.append(won)
        if won:
            time.sleep(0.3)
            lock.release()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert sorted(results) == [False, True]
    follow = InstanceLock(path)
    assert follow.acquire()
    follow.release()


def test_stale_or_other_profile_record_is_not_a_running_instance(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.runtime.is_frozen", lambda: False)
    stale = tmp_path / "instance.json"
    stale.write_text(json.dumps({"pid": 2**30, "port": 8765, "profile": "dev"}), encoding="utf-8")
    assert running_instance(stale) is None
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"pid": os.getpid(), "port": 8765, "profile": "qa"}), encoding="utf-8")
    assert running_instance(other) is None
    lock = InstanceLock(tmp_path / "instance.lock")
    assert lock.acquire()
    lock.release()


def test_reserved_socket_stays_held_and_a_foreign_port_is_not_closed():
    foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    foreign.bind(("127.0.0.1", 0))
    foreign.listen(1)
    occupied = foreign.getsockname()[1]
    try:
        assert claim_listen_socket(occupied) is None
        port, sock = bind_owned_port(occupied)
        try:
            assert port != occupied
            assert foreign.getsockname()[1] == occupied
            assert claim_listen_socket(port) is None
        finally:
            sock.close()
    finally:
        foreign.close()


def test_health_from_another_profile_is_not_ours():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": True, "name": "Crafts Prepare", "version": APP_VERSION, "profile": "qa"}).encode()
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
        assert health_is_ours(port, expected_profile="dev") is False
        assert health_is_ours(port, expected_profile="qa") is True
        chosen, sock = bind_owned_port(port)
        try:
            assert chosen != port
            assert health_is_ours(port, expected_profile="qa") is True
        finally:
            sock.close()
    finally:
        server.shutdown()
        server.server_close()


def test_second_launch_waits_until_the_record_is_ready(tmp_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": True, "name": "Crafts Prepare", "version": APP_VERSION, "profile": "dev"}).encode()
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
    record = tmp_path / "instance.json"
    try:
        assert wait_for_recorded_instance(record, timeout=0.3) is None

        def publish() -> None:
            time.sleep(0.2)
            write_instance(port, record)

        threading.Thread(target=publish, daemon=True).start()
        found = wait_for_recorded_instance(record, timeout=3)
        assert found is not None
        assert found[1] == port
    finally:
        server.shutdown()
        server.server_close()


def test_windowed_uvicorn_config_accepts_missing_standard_streams(tmp_path: Path):
    log = tmp_path / "crafts-prepare.log"
    saved_out, saved_err = sys.stdout, sys.stderr

    async def app(scope, receive, send):
        return None

    try:
        sys.stdout = None
        sys.stderr = None
        with pytest.raises((AttributeError, ValueError)):
            uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="info")
        prepare_standard_streams(log)
        assert sys.stdout is not None and sys.stdout.isatty() is False
        assert sys.stderr is not None and sys.stderr.isatty() is False
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=8765,
            log_level="info",
            access_log=False,
            use_colors=False,
            log_config=file_only_log_config(log),
        )
    finally:
        for stream in (sys.stdout, sys.stderr):
            if stream not in (saved_out, saved_err) and stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        sys.stdout = saved_out
        sys.stderr = saved_err
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                if isinstance(handler, logging.FileHandler):
                    logger.removeHandler(handler)
                    handler.close()


def test_launcher_window_clamps_to_the_screen_and_keeps_a_button_row():
    width, height = fitted_window_size(900, 4000, 800, 600)
    assert width <= 776
    assert height <= 552
    assert height >= 160
    narrow, short = fitted_window_size(100, 40, 640, 480)
    assert narrow >= 320
    assert short >= 160


def test_frozen_worker_starts_when_disable_flag_is_set(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("app.runtime.is_frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    from app.service import Service

    service = Service()
    service.start()
    try:
        assert service.worker is not None
        assert service.worker.is_alive()
    finally:
        service.shutdown(timeout=2)
    assert service.worker is not None
    assert service.worker.is_alive() is False


def test_shutdown_on_the_worker_thread_does_not_deadlock(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("app.runtime.is_frozen", lambda: False)
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    from app.service import Service

    service = Service()
    service.worker = threading.current_thread()
    started = time.monotonic()
    service.shutdown(timeout=5)
    assert time.monotonic() - started < 1
    assert service.stop.is_set()


def test_shutdown_stops_an_active_fake_child_and_removes_staging(client, media_dir: Path, tmp_path: Path, monkeypatch):
    from tests.gyro_sync import wait_for_fake_render
    from tests.test_gyroflow_adapter import _write_fake

    fake = tmp_path / "gyroflow_fake.py"
    log = tmp_path / "argv.txt"
    _write_fake(fake)
    monkeypatch.setenv("GYRO_LOG", str(log))
    monkeypatch.setenv("GYRO_MEDIA", str(media_dir / "h264_8.mp4"))
    monkeypatch.setenv("GYRO_MODE", "sleep")
    monkeypatch.delenv("GYRO_ENCODE", raising=False)
    client.put("/api/settings", json={"gyroflow": str(fake)})
    gyro = tmp_path / "motion.gcsv"
    gyro.write_text("sidecar")
    output = tmp_path / "shutdown-me.mp4"
    queued = client.post(
        "/api/jobs/gyroflow",
        json={"media_path": str(media_dir / "h264_8.mp4"), "gyro_path": str(gyro), "output_path": str(output)},
    ).json()
    monkeypatch.delenv("CRAFTS_DISABLE_WORKER", raising=False)
    service = client.app.state.service
    service.start()
    try:
        running = wait_for_fake_render(client, queued["id"], log, timeout=20)
        assert running["status"] == "running"
        assert list(tmp_path.glob(".crafts-gyro-*"))
        started = time.monotonic()
        service.shutdown(timeout=10)
        assert time.monotonic() - started < 10
        assert service.worker is not None
        assert service.worker.is_alive() is False
        assert list(tmp_path.glob(".crafts-gyro-*")) == []
        assert not output.exists()
        job = next(item for item in client.get("/api/jobs").json()["jobs"] if item["id"] == queued["id"])
        assert job["status"] == "cancelled"
    finally:
        service.shutdown(timeout=2)
