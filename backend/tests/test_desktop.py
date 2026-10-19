"""Native window behavior. Offscreen Qt. No installer is built here."""

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog

from app.desktop.dialogs import SettingsDialog
from app.desktop.main import attach_server, notify_running
from app.service import Service


@pytest.fixture(scope="module")
def qapp():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture()
def desktop(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CRAFTS_LOG_PATH", str(tmp_path / "app.log"))
    monkeypatch.setattr("app.runtime.is_frozen", lambda: False)
    service = Service()
    from app.desktop.window import MainWindow

    window = MainWindow(service, state_path=tmp_path / "desktop-state.json")
    window.show()
    qapp.processEvents()
    window.wait_until_idle(10000)
    yield window
    window.force_close = True
    window.close()
    qapp.processEvents()


def test_settings_dialog_cancel_does_not_save(qapp, desktop, tmp_path: Path):
    before = desktop.service.settings["ffmpeg"]
    dialog = SettingsDialog(desktop.service, desktop)
    dialog.fields["ffmpeg"].setText(str(tmp_path / "missing-ffmpeg"))
    QTimer.singleShot(0, dialog.reject)
    assert dialog.exec() == QDialog.DialogCode.Rejected
    assert desktop.service.settings["ffmpeg"] == before


def test_project_roundtrip_and_paused_jobs_do_not_resume(qapp, desktop, media_dir: Path, tmp_path: Path):
    desktop.import_paths([str(media_dir / "h264_8.mp4")])
    assert desktop.wait_until_idle(30000)
    assert desktop.clips
    desktop.destination.setText(str(tmp_path))
    desktop.queue_focused_export()
    job = desktop.service.jobs()[-1]
    assert job["status"] == "queued"
    assert job["request"]["preset_id"] == "h264_mp4"
    assert job["request"]["in_seconds"] == 0
    assert job["request"]["out_seconds"] > 0
    project = tmp_path / "bench.crafts.json"
    assert desktop.save_project_to(str(project))
    snapshot = tmp_path / "queued.crafts.json"
    snapshot.write_bytes(project.read_bytes())
    desktop.service.cancel(job["id"])
    assert desktop.new_project(discard=True)
    assert desktop.open_project_path(str(snapshot))
    reopened = desktop.service.jobs()[-1]
    assert reopened["status"] == "paused"
    assert reopened["code"] == "paused"
    assert desktop.service.worker is None
    document = json.loads(project.read_text(encoding="utf-8"))
    assert document["schema"] == "crafts-prepare.project"
    assert document["version"] == 1
    assert document["clips"][0]["sha256"]


def test_focus_and_batch_checks_are_separate(qapp, desktop, media_dir: Path):
    desktop.import_paths([str(media_dir / "h264_8.mp4"), str(media_dir / "hevc_10.mp4")])
    assert desktop.wait_until_idle(30000)
    assert len(desktop.clips) == 2
    desktop.focus_clip(desktop.clips[0]["id"])
    assert desktop.focused_id() == desktop.clips[0]["id"]
    assert desktop.checked_ids() == []
    desktop.select_all_batch()
    assert desktop.checked_ids() == [clip["id"] for clip in desktop.clips]
    assert desktop.focused_id() == desktop.clips[0]["id"]
    assert desktop.batch_count.text().startswith("2 of 2")
    desktop.clear_batch()
    assert desktop.checked_ids() == []
    assert desktop.focused_id() == desktop.clips[0]["id"]
    assert desktop.batch_count.text().startswith("0 of 2")


def test_theme_persists(qapp, desktop):
    assert desktop.set_theme("dark") == "dark"
    assert desktop.property("theme") == "dark"
    saved = json.loads(desktop.state_path.read_text(encoding="utf-8"))
    assert saved["theme"] == "dark"
    assert desktop.set_theme("light") == "light"
    assert desktop.property("themeChoice") == "light"


def test_stale_still_is_ignored(qapp, desktop):
    desktop._project_gen = 1
    desktop._still_seq = 5
    desktop._show_still(1, 4, True, "/tmp/not-the-current-still.jpg")
    assert desktop.still_path == ""


def test_close_cancel_keeps_the_window(qapp, desktop, media_dir: Path):
    desktop.import_paths([str(media_dir / "h264_8.mp4")])
    assert desktop.wait_until_idle(30000)
    desktop._prompt_unsaved = lambda: "cancel"
    event = QCloseEvent()
    desktop.closeEvent(event)
    assert event.isAccepted() is False
    assert desktop.clips


def test_export_result_and_missing_tool_are_honest(qapp, desktop, media_dir: Path, tmp_path: Path):
    desktop.import_paths([str(media_dir / "h264_8.mp4")])
    assert desktop.wait_until_idle(30000)
    desktop.destination.setText(str(tmp_path))
    desktop.ack.setChecked(True)
    desktop.queue_focused_export()
    desktop.service.drain()
    job = desktop.service.jobs()[-1]
    assert job["status"] == "succeeded"
    output = Path(job["result"]["output_path"])
    assert output.is_file() and output.stat().st_size > 0
    desktop.service.update_settings({"ffmpeg": str(tmp_path / "no-ffmpeg-binary"), "ffprobe": str(tmp_path / "no-ffprobe")})
    report = desktop.service.capabilities()
    assert report["ffmpeg"]["available"] is False
    desktop._apply_capabilities(report)
    assert "Not found yet" in desktop.statusBar().currentMessage()
    before = len(desktop.service.jobs())
    desktop.queue_focused_export()
    assert desktop.last_error
    assert len(desktop.service.jobs()) == before


def test_checked_gyro_batch_records_a_real_failure_without_gyroflow(qapp, desktop, media_dir: Path, tmp_path: Path):
    desktop.import_paths([str(media_dir / "h264_8.mp4")])
    assert desktop.wait_until_idle(30000)
    desktop.service.update_settings({"gyroflow": str(tmp_path / "missing-gyroflow")})
    desktop.destination.setText(str(tmp_path))
    desktop.select_all_batch()
    desktop.suffix.setText("_gyro")
    desktop.queue_checked_batch("gyro")
    assert desktop.last_batch_id
    job = desktop.service.jobs()[-1]
    assert job["kind"] == "gyroflow"
    assert job["request"]["automatic"] is True
    assert job["request"]["output_settings"]["format"] == "h264_mp4"
    desktop.service.drain()
    finished = desktop.service.jobs()[-1]
    assert finished["status"] == "failed"
    assert finished["status"] != "succeeded"


def test_second_instance_message_raises_the_window(qapp, desktop):
    from PySide6.QtCore import QEventLoop, QTimer

    name = "CraftsPrepare-test-desktop-ack"
    assert attach_server(desktop, name) is not None
    raised = []
    desktop.peer_raised.connect(lambda: raised.append(True))
    assert notify_running(name, timeout_ms=2000) is True
    loop = QEventLoop()
    QTimer.singleShot(500, loop.quit)
    if not raised:
        loop.exec()
    assert raised == [True]


def test_desktop_sources_do_not_start_a_browser_or_uvicorn():
    root = Path(__file__).resolve().parents[1] / "app" / "desktop"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "uvicorn" not in text
    assert "import webbrowser" not in text
    assert "webbrowser.open" not in text
    assert "QtWebEngine" not in text
    assert "QWebEngine" not in text
    entry = (Path(__file__).resolve().parents[1] / "crafts_prepare_entry.py").read_text(encoding="utf-8")
    assert "app.desktop.main" in entry
    assert "app.launcher" not in entry


def test_close_cancels_an_active_ffmpeg_child(qapp, desktop):
    import shutil
    import time

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    seen: dict[str, int] = {}

    def work(cancel):
        from app.process import run_captured

        run_captured(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=32x32:rate=5:duration=8",
                "-f",
                "null",
                "-",
            ],
            timeout=30,
            cancel=cancel,
            on_start=lambda proc: seen.__setitem__("pid", proc.pid),
        )

    desktop._start(work, lambda _value: None, key="long-ffmpeg")
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()

    def poll() -> None:
        if "pid" in seen:
            loop.quit()
        else:
            QTimer.singleShot(30, poll)

    QTimer.singleShot(4000, loop.quit)
    QTimer.singleShot(0, poll)
    loop.exec()
    assert "pid" in seen
    started = time.monotonic()
    desktop.force_close = True
    desktop.close()
    assert time.monotonic() - started < 4
    assert desktop._runner.isRunning() is False
    with pytest.raises(ProcessLookupError):
        os.kill(seen["pid"], 0)


