"""Native main window. It calls Service directly and does not open a browser."""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import QByteArray, QEventLoop, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..errors import CraftsError
from ..filters import FilterSpec
from ..presets import PRESETS, conversion_note
from ..preview import PROXY_LIMIT_SECONDS
from ..service import new_project
from ..version import APP_NAME
from .dialogs import AboutDialog, SettingsDialog, TextDialog
from .prefs import load_state, remember_project, save_state
from .theme import apply_theme
from .workers import TaskThread

_ACTIVE = {"queued", "running"}


class MainWindow(QMainWindow):
    idle = Signal()
    peer_raised = Signal()

    def __init__(self, service, state_path: Path | None = None) -> None:
        super().__init__()
        self.service = service
        self.state_path = state_path
        self.state = load_state(state_path)
        self.clips: list[dict] = []
        self.capabilities: dict | None = None
        self._token = 0
        self._inflight = 0
        self._view_gen = 0
        self._callbacks: dict[int, object] = {}
        self._tasks: list[TaskThread] = []
        self._filling = False
        self.still_path = ""
        self.playback_result: dict | None = None
        self.last_error = ""
        self.force_close = False
        self.last_batch_id = ""
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)
        self.setMinimumSize(960, 640)
        self.setAcceptDrops(True)
        self._build_player()
        self._build_library()
        self._build_viewer()
        self._build_inspector()
        self._build_queue()
        self._build_actions()
        self.set_theme(self.state["theme"])
        self._restore_window()
        self.reload_all()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(700)
        self._refresh_timer.timeout.connect(self.refresh_queue)
        self._refresh_timer.start()
        self._start(self.service.capabilities, self._apply_capabilities)

    def _build_player(self) -> None:
        self.player = None
        self.audio = None
        self.video = None
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget

            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.video = QVideoWidget()
            self.video.setObjectName("videoWidget")
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.video)
            self.player.positionChanged.connect(self._on_position)
            self.player.durationChanged.connect(self._on_duration)
        except Exception as exc:  # noqa: BLE001 — stills still work without a playback device
            self.player_error = str(exc)
        else:
            self.player_error = ""

    def _build_library(self) -> None:
        dock = QDockWidget("Library", self)
        dock.setObjectName("libraryDock")
        body = QWidget()
        layout = QVBoxLayout(body)
        controls = QHBoxLayout()
        select_all = QPushButton("Select all")
        select_all.setObjectName("selectAllButton")
        select_all.clicked.connect(self.select_all_batch)
        clear = QPushButton("Clear")
        clear.setObjectName("clearChecksButton")
        clear.clicked.connect(self.clear_batch)
        self.batch_count = QLabel("0 of 0 selected")
        self.batch_count.setObjectName("batchCount")
        controls.addWidget(select_all)
        controls.addWidget(clear)
        controls.addWidget(self.batch_count)
        layout.addLayout(controls)
        self.library = QTableWidget(0, 4)
        self.library.setObjectName("libraryTable")
        self.library.setHorizontalHeaderLabels(["Batch", "Clip", "Flag", "Duration"])
        self.library.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.library.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.library.verticalHeader().setVisible(False)
        self.library.currentCellChanged.connect(self._focus_changed)
        self.library.itemChanged.connect(self._check_changed)
        layout.addWidget(self.library)
        dock.setWidget(body)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def _build_viewer(self) -> None:
        center = QWidget()
        layout = QVBoxLayout(center)
        self.playback_note = QLabel(
            f"Playback is a viewing aid. A proxy is H.264, about 640px wide, and at most {int(PROXY_LIMIT_SECONDS)} seconds. "
            "Adjustments are not baked into the player. Stills use the export filter graph without vidstab or deshake."
        )
        self.playback_note.setObjectName("playbackNote")
        self.playback_note.setWordWrap(True)
        layout.addWidget(self.playback_note)
        split = QSplitter(Qt.Orientation.Vertical)
        if self.video is not None:
            split.addWidget(self.video)
        else:
            missing = QLabel(self.player_error or "Playback is unavailable on this machine.")
            missing.setObjectName("videoWidget")
            split.addWidget(missing)
        self.still_label = QLabel("Original and adjusted stills appear here.")
        self.still_label.setObjectName("stillLabel")
        self.still_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.still_label.setMinimumHeight(160)
        split.addWidget(self.still_label)
        layout.addWidget(split, 1)
        transport = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.setObjectName("playButton")
        self.play_button.clicked.connect(self.play)
        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self.pause)
        back = QPushButton("Frame −")
        back.clicked.connect(lambda: self.step_frame(-1))
        forward = QPushButton("Frame +")
        forward.clicked.connect(lambda: self.step_frame(1))
        mark_in = QPushButton("Set in")
        mark_in.clicked.connect(lambda: self.set_trim_from_player("in"))
        mark_out = QPushButton("Set out")
        mark_out.clicked.connect(lambda: self.set_trim_from_player("out"))
        original = QPushButton("Original still")
        original.clicked.connect(lambda: self.request_still(False))
        adjusted = QPushButton("Adjusted still")
        adjusted.setObjectName("adjustedStillButton")
        adjusted.clicked.connect(lambda: self.request_still(True))
        for button in (self.play_button, self.pause_button, back, forward, mark_in, mark_out, original, adjusted):
            transport.addWidget(button)
        layout.addLayout(transport)
        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.setObjectName("scrubSlider")
        self.scrub.sliderMoved.connect(self._seek)
        layout.addWidget(self.scrub)
        self.setCentralWidget(center)

    def _build_inspector(self) -> None:
        dock = QDockWidget("Inspector", self)
        dock.setObjectName("inspectorDock")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        self.meta = QLabel("No clip selected.")
        self.meta.setObjectName("metadataLabel")
        self.meta.setWordWrap(True)
        self.meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.meta)

        details = QGroupBox("Clip")
        form = QFormLayout(details)
        self.flag = QComboBox()
        self.flag.addItem("None", "")
        self.flag.addItem("OK", "OK")
        self.flag.addItem("KEEP", "KEEP")
        self.flag.addItem("NG", "NG")
        self.flag.currentIndexChanged.connect(self._save_flag)
        self.notes = QPlainTextEdit()
        self.notes.setObjectName("notesEdit")
        self.notes.setFixedHeight(72)
        self.notes.textChanged.connect(self._notes_edited)
        save_notes = QPushButton("Save notes")
        save_notes.clicked.connect(self.save_notes)
        self.trim_in = QDoubleSpinBox()
        self.trim_out = QDoubleSpinBox()
        for box in (self.trim_in, self.trim_out):
            box.setRange(0, 86400)
            box.setDecimals(3)
            box.setSingleStep(0.04)
        self.trim_in.editingFinished.connect(self.save_trim)
        self.trim_out.editingFinished.connect(self.save_trim)
        self.markers = QListWidget()
        self.markers.setFixedHeight(72)
        add_marker = QPushButton("Add marker at playhead")
        add_marker.clicked.connect(self.add_marker)
        remove_marker = QPushButton("Remove marker")
        remove_marker.clicked.connect(self.remove_marker)
        form.addRow("Rating", self.flag)
        form.addRow("Notes", self.notes)
        form.addRow("", save_notes)
        form.addRow("Trim in", self.trim_in)
        form.addRow("Trim out", self.trim_out)
        form.addRow("Markers", self.markers)
        form.addRow("", add_marker)
        form.addRow("", remove_marker)
        layout.addWidget(details)

        grade = QGroupBox("Adjustments")
        grade_form = QFormLayout(grade)
        self.exposure = QDoubleSpinBox()
        self.exposure.setRange(-1, 1)
        self.exposure.setSingleStep(0.05)
        self.saturation = QDoubleSpinBox()
        self.saturation.setRange(0, 3)
        self.saturation.setValue(1)
        self.saturation.setSingleStep(0.05)
        self.rotate = QComboBox()
        for label, value in (("0°", "0"), ("90°", "90"), ("180°", "180"), ("270°", "270")):
            self.rotate.addItem(label, value)
        self.fit = QComboBox()
        for label, value in (("None", "none"), ("Resize", "resize"), ("Crop", "crop"), ("Letterbox", "letterbox")):
            self.fit.addItem(label, value)
        self.fit.currentIndexChanged.connect(self._fit_changed)
        self.out_width = QSpinBox()
        self.out_height = QSpinBox()
        for box in (self.out_width, self.out_height):
            box.setRange(2, 7680)
            box.setSingleStep(2)
        self.out_width.setValue(1920)
        self.out_height.setValue(1080)
        self.lut = QLineEdit()
        self.lut.setObjectName("lutPath")
        lut_browse = QPushButton("LUT…")
        lut_browse.clicked.connect(self.browse_lut)
        lut_row = QHBoxLayout()
        lut_row.addWidget(self.lut)
        lut_row.addWidget(lut_browse)
        self.stabilization = QComboBox()
        for label, value in (("Off", "off"), ("vidstab", "vidstab"), ("deshake", "deshake")):
            self.stabilization.addItem(label, value)
        self.vidstab_auto = QCheckBox("Use the built-in vidstab starting values")
        self.smoothing = QSpinBox()
        self.smoothing.setRange(0, 1000)
        self.smoothing.setValue(10)
        self.shakiness = QSpinBox()
        self.shakiness.setRange(1, 10)
        self.shakiness.setValue(5)
        self.accuracy = QSpinBox()
        self.accuracy.setRange(1, 15)
        self.accuracy.setValue(15)
        self.crop = QComboBox()
        self.crop.addItem("black", "black")
        self.crop.addItem("keep", "keep")
        self.zoom = QSpinBox()
        self.zoom.setRange(-100, 100)
        self.optzoom = QComboBox()
        self.optzoom.addItem("0 off", 0)
        self.optzoom.addItem("1 static", 1)
        self.optzoom.addItem("2 adaptive", 2)
        self.deshake_rx = QSpinBox()
        self.deshake_ry = QSpinBox()
        for box in (self.deshake_rx, self.deshake_ry):
            box.setRange(0, 64)
            box.setValue(16)
        grade_form.addRow("Exposure", self.exposure)
        grade_form.addRow("Saturation", self.saturation)
        grade_form.addRow("Rotate", self.rotate)
        grade_form.addRow("Fit", self.fit)
        grade_form.addRow("Width", self.out_width)
        grade_form.addRow("Height", self.out_height)
        grade_form.addRow("LUT", lut_row)
        grade_form.addRow("Stabilization", self.stabilization)
        grade_form.addRow("", self.vidstab_auto)
        grade_form.addRow("Smoothing", self.smoothing)
        grade_form.addRow("Shakiness", self.shakiness)
        grade_form.addRow("Accuracy", self.accuracy)
        grade_form.addRow("Crop", self.crop)
        grade_form.addRow("Zoom", self.zoom)
        grade_form.addRow("Optzoom", self.optzoom)
        grade_form.addRow("Deshake rx", self.deshake_rx)
        grade_form.addRow("Deshake ry", self.deshake_ry)
        note = QLabel("vidstab and deshake run only during export. They are not gyro stabilization.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        grade_form.addRow(note)
        layout.addWidget(grade)

        export = QGroupBox("Export")
        export_form = QFormLayout(export)
        self.preset = QComboBox()
        self.preset.setObjectName("presetCombo")
        for preset in PRESETS.values():
            self.preset.addItem(preset["label"], preset["id"])
        self.preset.currentIndexChanged.connect(self._update_conversion)
        self.audio = QCheckBox("Include audio")
        self.audio.setChecked(True)
        self.ack = QCheckBox("Acknowledge bit-depth conversion")
        self.ack.setObjectName("bitDepthAck")
        self.conversion = QLabel("")
        self.conversion.setWordWrap(True)
        self.destination = QLineEdit()
        self.destination.setObjectName("destinationEdit")
        dest_button = QPushButton("Folder…")
        dest_button.clicked.connect(self.browse_destination)
        dest_row = QHBoxLayout()
        dest_row.addWidget(self.destination)
        dest_row.addWidget(dest_button)
        self.suffix = QLineEdit()
        self.suffix.setPlaceholderText("optional suffix")
        export_one = QPushButton("Export focused clip")
        export_one.setObjectName("exportButton")
        export_one.setProperty("primary", True)
        export_one.clicked.connect(self.queue_focused_export)
        export_batch = QPushButton("Export checked clips")
        export_batch.setObjectName("exportBatchButton")
        export_batch.clicked.connect(lambda: self.queue_checked_batch("export"))
        export_form.addRow("Preset", self.preset)
        export_form.addRow("", self.audio)
        export_form.addRow("", self.ack)
        export_form.addRow(self.conversion)
        export_form.addRow("Destination", dest_row)
        export_form.addRow("Suffix", self.suffix)
        export_form.addRow(export_one)
        export_form.addRow(export_batch)
        layout.addWidget(export)

        gyro = QGroupBox("Gyro batch")
        gyro_form = QFormLayout(gyro)
        self.gyro_format = QComboBox()
        self.gyro_format.addItem("H.264 MP4", "h264_mp4")
        self.gyro_format.addItem("ProRes 422", "prores_422")
        self.gyro_source_size = QCheckBox("Keep source size")
        self.gyro_source_size.setChecked(True)
        self.gyro_source_size.toggled.connect(self._gyro_size_toggled)
        self.gyro_width = QSpinBox()
        self.gyro_height = QSpinBox()
        for box in (self.gyro_width, self.gyro_height):
            box.setRange(2, 7680)
            box.setSingleStep(2)
            box.setEnabled(False)
        self.gyro_width.setValue(1920)
        self.gyro_height.setValue(1080)
        self.gyro_audio = QCheckBox("Audio")
        self.gyro_audio.setChecked(True)
        self.gyro_preset = QLineEdit()
        self.gyro_preset.setObjectName("gyroPresetPath")
        gyro_preset_button = QPushButton("Preset…")
        gyro_preset_button.clicked.connect(self.browse_gyro_preset)
        preset_row = QHBoxLayout()
        preset_row.addWidget(self.gyro_preset)
        preset_row.addWidget(gyro_preset_button)
        gyro_button = QPushButton("Gyro checked clips")
        gyro_button.setObjectName("gyroBatchButton")
        gyro_button.clicked.connect(lambda: self.queue_checked_batch("gyro"))
        gyro_form.addRow("Format", self.gyro_format)
        gyro_form.addRow("", self.gyro_source_size)
        gyro_form.addRow("Width", self.gyro_width)
        gyro_form.addRow("Height", self.gyro_height)
        gyro_form.addRow("", self.gyro_audio)
        gyro_form.addRow("Shared preset", preset_row)
        gyro_form.addRow(gyro_button)
        layout.addWidget(gyro)

        manual = QGroupBox("Advanced manual gyro")
        manual_form = QFormLayout(manual)
        self.manual_project = QLineEdit()
        self.manual_gcsv = QLineEdit()
        self.manual_preset = QLineEdit()
        self.manual_output = QLineEdit()
        manual_form.addRow("Project", self._file_row(self.manual_project, "Gyroflow project", "*.gyroflow"))
        manual_form.addRow("GCSV", self._file_row(self.manual_gcsv, "Gyro log", "*.gcsv"))
        manual_form.addRow("Preset", self._file_row(self.manual_preset, "Gyroflow preset", "*.gyroflow"))
        manual_form.addRow("Output", self._save_row(self.manual_output, "Gyro output"))
        parse_gcsv = QPushButton("Parse GCSV")
        parse_gcsv.clicked.connect(self.parse_gcsv)
        telemetry = QPushButton("Inspect telemetry")
        telemetry.clicked.connect(self.inspect_telemetry)
        manual_queue = QPushButton("Queue manual gyro")
        manual_queue.setObjectName("manualGyroButton")
        manual_queue.clicked.connect(self.queue_manual_gyro)
        manual_form.addRow(parse_gcsv)
        manual_form.addRow(telemetry)
        manual_form.addRow(manual_queue)
        layout.addWidget(manual)
        layout.addStretch(1)
        scroll.setWidget(body)
        dock.setWidget(scroll)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self._fit_changed()

    def _build_queue(self) -> None:
        dock = QDockWidget("Queue", self)
        dock.setObjectName("queueDock")
        body = QWidget()
        layout = QVBoxLayout(body)
        self.queue = QTableWidget(0, 5)
        self.queue.setObjectName("queueTable")
        self.queue.setHorizontalHeaderLabels(["File", "Kind", "Status", "Progress", "Error"])
        self.queue.verticalHeader().setVisible(False)
        self.queue.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.queue)
        self.batch_errors = QPlainTextEdit()
        self.batch_errors.setObjectName("batchErrors")
        self.batch_errors.setReadOnly(True)
        self.batch_errors.setFixedHeight(72)
        layout.addWidget(self.batch_errors)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel job")
        cancel.clicked.connect(self.cancel_selected_job)
        retry = QPushButton("Retry job")
        retry.clicked.connect(self.retry_selected_job)
        cancel_batch = QPushButton("Cancel batch")
        cancel_batch.setObjectName("cancelBatchButton")
        cancel_batch.clicked.connect(self.cancel_current_batch)
        retry_failed = QPushButton("Retry failed")
        retry_failed.setObjectName("retryFailedButton")
        retry_failed.clicked.connect(self.retry_failed_batch)
        for button in (cancel, retry, cancel_batch, retry_failed):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        dock.setWidget(body)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        toolbar = QToolBar("Main")
        toolbar.setObjectName("mainToolBar")
        self.addToolBar(toolbar)
        actions = [
            ("New", self.new_project, "Ctrl+N"),
            ("Open…", self.open_project_interactive, "Ctrl+O"),
            ("Save", self.save_project_interactive, "Ctrl+S"),
            ("Save as…", self.save_project_as, "Ctrl+Shift+S"),
            ("Import files…", self.import_files, None),
            ("Import folder…", self.import_folder, None),
        ]
        for label, slot, shortcut in actions:
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(lambda _checked=False, fn=slot: fn())
            file_menu.addAction(action)
            if label in {"Open…", "Save", "Import files…"}:
                toolbar.addAction(action)
        self.recent_menu = file_menu.addMenu("Recent")
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        self._rebuild_recent()

        edit_menu = self.menuBar().addMenu("Edit")
        edit_menu.addAction("Select all for batch", self.select_all_batch)
        edit_menu.addAction("Clear batch checks", self.clear_batch)

        queue_menu = self.menuBar().addMenu("Queue")
        queue_menu.addAction("Export focused clip", self.queue_focused_export)
        queue_menu.addAction("Export checked clips", lambda: self.queue_checked_batch("export"))
        queue_menu.addAction("Gyro checked clips", lambda: self.queue_checked_batch("gyro"))

        tools_menu = self.menuBar().addMenu("Tools")
        tools_menu.addAction("External programs…", self.open_settings)
        tools_menu.addAction("Refresh tool report", lambda: self._start(self.service.capabilities, self._apply_capabilities))

        view_menu = self.menuBar().addMenu("View")
        for label, choice in (("Light", "light"), ("Dark", "dark"), ("System", "system")):
            action = QAction(label, self)
            action.setObjectName(f"theme-{choice}")
            action.triggered.connect(lambda _checked=False, value=choice: self.set_theme(value))
            view_menu.addAction(action)

        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction("About", self.show_about)
        help_menu.addAction("Log folder", self.show_log)
        help_menu.addAction("Licenses", self.show_licenses)

        for label, slot in (("Play", self.play), ("Pause", self.pause)):
            action = QAction(label, self)
            action.triggered.connect(slot)
            toolbar.addAction(action)

    def _file_row(self, edit: QLineEdit, title: str, pattern: str):
        button = QPushButton("Browse")
        button.clicked.connect(lambda: self._browse_into(edit, title, pattern))
        row = QHBoxLayout()
        row.addWidget(edit)
        row.addWidget(button)
        return row

    def _save_row(self, edit: QLineEdit, title: str):
        button = QPushButton("Browse")

        def choose() -> None:
            chosen, _filter = QFileDialog.getSaveFileName(self, title, edit.text())
            if chosen:
                edit.setText(chosen)

        button.clicked.connect(choose)
        row = QHBoxLayout()
        row.addWidget(edit)
        row.addWidget(button)
        return row

    def _browse_into(self, edit: QLineEdit, title: str, pattern: str) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(self, title, edit.text(), pattern)
        if chosen:
            edit.setText(chosen)

    def set_theme(self, choice: str) -> str:
        resolved = apply_theme(self, choice)
        self.state["theme"] = choice if choice in {"light", "dark", "system"} else "system"
        save_state(self.state, self.state_path)
        return resolved

    def _restore_window(self) -> None:
        geometry = self.state.get("geometry") or ""
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        window_state = self.state.get("window_state") or ""
        if window_state:
            self.restoreState(QByteArray.fromBase64(window_state.encode("ascii")))

    def _persist_window(self) -> None:
        self.state["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.state["window_state"] = bytes(self.saveState().toBase64()).decode("ascii")
        save_state(self.state, self.state_path)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.import_paths(paths)

    def import_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, "Import media")
        if paths:
            self.import_paths(paths)

    def import_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Import folder")
        if path:
            self.import_paths([path])

    def import_paths(self, paths: list[str]) -> None:
        items = [path for path in paths if path]
        if not items:
            return

        def work():
            reports = [self.service.import_media(path) for path in items]
            return reports

        self._start(work, self._imported)

    def _imported(self, reports) -> None:
        added = sum(len(item.get("added") or []) for item in reports)
        duplicates = sum(len(item.get("duplicates") or []) for item in reports)
        skipped = sum(len(item.get("skipped") or []) for item in reports)
        self.statusBar().showMessage(f"Imported {added}. Duplicates {duplicates}. Skipped {skipped}.", 8000)
        self.reload_all()

    def reload_all(self) -> None:
        self.clips = self.service.clips()
        self._fill_library()
        self.refresh_queue()
        self._fill_focus()
        title = self.service.project.get("name") or "Untitled"
        path = self.service.project_path
        self.setWindowTitle(f"{APP_NAME} — {title}" + ("" if path is None else f" — {path.name}"))

    def _fill_library(self) -> None:
        previous = self.focused_id()
        checks = set(self.checked_ids())
        self._filling = True
        self.library.blockSignals(True)
        self.library.setRowCount(len(self.clips))
        for row, clip in enumerate(self.clips):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(Qt.CheckState.Checked if clip["id"] in checks else Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, clip["id"])
            self.library.setItem(row, 0, check)
            self.library.setItem(row, 1, QTableWidgetItem(clip["name"]))
            self.library.setItem(row, 2, QTableWidgetItem(clip.get("flag") or ""))
            duration = clip["summary"].get("duration")
            self.library.setItem(row, 3, QTableWidgetItem("" if duration is None else f"{float(duration):.2f}s"))
        self.library.blockSignals(False)
        self._filling = False
        self._update_count()
        if previous:
            self.focus_clip(previous)
        elif self.clips:
            self.library.setCurrentCell(0, 1)

    def _check_changed(self, _item) -> None:
        if not self._filling:
            self._update_count()

    def _update_count(self) -> None:
        self.batch_count.setText(f"{len(self.checked_ids())} of {len(self.clips)} selected")

    def checked_ids(self) -> list[str]:
        chosen = []
        for row in range(self.library.rowCount()):
            item = self.library.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                chosen.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return chosen

    def focused_id(self) -> str | None:
        row = self.library.currentRow()
        if row < 0:
            return None
        item = self.library.item(row, 0)
        if item is None:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))

    def focused_clip(self) -> dict | None:
        clip_id = self.focused_id()
        for clip in self.clips:
            if clip["id"] == clip_id:
                return clip
        return None

    def focus_clip(self, clip_id: str) -> None:
        for row in range(self.library.rowCount()):
            item = self.library.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == clip_id:
                self.library.setCurrentCell(row, 1)
                return

    def select_all_batch(self) -> None:
        self._set_all_checks(Qt.CheckState.Checked)

    def clear_batch(self) -> None:
        self._set_all_checks(Qt.CheckState.Unchecked)

    def _set_all_checks(self, state: Qt.CheckState) -> None:
        self.library.blockSignals(True)
        for row in range(self.library.rowCount()):
            item = self.library.item(row, 0)
            if item:
                item.setCheckState(state)
        self.library.blockSignals(False)
        self._update_count()

    def _focus_changed(self, row, _column, _previous, _previous_column) -> None:
        if self._filling or row < 0:
            return
        self._view_gen += 1
        self.still_path = ""
        self.playback_result = None
        self._fill_focus()
        self.request_playback()

    def _fill_focus(self) -> None:
        clip = self.focused_clip()
        self._filling = True
        if clip is None:
            self.meta.setText("No clip selected.")
            self._filling = False
            return
        summary = clip["summary"]
        video = (clip["probe"].get("video") or [{}])[0] or {}
        audio = ", ".join(str(item) for item in summary.get("audio_codecs") or []) or "none"
        data = summary.get("data_streams")
        self.meta.setText(
            "\n".join(
                [
                    clip["name"],
                    clip["path"],
                    f"SHA-256 {clip['sha256']}",
                    f"Codec {summary.get('codec')}  {summary.get('width')}×{summary.get('height')}  {summary.get('pix_fmt')}  {summary.get('bit_depth')}-bit",
                    f"Rate {summary.get('frame_rate')}  duration {summary.get('duration')}  timecode {summary.get('timecode')}",
                    f"Color {video.get('color_space')} / {video.get('color_transfer')} / {video.get('color_primaries')} / {video.get('color_range')}",
                    f"Audio {audio}",
                    f"Data streams {data}. {clip['probe'].get('motion_metadata', {}).get('note', '')}",
                ]
            )
        )
        index = self.flag.findData(clip.get("flag") or "")
        self.flag.setCurrentIndex(max(0, index))
        self.notes.setPlainText(clip.get("notes") or "")
        self.trim_in.setValue(float(clip.get("trim_in") or 0))
        self.trim_out.setValue(float(clip.get("trim_out") or 0))
        self.markers.clear()
        for marker in clip.get("markers") or []:
            self.markers.addItem(f"{float(marker.get('seconds') or 0):.3f}  {marker.get('label') or ''}")
            self.markers.item(self.markers.count() - 1).setData(Qt.ItemDataRole.UserRole, marker.get("id"))
        self._filling = False
        self._update_conversion()

    def _save_flag(self) -> None:
        if self._filling:
            return
        clip = self.focused_clip()
        if clip is None:
            return
        try:
            self.service.update_clip(clip["id"], {"flag": self.flag.currentData()})
        except CraftsError as exc:
            self.statusBar().showMessage(exc.message, 8000)
        self.reload_all()

    def _notes_edited(self) -> None:
        return

    def save_notes(self) -> None:
        clip = self.focused_clip()
        if clip is None:
            return
        try:
            self.service.update_clip(clip["id"], {"notes": self.notes.toPlainText()})
        except CraftsError as exc:
            self.statusBar().showMessage(exc.message, 8000)
            return
        self.clips = self.service.clips()
        self.statusBar().showMessage("Notes stored in the project. The source file was not changed.", 5000)

    def save_trim(self) -> None:
        if self._filling:
            return
        clip = self.focused_clip()
        if clip is None:
            return
        try:
            self.service.update_clip(clip["id"], {"trim_in": self.trim_in.value(), "trim_out": self.trim_out.value()})
        except CraftsError as exc:
            self.statusBar().showMessage(exc.message, 8000)
            self._fill_focus()
            return
        self.clips = self.service.clips()
        self.statusBar().showMessage("Trim stored in the project. The source file was not cut.", 5000)

    def add_marker(self) -> None:
        clip = self.focused_clip()
        if clip is None:
            return
        seconds = (self.player.position() / 1000.0) if self.player else float(clip.get("trim_in") or 0)
        markers = list(clip.get("markers") or [])
        markers.append({"id": uuid.uuid4().hex, "seconds": seconds, "label": "Marker"})
        try:
            self.service.update_clip(clip["id"], {"markers": markers})
        except CraftsError as exc:
            self.statusBar().showMessage(exc.message, 8000)
            return
        self.clips = self.service.clips()
        self._fill_focus()

    def remove_marker(self) -> None:
        clip = self.focused_clip()
        row = self.markers.currentRow()
        if clip is None or row < 0:
            return
        marker_id = self.markers.item(row).data(Qt.ItemDataRole.UserRole)
        markers = [item for item in clip.get("markers") or [] if item.get("id") != marker_id]
        self.service.update_clip(clip["id"], {"markers": markers})
        self.clips = self.service.clips()
        self._fill_focus()

    def _fit_changed(self) -> None:
        enabled = self.fit.currentData() != "none"
        self.out_width.setEnabled(enabled)
        self.out_height.setEnabled(enabled)

    def _gyro_size_toggled(self, checked: bool) -> None:
        self.gyro_width.setEnabled(not checked)
        self.gyro_height.setEnabled(not checked)

    def browse_lut(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(self, "LUT", self.lut.text(), "*.cube")
        if chosen:
            self.lut.setText(chosen)

    def browse_destination(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Destination", self.destination.text())
        if chosen:
            self.destination.setText(chosen)

    def browse_gyro_preset(self) -> None:
        self._browse_into(self.gyro_preset, "Gyroflow preset", "*.gyroflow")

    def adjustment_body(self) -> dict:
        fit = self.fit.currentData()
        return {
            "preset_id": self.preset.currentData(),
            "audio": self.audio.isChecked(),
            "acknowledge_bit_depth_conversion": self.ack.isChecked(),
            "exposure": self.exposure.value(),
            "saturation": self.saturation.value(),
            "rotate": self.rotate.currentData(),
            "fit": fit,
            "width": self.out_width.value() if fit != "none" else None,
            "height": self.out_height.value() if fit != "none" else None,
            "stabilization": self.stabilization.currentData(),
            "use_vidstab_auto": self.vidstab_auto.isChecked(),
            "stab_smoothing": self.smoothing.value(),
            "stab_shakiness": self.shakiness.value(),
            "stab_accuracy": self.accuracy.value(),
            "stab_crop": self.crop.currentData(),
            "stab_zoom": self.zoom.value(),
            "stab_optzoom": int(self.optzoom.currentData()),
            "deshake_rx": self.deshake_rx.value(),
            "deshake_ry": self.deshake_ry.value(),
            "lut_path": self.lut.text().strip() or None,
        }

    def filter_spec(self, adjusted: bool) -> FilterSpec:
        if not adjusted:
            return FilterSpec(include_temporal=False, stabilization="off")
        body = self.adjustment_body()
        return FilterSpec(
            exposure=float(body["exposure"]),
            saturation=float(body["saturation"]),
            lut_path=body["lut_path"],
            rotate=str(body["rotate"]),
            fit=str(body["fit"]),
            width=body["width"],
            height=body["height"],
            stabilization="off",
            include_temporal=False,
        )

    def _update_conversion(self) -> None:
        clip = self.focused_clip()
        preset = PRESETS.get(self.preset.currentData() or "")
        if clip is None or preset is None:
            self.conversion.setText("")
            return
        summary = clip["summary"]
        note = conversion_note(summary.get("pix_fmt"), summary.get("bit_depth"), preset)
        self.conversion.setText(note["message"])

    def play(self) -> None:
        if self.player:
            self.player.play()

    def pause(self) -> None:
        if self.player:
            self.player.pause()

    def step_frame(self, direction: int) -> None:
        if not self.player:
            return
        self.player.pause()
        delta = int(self._frame_seconds() * 1000 * direction)
        self.player.setPosition(max(0, self.player.position() + delta))

    def _frame_seconds(self) -> float:
        clip = self.focused_clip()
        rate = "" if clip is None else str(clip["summary"].get("frame_rate") or "")
        try:
            if "/" in rate:
                num, den = rate.split("/", 1)
                value = float(num) / float(den)
            else:
                value = float(rate)
            if value > 0:
                return 1.0 / value
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        return 0.04

    def _seek(self, position: int) -> None:
        if self.player:
            self.player.setPosition(position)

    def _on_position(self, position: int) -> None:
        if not self.scrub.isSliderDown():
            self.scrub.setValue(position)

    def _on_duration(self, duration: int) -> None:
        self.scrub.setRange(0, max(0, duration))

    def set_trim_from_player(self, edge: str) -> None:
        clip = self.focused_clip()
        if clip is None:
            return
        seconds = (self.player.position() / 1000.0) if self.player else 0.0
        if edge == "in":
            self.trim_in.setValue(seconds)
        else:
            self.trim_out.setValue(seconds)
        self.save_trim()

    def request_playback(self) -> None:
        clip = self.focused_clip()
        if clip is None:
            return
        generation = self._view_gen
        clip_id = clip["id"]

        def work():
            return self.service.proxy(clip_id)

        self._start(work, lambda result: self._show_playback(generation, result))

    def _show_playback(self, generation: int, result: dict) -> None:
        if generation != self._view_gen:
            return
        self.playback_result = result
        note = result.get("note") or ""
        if result.get("limited"):
            note += f" Limit {int(PROXY_LIMIT_SECONDS)} seconds."
        self.playback_note.setText(note)
        path = result.get("proxy_path")
        if result.get("kind") == "source":
            clip = self.focused_clip()
            path = clip["path"] if clip else None
        if path and self.player:
            self.player.setSource(QUrl.fromLocalFile(str(path)))

    def request_still(self, adjusted: bool) -> None:
        clip = self.focused_clip()
        if clip is None:
            return
        generation = self._view_gen
        seconds = (self.player.position() / 1000.0) if self.player else 0.0
        spec = self.filter_spec(adjusted)
        clip_id = clip["id"]

        def work():
            return str(self.service.thumbnail(clip_id, seconds, spec))

        self._start(work, lambda path: self._show_still(generation, path))

    def _show_still(self, generation: int, path: str) -> None:
        if generation != self._view_gen:
            return
        self.still_path = path
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.still_label.setText(path)
            return
        self.still_label.setPixmap(
            pixmap.scaled(self.still_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )

    def queue_focused_export(self) -> None:
        clip = self.focused_clip()
        if clip is None:
            self.statusBar().showMessage("Select a clip first.", 5000)
            return
        if not self._require_destination():
            return
        body = self.adjustment_body()
        body.update(
            {
                "clip_id": clip["id"],
                "in_seconds": float(clip.get("trim_in") or 0),
                "out_seconds": float(clip.get("trim_out") or 0),
                "destination_dir": self.destination.text().strip(),
                "suffix": self.suffix.text().strip(),
            }
        )
        try:
            job = self.service.enqueue_export(body)
        except CraftsError as exc:
            self.last_error = exc.message
            self.statusBar().showMessage(exc.message, 10000)
            if exc.code == "bit_depth":
                self.ack.setChecked(False)
            return
        self.statusBar().showMessage(f"Queued {job.get('request', {}).get('filename', 'export')}.", 5000)
        self.refresh_queue()

    def queue_checked_batch(self, operation: str) -> None:
        clip_ids = self.checked_ids()
        if not clip_ids:
            self.statusBar().showMessage("Check at least one clip. Focus and batch selection are separate.", 6000)
            return
        if not self._require_destination():
            return
        if operation == "export":
            payload = {
                "operation": "export",
                "clip_ids": clip_ids,
                "destination_dir": self.destination.text().strip(),
                "suffix": self.suffix.text().strip(),
                "export": self.adjustment_body(),
            }
        else:
            width = None if self.gyro_source_size.isChecked() else self.gyro_width.value()
            height = None if self.gyro_source_size.isChecked() else self.gyro_height.value()
            payload = {
                "operation": "gyro",
                "clip_ids": clip_ids,
                "destination_dir": self.destination.text().strip(),
                "suffix": self.suffix.text().strip(),
                "gyro": {
                    "format": self.gyro_format.currentData(),
                    "audio": self.gyro_audio.isChecked(),
                    "width": width,
                    "height": height,
                    "preset_path": self.gyro_preset.text().strip() or None,
                },
            }
        try:
            result = self.service.enqueue_batch(payload)
        except CraftsError as exc:
            self.last_error = exc.message
            lines = [exc.message]
            for item in exc.details or []:
                if isinstance(item, dict):
                    lines.append(f"{item.get('clip_id') or 'clip'}: {item.get('message')}")
            self.batch_errors.setPlainText("\n".join(lines))
            self.statusBar().showMessage(exc.message, 10000)
            return
        self.last_batch_id = result["batch_id"]
        self.batch_errors.setPlainText("")
        self.statusBar().showMessage(f"Queued {result['count']} {operation} job(s).", 6000)
        self.refresh_queue()

    def queue_manual_gyro(self) -> None:
        clip = self.focused_clip()
        output = self.manual_output.text().strip()
        if not output:
            self.statusBar().showMessage("Choose a manual gyro output path.", 6000)
            return
        body = {
            "clip_id": None if clip is None else clip["id"],
            "media_path": None if clip is None else clip["path"],
            "project_path": self.manual_project.text().strip() or None,
            "gyro_path": self.manual_gcsv.text().strip() or None,
            "preset_path": self.manual_preset.text().strip() or None,
            "output_path": output,
        }
        try:
            job = self.service.enqueue_gyro(body)
        except CraftsError as exc:
            self.last_error = exc.message
            self.statusBar().showMessage(exc.message, 10000)
            return
        self.statusBar().showMessage(f"Queued manual gyro {job['id']}.", 5000)
        self.refresh_queue()

    def parse_gcsv(self) -> None:
        path = self.manual_gcsv.text().strip()
        if not path:
            self.statusBar().showMessage("Choose a GCSV file first.", 5000)
            return
        self._start(lambda: self.service.parse_gcsv(path), lambda result: self._show_text("GCSV", result))

    def inspect_telemetry(self) -> None:
        clip = self.focused_clip()
        if clip is None:
            return
        clip_id = clip["id"]
        self._start(lambda: self.service.inspect_telemetry(clip_id), lambda result: self._show_text("Telemetry", result))

    def _show_text(self, title: str, result) -> None:
        import json

        TextDialog(title, json.dumps(result, indent=2), self).exec()

    def _require_destination(self) -> bool:
        if self.destination.text().strip():
            return True
        self.browse_destination()
        if self.destination.text().strip():
            return True
        self.statusBar().showMessage("Choose a destination folder. Nothing was queued.", 6000)
        return False

    def refresh_queue(self) -> None:
        jobs = self.service.jobs()
        self.queue.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            request = job.get("request") or {}
            name = request.get("filename") or Path(str(request.get("output_path") or "")).name or job["id"][:8]
            values = [
                str(name),
                str(job.get("kind") or ""),
                str(job.get("status") or ""),
                f"{float(job.get('progress') or 0):.0%}",
                str(job.get("error") or ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, job["id"])
                self.queue.setItem(row, column, item)
        if self.last_batch_id:
            try:
                status = self.service.batch_status(self.last_batch_id)
            except CraftsError:
                return
            self.statusBar().showMessage(
                f"Batch {status['succeeded']} succeeded, {status['failed']} failed, "
                f"{status['running']} running, {status['queued']} queued, {status['cancelled']} cancelled.",
                3000,
            )

    def _selected_job_id(self) -> str | None:
        row = self.queue.currentRow()
        if row < 0:
            return None
        item = self.queue.item(row, 0)
        if item is None:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))

    def cancel_selected_job(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        try:
            self.service.cancel(job_id)
        except CraftsError as exc:
            self.statusBar().showMessage(exc.message, 6000)
        self.refresh_queue()

    def retry_selected_job(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        try:
            self.service.retry(job_id)
        except CraftsError as exc:
            self.statusBar().showMessage(exc.message, 6000)
        self.refresh_queue()

    def cancel_current_batch(self) -> None:
        if not self.last_batch_id:
            self.statusBar().showMessage("No batch has been queued in this window.", 5000)
            return
        try:
            self.service.cancel_batch(self.last_batch_id)
        except CraftsError as exc:
            self.statusBar().showMessage(exc.message, 6000)
        self.refresh_queue()

    def retry_failed_batch(self) -> None:
        if not self.last_batch_id:
            self.statusBar().showMessage("No batch has been queued in this window.", 5000)
            return
        try:
            result = self.service.retry_batch_failures(self.last_batch_id)
        except CraftsError as exc:
            self.statusBar().showMessage(exc.message, 6000)
            return
        self.statusBar().showMessage(f"Requeued {len(result['retried'])} failed job(s).", 5000)
        self.refresh_queue()

    def is_unsaved(self) -> bool:
        if self.service.project_path is not None:
            return False
        project = self.service.project
        if project.get("clips") or project.get("jobs"):
            return True
        return (project.get("name") or "Untitled") != "Untitled"

    def _prompt_unsaved(self) -> str:
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setText("This project has not been saved.")
        box.setInformativeText("Queued exports are not resumed automatically when a project is opened later.")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return "save"
        if clicked is discard:
            return "discard"
        if clicked is cancel:
            return "cancel"
        return "cancel"

    def new_project(self, discard: bool = False) -> bool:
        if self.is_unsaved() and not discard:
            choice = self._prompt_unsaved()
            if choice == "cancel":
                return False
            if choice == "save" and not self.save_project_interactive():
                return False
        if self._busy_queue():
            return False
        with self.service.lock:
            self.service.project = new_project()
            self.service.project_path = None
        self.reload_all()
        return True

    def _busy_queue(self) -> bool:
        if any(job["status"] in _ACTIVE for job in self.service.jobs()):
            self.statusBar().showMessage("Finish or cancel the current queue first. Nothing was switched.", 8000)
            return True
        return False

    def open_project_interactive(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Open project", "", "Crafts Prepare project (*.crafts.json)")
        if path:
            self.open_project_path(path)

    def open_project_path(self, path: str) -> bool:
        if self.is_unsaved():
            choice = self._prompt_unsaved()
            if choice == "cancel":
                return False
            if choice == "save" and not self.save_project_interactive():
                return False
        try:
            self.service.open_project(path)
        except CraftsError as exc:
            self.last_error = exc.message
            self.statusBar().showMessage(exc.message, 10000)
            return False
        remember_project(self.state, str(self.service.project_path))
        save_state(self.state, self.state_path)
        self._rebuild_recent()
        self.reload_all()
        self.statusBar().showMessage("Opened. Saved queued exports stay paused until you retry them.", 8000)
        return True

    def save_project_interactive(self) -> bool:
        if self.service.project_path is None:
            return self.save_project_as()
        try:
            self.service.save_project(None)
        except CraftsError as exc:
            self.statusBar().showMessage(exc.message, 8000)
            return False
        self.reload_all()
        return True

    def save_project_as(self) -> bool:
        path, _filter = QFileDialog.getSaveFileName(self, "Save project", "", "Crafts Prepare project (*.crafts.json)")
        if not path:
            return False
        if not path.endswith(".crafts.json"):
            path += ".crafts.json"
        return self.save_project_to(path)

    def save_project_to(self, path: str) -> bool:
        try:
            self.service.save_project(path)
        except CraftsError as exc:
            self.last_error = exc.message
            self.statusBar().showMessage(exc.message, 8000)
            return False
        remember_project(self.state, str(self.service.project_path))
        save_state(self.state, self.state_path)
        self._rebuild_recent()
        self.reload_all()
        return True

    def _rebuild_recent(self) -> None:
        self.recent_menu.clear()
        for path in self.state.get("recent") or []:
            action = QAction(path, self)
            action.triggered.connect(lambda _checked=False, value=path: self.open_project_path(value))
            self.recent_menu.addAction(action)
        if not self.state.get("recent"):
            empty = QAction("None", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.service, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self._start(self.service.capabilities, self._apply_capabilities)

    def _apply_capabilities(self, report: dict) -> None:
        self.capabilities = report
        missing = [
            name
            for name in ("ffmpeg", "ffprobe", "gyroflow")
            if not (report.get(name) or {}).get("available")
        ]
        if missing:
            self.statusBar().showMessage(
                "Not found yet: " + ", ".join(missing) + ". Set the paths under Tools. Crafts Prepare does not download them.",
                12000,
            )
        else:
            self.statusBar().showMessage("ffmpeg, ffprobe, and Gyroflow were found.", 6000)

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def show_log(self) -> None:
        from ..runtime import log_path

        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def show_licenses(self) -> None:
        from ..runtime import bundle_dir, is_frozen, repo_root

        if is_frozen():
            bundled = bundle_dir()
            folder = (bundled / "licenses") if bundled else None
        else:
            folder = repo_root() / "packaging" / "third-party-licenses"
        text = "License files"
        if folder and folder.is_dir():
            text = str(folder)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        TextDialog("Licenses", text, self).exec()

    def handle_peer(self, payload: bytes) -> None:
        if b"raise" in payload:
            self.showNormal()
            self.raise_()
            self.activateWindow()
            self.peer_raised.emit()

    def shutdown_owned(self) -> None:
        for job in self.service.jobs():
            if job["status"] in _ACTIVE:
                try:
                    self.service.cancel(job["id"])
                except CraftsError:
                    pass
        self.service.shutdown(timeout=8)
        for task in self._tasks:
            task.wait(2000)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.is_unsaved() and not self.force_close:
            choice = self._prompt_unsaved()
            if choice == "cancel":
                event.ignore()
                return
            if choice == "save" and not self.save_project_interactive():
                event.ignore()
                return
        self._refresh_timer.stop()
        if self.player is not None:
            self.player.stop()
            self.player.setVideoOutput(None)
            self.player.setSource(QUrl())
        self.shutdown_owned()
        self._persist_window()
        event.accept()

    def _start(self, fn, on_ok) -> None:
        token = self._token
        self._token += 1
        self._inflight += 1
        task = TaskThread(token, fn)
        self._callbacks[token] = on_ok
        task.succeeded.connect(self._task_ok)
        task.failed.connect(self._task_fail)
        task.finished.connect(lambda tok=token: self._task_finished(tok))
        self._tasks.append(task)
        task.start()

    def _task_ok(self, token: int, value) -> None:
        callback = self._callbacks.pop(token, None)
        if callback:
            callback(value)

    def _task_fail(self, token: int, message: str) -> None:
        self._callbacks.pop(token, None)
        self.last_error = message
        self.statusBar().showMessage(message, 10000)

    def _task_finished(self, token: int) -> None:
        self._callbacks.pop(token, None)
        self._inflight = max(0, self._inflight - 1)
        if self._inflight == 0:
            self.idle.emit()

    def wait_until_idle(self, timeout_ms: int = 30000) -> bool:
        from PySide6.QtWidgets import QApplication

        if self._inflight == 0:
            QApplication.processEvents()
            return True
        loop = QEventLoop()
        self.idle.connect(loop.quit)
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        QApplication.processEvents()
        return self._inflight == 0
