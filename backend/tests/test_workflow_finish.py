"""Release regressions for real UI actions, refresh isolation and decoded frames."""
import concurrent.futures
import copy
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

from app.desktop.window import MainWindow
from app.errors import Cancelled
from app.service import Service, new_project


@pytest.fixture()
def window(tmp_path, monkeypatch, media_dir):
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CRAFTS_LOG_PATH", str(tmp_path / "app.log"))
    app = QApplication.instance() or QApplication([])
    desktop = MainWindow(Service(), state_path=tmp_path / "state.json")
    desktop._prompt_unsaved = lambda: "discard"
    desktop.resize(1280, 800)
    desktop.show()
    desktop.import_paths([str(media_dir / "h264_8.mp4")])
    assert desktop.wait_until_idle(60000)
    desktop.focus_clip(desktop.clips[0]["id"])
    yield desktop
    desktop.force_close = True
    desktop.close()
    app.processEvents()
    assert not desktop._runner.isRunning()


def _wait(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)  # Yield Python's GIL to the production QThread worker.
    return bool(predicate())


def test_lut_picker_and_clear_store_the_actual_button_action(window, tmp_path, monkeypatch):
    lut = tmp_path / "look.cube"
    lut.write_text("LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(lut), ""))
    buttons = window.look_lut.parentWidget().findChildren(QPushButton)
    picker = next(b for b in buttons if b.text() == "…")
    clear = next(b for b in buttons if b.text() == "Clear")
    QTest.mouseClick(picker, Qt.MouseButton.LeftButton)
    assert _wait(lambda: window.service.clips()[0]["recipe"]["look_lut"] == str(lut))
    QTest.mouseClick(clear, Qt.MouseButton.LeftButton)
    assert _wait(lambda: window.service.clips()[0]["recipe"]["look_lut"] == "")


def test_failed_edit_cannot_switch_folder_and_can_be_discarded(window, tmp_path):
    window.look_lut.setText(str(tmp_path / "missing.cube"))
    window._schedule_recipe()
    before = window._browsed_root
    window._folder_chosen(str(tmp_path))
    assert window._browsed_root == before
    assert window._pending_edit is not None
    window._prompt_discard_edit = lambda: False
    assert not window.new_project()
    assert window._pending_edit is not None
    window.close()
    assert window.isVisible()
    window._prompt_discard_edit = lambda: True
    window.close()
    assert not window.isVisible()
    assert window._pending_edit is None


def test_force_close_stops_workers_even_with_invalid_pending_lut(window, tmp_path):
    window.look_lut.setText(str(tmp_path / "missing.cube"))
    window._schedule_recipe()
    assert not window._flush_pending_edit()
    window.force_close = True
    window.close()
    assert not window.isVisible()
    assert not window._runner.isRunning()


def test_gyro_batch_apply_keeps_each_clips_colour_recipe(window, media_dir):
    window.import_paths([str(media_dir / "hevc_10.mp4")])
    assert window.wait_until_idle(60000)
    first, second = window.service.clips()
    window.service.update_clip(first["id"], {"recipe": {"exposure_ev": 0.7}})
    window.service.update_clip(second["id"], {"recipe": {"exposure_ev": 0.3}})
    window.reload_all()
    window.select_all_batch()
    window.gyro_enabled.setChecked(True)
    window.gyro_strength.setValue(75)
    QTest.mouseClick(window.gyro_apply_button, Qt.MouseButton.LeftButton)
    recipes = [clip["recipe"] for clip in window.service.clips()]
    assert all(r["gyro_enabled"] and r["gyro_smoothness"] == 0.75 for r in recipes)
    assert [r["exposure_ev"] for r in recipes] == [0.7, 0.3]
    window.service.update_clip(first["id"], {"recipe": {"gyro_smoothness": 0.2}})
    window.reload_all()
    window.exposure.setValue(0.1)
    window.apply_recipe_to_selected()
    recipes = [clip["recipe"] for clip in window.service.clips()]
    assert [r["gyro_smoothness"] for r in recipes] == [0.2, 0.75]
    assert all(r["exposure_ev"] == 0.1 for r in recipes)


def test_refresh_is_cancellable_and_does_not_block_ui_reads(window, monkeypatch):
    import app.probe as probe_module

    started = threading.Event()
    original = probe_module.probe_file
    before = copy.deepcopy(window.service.clips())

    def slow_probe(*args, cancel=None, **kwargs):
        started.set()
        while not cancel.wait(0.01):
            pass
        raise Cancelled()

    monkeypatch.setattr(probe_module, "probe_file", slow_probe)
    window.refresh_selected_metadata()
    assert _wait(started.is_set), (window.last_error, window.selected_ids(), window._inflight, window.statusBar().currentMessage())
    # These are the same lock-taking reads used by the UI refresh timer.
    start = time.monotonic()
    assert window.service.clips() == before
    window.refresh_queue()
    assert time.monotonic() - start < 0.5
    assert window.metadata_cancel_button.isEnabled()
    QTest.mouseClick(window.metadata_cancel_button, Qt.MouseButton.LeftButton)
    assert window.wait_until_idle(5000)
    assert window.service.clips() == before
    assert not window.metadata_cancel_button.isEnabled()
    monkeypatch.setattr(probe_module, "probe_file", original)


def test_refresh_cannot_enter_a_replacement_project(window, monkeypatch):
    import app.probe as probe_module

    started, release = threading.Event(), threading.Event()
    original = probe_module.probe_file
    clip = window.service.clips()[0]

    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(probe_module, "probe_file", delayed)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(window.service.refresh_metadata, [clip["id"]])
        try:
            assert started.wait(5)
            with window.service.lock:
                window.service.project = new_project()
        finally:
            release.set()
        with pytest.raises(Cancelled):
            future.result(timeout=10)
    assert not window.service.clips()


def test_geometry_retention_releases_old_files_and_keeps_current_users(window):
    folder = window.service.data_dir / "gyro-geometry"
    folder.mkdir()
    files = []
    for index in range(10):
        path = folder / f"{index:024x}-{index:08x}.mov"
        path.write_bytes(b"owned")
        os.utime(path, (100 + index, 100 + index))
        files.append(path)
    foreign = folder / "user-export.mov"
    foreign.write_bytes(b"keep")
    window._gyro_geometry[window.focused_id()] = {"path": str(files[1]), "key": "current"}
    owner = ("compare", 900)
    window._geometry_users[owner] = str(files[0])
    window._retire_owned_previews()
    assert files[0].is_file() and files[1].is_file()
    assert len(list(folder.glob("*.mov"))) == 5  # Four owned plus the unrelated file.
    newest = folder / f"{11:024x}-{11:08x}.mov"
    newest.write_bytes(b"new owned render")
    window._release_geometry(owner)
    assert not files[0].exists()
    assert files[1].is_file() and foreign.read_bytes() == b"keep"


def test_dark_checkbox_text_and_wrapped_profile_are_readable(window):
    window._show_mode(1)
    window.set_theme("dark")
    window.inspector_tabs.setCurrentIndex(0)
    window.profile_detected.setText("S-Log3 / S-Gamut3.Cine is the detected camera input profile for this clip.")
    QApplication.processEvents()
    assert window.look_includes.palette().color(QPalette.ColorRole.WindowText).lightness() > 180
    assert window.gyro_enabled.palette().color(QPalette.ColorRole.WindowText).lightness() > 180
    label = window.profile_detected
    assert label.height() >= label.heightForWidth(label.width())
    window.set_theme("light")
    QApplication.processEvents()
    assert window.look_includes.palette().color(QPalette.ColorRole.WindowText).lightness() < 80


def test_visible_appearance_menu_switches_and_restores_the_choice(window, tmp_path):
    import json

    tools = next(action.menu() for action in window.menuBar().actions() if action.text() == "Tools")
    appearance = next(action.menu() for action in tools.actions() if action.text() == "Appearance")
    actions = {action.text(): action for action in appearance.actions()}
    for name in ("Light", "Dark"):
        actions[name].trigger()
        QApplication.processEvents()
        assert window.property("theme") == name.lower()
        assert actions[name].isChecked()
        assert json.loads((tmp_path / "state.json").read_text())["theme"] == name.lower()
    restored = MainWindow(Service(), state_path=tmp_path / "state.json")
    try:
        assert restored.property("theme") == "dark"
        assert restored.theme_actions["dark"].isChecked()
    finally:
        restored.force_close = True
        restored.close()
        assert not restored._runner.isRunning()


def test_paused_step_uses_the_decoded_adjacent_frame(window, tmp_path):
    from app.preview import make_comparison_proxy

    ffmpeg, _probe = window.service._tools()
    proxy = tmp_path / "comparison.mp4"
    make_comparison_proxy(ffmpeg=ffmpeg, source=Path(window.clips[0]["path"]), destination=proxy, colour_filter="")
    window._compare_path = str(proxy)
    window.player.setSource(QUrl.fromLocalFile(str(proxy)))
    assert _wait(lambda: window.player.duration() > 0)
    window.compare.seconds = 0.4
    window.step_frame(1)
    assert window.wait_until_idle(30000)
    assert window.playback_result["frame_time_seconds"] == pytest.approx(0.44, abs=0.0001)
    expected = tmp_path / "frame11.png"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(proxy), "-vf", "select=eq(n\\,11)", "-frames:v", "1", str(expected)], check=True)
    image = QImage(str(expected))
    half = image.copy(0, 0, image.width() // 2, image.height())
    actual = window.compare._left.toImage()
    fmt = QImage.Format.Format_RGBA8888
    assert bytes(actual.convertToFormat(fmt).bits()) == bytes(half.convertToFormat(fmt).bits())
    assert window.compare.same_time()
    assert not list(window.service.data_dir.glob(".frame-step-*.png"))


def test_trimmed_smoke_keeps_both_selected_exports(window, tmp_path, media_dir):
    from app.desktop.smoke import run_smoke

    report = tmp_path / "report.json"
    code = run_smoke(output=tmp_path / "exports", report=report, screenshots=tmp_path / "shots",
                     samples=[media_dir / "h264_8.mp4", media_dir / "hevc_10.mp4"],
                     config={"recipe": {"profile": "preserve"}, "export": {"preset": "prores_422", "audio": False, "acknowledge": True, "trim_in": 0.1, "trim_out": 0.5}, "expect": {"min_export_succeeded": 2}})
    import json

    proof = json.loads(report.read_text())
    assert code == 0, proof["steps"]
    assert proof["selected_for_export"] == 2
    queued = next(step for step in proof["steps"] if step["step"] == "queue")
    assert queued["export_succeeded"] == 2