def test_late_import_does_not_enter_a_replaced_project(qapp, desktop, media_dir: Path):
    import time

    from app.errors import Cancelled

    original = desktop.service.import_media

    def slow(raw, cancel=None):
        while cancel is None or not cancel.is_set():
            time.sleep(0.02)
        raise Cancelled()

    desktop.service.import_media = slow
    desktop.import_paths([str(media_dir / "h264_8.mp4")])
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(200, loop.quit)
    loop.exec()
    assert desktop.new_project(discard=True) is True
    assert desktop.wait_until_idle(4000)
    assert desktop.clips == []
    desktop.service.import_media = original


def test_reopen_clears_checks_focus_and_player(qapp, desktop, media_dir: Path, tmp_path: Path):
    desktop.import_paths([str(media_dir / "h264_8.mp4")])
    assert desktop.wait_until_idle(30000)
    desktop.select_all_batch()
    assert desktop.checked_ids()
    if desktop.player is not None:
        from PySide6.QtCore import QUrl

        desktop.player.setSource(QUrl.fromLocalFile(desktop.clips[0]["path"]))
        assert desktop.player.source().isEmpty() is False
    project = tmp_path / "again.crafts.json"
    assert desktop.save_project_to(str(project))
    assert desktop.open_project_path(str(project))
    assert desktop.checked_ids() == []
    assert desktop.focused_id() is None
    assert desktop.last_batch_id == ""
    if desktop.player is not None:
        assert desktop.player.source().isEmpty()
    assert desktop.still_path == ""


