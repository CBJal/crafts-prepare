"""Charcoal and silver palettes. Surfaces name their own backgrounds.

A transparent widget follows the platform palette. On Windows that palette
was black behind the light theme, so every shell below has an explicit color.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

LIGHT = """
QMainWindow { background: #e8ecef; color: #1a2028; }
QDialog { background: #f6f7f8; color: #1a2028; }
QWidget#viewerShell, QWidget#transportHost { background: #e8ecef; color: #1a2028; }
QDockWidget, QWidget#libraryBody, QWidget#queueBody, QWidget#inspectorPage, QScrollArea, QMenuBar, QToolBar, QStatusBar {
  background: #f6f7f8; color: #1a2028;
}
QMenuBar { padding: 2px 8px; }
QMenu { background: #ffffff; color: #1a2028; padding: 4px; border: 1px solid #d7dde3; }
QMenu::item:selected { background: #2a3340; color: #f7f8f9; }
QMenu::item:disabled { color: #8b97a3; }
QDockWidget { border: 0; }
QDockWidget::title { background: #f6f7f8; color: #1a2028; padding: 8px 10px; border-bottom: 1px solid #d7dde3; }
QToolBar { border: 0; border-bottom: 1px solid #d7dde3; spacing: 4px; padding: 4px 8px; }
QStatusBar { border-top: 1px solid #d7dde3; }
QListWidget, QTableWidget, QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {
  background: #ffffff; color: #1a2028; border: 1px solid #d0d7de; border-radius: 6px; padding: 4px 6px;
  selection-background-color: #2a3340; selection-color: #f7f8f9;
}
QTableWidget { gridline-color: #e4e9ee; alternate-background-color: #f3f6f8; }
QTableWidget::item:hover { background: #e6ebf0; color: #1a2028; }
QTableWidget::item:selected, QTableWidget::item:selected:hover { background: #1d5fd0; color: #ffffff; }
QFrame#clipCard { background: #ffffff; border: 1px solid #d7dde3; border-radius: 8px; }
QFrame#clipCard[selected="true"] { background: #1d5fd0; border: 1px solid #1d5fd0; }
QFrame#clipCard[selected="true"] QLabel { color: #ffffff; }
QFrame#clipCard QLabel#cardImage, QFrame#clipCard[selected="true"] QLabel#cardImage { background: #10141a; color: #d5dde6; }
QFrame#modeSwitch { background: #ffffff; border: 1px solid #d7dde3; border-radius: 8px; }
QHeaderView::section { background: #f6f7f8; color: #5c6770; padding: 6px; border: 0; border-bottom: 1px solid #d7dde3; }
QPushButton {
  background: #ffffff; color: #1a2028; border: 1px solid #c9d1d8; border-radius: 6px; padding: 6px 10px;
}
QPushButton:hover { background: #f0f3f5; }
QPushButton:focus { border: 1px solid #2a3340; }
QPushButton:disabled { color: #8b97a3; background: #eef1f3; }
QPushButton#primary, QPushButton#exportButton { background: #2a3340; color: #f4f7f8; border: 0; }
QPushButton#primary:hover, QPushButton#exportButton:hover { background: #1a2028; }
QPushButton#transportButton { background: #ffffff; color: #1a2028; padding: 4px; border-radius: 6px; }
QGroupBox { border: 1px solid #d7dde3; border-radius: 8px; margin-top: 12px; padding: 10px 8px 8px; background: #fbfcfd; color: #1a2028; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #3c4650; }
QLabel { background: transparent; color: #1a2028; }
QLabel#muted, QLabel#playbackNote, QLabel#libraryEmpty, QLabel#queueEmpty { color: #5c6770; }
QLabel#metadataLabel { color: #1a2028; }
QLabel#brandTitle { font-size: 15px; font-weight: 600; color: #1a2028; }
QLabel#projectChip { color: #3c4650; background: #ffffff; border: 1px solid #d7dde3; border-radius: 999px; padding: 3px 10px; }
QLabel#timeLabel { font-family: "Cascadia Mono", "Consolas", "Liberation Mono", monospace; color: #1a2028; }
QTabWidget::pane { border: 0; background: #f6f7f8; }
QTabBar::tab { background: #f6f7f8; color: #5c6770; padding: 8px 12px; border: 0; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #1a2028; border-bottom: 2px solid #2a3340; }
QScrollArea, QTreeWidget, QTreeView, QListWidget, QWidget#gridHost, QWidget#placesPanel {
  border: 0; background: #f6f7f8; color: #1a2028;
}
QSlider::groove:horizontal { height: 4px; background: #d0d7de; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -5px 0; background: #2a3340; border-radius: 6px; }
QStackedWidget#viewerStack, QLabel#stillLabel, QVideoWidget#videoWidget, QLabel#videoWidget {
  background: #10141a; color: #d5dde6;
}
QToolBar QToolButton {
  background: #f6f7f8; color: #1a2028; border: 0; border-radius: 6px; padding: 4px 8px;
}
QToolBar QToolButton:hover { background: #e6ebf0; color: #1a2028; }
QToolBar QToolButton:pressed, QToolBar QToolButton:checked { background: #d7dde3; color: #1a2028; }
QToolBar QToolButton:disabled { color: #8b97a3; background: #f6f7f8; }
QToolBar QToolButton:focus { border: 1px solid #2a3340; }
QTabBar QToolButton { background: #f6f7f8; color: #1a2028; border: 0; border-radius: 4px; }
QTabBar QToolButton:hover { background: #e6ebf0; }
QTabBar QToolButton:disabled { color: #8b97a3; }
QScrollBar:vertical { background: #f6f7f8; width: 8px; margin: 0; }
QScrollBar:horizontal { background: #f6f7f8; height: 8px; margin: 0; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #c5ced6; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: #8b97a3; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; background: #f6f7f8; }
QScrollBar::add-page, QScrollBar::sub-page { background: #f6f7f8; }
QCheckBox::indicator, QTableWidget::indicator {
  width: 16px; height: 16px; border: 1px solid #5c6770; background: #ffffff; border-radius: 3px;
}
QCheckBox::indicator:checked, QTableWidget::indicator:checked { background: #2a3340; border: 1px solid #2a3340; }
QComboBox QAbstractItemView {
  background: #ffffff; color: #1a2028; selection-background-color: #2a3340; selection-color: #f7f8f9; border: 1px solid #d0d7de;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { color: #8b97a3; background: #eef1f3; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid #2a3340; }
"""

DARK = """
QMainWindow { background: #12161c; color: #e8eef4; }
QDialog { background: #171c23; color: #e8eef4; }
QWidget#viewerShell, QWidget#transportHost { background: #12161c; color: #e8eef4; }
QDockWidget, QWidget#libraryBody, QWidget#queueBody, QWidget#inspectorPage, QScrollArea, QMenuBar, QToolBar, QStatusBar {
  background: #171c23; color: #e8eef4;
}
QMenuBar { padding: 2px 8px; }
QMenu { background: #1c232c; color: #e8eef4; padding: 4px; border: 1px solid #313a46; }
QMenu::item:selected { background: #3d4b5c; color: #f4f7fa; }
QMenu::item:disabled { color: #6e7c8c; }
QDockWidget { border: 0; }
QDockWidget::title { background: #171c23; color: #e8eef4; padding: 8px 10px; border-bottom: 1px solid #2c343e; }
QToolBar { border: 0; border-bottom: 1px solid #2c343e; spacing: 4px; padding: 4px 8px; }
QStatusBar { border-top: 1px solid #2c343e; }
QListWidget, QTableWidget, QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {
  background: #10151b; color: #e8eef4; border: 1px solid #313a46; border-radius: 6px; padding: 4px 6px;
  selection-background-color: #3d4b5c; selection-color: #f4f7fa;
}
QTableWidget { gridline-color: #2a323c; alternate-background-color: #151b22; }
QTableWidget::item:hover { background: #24303c; color: #e8eef4; }
QTableWidget::item:selected, QTableWidget::item:selected:hover { background: #2f6fed; color: #ffffff; }
QFrame#clipCard { background: #1c232c; border: 1px solid #313a46; border-radius: 8px; }
QFrame#clipCard[selected="true"] { background: #2f6fed; border: 1px solid #2f6fed; }
QFrame#clipCard[selected="true"] QLabel { color: #ffffff; }
QFrame#clipCard QLabel#cardImage, QFrame#clipCard[selected="true"] QLabel#cardImage { background: #10141a; color: #d5dde6; }
QFrame#modeSwitch { background: #171c23; border: 1px solid #313a46; border-radius: 8px; }
QHeaderView::section { background: #171c23; color: #9aa6b2; padding: 6px; border: 0; border-bottom: 1px solid #2c343e; }
QPushButton {
  background: #222a34; color: #e8eef4; border: 1px solid #313a46; border-radius: 6px; padding: 6px 10px;
}
QPushButton:hover { background: #2b3440; }
QPushButton:focus { border: 1px solid #d5dde6; }
QPushButton:disabled { color: #6e7c8c; background: #1a2028; }
QPushButton#primary, QPushButton#exportButton { background: #d5dde6; color: #12161c; border: 0; }
QPushButton#primary:hover, QPushButton#exportButton:hover { background: #eef2f5; }
QPushButton#transportButton { background: #222a34; color: #e8eef4; padding: 4px; border-radius: 6px; }
QGroupBox { border: 1px solid #2c343e; border-radius: 8px; margin-top: 12px; padding: 10px 8px 8px; background: #171c23; color: #e8eef4; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #c5ced6; }
QLabel { background: transparent; color: #e8eef4; }
QLabel#muted, QLabel#playbackNote, QLabel#libraryEmpty, QLabel#queueEmpty { color: #9aa6b2; }
QLabel#metadataLabel { color: #e8eef4; }
QLabel#brandTitle { font-size: 15px; font-weight: 600; color: #e8eef4; }
QLabel#projectChip { color: #d5dde6; background: #222a34; border: 1px solid #313a46; border-radius: 999px; padding: 3px 10px; }
QLabel#timeLabel { font-family: "Cascadia Mono", "Consolas", "Liberation Mono", monospace; color: #e8eef4; }
QTabWidget::pane { border: 0; background: #171c23; }
QTabBar::tab { background: #171c23; color: #9aa6b2; padding: 8px 12px; border: 0; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #e8eef4; border-bottom: 2px solid #d5dde6; }
QScrollArea, QTreeWidget, QTreeView, QListWidget, QWidget#gridHost, QWidget#placesPanel {
  border: 0; background: #171c23; color: #e8eef4;
}
QSlider::groove:horizontal { height: 4px; background: #313a46; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -5px 0; background: #d5dde6; border-radius: 6px; }
QStackedWidget#viewerStack, QLabel#stillLabel, QVideoWidget#videoWidget, QLabel#videoWidget {
  background: #10141a; color: #d5dde6;
}
QToolBar QToolButton {
  background: #171c23; color: #e8eef4; border: 0; border-radius: 6px; padding: 4px 8px;
}
QToolBar QToolButton:hover { background: #2b3440; color: #e8eef4; }
QToolBar QToolButton:pressed, QToolBar QToolButton:checked { background: #24303c; color: #ffffff; }
QToolBar QToolButton:disabled { color: #6e7c8c; background: #171c23; }
QToolBar QToolButton:focus { border: 1px solid #d5dde6; }
QTabBar QToolButton { background: #171c23; color: #e8eef4; border: 0; border-radius: 4px; }
QTabBar QToolButton:hover { background: #2b3440; }
QTabBar QToolButton:disabled { color: #6e7c8c; }
QScrollBar:vertical { background: #171c23; width: 8px; margin: 0; }
QScrollBar:horizontal { background: #171c23; height: 8px; margin: 0; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #3a4452; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: #5c6770; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; background: #171c23; }
QScrollBar::add-page, QScrollBar::sub-page { background: #171c23; }
QCheckBox::indicator, QTableWidget::indicator {
  width: 16px; height: 16px; border: 1px solid #9aa6b2; background: #10151b; border-radius: 3px;
}
QCheckBox::indicator:checked, QTableWidget::indicator:checked { background: #d5dde6; border: 1px solid #d5dde6; }
QComboBox QAbstractItemView {
  background: #1c232c; color: #e8eef4; selection-background-color: #3d4b5c; selection-color: #f4f7fa; border: 1px solid #313a46;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { color: #6e7c8c; background: #151b22; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid #d5dde6; }
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
