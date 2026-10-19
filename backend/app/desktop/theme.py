"""Neutral light, dark, and system palettes for native widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

LIGHT = """
QMainWindow, QDialog, QWidget { background: #f4f6f8; color: #1c2430; }
QDockWidget, QMenuBar, QMenu, QToolBar, QStatusBar { background: #ffffff; color: #1c2430; }
QDockWidget::title { background: #e7edf4; padding: 6px; }
QListWidget, QTableWidget, QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
  background: #ffffff; color: #1c2430; border: 1px solid #d5dde6; padding: 4px;
}
QHeaderView::section { background: #e7edf4; color: #1c2430; padding: 4px; border: 0; }
QPushButton { background: #e7edf4; color: #1c2430; border: 1px solid #c5d0dc; padding: 6px 10px; }
QPushButton:hover { background: #d9e4f2; }
QPushButton#primary { background: #2f6fed; color: #ffffff; border: 0; }
QGroupBox { border: 1px solid #d5dde6; margin-top: 8px; padding: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QLabel#muted { color: #5c6b7a; }
"""

DARK = """
QMainWindow, QDialog, QWidget { background: #171d25; color: #e7edf4; }
QDockWidget, QMenuBar, QMenu, QToolBar, QStatusBar { background: #1e2631; color: #e7edf4; }
QDockWidget::title { background: #121820; padding: 6px; }
QListWidget, QTableWidget, QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
  background: #121820; color: #e7edf4; border: 1px solid #2c3848; padding: 4px;
}
QHeaderView::section { background: #121820; color: #e7edf4; padding: 4px; border: 0; }
QPushButton { background: #243140; color: #e7edf4; border: 1px solid #2c3848; padding: 6px 10px; }
QPushButton:hover { background: #2c3c50; }
QPushButton#primary { background: #4c8dff; color: #061018; border: 0; }
QGroupBox { border: 1px solid #2c3848; margin-top: 8px; padding: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QLabel#muted { color: #9aa8b8; }
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