def test_failed_open_keeps_the_current_project(qapp, desktop, media_dir: Path, tmp_path: Path):
    desktop.import_paths([str(media_dir / "h264_8.mp4")])
    assert desktop.wait_until_idle(30000)
    assert desktop.save_project_to(str(tmp_path / "keep.crafts.json"))
    before = [clip["id"] for clip in desktop.clips]
    assert desktop.open_project_path(str(tmp_path / "missing.crafts.json")) is False
    assert [clip["id"] for clip in desktop.service.clips()] == before


def test_notes_trim_and_rating_roundtrip(qapp, desktop, media_dir: Path, tmp_path: Path):
    desktop.import_paths([str(media_dir / "h264_8.mp4")])
    assert desktop.wait_until_idle(30000)
    desktop.notes.setPlainText("hold this note")
    desktop.flag.setCurrentIndex(desktop.flag.findData("KEEP"))
    desktop.trim_in.setValue(0.2)
    desktop.trim_out.setValue(0.8)
    desktop.save_trim()
    desktop.add_marker()
    project = tmp_path / "edited.crafts.json"
    assert desktop.save_project_to(str(project))
    saved = json.loads(project.read_text(encoding="utf-8"))
    clip = saved["clips"][0]
    assert clip["notes"] == "hold this note"
    assert clip["flag"] == "KEEP"
    assert clip["trim_in"] == pytest.approx(0.2)
    assert clip["trim_out"] == pytest.approx(0.8)
    assert clip["markers"]
    assert desktop.new_project(discard=True)
    assert desktop.open_project_path(str(project))
    assert desktop.clips[0]["notes"] == "hold this note"


def test_playback_emits_a_frame_and_advances(qapp, media_dir: Path):
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink

    player = QMediaPlayer()
    player.setAudioOutput(QAudioOutput())
    sink = QVideoSink()
    seen = {"frames": 0, "position": 0}
    sink.videoFrameChanged.connect(lambda frame: seen.__setitem__("frames", seen["frames"] + 1) if frame.isValid() else None)
    player.positionChanged.connect(lambda position: seen.__setitem__("position", max(seen["position"], position)))
    player.setVideoSink(sink)
    player.setSource(QUrl.fromLocalFile(str(media_dir / "h264_8.mp4")))
    player.play()
    loop = QEventLoop()

    def poll() -> None:
        if seen["position"] > 0 and seen["frames"] > 0:
            loop.quit()
        else:
            QTimer.singleShot(40, poll)

    QTimer.singleShot(5000, loop.quit)
    QTimer.singleShot(0, poll)
    loop.exec()
    player.stop()
    assert seen["position"] > 0
    assert seen["frames"] > 0
    assert player.errorString() == ""


def test_smoke_harness_uses_the_real_window(qapp, tmp_path: Path, media_dir: Path, monkeypatch):
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CRAFTS_LOG_PATH", str(tmp_path / "app.log"))
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    from app.desktop.smoke import run_smoke

    report = tmp_path / "smoke.json"
    code = run_smoke(
        sample=media_dir / "h264_8.mp4",
        output=tmp_path / "out",
        report=report,
        screenshots=tmp_path / "shots",
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["http_server_started"] is False
    assert payload["version"] == "0.2.0"
    steps = {item["step"]: item for item in payload["steps"]}
    assert steps["import"]["ok"] is True
    assert steps["project"]["ok"] is True
    assert steps["viewer"]["ok"] is True
    assert steps["queue"]["ok"] is True
    assert any(item["kind"] == "export" and item["status"] == "succeeded" for item in steps["queue"]["jobs"])
    assert any(item["kind"] == "gyroflow" and item["status"] == "failed" for item in steps["queue"]["jobs"])
    assert any(Path(item["path"]).is_file() and item["width"] > 0 for item in payload["screenshots"])
    assert code == 0
    qapp.processEvents()
