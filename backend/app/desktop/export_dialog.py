"""Export dialog. The queue lives here instead of a permanent lower pane."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from ..presets import PRESETS


class ExportDialog(QDialog):
    queued = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setObjectName("exportDialog")
        self.resize(560, 640)
        layout = QVBoxLayout(self)
        self.count_label = QLabel("0 clips selected")
        self.count_label.setObjectName("exportCount")
        layout.addWidget(self.count_label)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        form = QFormLayout()
        self.destination = QLineEdit()
        self.destination.setObjectName("destination")
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.destination)
        row.addWidget(browse)
        form.addRow("Destination", row)
        self.suffix = QLineEdit("_prep")
        self.suffix.setObjectName("suffix")
        form.addRow("Suffix", self.suffix)
        self.preset = QComboBox()
        self.preset.setObjectName("preset")
        for preset in PRESETS.values():
            self.preset.addItem(preset["label"], preset["id"])
        form.addRow("Format", self.preset)
        self.crf = QSpinBox()
        self.crf.setRange(0, 51)
        self.crf.setValue(18)
        form.addRow("Quality CRF", self.crf)
        self.fit = QComboBox()
        self.fit.addItem("Source size", "none")
        self.fit.addItem("Resize", "resize")
        form.addRow("Resolution", self.fit)
        self.out_width = QSpinBox()
        self.out_height = QSpinBox()
        for box, value in ((self.out_width, 1920), (self.out_height, 1080)):
            box.setRange(2, 7680)
            box.setSingleStep(2)
            box.setValue(value)
        size_row = QHBoxLayout()
        size_row.addWidget(self.out_width)
        size_row.addWidget(self.out_height)
        form.addRow("Size", size_row)
        self.audio = QCheckBox("Keep audio")
        self.audio.setChecked(True)
        form.addRow("Audio", self.audio)
        self.ack = QCheckBox("Allow a change of bit depth")
        form.addRow("Bit depth", self.ack)
        self.use_trim = QCheckBox("Use saved trim (advanced)")
        self.use_trim.setChecked(False)
        form.addRow("Range", self.use_trim)
        layout.addLayout(form)
        self.jobs = QTextEdit()
        self.jobs.setReadOnly(True)
        self.jobs.setObjectName("exportJobs")
        self.jobs.setPlaceholderText("Queued preparation jobs appear here.")
        layout.addWidget(self.jobs, 1)
        buttons = QHBoxLayout()
        self.queue_button = QPushButton("Export selected")
        self.queue_button.setObjectName("exportConfirm")
        self.queue_button.clicked.connect(self.queued.emit)
        self.cancel_button = QPushButton("Cancel batch")
        self.retry_button = QPushButton("Retry failed")
        buttons.addWidget(self.queue_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.retry_button)
        layout.addLayout(buttons)

    def _browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        chosen = QFileDialog.getExistingDirectory(self, "Export destination", self.destination.text())
        if chosen:
            self.destination.setText(chosen)

    def set_summary(self, count: int, text: str) -> None:
        self.count_label.setText(f"{count} clip{'s' if count != 1 else ''} selected")
        self.summary.setText(text)
