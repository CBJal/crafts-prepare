import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    from app.main import create_app

    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


@pytest.fixture(scope="session", autouse=True)
def _close_qt_before_interpreter_shutdown():
    """Stop player threads before Qt tears the process down."""
    yield
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return
    application = QApplication.instance()
    if application is None:
        return
    from PySide6.QtCore import QThread, QUrl

    for widget in list(application.topLevelWidgets()):
        player = getattr(widget, "player", None)
        if player is not None:
            player.stop()
            player.setVideoSink(None)
            player.setSource(QUrl())
        runner = getattr(widget, "_runner", None)
        if runner is not None and runner.isRunning():
            runner.shutdown(timeout=2)
        thumbs = getattr(widget, "_thumbs", None)
        if thumbs is not None:
            thumbs.shutdown()
        widget.close()
        widget.deleteLater()
    application.processEvents()
    lingering = [thread.objectName() or type(thread).__name__ for thread in application.findChildren(QThread) if thread.isRunning()]
    if lingering:
        print("QT_THREADS_STILL_RUNNING", lingering)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("media")
    generate_fixtures(root)
    return root


def generate_fixtures(root: Path) -> None:
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    common = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]

    def run(args: list[str]) -> None:
        subprocess.run(args, check=True)

    run(
        common
        + [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(root / "h264_8.mp4"),
        ]
    )
    run(
        common
        + [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-shortest",
            "-c:v",
            "libx265",
            "-pix_fmt",
            "yuv420p10le",
            "-c:a",
            "aac",
            str(root / "hevc_10.mp4"),
        ]
    )
    run(
        common
        + [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-shortest",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "2",
            "-pix_fmt",
            "yuv422p10le",
            "-c:a",
            "pcm_s16le",
            str(root / "prores_422_10.mov"),
        ]
    )
    run(
        common
        + [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-shortest",
            "-c:v",
            "dnxhd",
            "-profile:v",
            "dnxhr_hq",
            "-pix_fmt",
            "yuv422p",
            "-c:a",
            "pcm_s16le",
            str(root / "dnxhr_hq.mxf"),
        ]
    )
    run(
        common
        + [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=30000/1001:duration=0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=0.5",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30000/1001",
            "-c:a",
            "aac",
            str(root / "ntsc.mp4"),
        ]
    )
    run(
        common
        + [
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=400x240:r=25:d=2",
            "-vf",
            "drawbox=x=150:y=70:w=60:h=60:color=white:t=fill,crop=320:180:x='40+28*sin(8*PI*t)':y='24+18*sin(6*PI*t)'",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(root / "shake.mp4"),
        ]
    )


def require_encoder(client, name: str) -> None:
    caps = client.get("/api/capabilities").json()
    encoders = (caps.get("ffmpeg") or {}).get("encoders") or {}
    if not encoders.get(name):
        pytest.skip(f"encoder {name} is not available in the installed ffmpeg")
