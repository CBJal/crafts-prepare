"""Browse, adjust, and export. One selection model and one preparation queue."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEventLoop, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..errors import Cancelled, CraftsError
from ..filters import FilterSpec
from ..importer import list_media_paths
from ..preview import PROXY_LIMIT_SECONDS, extract_thumbnail, make_proxy
from ..recipe import default_recipe, lut_status, recipe_fingerprint
from ..service import new_project
from ..version import APP_NAME
from .brand import load_app_icon
from .compare import CompareView
from .dialogs import AboutDialog, SettingsDialog, TextDialog
from .export_dialog import ExportDialog
from .grade import describe_clip, safe_filters
from .grid import ThumbnailGrid
from .icons import action_icon
from .places import PlacesPanel
from .prefs import load_state, remember_project, save_state
from .theme import apply_theme
from .thumbs import ThumbnailHub
from .workers import TaskRunner

_ACTIVE = {"queued", "running"}


class FitLabel(QLabel):
    """Show a full frame fitted inside the viewer. The pixmap does not force the layout size."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._source = QPixmap()
        self.setObjectName("stillLabel")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(120, 80)

    def set_source(self, pixmap: QPixmap) -> None:
        self._source = pixmap
        self._refit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._refit()
        super().resizeEvent(event)

    def _refit(self) -> None:
        if self._source.isNull() or self.width() < 2 or self.height() < 2:
            super().setPixmap(QPixmap())
            return
        fitted = self._source.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        super().setPixmap(fitted)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(120, 80)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(640, 360)


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
        self._project_gen = 0
        self._still_seq = 0
        self._folder_token = 0
        self._notes_dirty = False
        self._filling = False
        self._proxy_fallback = False
        self._callbacks: dict[int, object] = {}
        self._partial: dict | None = None
        self._import_errors: list[dict] = []
        self._browsed_root: Path | None = None
        self._pending_markers: list | None = None
        self._compare_token = 0
        self.still_path = ""
        self.playback_result: dict | None = None
        self.last_error = ""
        self.force_close = False
        self.last_batch_id = ""
        self.player_error = ""
        self.setWindowTitle(APP_NAME)
        icon = load_app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(1280, 800)
        self.setMinimumSize(960, 640)
        self.setAcceptDrops(True)
        self._runner = TaskRunner()
        self._runner.succeeded.connect(self._task_ok)
        self._runner.failed.connect(self._task_fail)
        self._runner.start()
        self._thumbs = ThumbnailHub(service)
        self._thumbs.ready.connect(self._thumb_ready)
        self._build_player()
        self._build_shell()
        self._build_actions()
        self.set_theme(self.state.get("theme") or "system")
        self._restore_window()
        self.reload_all()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(700)
        self._refresh_timer.timeout.connect(self.refresh_queue)
        self._refresh_timer.start()
        self._import_timer = QTimer(self)
        self._import_timer.setInterval(150)
        self._import_timer.timeout.connect(self._paint_partial)
        self._start(lambda cancel: self.service.capabilities(cancel=cancel), self._apply_capabilities, key="caps")

    def _build_player(self) -> None:
        self.player = None
        self.audio_output = None
        self.video = None
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget

            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.video = QVideoWidget()
            self.video.setObjectName("videoWidget")
            self.player.setAudioOutput(self.audio_output)
            self.player.setVideoOutput(self.video)
            self.player.positionChanged.connect(self._on_position)
            self.player.durationChanged.connect(self._on_duration)
            self.player.errorOccurred.connect(self._on_player_error)
        except Exception as exc:  # noqa: BLE001
            self.player_error = str(exc)

    def _build_shell(self) -> None:
        shell = QWidget()
        shell.setObjectName("viewerShell")
        self.setCentralWidget(shell)
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter()
        self.splitter.setObjectName("browserSplit")
        outer.addWidget(self.splitter, 1)
        self.places = PlacesPanel()
        self.places.setMinimumWidth(180)
        self.places.setMaximumWidth(280)
        self.places.folder_chosen.connect(self._folder_chosen)
        self.splitter.addWidget(self.places)
        self.center = QStackedWidget()
        self.center.addWidget(self._build_browse())
        self.center.addWidget(self._build_view())
        self.splitter.addWidget(self.center)
        self.right = QStackedWidget()
        self.right.setMinimumWidth(260)
        self.right.setMaximumWidth(420)
        self.right.addWidget(self._build_browse_meta())
        self.right.addWidget(self._build_corrections())
        self.splitter.addWidget(self.right)
        self.splitter.setSizes([220, 900, 320])
        self.splitter.setStretchFactor(1, 1)
        self.setStatusBar(QStatusBar())
        self._show_mode(0)

    def _build_browse(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter filenames")
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(140, 240)
        self.size_slider.setValue(180)
        self.size_slider.setFixedWidth(120)
        self.batch_count = QLabel("0 selected")
        row.addWidget(self.search, 1)
        row.addWidget(QLabel("Size"))
        row.addWidget(self.size_slider)
        row.addWidget(self.batch_count)
        layout.addLayout(row)
        self.grid = ThumbnailGrid()
        self.grid.selection_changed.connect(self._selection_changed)
        self.grid.activated.connect(self._open_adjust)
        self.search.textChanged.connect(self.grid.set_filter)
        self.size_slider.valueChanged.connect(self.grid.set_card_width)
        layout.addWidget(self.grid, 1)
        return page

    def _build_browse_meta(self) -> QWidget:
        page = QWidget()
        page.setObjectName("inspectorPage")
        layout = QVBoxLayout(page)
        self.meta_summary = QLabel("Select a clip.")
        self.meta_summary.setWordWrap(True)
        self.meta_summary.setObjectName("metadataLabel")
        layout.addWidget(self.meta_summary)
        self.meta_toggle = QPushButton("Details")
        self.meta_toggle.setCheckable(True)
        self.meta_toggle.toggled.connect(lambda checked: self.meta_details.setVisible(checked))
        layout.addWidget(self.meta_toggle)
        self.meta_details = QPlainTextEdit()
        self.meta_details.setReadOnly(True)
        self.meta_details.setVisible(False)
        layout.addWidget(self.meta_details, 1)
        return page

    def _build_view(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.viewer_stack = QStackedWidget()
        self.viewer_stack.setObjectName("viewerStack")
        self.compare = CompareView()
        self.still_original = FitLabel("Original")
        self.still_adjusted = FitLabel("Processed")
        self.still_original.hide()
        self.still_adjusted.hide()
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(self.compare, 1)
        holder_layout.addWidget(self.still_original)
        holder_layout.addWidget(self.still_adjusted)
        self.viewer_stack.addWidget(holder)
        if self.video is not None:
            self.viewer_stack.addWidget(self.video)
        layout.addWidget(self.viewer_stack, 1)
        transport = QHBoxLayout()
        self.play_button = self._transport_button("Play", "play", self.play)
        self.pause_button = self._transport_button("Pause", "pause", self.pause)
        self.frame_back_button = self._transport_button("Previous frame", "frame-back", lambda: self.step_frame(-1))
        self.frame_forward_button = self._transport_button("Next frame", "frame-forward", lambda: self.step_frame(1))
        self.mark_in_button = self._transport_button("Set trim in", "mark-in", lambda: self.set_trim_from_player("in"))
        self.mark_out_button = self._transport_button("Set trim out", "mark-out", lambda: self.set_trim_from_player("out"))
        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.sliderMoved.connect(self._seek)
        self.time_label = QLabel("0:00.0 / 0:00.0")
        self.time_label.setObjectName("timeLabel")
        self.compare_mode = QComboBox()
        for label, value in (("Original", "original"), ("Processed", "processed"), ("2-up", "split"), ("Wipe", "wipe")):
            self.compare_mode.addItem(label, value)
        self.compare_mode.setCurrentIndex(2)
        self.compare_mode.currentIndexChanged.connect(self._compare_mode_changed)
        for button in (
            self.play_button,
            self.pause_button,
            self.frame_back_button,
            self.frame_forward_button,
            self.mark_in_button,
            self.mark_out_button,
        ):
            transport.addWidget(button)
        transport.addWidget(self.scrub, 1)
        transport.addWidget(self.time_label)
        transport.addWidget(self.compare_mode)
        layout.addLayout(transport)
        self.playback_note = QLabel("Original and processed stay on the same frame.")
        self.playback_note.setObjectName("playbackNote")
        self.playback_note.setWordWrap(True)
        layout.addWidget(self.playback_note)
        self.filmstrip = QLabel("")
        self.filmstrip.setObjectName("muted")
        layout.addWidget(self.filmstrip)
        self.preview_status = QLabel("")
        self.preview_status.setWordWrap(True)
        layout.addWidget(self.preview_status)
        return page

    def _build_corrections(self) -> QWidget:
        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.addTab(self._color_page(), "Color")
        self.inspector_tabs.addTab(self._stab_page(), "Stabilization")
        self.inspector_tabs.addTab(self._meta_page(), "Metadata")
        return self.inspector_tabs

    def _color_page(self) -> QWidget:
        page = QScrollArea()
        page.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        self.profile_detected = QLabel("No clip.")
        self.profile_detected.setWordWrap(True)
        form.addRow("Detected", self.profile_detected)
        self.profile = QComboBox()
        self.profile.addItem("Auto", "auto")
        self.profile.addItem("S-Log3 / S-Gamut3.Cine", "slog3_sgamut3cine")
        self.profile.addItem("HLG / BT.2020", "hlg_bt2020")
        self.profile.addItem("Preserve input", "preserve")
        form.addRow("Input profile", self.profile)
        self.exposure = QDoubleSpinBox()
        self.exposure.setRange(-6, 6)
        self.exposure.setSingleStep(0.1)
        self.exposure.setSuffix(" EV")
        form.addRow("Exposure", self.exposure)
        self.contrast = QDoubleSpinBox()
        self.contrast.setRange(0.5, 2.0)
        self.contrast.setSingleStep(0.05)
        self.contrast.setValue(1.0)
        form.addRow("Contrast", self.contrast)
        self.saturation = QDoubleSpinBox()
        self.saturation.setRange(0, 2)
        self.saturation.setSingleStep(0.05)
        self.saturation.setValue(1.0)
        form.addRow("Saturation", self.saturation)
        self.input_lut = QLineEdit()
        self.look_lut = QLineEdit()
        form.addRow("Input LUT", self._path_row(self.input_lut, "Input LUT"))
        form.addRow("Look LUT", self._path_row(self.look_lut, "Look LUT"))
        self.look_includes = QCheckBox("Look LUT already includes the camera transform")
        form.addRow("Double conversion", self.look_includes)
        note = QLabel(
            "Exposure is stops of scene light after the camera decode, not FFmpeg brightness. "
            "An input LUT replaces automatic normalization."
        )
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QHBoxLayout()
        apply = QPushButton("Apply to selected")
        apply.clicked.connect(self.apply_recipe_to_selected)
        reset = QPushButton("Reset")
        reset.clicked.connect(self.reset_recipe)
        buttons.addWidget(apply)
        buttons.addWidget(reset)
        form.addRow(buttons)
        page.setWidget(body)
        return page

    def _stab_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.gyro_enabled = QCheckBox("Stabilize with embedded gyro")
        form.addRow(self.gyro_enabled)
        self.gyro_strength = QSlider(Qt.Orientation.Horizontal)
        self.gyro_strength.setRange(0, 100)
        self.gyro_strength.setValue(50)
        form.addRow("Strength", self.gyro_strength)
        self.gyro_crop = QSlider(Qt.Orientation.Horizontal)
        self.gyro_crop.setRange(0, 50)
        form.addRow("Crop", self.gyro_crop)
        self.gyro_horizon = QSlider(Qt.Orientation.Horizontal)
        self.gyro_horizon.setRange(0, 100)
        form.addRow("Horizon", self.gyro_horizon)
        note = QLabel(
            "Gyroflow reads this clip's embedded motion. XML names are not samples. "
            "Visual stabilization is not substituted. Sony breathing correction is not available."
        )
        note.setWordWrap(True)
        form.addRow(note)
        self.gyro_preview_button = QPushButton("Update stabilization preview")
        self.gyro_preview_button.clicked.connect(self.update_stabilization_preview)
        form.addRow(self.gyro_preview_button)
        self.gyro_status = QLabel("Stabilization preview has not been rendered.")
        self.gyro_status.setWordWrap(True)
        form.addRow(self.gyro_status)
        return page

    def _meta_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.notes = QPlainTextEdit()
        self.notes.textChanged.connect(self._notes_edited)
        self.flag = QComboBox()
        self.flag.addItem("", "")
        self.flag.addItem("OK", "OK")
        self.flag.addItem("KEEP", "KEEP")
        self.flag.addItem("NG", "NG")
        self.trim_in = QDoubleSpinBox()
        self.trim_out = QDoubleSpinBox()
        for box in (self.trim_in, self.trim_out):
            box.setRange(0, 86400)
            box.setDecimals(3)
            box.setSingleStep(0.1)
        form.addRow("Flag", self.flag)
        form.addRow("Notes", self.notes)
        form.addRow("Trim in", self.trim_in)
        form.addRow("Trim out", self.trim_out)
        save = QPushButton("Save trim")
        save.clicked.connect(self.save_trim)
        form.addRow(save)
        return page

    def _path_row(self, edit: QLineEdit, title: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        browse = QPushButton("…")
        browse.clicked.connect(lambda: self._browse_into(edit, title))
        clear = QPushButton("Clear")
        clear.clicked.connect(edit.clear)
        layout.addWidget(edit, 1)
        layout.addWidget(browse)
        layout.addWidget(clear)
        return row

    def _transport_button(self, name: str, icon: str, slot) -> QPushButton:
        button = QPushButton()
        button.setObjectName("transportButton")
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setFixedSize(36, 32)
        button.clicked.connect(slot)
        button.setProperty("iconName", icon)
        return button

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        toolbar = QToolBar("Main")
        toolbar.setObjectName("mainToolBar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.brand_mark = QLabel()
        icon = load_app_icon()
        if not icon.isNull():
            self.brand_mark.setPixmap(icon.pixmap(18, 18))
        toolbar.addWidget(self.brand_mark)
        self.mode_group = QButtonGroup(self)
        self.browse_button = QPushButton("Browse")
        self.view_button = QPushButton("View")
        self.browse_button.setCheckable(True)
        self.view_button.setCheckable(True)
        self.browse_button.setChecked(True)
        self.mode_group.addButton(self.browse_button, 0)
        self.mode_group.addButton(self.view_button, 1)
        self.mode_group.idClicked.connect(self._show_mode)
        toolbar.addWidget(self.browse_button)
        toolbar.addWidget(self.view_button)
        self.project_chip = QLabel("New")
        self.project_chip.setObjectName("projectChip")
        toolbar.addWidget(self.project_chip)
        for label, slot, shortcut in (
            ("Open…", self.open_project_interactive, "Ctrl+O"),
            ("Save", self.save_project_interactive, "Ctrl+S"),
            ("Import folder…", self.import_folder, None),
        ):
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(slot)
            file_menu.addAction(action)
            toolbar.addAction(action)
        file_menu.addAction("Import files…", self.import_files)
        file_menu.addAction("New", self.new_project)
        self.recent_menu = file_menu.addMenu("Recent")
        self._rebuild_recent()
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.export_button = QPushButton("Export")
        self.export_button.setObjectName("exportButton")
        self.export_button.clicked.connect(self.open_export)
        toolbar.addWidget(self.export_button)
        self.settings_action = QAction("Tools", self)
        self.settings_action.setProperty("iconName", "settings")
        self.settings_action.triggered.connect(self.open_settings)
        toolbar.addAction(self.settings_action)
        tools_menu = self.menuBar().addMenu("Tools")
        tools_menu.addAction(self.settings_action)
        edit = self.menuBar().addMenu("Edit")
        edit.addAction("Select all", self.select_all_batch, QKeySequence.StandardKey.SelectAll)
        edit.addAction("Clear selection", self.clear_batch)
        self.export_dialog = ExportDialog(self)
        self.export_dialog.queued.connect(lambda: self.queue_checked_batch("prepare"))
        self.export_dialog.cancel_button.clicked.connect(self.cancel_current_batch)
        self.export_dialog.retry_button.clicked.connect(self.retry_failed_batch)
        self.destination = self.export_dialog.destination
        self.suffix = self.export_dialog.suffix
        self.preset = self.export_dialog.preset
        self.audio = self.export_dialog.audio
        self.ack = self.export_dialog.ack
        self.fit = self.export_dialog.fit
        self.out_width = self.export_dialog.out_width
        self.out_height = self.export_dialog.out_height
        self.batch_errors = self.export_dialog.jobs

    def _show_mode(self, index: int) -> None:
        self.center.setCurrentIndex(0 if index == 0 else 1)
        self.right.setCurrentIndex(0 if index == 0 else 1)
        if hasattr(self, "browse_button"):
            self.browse_button.setChecked(index == 0)
            self.view_button.setChecked(index != 0)
        if index != 0 and self.clips:
            self._fill_focus()
            self.request_comparison()

    def _open_adjust(self, clip_id: str) -> None:
        self.focus_clip(clip_id)
        self._show_mode(1)

    def set_theme(self, choice: str) -> str:
        resolved = apply_theme(self, choice)
        self.state["theme"] = choice
        self._paint_icons(resolved)
        self._save_preferences()
        return resolved

    def _paint_icons(self, theme: str) -> None:
        for button in (
            self.play_button,
            self.pause_button,
            self.frame_back_button,
            self.frame_forward_button,
            self.mark_in_button,
            self.mark_out_button,
        ):
            button.setIcon(action_icon(str(button.property("iconName")), theme))
        self.export_button.setIcon(action_icon("export", theme, role="on-primary"))
        self.settings_action.setIcon(action_icon("settings", theme))
        if hasattr(self, "inspector_tabs"):
            for index, name in enumerate(("adjusted", "gyro", "clip")):
                self.inspector_tabs.setTabIcon(index, action_icon(name, theme))

    def _folder_chosen(self, folder: str) -> None:
        folders = [item for item in self.state.get("folders") or [] if item != folder]
        folders.insert(0, folder)
        self.state["folders"] = folders[:8]
        self._save_preferences()
        self.places.set_lists(self.state.get("folders") or [], self.state.get("favorites") or [])
        self.import_paths([folder])

    def import_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, "Import clips", "", "Video (*.mp4 *.mov *.mxf *.mts *.m2ts)")
        if paths:
            self.import_paths(paths)

    def import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Import folder")
        if folder:
            self.import_paths([folder])

    def import_paths(self, paths: list[str]) -> None:
        files: list[Path] = []
        missing: list[str] = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                try:
                    files.extend(list_media_paths(path))
                except OSError as exc:
                    missing.append(f"{raw} ({exc})")
            elif path.is_file():
                files.append(path.resolve())
            else:
                missing.append(raw)
        if len(paths) == 1 and Path(paths[0]).is_dir():
            self._browsed_root = Path(paths[0]).resolve()
        self._folder_token += 1
        token = self._folder_token
        self._thumbs.cancel()
        skipped = [{"path": item, "reason": "File was not found."} for item in missing]
        self._partial = {"added": [], "skipped": skipped, "token": token, "pending": files}
        self._paint_cards([], skipped, pending=files)
        self._import_timer.start()

        def work(cancel) -> dict:
            added: list[dict] = []
            local_skipped = list(skipped)
            for path in files:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                if self._folder_token != token:
                    raise Cancelled()
                try:
                    report = self.service.import_media(str(path), cancel=cancel)
                except Cancelled:
                    raise
                except CraftsError as exc:
                    local_skipped.append({"path": str(path), "reason": "Unreadable media"})
                else:
                    added.extend(report.get("added") or [])
                    local_skipped.extend(report.get("skipped") or [])
                self._partial = {"added": list(added), "skipped": list(local_skipped), "token": token, "pending": files}
            return {"added": added, "skipped": local_skipped, "missing": missing, "token": token}

        self._start(work, self._imported, key="import")

    def _paint_partial(self) -> None:
        partial = self._partial
        if not partial or partial.get("token") != self._folder_token:
            return
        self._paint_cards(partial.get("added") or [], partial.get("skipped") or [], pending=partial.get("pending") or [])

    def _imported(self, report: dict) -> None:
        self._import_timer.stop()
        if report.get("token") != self._folder_token:
            return
        self._partial = None
        skipped = report.get("skipped") or []
        self._import_errors = [
            {
                "id": "error:" + str(item.get("path")),
                "name": Path(str(item.get("path") or "clip")).name,
                "meta": "Unreadable",
                "error": str(item.get("reason") or "Unreadable"),
            }
            for item in skipped
        ]
        if skipped:
            self.last_error = "\n".join(f"{item['name']}: {item['error']}" for item in self._import_errors)
            self.statusBar().showMessage(self.last_error, 8000)
        self.reload_all(keep_focus=False)
        shown = self._shown_clips()
        if shown:
            self.focus_clip(shown[0]["id"])

    def _paint_cards(self, added: list[dict], skipped: list[dict], pending: list[Path] | None = None) -> None:
        known = {clip["path"] for clip in added}
        items = [self._card_item(clip) for clip in added]
        for item in skipped:
            items.append(
                {
                    "id": "error:" + str(item.get("path")),
                    "name": Path(str(item.get("path") or "clip")).name,
                    "meta": "Unreadable",
                    "error": str(item.get("reason") or "Unreadable"),
                }
            )
        for path in pending or []:
            if str(path) in known:
                continue
            items.append({"id": "pending:" + str(path), "name": path.name, "meta": "Reading", "error": ""})
        self.grid.set_items(items)
        for clip in added:
            self._thumbs.submit(clip, adjusted=True)

    def _shown_clips(self) -> list[dict]:
        root = self._browsed_root
        if root is None:
            return self.clips
        shown = []
        for clip in self.clips:
            try:
                Path(clip["path"]).resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            shown.append(clip)
        return shown

    def _card_item(self, clip: dict) -> dict:
        described = describe_clip(clip)
        profile = described["profile"]
        sidecar = described["sidecar"]
        duration = (clip.get("summary") or {}).get("duration")
        if duration is None:
            duration = (clip.get("probe") or {}).get("duration")
        badges = []
        if sidecar.get("status") == "ok":
            badges.append("XML")
        elif sidecar.get("status") not in {None, "missing"}:
            badges.append(str(sidecar.get("status")))
        data_streams = (clip.get("summary") or {}).get("data_streams")
        if data_streams is None:
            data_streams = len((clip.get("probe") or {}).get("data") or [])
        if data_streams:
            badges.append("data")
        if sidecar.get("gyro_announced"):
            badges.append("gyro announced")
        recipe = described["recipe"]
        if any(
            (
                recipe.get("profile") not in {None, "auto"},
                recipe.get("exposure_ev"),
                recipe.get("input_lut"),
                recipe.get("look_lut"),
                recipe.get("gyro_enabled"),
            )
        ):
            badges.append("adjusted")
        text = f"{float(duration or 0):.1f}s"
        if badges:
            text += "  " + " ".join(badges)
        pipeline = profile.get("pipeline")
        if pipeline and pipeline not in {"unknown", "preserve"}:
            text += f"  {pipeline}"
        elif profile.get("status") == "preserve":
            text += "  preserve"
        return {"id": clip["id"], "name": clip.get("name") or Path(clip["path"]).name, "meta": text, "error": ""}

    def _thumb_ready(self, generation: int, clip_id: str, path: str, error: str) -> None:
        if generation != self._thumbs.generation:
            return
        if not any(clip["id"] == clip_id for clip in self._shown_clips()):
            return
        if error and not path:
            self.grid.set_card_error(clip_id, "Unreadable")
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.grid.set_card_error(clip_id, "Unreadable thumbnail")
            return
        self.grid.set_thumbnail(clip_id, pixmap)

    def reload_all(self, *, keep_checks: bool = True, keep_focus: bool = True) -> None:
        del keep_checks, keep_focus
        self.clips = self.service.clips()
        shown = self._shown_clips()
        self.grid.set_items([self._card_item(clip) for clip in shown] + list(self._import_errors))
        self._update_count()
        self._refresh_project_chip()
        self.places.set_lists(self.state.get("folders") or [], self.state.get("favorites") or [])
        for clip in shown:
            self._thumbs.submit(clip, adjusted=True)
        self._fill_focus()

    def _selection_changed(self) -> None:
        self._update_count()
        self._fill_focus()
        if self.center.currentIndex() == 1:
            self.request_comparison()

    def _update_count(self) -> None:
        self.batch_count.setText(f"{len(self.selected_ids())} selected")

    def selected_ids(self) -> list[str]:
        return [clip_id for clip_id in self.grid.selected_ids() if not str(clip_id).startswith(("error:", "pending:"))]

    def checked_ids(self) -> list[str]:
        return self.selected_ids()

    def focused_id(self) -> str | None:
        current = self.grid.current_id()
        if current and not str(current).startswith(("error:", "pending:")):
            return current
        return None

    def focused_clip(self) -> dict | None:
        clip_id = self.focused_id()
        for clip in self.clips:
            if clip["id"] == clip_id:
                return clip
        return None

    def focus_clip(self, clip_id: str) -> None:
        self._invalidate_viewer()
        self.grid.select_only(clip_id)
        self._fill_focus()

    def select_all_batch(self) -> None:
        self.grid.select_all()

    def clear_batch(self) -> None:
        self.grid.clear_selection()

    def _fill_focus(self) -> None:
        clip = self.focused_clip()
        self._filling = True
        try:
            if clip is None:
                self.meta_summary.setText("Select a clip.")
                self.meta_details.setPlainText("")
                self.profile_detected.setText("No clip.")
                return
            described = describe_clip(clip)
            profile = described["profile"]
            sidecar = described["sidecar"]
            recipe = described["recipe"]
            video = ((clip.get("probe") or {}).get("video") or [{}])[0]
            lines = [
                clip.get("name") or "",
                f"{video.get('width')}×{video.get('height')}  {video.get('codec')}  {video.get('pix_fmt')}",
                profile.get("message") or "",
                sidecar.get("message") or "",
            ]
            if sidecar.get("gyro_announced"):
                lines.append("Sidecar announces gyroscope acquisition. It is not a numeric gyro log.")
            if sidecar.get("device"):
                lines.append(" ".join(str(part) for part in sidecar["device"].values()))
            lines.extend(lut_status(recipe))
            self.meta_summary.setText("\n".join(line for line in lines if line))
            self.meta_details.setPlainText(
                f"{clip.get('path')}\nsha256 {clip.get('sha256')}\n"
                f"gamma {sidecar.get('gamma_raw') or '-'}  primaries {sidecar.get('primaries_raw') or '-'}\n"
                f"tags {video.get('color_transfer')} / {video.get('color_primaries')} / {video.get('color_range')}"
            )
            self.profile_detected.setText(profile.get("message") or "")
            index = self.profile.findData(recipe.get("profile") or "auto")
            if index >= 0:
                self.profile.setCurrentIndex(index)
            self.exposure.setValue(float(recipe.get("exposure_ev") or 0))
            self.contrast.setValue(float(recipe.get("contrast") if recipe.get("contrast") is not None else 1))
            self.saturation.setValue(float(recipe.get("saturation") if recipe.get("saturation") is not None else 1))
            self.input_lut.setText(recipe.get("input_lut") or "")
            self.look_lut.setText(recipe.get("look_lut") or "")
            self.look_includes.setChecked(bool(recipe.get("look_includes_camera")))
            self.gyro_enabled.setChecked(bool(recipe.get("gyro_enabled")))
            self.gyro_strength.setValue(int(round(float(recipe.get("gyro_smoothness") or 0.5) * 100)))
            self.gyro_crop.setValue(int(round((float(recipe.get("gyro_fov") or 1) - 1) * 100)))
            self.gyro_horizon.setValue(int(round(float(recipe.get("gyro_horizon") or 0) * 100)))
            self.notes.setPlainText(clip.get("notes") or "")
            flag_index = self.flag.findData(clip.get("flag") or "")
            if flag_index >= 0:
                self.flag.setCurrentIndex(flag_index)
            self.trim_in.setValue(float(clip.get("trim_in") or 0))
            self.trim_out.setValue(float(clip.get("trim_out") or 0))
            self._notes_dirty = False
            self.filmstrip.setText(", ".join(item.get("name") or "" for item in self.clips[:12]))
        finally:
            self._filling = False

    def _notes_edited(self) -> None:
        if not self._filling:
            self._notes_dirty = True

    def recipe_from_controls(self) -> dict:
        return {
            "profile": self.profile.currentData(),
            "exposure_ev": self.exposure.value(),
            "contrast": self.contrast.value(),
            "saturation": self.saturation.value(),
            "input_lut": self.input_lut.text().strip(),
            "look_lut": self.look_lut.text().strip(),
            "look_includes_camera": self.look_includes.isChecked(),
            "gyro_enabled": self.gyro_enabled.isChecked(),
            "gyro_smoothness": self.gyro_strength.value() / 100,
            "gyro_fov": 1 + (self.gyro_crop.value() / 100),
            "gyro_horizon": self.gyro_horizon.value() / 100,
        }

    def apply_recipe_to_selected(self) -> None:
        recipe = self.recipe_from_controls()
        ids = self.selected_ids() or ([self.focused_id()] if self.focused_id() else [])
        if not ids:
            self.statusBar().showMessage("Select at least one clip.", 5000)
            return
        try:
            for clip_id in ids:
                self.service.update_clip(clip_id, {"recipe": recipe})
        except CraftsError as exc:
            self.last_error = exc.message
            self.statusBar().showMessage(exc.message, 8000)
            return
        self.reload_all()
        self.preview_status.setText("Colour changed. The processed preview is stale until it is rebuilt.")
        self.request_comparison()

    def reset_recipe(self) -> None:
        self.profile.setCurrentIndex(0)
        self.exposure.setValue(0)
        self.contrast.setValue(1)
        self.saturation.setValue(1)
        self.input_lut.clear()
        self.look_lut.clear()
        self.look_includes.setChecked(False)
        self.gyro_enabled.setChecked(False)
        self.apply_recipe_to_selected()

    def commit_editor(self) -> bool:
        clip = self.focused_clip()
        if clip is None:
            return True
        notes = self.notes.toPlainText()
        if len(notes) > 8000:
            self.last_error = "Notes must be text up to 8000 characters."
            self.statusBar().showMessage(self.last_error, 6000)
            return False
        markers = self._pending_markers if self._pending_markers is not None else list(clip.get("markers") or [])
        try:
            self.service.update_clip(
                clip["id"],
                {
                    "notes": notes,
                    "flag": self.flag.currentData() or "",
                    "trim_in": self.trim_in.value(),
                    "trim_out": self.trim_out.value(),
                    "markers": markers,
                },
            )
        except CraftsError as exc:
            self.last_error = exc.message
            self.statusBar().showMessage(exc.message, 6000)
            return False
        self._pending_markers = None
        self._notes_dirty = False
        self.clips = self.service.clips()
        return True

    def save_trim(self) -> None:
        self.commit_editor()

    def add_marker(self) -> None:
        clip = self.focused_clip()
        if clip is None:
            return
        markers = list(clip.get("markers") or [])
        seconds = (self.player.position() / 1000.0) if self.player is not None else float(self.trim_in.value())
        markers.append({"seconds": seconds, "label": "mark"})
        self._pending_markers = markers
        self.commit_editor()

    def set_trim_from_player(self, edge: str) -> None:
        seconds = (self.player.position() / 1000.0) if self.player is not None else 0.0
        if edge == "in":
            self.trim_in.setValue(seconds)
        else:
            self.trim_out.setValue(seconds)
        self.save_trim()

    def open_export(self) -> None:
        ids = set(self.selected_ids())
        profiles = []
        gyro = 0
        for clip in self.clips:
            if clip["id"] not in ids:
                continue
            described = describe_clip(clip)
            profiles.append(str(described["profile"].get("pipeline")))
            if described["recipe"].get("gyro_enabled"):
                gyro += 1
        summary = (
            f"One preparation job per clip. Profiles: {', '.join(profiles) or 'none'}. "
            f"Gyro on {gyro} clip(s). Colour and LUT stay in the same job. Full clip unless saved trim is enabled."
        )
        self.export_dialog.set_summary(len(ids), summary)
        self.refresh_queue()
        self.export_dialog.show()
        self.export_dialog.raise_()

    def queue_focused_export(self) -> None:
        if self.focused_id() and self.focused_id() not in self.selected_ids():
            self.focus_clip(self.focused_id())
        self.queue_checked_batch("prepare")

    def queue_checked_batch(self, operation: str) -> None:
        if not self.commit_editor():
            return
        ids = self.selected_ids()
        if not ids and self.focused_id():
            ids = [self.focused_id()]
        if not ids:
            self.last_error = "Select at least one clip."
            self.statusBar().showMessage(self.last_error, 5000)
            return
        if not self.destination.text().strip():
            self.last_error = "Choose a destination folder. Nothing was queued."
            self.statusBar().showMessage(self.last_error, 6000)
            return
        if operation == "gyro":
            try:
                for clip_id in ids:
                    current = next(clip for clip in self.service.clips() if clip["id"] == clip_id)
                    recipe = {**(current.get("recipe") or default_recipe()), "gyro_enabled": True}
                    self.service.update_clip(clip_id, {"recipe": recipe})
            except (CraftsError, StopIteration) as exc:
                self.last_error = str(exc)
                return
            operation = "prepare"
        shared = {
            "preset_id": self.preset.currentData(),
            "audio": self.audio.isChecked(),
            "acknowledge_bit_depth_conversion": self.ack.isChecked(),
            "use_trim": self.export_dialog.use_trim.isChecked(),
            "fit": self.fit.currentData() or "none",
            "crf": self.export_dialog.crf.value(),
        }
        if shared["fit"] == "resize":
            shared["width"] = self.out_width.value()
            shared["height"] = self.out_height.value()
        try:
            result = self.service.enqueue_batch(
                {
                    "operation": "prepare" if operation in {"export", "prepare"} else operation,
                    "clip_ids": ids,
                    "destination_dir": self.destination.text().strip(),
                    "suffix": self.suffix.text(),
                    "prepare": shared,
                }
            )
        except CraftsError as exc:
            self.last_error = exc.message
            self.statusBar().showMessage(exc.message, 8000)
            return
        self.last_batch_id = result["batch_id"]
        self.statusBar().showMessage(f"Queued {result['count']} preparation job(s).", 6000)
        self.refresh_queue()

    def cancel_current_batch(self) -> None:
        if not self.last_batch_id:
            return
        try:
            self.service.cancel_batch(self.last_batch_id)
        except CraftsError as exc:
            self.statusBar().showMessage(exc.message, 6000)
        self.refresh_queue()

    def retry_failed_batch(self) -> None:
        if not self.last_batch_id:
            return
        try:
            result = self.service.retry_batch_failures(self.last_batch_id)
        except CraftsError as exc:
            self.statusBar().showMessage(exc.message, 6000)
            return
        self.statusBar().showMessage(f"Requeued {len(result['retried'])} failed job(s).", 5000)
        self.refresh_queue()

    def refresh_queue(self) -> None:
        if self.service.persist_error:
            self.statusBar().showMessage(self.service.persist_error, 8000)
        elif "Could not replace the project file" in (self.statusBar().currentMessage() or ""):
            self.statusBar().clearMessage()
        lines = []
        for job in self.service.jobs():
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            colour = result.get("colour") or {}
            gyro = result.get("gyro") or {}
            lines.append(
                " ".join(
                    part
                    for part in (
                        str(job.get("status") or ""),
                        str(job.get("kind") or ""),
                        str((job.get("request") or {}).get("filename") or ""),
                        str(colour.get("pipeline") or ""),
                        "gyro" if gyro.get("applied") else "",
                        str(job.get("error") or ""),
                    )
                    if part
                )
            )
        self.export_dialog.jobs.setPlainText("\n".join(lines))

    def request_playback(self) -> None:
        self._request_proxy(force=False, processed=self.compare_mode.currentData() == "processed")

    def request_still(self, adjusted: bool) -> None:
        clip = self.focused_clip()
        if clip is None:
            return
        seconds = self._current_seconds(clip)
        sequence = self._still_seq
        generation = self._project_gen

        def work(cancel):
            ffmpeg, _ffprobe = self.service._tools()
            directory = self.service.data_dir / "still-src" / clip["id"]
            filters, files, _message = safe_filters(clip, directory, adjusted=adjusted, cache=self.service.data_dir / "colour-cache")
            destination = self.service.data_dir / f"still-{clip['id']}-{int(adjusted)}-{sequence}.jpg"
            duration = float((clip.get("probe") or {}).get("duration") or 0)
            return str(
                extract_thumbnail(
                    ffmpeg=ffmpeg,
                    source=Path(clip["path"]),
                    seconds=seconds,
                    destination=destination,
                    spec=FilterSpec(),
                    duration=duration,
                    cancel=cancel,
                    extra_filters=filters or None,
                    stage_files=files or None,
                )
            )

        self._start(work, lambda path: self._show_still(generation, sequence, adjusted, path), key="still")

    def request_comparison(self) -> None:
        clip = self.focused_clip()
        if clip is None:
            return
        self._compare_token += 1
        token = self._compare_token
        seconds = self._current_seconds(clip)
        self.preview_status.setText("Comparison updating.")

        def work(cancel):
            ffmpeg, _ffprobe = self.service._tools()
            duration = float((clip.get("probe") or {}).get("duration") or 0)
            paths = {"seconds": seconds, "message": ""}
            for adjusted, name in ((False, "original"), (True, "processed")):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                directory = self.service.data_dir / "compare-src" / f"{clip['id']}-{name}"
                filters, files, message = safe_filters(clip, directory, adjusted=adjusted, cache=self.service.data_dir / "colour-cache")
                destination = self.service.data_dir / f"compare-{clip['id']}-{name}-{token}.jpg"
                extract_thumbnail(
                    ffmpeg=ffmpeg,
                    source=Path(clip["path"]),
                    seconds=seconds,
                    destination=destination,
                    spec=FilterSpec(),
                    duration=duration,
                    cancel=cancel,
                    extra_filters=filters or None,
                    stage_files=files or None,
                )
                paths[name] = str(destination)
                paths["message"] = message
            return paths

        def done(paths: dict) -> None:
            if token != self._compare_token:
                return
            left = QPixmap(paths["original"])
            right = QPixmap(paths["processed"])
            self.still_original.set_source(left)
            self.still_adjusted.set_source(right)
            self.compare.set_pair(left, right, float(paths["seconds"]))
            self.still_path = paths["processed"]
            stale = "Stabilization preview has not been updated." if self.gyro_enabled.isChecked() else ""
            self.preview_status.setText(" ".join(part for part in (paths.get("message"), stale, "Both sides use this frame.") if part))

        self._start(work, done, key="compare")

    def update_stabilization_preview(self) -> None:
        clip = self.focused_clip()
        if clip is None:
            return
        if not self.gyro_enabled.isChecked():
            self.gyro_status.setText("Gyro is off, so there is no stabilization preview to render.")
            return
        self.gyro_status.setText("Rendering a stabilization preview with Gyroflow. This does not publish an export.")

        def work(cancel):
            from ..prepare import run_prepare

            ffmpeg, ffprobe = self.service._tools()
            described = describe_clip(clip)
            recipe = dict(described["recipe"])
            recipe["gyro_enabled"] = True
            destination = self.service.data_dir / "gyro-preview"
            destination.mkdir(parents=True, exist_ok=True)
            filename = f"preview-{clip['id'][:8]}.mp4"
            target = destination / filename
            if target.exists():
                target.unlink()
            request = {
                "clip_id": clip["id"],
                "preset_id": "h264_mp4",
                "in_seconds": 0,
                "out_seconds": float((clip.get("probe") or {}).get("duration") or 0.1),
                "use_trim": False,
                "destination_dir": str(destination),
                "filename": filename,
                "audio": False,
                "acknowledge_bit_depth_conversion": True,
                "fit": "resize",
                "width": 640,
                "height": 360,
                "recipe": recipe,
                "profile": described["profile"],
                "gyro_enabled": True,
            }
            try:
                result = run_prepare(
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    settings=self.service.settings,
                    source=Path(clip["path"]),
                    source_sha256=clip["sha256"],
                    probe=clip["probe"],
                    sidecar=clip.get("sidecar"),
                    request=request,
                    job_id=f"preview{clip['id'][:8]}",
                    cancel=cancel,
                    on_progress=lambda _value: None,
                    encoders=self.service._encoder_flags(),
                )
            except CraftsError as exc:
                return {"error": exc.message}
            return result

        def done(result: dict) -> None:
            if result.get("error"):
                self.gyro_status.setText(str(result["error"]))
                return
            self.gyro_status.setText("Stabilization preview is ready and uses Gyroflow output.")
            self.playback_result = {"kind": "gyro-preview", "proxy_path": result.get("output_path")}
            if self.player is not None and result.get("output_path"):
                self.player.setSource(QUrl.fromLocalFile(str(result["output_path"])))

        self._start(work, done, key="gyro-preview")

    def _current_seconds(self, clip: dict) -> float:
        if self.player is not None and self.player.position() > 0:
            return min(self.player.position() / 1000.0, float((clip.get("probe") or {}).get("duration") or 0) or 0)
        duration = float((clip.get("probe") or {}).get("duration") or 0)
        return min(0.2, duration / 2 if duration else 0.0)

    def _show_still(self, generation: int, sequence: int, adjusted: bool, path: str) -> None:
        if generation != self._project_gen or sequence != self._still_seq:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        self.still_path = path
        if adjusted:
            self.still_adjusted.set_source(pixmap)
        else:
            self.still_original.set_source(pixmap)

    def _invalidate_viewer(self) -> None:
        self._view_gen += 1
        self._still_seq += 1
        self._compare_token += 1
        self._proxy_fallback = False
        self.still_path = ""
        self.playback_result = None
        self.still_original.set_source(QPixmap())
        self.still_adjusted.set_source(QPixmap())
        self.compare.set_pair(QPixmap(), QPixmap(), 0.0)
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        if hasattr(self, "scrub"):
            self.scrub.setRange(0, 0)
            self.time_label.setText("0:00.0 / 0:00.0")

    def _compare_mode_changed(self) -> None:
        mode = self.compare_mode.currentData()
        self.compare.set_mode("split" if mode == "split" else "wipe" if mode == "wipe" else "split")
        if mode in {"original", "processed"} and self.video is not None:
            self.viewer_stack.setCurrentWidget(self.video)
            self.request_playback()
        else:
            self.viewer_stack.setCurrentIndex(0)
            self.request_comparison()

    def play(self) -> None:
        if self.player is not None:
            self.player.play()

    def pause(self) -> None:
        if self.player is not None:
            self.player.pause()

    def step_frame(self, direction: int) -> None:
        if self.player is None:
            return
        self.player.pause()
        self.player.setPosition(max(0, self.player.position() + direction * 40))
        self.request_comparison()

    def _seek(self, position: int) -> None:
        if self.player is not None:
            self.player.setPosition(position)
        self.request_comparison()

    def _on_position(self, position: int) -> None:
        self.time_label.setText(self._format_time(position))
        if not self.scrub.isSliderDown():
            self.scrub.setValue(position)

    def _on_duration(self, duration: int) -> None:
        self.scrub.setRange(0, max(0, duration))

    def _format_time(self, ms: int) -> str:
        seconds = ms / 1000
        return f"{int(seconds // 60)}:{seconds % 60:04.1f}"

    def _on_player_error(self, error, message: str) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if error == QMediaPlayer.Error.NoError or self._proxy_fallback:
            return
        self.playback_note.setText(f"Playback failed ({message}). Building a viewing proxy.")
        self._proxy_fallback = True
        self._request_proxy(force=True, processed=False)

    def _request_proxy(self, force: bool, processed: bool = False) -> None:
        clip = self.focused_clip()
        if clip is None:
            return
        clip_id = clip["id"]

        def work(cancel):
            if not processed and not force:
                report = self.service.proxy(clip_id, cancel=cancel, force=False)
                if report.get("kind") == "source":
                    report["path"] = clip["path"]
                    return report
            ffmpeg, _ffprobe = self.service._tools()
            described = describe_clip(clip)
            digest = recipe_fingerprint(described["recipe"]) if processed else "original"
            destination = self.service.data_dir / f"view-{clip['sha256']}-{digest}.mp4"
            filters = ""
            files: dict = {}
            if processed:
                filters, files, _message = safe_filters(
                    clip,
                    self.service.data_dir / "view-src" / clip_id,
                    adjusted=True,
                    cache=self.service.data_dir / "colour-cache",
                )
            video_filter = filters + ",scale=960:-2" if filters else "scale=960:-2"
            if force or not destination.is_file():
                if force and destination.exists():
                    destination.unlink()
                make_proxy(
                    ffmpeg=ffmpeg,
                    source=Path(clip["path"]),
                    destination=destination,
                    duration=float((clip.get("probe") or {}).get("duration") or 0),
                    cancel=cancel,
                    max_seconds=None,
                    video_filter=video_filter,
                    stage_files=files or None,
                )
            return {
                "kind": "proxy",
                "proxy_path": str(destination),
                "limited": False,
                "note": f"Full-length preview. It is not cut at {int(PROXY_LIMIT_SECONDS)} seconds.",
            }

        self._start(work, self._show_proxy, key="viewer-proxy")

    def _show_proxy(self, result: dict) -> None:
        self.playback_result = result
        path = result.get("proxy_path") or result.get("path")
        if self.player is not None and path:
            self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.playback_note.setText(result.get("note") or "Playback ready.")

    def _browse_into(self, edit: QLineEdit, title: str) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, title, edit.text(), "LUT (*.cube)")
        if path:
            edit.setText(path)

    def is_unsaved(self) -> bool:
        if self._notes_dirty:
            return True
        if self.service.project_path is not None:
            return False
        project = self.service.project
        return bool(project.get("clips") or project.get("jobs") or (project.get("name") or "Untitled") != "Untitled")

    def _prompt_unsaved(self) -> str:
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setText("This project has not been saved.")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return "save"
        if clicked is discard:
            return "discard"
        return "cancel"

    def new_project(self, discard: bool = False) -> bool:
        if not self.commit_editor():
            return False
        if self.is_unsaved() and not discard:
            choice = self._prompt_unsaved()
            if choice == "cancel":
                return False
            if choice == "save" and not self.save_project_interactive():
                return False
        if self._busy_queue() or not self._runner.settle():
            self.statusBar().showMessage("Background work did not stop. The project was not replaced.", 8000)
            return False
        with self.service.lock:
            self.service.project = new_project()
            self.service.project_path = None
        self._reset_workspace()
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
        if not self.commit_editor():
            return False
        if self.is_unsaved():
            choice = self._prompt_unsaved()
            if choice == "cancel":
                return False
            if choice == "save" and not self.save_project_interactive():
                return False
        if self._busy_queue() or not self._runner.settle():
            self.statusBar().showMessage("Background work did not stop. The current project was kept.", 8000)
            return False
        try:
            self.service.open_project(path)
        except CraftsError as exc:
            self.last_error = exc.message
            self.statusBar().showMessage(exc.message, 10000)
            return False
        remember_project(self.state, str(self.service.project_path))
        self._save_preferences()
        self._rebuild_recent()
        self._reset_workspace()
        return True

    def _reset_workspace(self) -> None:
        self._project_gen += 1
        self._folder_token += 1
        self._thumbs.cancel()
        self._still_seq += 1
        self._notes_dirty = False
        self._proxy_fallback = False
        self.last_batch_id = ""
        self.batch_errors.setPlainText("")
        self._import_errors = []
        self._browsed_root = None
        self.grid.clear_selection()
        self._invalidate_viewer()
        self.reload_all()

    def save_project_interactive(self) -> bool:
        if not self.commit_editor():
            return False
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
        if not self.commit_editor():
            return False
        try:
            self.service.save_project(path)
        except CraftsError as exc:
            self.last_error = exc.message
            self.statusBar().showMessage(exc.message, 8000)
            return False
        remember_project(self.state, str(self.service.project_path))
        self._save_preferences()
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
            self._start(lambda cancel: self.service.capabilities(cancel=cancel), self._apply_capabilities, key="caps")

    def _apply_capabilities(self, report: dict) -> None:
        self.capabilities = report
        missing = [name for name in ("ffmpeg", "ffprobe", "gyroflow") if not (report.get(name) or {}).get("available")]
        if missing:
            self.statusBar().showMessage("Not found yet: " + ", ".join(missing) + ". Set the paths under Tools.", 12000)
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

        folder = (bundle_dir() / "licenses") if is_frozen() and bundle_dir() else repo_root() / "packaging" / "third-party-licenses"
        if folder and folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        TextDialog("Licenses", str(folder), self).exec()

    def handle_peer(self, payload: bytes) -> None:
        if b"raise" in payload:
            self.showNormal()
            self.raise_()
            self.activateWindow()
            self.peer_raised.emit()

    def _refresh_project_chip(self) -> None:
        if self.service.project_path is None:
            text = "New" if not self.service.project.get("clips") else "New · Unsaved"
            self.project_chip.setText(text)
            self.project_chip.setToolTip(text)
            return
        name = Path(str(self.service.project_path)).name
        self.project_chip.setToolTip(str(self.service.project_path))
        self.project_chip.setText(name if len(name) < 42 else name[:18] + "…" + name[-12:])

    def _restore_window(self) -> None:
        self.places.set_lists(self.state.get("folders") or [], self.state.get("favorites") or [])

    def _save_preferences(self) -> bool:
        try:
            save_state(self.state, self.state_path)
            return True
        except Exception:
            return False

    def _persist_window(self) -> None:
        self._save_preferences()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.import_paths(paths)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.select_all_batch()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self.commit_editor():
            event.ignore()
            return
        if self.is_unsaved() and not self.force_close:
            choice = self._prompt_unsaved()
            if choice == "cancel":
                event.ignore()
                return
            if choice == "save" and not self.save_project_interactive():
                event.ignore()
                return
        if not self._runner.settle(timeout=8):
            self.statusBar().showMessage("A background tool is still stopping. The window stays open.", 8000)
            event.ignore()
            return
        self._refresh_timer.stop()
        self._import_timer.stop()
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        for job in self.service.jobs():
            if job["status"] in _ACTIVE:
                try:
                    self.service.cancel(job["id"])
                except CraftsError:
                    pass
        self.service.shutdown(timeout=8)
        self._thumbs.shutdown()
        if not self._runner.shutdown(timeout=4):
            event.ignore()
            return
        try:
            self._persist_window()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Window preferences failed during close")
        event.accept()

    def _start(self, fn, on_ok, *, key: str | None = None) -> None:
        token = self._token
        self._token += 1
        self._inflight += 1
        self._callbacks[token] = on_ok
        self._runner.submit(token, fn, key)

    def _task_ok(self, token: int, value) -> None:
        callback = self._callbacks.pop(token, None)
        if callback and value is not None:
            callback(value)
        self._note_finished()

    def _task_fail(self, token: int, message: str) -> None:
        self._callbacks.pop(token, None)
        if message != "cancelled":
            self.last_error = message
            self.statusBar().showMessage(message, 10000)
        self._note_finished()

    def _note_finished(self) -> None:
        self._inflight = max(0, self._inflight - 1)
        if self._inflight == 0:
            self.idle.emit()

    def wait_until_idle(self, timeout_ms: int = 30000) -> bool:
        from PySide6.QtWidgets import QApplication

        if self._inflight == 0:
            QApplication.processEvents()
            return True
        loop = QEventLoop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        self.idle.connect(loop.quit)
        timer.start(timeout_ms)
        loop.exec()
        timer.stop()
        try:
            self.idle.disconnect(loop.quit)
        except RuntimeError:
            pass
        QApplication.processEvents()
        return self._inflight == 0
