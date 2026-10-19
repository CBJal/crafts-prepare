"""Readable surfaces, compact controls, icon contrast, and preference writes."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QToolBar, QToolButton

from app.desktop.prefs import PrefsWriteError, save_state
from app.desktop.window import MainWindow
from app.service import Service


@pytest.fixture(scope="module")
def qapp():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture()
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CRAFTS_LOG_PATH", str(tmp_path / "app.log"))
    desktop = MainWindow(Service(), state_path=tmp_path / "desktop-state.json")
    desktop.show()
    qapp.processEvents()
    yield desktop
    desktop.force_close = True
    desktop.close()
    if desktop._runner.isRunning():
        desktop._runner.shutdown(timeout=3)


def _luminance(pixmap) -> float:
    image = pixmap.toImage()
    total = 0.0
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() < 40:
                continue
            total += 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()
            count += 1
    return total / count if count else 0.0


def test_light_shell_is_not_black_and_stylesheet_names_the_surface(qapp, window):
    window.set_theme("light")
    qapp.processEvents()
    sheet = window.styleSheet()
    assert "QWidget { background: transparent" not in sheet
    assert "QWidget#viewerShell" in sheet
    assert "#e8ecef" in sheet
    shell = window.centralWidget()
    color = shell.grab().toImage().pixelColor(6, 6)
    assert color.lightness() > 180
    window.set_theme("dark")
    qapp.processEvents()
    color = shell.grab().toImage().pixelColor(6, 6)
    assert color.lightness() < 50
    assert "#3d4b5c" in window.styleSheet()
    assert "QTableWidget::item:selected" in window.styleSheet()


def test_dark_selection_is_not_a_white_row(qapp, window):
    window.set_theme("dark")
    window.library.setRowCount(1)
    window.library.setItem(0, 1, QTableWidgetItem("clip.mp4"))
    window.library.selectRow(0)
    qapp.processEvents()
    rect = window.library.visualItemRect(window.library.item(0, 1))
    assert rect.width() > 4
    color = window.library.grab().toImage().pixelColor(rect.center().x(), rect.center().y())
    assert color.lightness() < 160
    assert not (color.red() > 220 and color.green() > 220 and color.blue() > 220)


def test_primary_export_icon_contrasts_with_its_button(qapp, window):
    export = window.findChild(type(window.play_button), "exportButton")
    window.set_theme("dark")
    qapp.processEvents()
    assert _luminance(export.icon().pixmap(24, 24)) < 80
    assert _luminance(window.play_button.icon().pixmap(24, 24)) > 160
    window.set_theme("light")
    qapp.processEvents()
    assert _luminance(export.icon().pixmap(24, 24)) > 180
    assert _luminance(window.play_button.icon().pixmap(24, 24)) < 80


def test_compact_transport_stays_on_screen_with_names(qapp, window):
    window.resize(960, 640)
    window.show()
    qapp.processEvents()
    buttons = [
        window.play_button,
        window.pause_button,
        window.frame_back_button,
        window.frame_forward_button,
        window.mark_in_button,
        window.mark_out_button,
    ]
    names = []
    for button in buttons:
        assert button.text() == ""
        assert button.accessibleName()
        names.append(button.accessibleName())
        assert button.toolTip() == button.accessibleName()
        point = button.mapTo(window, button.rect().center())
        assert 0 <= point.x() <= window.width()
        assert button.width() >= 28
        assert button.isVisible()
    assert names == ["Play", "Pause", "Previous frame", "Next frame", "Set trim in", "Set trim out"]
    time_point = window.time_label.mapTo(window, QPoint(0, 0))
    assert time_point.x() + 20 < window.width()
    assert window.time_label.isVisible()
    assert window.settings_action.text() == "Tools"
    assert window.inspector_tabs.tabIcon(0).isNull() is False
    assert window.inspector_tabs.tabIcon(3).isNull() is False


def test_project_chip_is_honest_and_elides(qapp, window):
    assert "Unsaved" not in window.project_chip.text()
    window.service.project["clips"] = [{"id": "pending"}]
    window._refresh_project_chip()
    assert "Unsaved" in window.project_chip.text()
    window.service.project["clips"] = []
    window.service.project_path = Path("/tmp") / (("project-" * 12) + ".crafts.json")
    window._refresh_project_chip()
    assert "Unsaved" not in window.project_chip.text()
    assert len(window.project_chip.text()) < 48
    assert "project-" in window.project_chip.toolTip()
    window.service.project_path = None


def test_dark_toolbar_text_is_light_and_tools_menu_keeps_its_label(qapp, window):
    from app.desktop.icons import action_icon

    window.set_theme("dark")
    qapp.processEvents()
    toolbar = window.findChild(QToolBar, "mainToolBar")
    labeled = [button for button in toolbar.findChildren(QToolButton) if button.text()]
    assert labeled
    for button in labeled:
        ink = button.palette().color(QPalette.ColorRole.ButtonText)
        assert ink.lightness() > 180
        surface = button.grab().toImage().pixelColor(2, 2)
        assert surface.lightness() < 80
    window.set_theme("light")
    qapp.processEvents()
    for button in toolbar.findChildren(QToolButton):
        if not button.text():
            continue
        ink = button.palette().color(QPalette.ColorRole.ButtonText)
        assert ink.lightness() < 80
    tools = next(action for action in window.menuBar().actions() if action.text() == "Tools")
    assert tools.icon().isNull()
    assert window.settings_action.property("iconName") == "settings"
    assert window.settings_action.icon().isNull() is False
    gear = action_icon("settings", "dark").pixmap(24, 24).toImage()
    sun = action_icon("sun", "dark").pixmap(24, 24).toImage()
    assert gear != sun
    sheet = window.styleSheet()
    assert "QScrollBar::handle" in sheet
    assert "QToolBar QToolButton" in sheet
    assert "width: 16px" in sheet


def test_transient_preference_replace_keeps_then_updates(tmp_path, monkeypatch):
    path = tmp_path / "desktop-state.json"
    save_state({"theme": "dark", "recent": [], "geometry": "", "window_state": ""}, path)
    original = path.read_bytes()
    real = os.replace
    calls = {"n": 0}

    def flaky(source, dest):
        calls["n"] += 1
        if calls["n"] == 1:
            assert Path(dest).read_bytes() == original
            raise PermissionError(13, "Access is denied")
        return real(source, dest)

    monkeypatch.setattr("app.desktop.prefs.os.replace", flaky)
    save_state({"theme": "light", "recent": [], "geometry": "", "window_state": ""}, path)
    assert calls["n"] == 2
    assert "light" in path.read_text(encoding="utf-8")
    assert list(tmp_path.glob(".desktop-state.json.*.tmp")) == []


def test_persistent_preference_lock_keeps_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "desktop-state.json"
    save_state({"theme": "dark", "recent": ["kept"], "geometry": "", "window_state": ""}, path)
    original = path.read_bytes()

    def locked(source, dest):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("app.desktop.prefs.os.replace", locked)
    with pytest.raises(PrefsWriteError):
        save_state({"theme": "light", "recent": [], "geometry": "", "window_state": ""}, path)
    assert path.read_bytes() == original
    assert "dark" in path.read_text(encoding="utf-8")
    assert list(tmp_path.glob(".desktop-state.json.*.tmp")) == []


def test_close_finishes_when_preferences_cannot_be_replaced(qapp, window, monkeypatch):
    def locked(*_args, **_kwargs):
        raise PrefsWriteError("locked")

    monkeypatch.setattr("app.desktop.window.save_state", locked)
    window.force_close = True
    window.close()
    assert window._runner.isRunning() is False
    assert window.isVisible() is False
