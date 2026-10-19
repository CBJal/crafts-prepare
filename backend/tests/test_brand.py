"""Approved icon discovery and action-icon rendering."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop.brand import brand_directory, load_app_icon
from app.desktop.icons import action_icon, known_icons


@pytest.fixture(scope="module")
def qapp():
    application = QApplication.instance() or QApplication([])
    yield application


def test_brand_files_are_found_from_another_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = brand_directory()
    assert folder is not None
    assert (folder / "app-icon.png").is_file()
    assert (folder / "app-icon.ico").is_file()
    assert (folder / "app-icon.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_frozen_bundle_is_preferred_over_the_source_tree(tmp_path, monkeypatch):
    bundled = tmp_path / "assets" / "brand"
    bundled.mkdir(parents=True)
    (bundled / "app-icon.png").write_bytes(b"png-mark")
    (bundled / "app-icon.ico").write_bytes(b"\x00\x00\x01\x00frozen")
    monkeypatch.setattr("app.desktop.brand.is_frozen", lambda: True)
    monkeypatch.setattr("app.desktop.brand.bundle_dir", lambda: tmp_path)
    assert brand_directory() == bundled
    assert (brand_directory() / "app-icon.ico").read_bytes().endswith(b"frozen")


def test_action_icons_render_for_both_themes(qapp):
    assert len(known_icons()) >= 20
    for theme in ("light", "dark"):
        for name in known_icons():
            icon = action_icon(name, theme)
            normal = icon.pixmap(24, 24)
            disabled = icon.pixmap(24, 24, mode=icon.Mode.Disabled)
            assert not normal.isNull()
            assert normal.width() == 24
            assert not disabled.isNull()
    assert action_icon("missing", "dark").isNull()


def test_window_uses_the_approved_icon_and_repaints_with_the_theme(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CRAFTS_LOG_PATH", str(tmp_path / "app.log"))
    from app.desktop.window import MainWindow
    from app.service import Service

    window = MainWindow(Service(), state_path=tmp_path / "desktop-state.json")
    window.show()
    qapp.processEvents()
    try:
        assert not window.windowIcon().isNull()
        assert not window.brand_mark.pixmap().isNull()
        window.set_theme("dark")
        dark = window.play_button.icon().pixmap(16, 16)
        window.set_theme("light")
        light = window.play_button.icon().pixmap(16, 16)
        assert not dark.isNull() and not light.isNull()
        window.meta_toggle.setChecked(True)
        qapp.processEvents()
        assert window.meta_details.isVisible()
        assert "New" in window.project_chip.text()
        assert "Unsaved" not in window.project_chip.text()
    finally:
        window.force_close = True
        window.close()
        if window._runner.isRunning():
            window._runner.shutdown(timeout=3)
