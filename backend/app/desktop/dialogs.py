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
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "These are the external programs Crafts Prepare runs for export, proxies, stills, and gyro. "
                "They are not the playback libraries inside Qt."
            )
        )
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
            browse = QPushButton("Browse")
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
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{APP_NAME} {APP_VERSION}"))
        layout.addWidget(
            QLabel(
                "Independent desktop application for local video preparation.\n"
                "It is not Sony Catalyst Prepare. It does not use Sony artwork, Sony code, "
                "or a Sony activation check, and it does not claim the same color science "
                "or the same feature set.\n\n"
                "FFmpeg, ffprobe, and Gyroflow stay separate programs you install. "
                "Qt may play a viewing proxy with its own multimedia libraries. "
                "Those libraries are not the export tools."
            )
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
