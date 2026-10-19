"""Native dialogs. None of these embed a browser."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..version import APP_NAME, APP_VERSION


class SettingsDialog(QDialog):
    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Tools")
        self.setObjectName("settingsDialog")
        self.setMinimumWidth(420)
        self.setMaximumWidth(720)
        self.resize(560, 280)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "These paths are the external programs used for export, proxies, stills, and gyro. "
            "They are separate from Qt playback."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.fields: dict[str, QLineEdit] = {}
        for key, label in (
            ("ffmpeg", "ffmpeg"),
            ("ffprobe", "ffprobe"),
            ("gyroflow", "Gyroflow"),
            ("telemetry_parser", "Telemetry parser"),
        ):
            edit = QLineEdit(service.settings.get(key) or "")
            edit.setObjectName(f"tool-{key}")
            edit.setMinimumWidth(180)
            browse = QPushButton("Browse")
            browse.setToolTip("Browse for this program")
            theme = "dark"
            if parent is not None and parent.property("theme") in {"light", "dark"}:
                theme = str(parent.property("theme"))
            from .icons import action_icon

            browse.setIcon(action_icon("browse", theme))
            browse.clicked.connect(lambda _checked=False, box=edit: self._browse(box))
            row = QHBoxLayout()
            row.addWidget(edit)
            row.addWidget(browse)
            form.addRow(label, row)
            self.fields[key] = edit
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        layout.addWidget(buttons)

    def _browse(self, edit: QLineEdit) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(self, "Choose a program", edit.text())
        if chosen:
            edit.setText(chosen)

    def _save(self) -> None:
        self.service.update_settings({key: edit.text().strip() for key, edit in self.fields.items()})
        self.accept()


class TextDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(420)
        self.setMaximumWidth(640)
        self.resize(520, 240)
        layout = QVBoxLayout(self)
        from .brand import load_app_icon

        mark = QLabel()
        pixmap = load_app_icon().pixmap(48, 48)
        if not pixmap.isNull():
            mark.setPixmap(pixmap)
        layout.addWidget(mark)
        title = QLabel(f"{APP_NAME} {APP_VERSION}")
        title.setWordWrap(True)
        layout.addWidget(title)
        body = QLabel(
            "Independent desktop application for local video preparation. "
            "It is not Sony Catalyst Prepare, and it does not claim the same color science or feature set. "
            "FFmpeg, ffprobe, and Gyroflow stay separate programs. "
            "Qt playback libraries are not those export tools."
        )
        body.setWordWrap(True)
        layout.addWidget(body)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
