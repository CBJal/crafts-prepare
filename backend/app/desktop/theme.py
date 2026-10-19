"""Charcoal and silver palettes. One cool silver accent, no second brand color."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

LIGHT = """
QMainWindow, QDialog { background: #e8ecef; color: #1a2028; }
QWidget { background: transparent; color: #1a2028; font-size: 13px; }
QDockWidget, QMenuBar, QMenu, QToolBar, QStatusBar { background: #f6f7f8; color: #1a2028; }
QMenuBar { padding: 2px 8px; }
QMenu { padding: 4px; }
QDockWidget { border: 0; }
QDockWidget::title { background: #f6f7f8; padding: 8px 10px; border-bottom: 1px solid #d7dde3; }
QToolBar { border: 0; border-bottom: 1px solid #d7dde3; spacing: 4px; padding: 4px 8px; }
QStatusBar { border-top: 1px solid #d7dde3; }
QListWidget, QTableWidget, QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {
  background: #ffffff; color: #1a2028; border: 1px solid #d0d7de; border-radius: 6px; padding: 4px 6px;
}
QTableWidget { gridline-color: #e4e9ee; }
QHeaderView::section { background: #f6f7f8; color: #5c6770; padding: 6px; border: 0; border-bottom: 1px solid #d7dde3; }
QPushButton {
  background: #ffffff; color: #1a2028; border: 1px solid #c9d1d8; border-radius: 6px; padding: 6px 10px;
}
QPushButton:hover { background: #f0f3f5; }
QPushButton:disabled { color: #8b97a3; background: #eef1f3; }
QPushButton#primary, QPushButton#exportButton { background: #2a3340; color: #f4f7f8; border: 0; }
QPushButton#primary:hover, QPushButton#exportButton:hover { background: #1a2028; }
QGroupBox { border: 1px solid #d7dde3; border-radius: 8px; margin-top: 12px; padding: 10px 8px 8px; background: #fbfcfd; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #3c4650; }
QLabel#muted, QLabel#playbackNote, QLabel#libraryEmpty, QLabel#queueEmpty { color: #5c6770; }
QLabel#metadataLabel { background: transparent; color: #1a2028; }
QLabel#brandTitle { font-size: 15px; font-weight: 600; }
QLabel#projectChip { color: #3c4650; background: #ffffff; border: 1px solid #d7dde3; border-radius: 999px; padding: 3px 10px; }
QLabel#timeLabel { font-family: "Cascadia Mono", "Consolas", "Liberation Mono", monospace; color: #1a2028; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: transparent; color: #5c6770; padding: 8px 12px; border: 0; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #1a2028; border-bottom: 2px solid #2a3340; }
QScrollArea, QScrollArea#transportScroll, QWidget#transportHost { border: 0; background: transparent; }
QSlider::groove:horizontal { height: 4px; background: #d0d7de; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -5px 0; background: #2a3340; border-radius: 6px; }
"""

DARK = """
QMainWindow, QDialog { background: #12161c; color: #e8eef4; }
QWidget { background: transparent; color: #e8eef4; font-size: 13px; }
QDockWidget, QMenuBar, QMenu, QToolBar, QStatusBar { background: #171c23; color: #e8eef4; }
QMenuBar { padding: 2px 8px; }
QMenu { padding: 4px; background: #1c232c; }
QDockWidget { border: 0; }
QDockWidget::title { background: #171c23; padding: 8px 10px; border-bottom: 1px solid #2c343e; }
QToolBar { border: 0; border-bottom: 1px solid #2c343e; spacing: 4px; padding: 4px 8px; }
QStatusBar { border-top: 1px solid #2c343e; }
QListWidget, QTableWidget, QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {
  background: #10151b; color: #e8eef4; border: 1px solid #313a46; border-radius: 6px; padding: 4px 6px;
}
QTableWidget { gridline-color: #2a323c; }
QHeaderView::section { background: #171c23; color: #9aa6b2; padding: 6px; border: 0; border-bottom: 1px solid #2c343e; }
QPushButton {
  background: #222a34; color: #e8eef4; border: 1px solid #313a46; border-radius: 6px; padding: 6px 10px;
}
QPushButton:hover { background: #2b3440; }
QPushButton:disabled { color: #6e7c8c; background: #1a2028; }
QPushButton#primary, QPushButton#exportButton { background: #d5dde6; color: #12161c; border: 0; }
QPushButton#primary:hover, QPushButton#exportButton:hover { background: #eef2f5; }
QGroupBox { border: 1px solid #2c343e; border-radius: 8px; margin-top: 12px; padding: 10px 8px 8px; background: #171c23; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #c5ced6; }
QLabel#muted, QLabel#playbackNote, QLabel#libraryEmpty, QLabel#queueEmpty { color: #9aa6b2; }
QLabel#metadataLabel { background: transparent; color: #e8eef4; }
QLabel#brandTitle { font-size: 15px; font-weight: 600; }
QLabel#projectChip { color: #d5dde6; background: #222a34; border: 1px solid #313a46; border-radius: 999px; padding: 3px 10px; }
QLabel#timeLabel { font-family: "Cascadia Mono", "Consolas", "Liberation Mono", monospace; color: #e8eef4; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: transparent; color: #9aa6b2; padding: 8px 12px; border: 0; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #e8eef4; border-bottom: 2px solid #d5dde6; }
QScrollArea, QScrollArea#transportScroll, QWidget#transportHost { border: 0; background: transparent; }
QSlider::groove:horizontal { height: 4px; background: #313a46; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -5px 0; background: #d5dde6; border-radius: 6px; }
"""


def resolved_theme(choice: str) -> str:
    if choice == "dark":
        return "dark"
    if choice == "light":
        return "light"
    hints = QGuiApplication.styleHints()
    scheme = hints.colorScheme()
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def apply_theme(widget, choice: str) -> str:
    resolved = resolved_theme(choice)
    widget.setStyleSheet(DARK if resolved == "dark" else LIGHT)
    widget.setProperty("themeChoice", choice)
    widget.setProperty("theme", resolved)
    return resolved
