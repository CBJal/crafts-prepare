"""Production browse grid: 35 clips, themes, and comparison alignment."""

import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app.desktop.window import MainWindow
from app.service import Service


@pytest.fixture(scope="module")
def qapp():
    application = QApplication.instance() or QApplication([])
    yield application


def _clip(folder: Path, index: int) -> None:
    target = folder / f"C{index:04d}.MP4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=0x{index % 200:02x}4488:s=160x90:r=25:d=0.2",
            "-metadata", f"comment=clip-{index}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(target),
        ],
        check=True,
    )
    gamma = "s-log3-cine" if index % 2 == 0 else "hlg-slog3ootf"
    primaries = "s-gamut3-cine" if index % 2 == 0 else "rec2020"
    coding = "rec709" if index % 2 == 0 else "rec2020ncl"
    (folder / f"C{index:04d}M01.XML").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.00">
  <Duration value="5"/>
  <Device manufacturer="Sony" modelName="ILCE-6700" serialNo="SYNTH{index:04d}"/>
  <AcquisitionRecord>
    <Group name="CameraUnitMetadataSet">
      <Item name="CaptureGammaEquation" value="{gamma}"/>
      <Item name="CaptureColorPrimaries" value="{primaries}"/>
      <Item name="CodingEquations" value="{coding}"/>
    </Group>
  </AcquisitionRecord>
</NonRealTimeMeta>
""",
        encoding="utf-8",
    )


def test_thirty_five_clip_grid_themes_and_comparison(qapp, tmp_path, monkeypatch):
    if not __import__("shutil").which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    monkeypatch.setenv("CRAFTS_DISABLE_WORKER", "1")
    monkeypatch.setenv("CRAFTS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CRAFTS_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CRAFTS_LOG_PATH", str(tmp_path / "app.log"))
    folder = tmp_path / "roll"
    folder.mkdir()
    for index in range(1, 36):
        _clip(folder, index)
    (folder / "broken.mp4").write_bytes(b"not a video")
    window = MainWindow(Service(), state_path=tmp_path / "desktop-state.json")
    window.resize(1600, 900)
    window.show()
    qapp.processEvents()
    shots = tmp_path / "shots"
    shots.mkdir()
    try:
        window.import_paths([str(folder)])
        assert window.wait_until_idle(120000)
        assert window.grid.item_count() >= 35
        assert len(window.clips) == 35
        assert window.grid.item_count() == 36
        assert any(item.get("error") for item in window.grid._items.values())
        slog = next(clip for clip in window.clips if clip["name"] == "C0002.MP4")
        hlg = next(clip for clip in window.clips if clip["name"] == "C0001.MP4")
        from app.desktop.grade import describe_clip

        assert describe_clip(slog)["profile"]["pipeline"] == "slog3_sgamut3cine"
        assert describe_clip(hlg)["profile"]["pipeline"] == "hlg_bt2020"
        window.focus_clip(window.clips[0]["id"])
        window.grid._on_pressed(window.clips[3]["id"], 1)
        assert len(window.selected_ids()) == 2
        window.select_all_batch()
        assert len(window.selected_ids()) == 35
        import time

        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            qapp.processEvents()
            ready = sum(1 for clip_id in window.grid._pixmaps if not str(clip_id).startswith("error"))
            if ready >= 30:
                break
            time.sleep(0.05)
        window.set_theme("dark")
        qapp.processEvents()
        window.grab().save(str(shots / "browse-dark.png"))
        window.set_theme("light")
        window.resize(1280, 800)
        qapp.processEvents()
        window.grab().save(str(shots / "browse-light-1280.png"))
        window.resize(int(1280 * 1.5), int(800 * 1.5))
        font = QFont("Segoe UI", 15)
        qapp.setFont(font)
        qapp.processEvents()
        export = window.export_button.mapTo(window, window.export_button.rect().center())
        assert 0 <= export.x() <= window.width()
        assert window.export_button.isVisible()
        window.grab().save(str(shots / "browse-150dpi.png"))
        window._show_mode(1)
        window.compare_mode.setCurrentIndex(window.compare_mode.findData("split"))
        window.request_comparison()
        assert window.wait_until_idle(60000)
        assert window.compare.seconds >= 0
        assert not window.still_original._source.isNull()
        assert not window.still_adjusted._source.isNull()
        window.compare.set_mode("wipe")
        qapp.processEvents()
        window.grab().save(str(shots / "view-wipe.png"))
        other = tmp_path / "other"
        other.mkdir()
        _clip(other, 99)
        previous = window._thumbs.generation
        window.import_paths([str(other)])
        assert window._thumbs.generation != previous
        window._thumb_ready(previous, window.clips[0]["id"], str(folder / "C0001.MP4"), "")
        assert window.wait_until_idle(60000)
        assert window.grid.item_count() == 1
        assert window._shown_clips()[0]["name"] == "C0099.MP4"
        assert "C0001.MP4" not in {clip["name"] for clip in window._shown_clips()}
    finally:
        window.force_close = True
        window.close()
        if window._runner.isRunning():
            window._runner.shutdown(timeout=3)
        qapp.processEvents()
