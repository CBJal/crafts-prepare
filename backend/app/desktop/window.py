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
from ..importer import list_directory_media, list_media_paths
from ..preview import PROXY_LIMIT_SECONDS, extract_thumbnail, make_comparison_proxy, make_proxy
from ..recipe import default_recipe, lut_status, recipe_fingerprint
from ..service import new_project
from ..version import APP_NAME
from .brand import load_app_icon
from .compare import CompareView
from .dialogs import AboutDialog, SettingsDialog, TextDialog
from .export_dialog import ExportDialog
from .grade import describe_clip, profile_label, safe_filters
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


class WrappedLabel(QLabel):
    """Keep wrapped form text tall enough after a panel or DPI resize."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self._fit_height()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self) -> None:
        height = self.heightForWidth(max(1, self.width()))
        if height >= 0 and self.minimumHeight() != height:
            self.setMinimumHeight(height)


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
        self._finalizers: dict[int, object] = {}
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
        self._pending_edit: tuple[str, dict] | None = None
        self._selection_guard = False
        self._filled_id: str | None = None
        self._gyro_geometry: dict[str, dict] = {}
        self._gyro_token = 0
        self._geometry_users: dict[tuple[str, int], str] = {}
        self._metadata_token = 0
        self._metadata_cancel = None
        self._frame_token = 0
        self._frame_cancel = None
        self._paused_frame_target: float | None = None
        self.comparison_used_gyro = False
        self._recipe_timer = QTimer(self)
        self._recipe_timer.setSingleShot(True)
        self._recipe_timer.setInterval(200)
        self._recipe_timer.timeout.connect(self._commit_focused_recipe)
        for widget in (self.exposure, self.contrast, self.saturation, self.gyro_fov):
            widget.valueChanged.connect(self._schedule_recipe)
        self.profile.currentIndexChanged.connect(self._schedule_recipe)
        self.gyro_enabled.toggled.connect(self._schedule_recipe)
        self.gyro_strength.valueChanged.connect(self._schedule_recipe)
        self.gyro_horizon.valueChanged.connect(self._schedule_recipe)
        self.look_includes.toggled.connect(self._schedule_recipe)
        self.input_lut.editingFinished.connect(self._schedule_recipe)
        self.look_lut.editingFinished.connect(self._schedule_recipe)
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

            from PySide6.QtMultimedia import QVideoSink

            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.video = QVideoWidget()
            self.video.setObjectName("videoWidget")
            self.frame_sink = QVideoSink(self)
            self.player.setAudioOutput(self.audio_output)
            self.player.setVideoSink(self.frame_sink)
            self.frame_sink.videoFrameChanged.connect(self._on_compare_frame)
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
        self.right.setMinimumWidth(240)
        self.right.setMaximumWidth(360)
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
        self.browse_preview = FitLabel("No clip")
        self.browse_preview.setMinimumHeight(96)
        self.browse_preview.setMaximumHeight(160)
        layout.addWidget(self.browse_preview)
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
        self.meta_details.setMaximumHeight(160)
        layout.addWidget(self.meta_details)
        layout.addStretch(1)
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
        self.filmstrip.setWordWrap(True)
        layout.addWidget(self.filmstrip)
        self.preview_status = QLabel("")
        self.preview_status.setWordWrap(True)
        layout.addWidget(self.preview_status)
        return page

    def _build_corrections(self) -> QWidget:
        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.addTab(self._color_page(), "Color")
        self.inspector_tabs.addTab(self._stab_page(), "Gyro")
        self.inspector_tabs.addTab(self._meta_page(), "Meta")
        self.inspector_tabs.setUsesScrollButtons(True)
        return self.inspector_tabs

    def _scroll_form(self) -> tuple[QScrollArea, QFormLayout]:
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body = QWidget()
        body.setObjectName("inspectorBody")
        form = QFormLayout(body)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        page.setWidget(body)
        return page, form

    def _color_page(self) -> QWidget:
        page, form = self._scroll_form()
        self.profile_detected = WrappedLabel("No clip.")
        form.addRow(self.profile_detected)
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
        self.look_includes = QCheckBox("Look includes camera")
        self.look_includes.setToolTip("The look LUT already includes the camera transform, so automatic normalization stays off.")
        form.addRow("Look", self.look_includes)
        note = WrappedLabel(
            "Exposure adjusts scene light. An Input LUT replaces automatic camera conversion. "
            "A Look LUT is applied afterwards."
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
        return page

    def _stab_page(self) -> QWidget:
        page, form = self._scroll_form()
        self.gyro_enabled = QCheckBox("Stabilize with embedded gyro")
        form.addRow(self.gyro_enabled)
        self.gyro_strength = QSlider(Qt.Orientation.Horizontal)
        self.gyro_strength.setRange(0, 100)
        self.gyro_strength.setValue(50)
        self.gyro_strength_value = QLabel("50%")
        self.gyro_strength.valueChanged.connect(lambda value: self.gyro_strength_value.setText(f"{value}%"))
        strength_row = QHBoxLayout()
        strength_row.addWidget(self.gyro_strength, 1)
        strength_row.addWidget(self.gyro_strength_value)
        form.addRow("Strength", strength_row)
        self.gyro_fov = QDoubleSpinBox()
        self.gyro_fov.setRange(0.1, 3.0)
        self.gyro_fov.setSingleStep(0.05)
        self.gyro_fov.setDecimals(2)
        self.gyro_fov.setValue(1.0)
        form.addRow("Field of view", self.gyro_fov)
        self.gyro_horizon = QSlider(Qt.Orientation.Horizontal)
        self.gyro_horizon.setRange(0, 100)
        self.gyro_horizon_value = QLabel("0%")
        self.gyro_horizon.valueChanged.connect(lambda value: self.gyro_horizon_value.setText(f"{value}%"))
        horizon_row = QHBoxLayout()
        horizon_row.addWidget(self.gyro_horizon, 1)
        horizon_row.addWidget(self.gyro_horizon_value)
        form.addRow("Horizon lock", horizon_row)
        note = WrappedLabel(
            "Higher strength smooths more camera movement. Field of view above 1 keeps more of the frame. "
            "Horizon lock at 0% leaves the original horizon."
        )
        note.setWordWrap(True)
        form.addRow(note)
        self.gyro_preview_button = QPushButton("Update preview")
        self.gyro_preview_button.setToolTip("Render the current gyro geometry, then compare it with colour applied once.")
        self.gyro_preview_button.clicked.connect(self.update_stabilization_preview)
        form.addRow(self.gyro_preview_button)
        self.gyro_apply_button = QPushButton("Apply to selected")
        self.gyro_apply_button.setToolTip("Apply these stabilization settings to every selected clip. Keep each clip's colour and LUT settings.")
        self.gyro_apply_button.clicked.connect(self.apply_gyro_to_selected)
        form.addRow(self.gyro_apply_button)
        self.gyro_status = WrappedLabel("Stabilization preview has not been rendered.")
        self.gyro_status.setWordWrap(True)
        form.addRow(self.gyro_status)
        return page

    def _meta_page(self) -> QWidget:
        page, form = self._scroll_form()
        refresh = QPushButton("Refresh metadata")
        refresh.setToolTip("Re-read the selected clips and their sidecars. Recipes stay. Re-import does not do this.")
        refresh.clicked.connect(self.refresh_selected_metadata)
        form.addRow(refresh)
        self.metadata_cancel_button = QPushButton("Cancel refresh")
        self.metadata_cancel_button.setEnabled(False)
        self.metadata_cancel_button.clicked.connect(self.cancel_metadata_refresh)
        form.addRow(self.metadata_cancel_button)
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
        clear.clicked.connect(self._schedule_recipe)
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
        file_menu.addAction("Import folder and subfolders…", self.import_folder_deep)
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
        if hasattr(self, "places"):
            # View keeps the picture and the correction panel. Places stays on Browse.
            self.places.setVisible(index == 0)
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
        if not self._flush_pending_edit():
            return
        self._remember_folder(folder)
        self.places.show_tree(folder)
        self.import_paths([folder])

    def import_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, "Import clips", "", "Video (*.mp4 *.mov *.mxf *.mts *.m2ts)")
        if paths:
            self.import_paths(paths)

    def import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Import folder")
        if folder:
            if not self._flush_pending_edit():
                return
            self._remember_folder(folder)
            self.places.show_tree(folder)
            self.import_paths([folder])

    def import_folder_deep(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Import folder and subfolders")
        if folder:
            if not self._flush_pending_edit():
                return
            self._remember_folder(folder)
            self.places.show_tree(folder)
            self.import_paths([folder], deep=True)

    def _remember_folder(self, folder: str) -> None:
        folders = [item for item in self.state.get("folders") or [] if item != folder]
        folders.insert(0, folder)
        self.state["folders"] = folders[:8]
        self._save_preferences()
        self.places.set_lists(self.state.get("folders") or [], self.state.get("favorites") or [])

    def import_paths(self, paths: list[str], *, deep: bool = False) -> None:
        if not self._flush_pending_edit():
            return
        files: list[Path] = []
        missing: list[str] = []
        directories: list[Path] = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                directories.append(path)
            elif path.is_file():
                files.append(path.resolve())
            else:
                missing.append(raw)
        if directories:
            self._import_directories(directories, files, missing, deep=deep)
            return
        files.sort(key=lambda item: item.name.casefold())
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

    def _import_directories(self, directories: list[Path], files: list[Path], missing: list[str], *, deep: bool) -> None:
        """List a folder off the UI thread. One level unless the user asked for subfolders."""
        self._folder_token += 1
        token = self._folder_token
        if len(directories) == 1:
            self._browsed_root = directories[0].resolve()
        self._thumbs.cancel()
        self.statusBar().showMessage("Listing folder…")
        self._partial = {"added": [], "skipped": [], "token": token, "pending": []}
        self._import_timer.start()

        def work(cancel) -> dict:
            listed = list(files)
            local_missing = list(missing)
            for folder in directories:
                if cancel is not None and cancel.is_set() or self._folder_token != token:
                    raise Cancelled()
                try:
                    # Normal import stays in this folder. Deep import is the explicit subfolder action.
                    found = list_media_paths(folder) if deep else list_directory_media(folder)
                    listed.extend(found)
                except (OSError, CraftsError) as exc:
                    local_missing.append(f"{folder} ({exc})")
            listed.sort(key=lambda item: item.name.casefold())
            self._partial = {
                "added": [],
                "skipped": [{"path": item, "reason": "File was not found."} for item in local_missing],
                "token": token,
                "pending": listed,
            }
            added: list[dict] = []
            local_skipped = list(self._partial["skipped"])
            for path in listed:
                if cancel is not None and cancel.is_set() or self._folder_token != token:
                    raise Cancelled()
                try:
                    report = self.service.import_media(str(path), cancel=cancel)
                except Cancelled:
                    raise
                except CraftsError:
                    local_skipped.append({"path": str(path), "reason": "Unreadable media"})
                else:
                    added.extend(report.get("added") or [])
                    local_skipped.extend(report.get("skipped") or [])
                self._partial = {"added": list(added), "skipped": list(local_skipped), "token": token, "pending": listed}
            return {"added": added, "skipped": local_skipped, "missing": local_missing, "token": token}

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
        if added:
            self._thumbs.request(added, adjusted=True)

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
        shown.sort(key=lambda clip: str(clip.get("name") or "").casefold())
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
            badges.append(str(sidecar.get("status")).replace("_", " "))
        label = profile_label(str(profile.get("pipeline") or ""))
        if label not in {"Unknown", "Preserve"}:
            badges.append(label)
        elif profile.get("status") == "preserve":
            badges.append("Preserve")
        text = f"{float(duration or 0):.1f}s"
        if badges:
            text += "  " + " ".join(badges)
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
        self._thumbs.request(shown, adjusted=True)
        self._fill_focus()

    def _selection_changed(self) -> None:
        if self._selection_guard:
            return
        if self._pending_edit is not None:
            origin = self._pending_edit[0]
            if not self._flush_pending_edit():
                self._selection_guard = True
                try:
                    self.grid.select_only(origin)
                finally:
                    self._selection_guard = False
                self.statusBar().showMessage(self.last_error or "The edit was not saved.", 6000)
                return
        previous = self._filled_id
        self._update_count()
        current = self.focused_id()
        if current != previous:
            self._invalidate_viewer()
            if self.player is not None:
                self.player.setPosition(0)
        self._fill_focus()
        self._filled_id = current
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
        self.grid.select_only(clip_id)

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
            preview = self.grid._pixmaps.get(clip["id"])
            if isinstance(preview, QPixmap) and not preview.isNull():
                self.browse_preview.set_source(preview)
            self.meta_details.setPlainText(
                f"{clip.get('path')}\nsha256 {clip.get('sha256')}\n"
                f"gamma {sidecar.get('gamma_raw') or '-'}  primaries {sidecar.get('primaries_raw') or '-'}\n"
                f"tags {video.get('color_transfer')} / {video.get('color_primaries')} / {video.get('color_range')}"
            )
            self.profile_detected.setText(profile.get("message") or profile_label(str(profile.get("pipeline") or "")))
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
            smoothness = recipe.get("gyro_smoothness")
            self.gyro_strength.setValue(int(round(float(0.5 if smoothness is None else smoothness) * 100)))
            fov = recipe.get("gyro_fov")
            self.gyro_fov.setValue(float(1.0 if fov is None else fov))
            horizon = recipe.get("gyro_horizon")
            self.gyro_horizon.setValue(int(round(float(0 if horizon is None else horizon))))
            self.notes.setPlainText(clip.get("notes") or "")
            flag_index = self.flag.findData(clip.get("flag") or "")
            if flag_index >= 0:
                self.flag.setCurrentIndex(flag_index)
            self.trim_in.setValue(float(clip.get("trim_in") or 0))
            self.trim_out.setValue(float(clip.get("trim_out") or 0))
            self._notes_dirty = False
            self.filmstrip.setText(clip.get("name") or "")
            self._show_gyro_state(clip)
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
            "gyro_fov": self.gyro_fov.value(),
            "gyro_horizon": float(self.gyro_horizon.value()),
        }

    def _schedule_recipe(self, *_args) -> None:
        if self._filling:
            return
        clip_id = self.focused_id()
        if not clip_id:
            return
        # The snapshot is this clip, not whoever is focused when the timer fires.
        self._pending_edit = (clip_id, self.recipe_from_controls())
        self._recipe_timer.start()

    def _flush_pending_edit(self) -> bool:
        pending = self._pending_edit
        self._recipe_timer.stop()
        if pending is None:
            return True
        clip_id, recipe = pending
        try:
            updated = self.service.update_clip(clip_id, {"recipe": recipe})
        except CraftsError as exc:
            self.last_error = exc.message
            self.statusBar().showMessage(exc.message, 6000)
            return False
        self._pending_edit = None
        self.clips = self.service.clips()
        self._show_gyro_state(updated)
        return True

    def _commit_focused_recipe(self, preview: bool = True) -> bool:
        if not self._flush_pending_edit():
            return False
        if not preview:
            return True
        clip = self.focused_clip()
        if clip is None:
            return True
        if self.center.currentIndex() == 1:
            self.request_comparison()
        else:
            self._thumbs.request([clip], adjusted=True)
        return True

    def apply_recipe_to_selected(self) -> None:
        if not self._flush_pending_edit():
            return
        controls = self.recipe_from_controls()
        recipe = {key: value for key, value in controls.items() if not key.startswith("gyro_")}
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
        self.apply_recipe_to_selected()

    def apply_gyro_to_selected(self) -> None:
        if not self._flush_pending_edit():
            return
        controls = self.recipe_from_controls()
        recipe = {key: controls[key] for key in ("gyro_enabled", "gyro_smoothness", "gyro_fov", "gyro_horizon")}
        ids = self.selected_ids()
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
        self.statusBar().showMessage(f"Stabilization settings applied to {len(ids)} clip(s).", 5000)
        if self.center.currentIndex() == 1:
            self.request_comparison()

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
        if not self._commit_focused_recipe(preview=False) or not self.commit_editor():
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
        self.request_comparison()

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
        position = self.player.position() if self.player is not None else 0
        described = describe_clip(clip)
        pipeline = str(described["profile"].get("pipeline") or "")
        gyro_on = bool(described["recipe"].get("gyro_enabled"))
        geometry = self._current_geometry(clip) if gyro_on else None
        gyro_path = geometry
        owner = ("compare", token)
        if gyro_path:
            self._geometry_users[owner] = str(gyro_path)
        self.preview_status.setText(self._gyro_message(clip) if gyro_on and geometry is None else "Comparison preview is rendering.")

        def work(cancel):
            ffmpeg, _ffprobe = self.service._tools()
            directory = self.service.data_dir / "compare-src" / clip["id"]
            filters, files, message = safe_filters(clip, directory, adjusted=True, cache=self.service.data_dir / "colour-cache")
            if pipeline in {"slog3_sgamut3cine", "hlg_bt2020"} and not filters:
                raise CraftsError(message or "The processed comparison was not built.", code="colour_unresolved")
            right = Path(gyro_path) if gyro_on and gyro_path and Path(gyro_path).is_file() else None
            destination = self.service.data_dir / f"compare-{clip['id']}-{token}.mp4"
            make_comparison_proxy(
                ffmpeg=ffmpeg,
                source=Path(clip["path"]),
                destination=destination,
                colour_filter=filters,
                stage_files=files or None,
                right_source=right,
                cancel=cancel,
            )
            prime = destination.with_suffix(".png")
            from ..process import run_captured

            run_captured(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", "0", "-i", str(destination), "-frames:v", "1", str(prime)],
                timeout=60,
                cancel=cancel,
            )
            return {
                "path": str(destination),
                "prime": str(prime),
                "message": message,
                "gyro": bool(right),
                "pipeline": pipeline,
                "token": token,
            }

        def done(result: dict) -> None:
            if result.get("token") != self._compare_token or self.player is None:
                return
            prime = QPixmap(result.get("prime") or "")
            if not prime.isNull():
                self.compare.present_split(prime, position / 1000.0)
                self.still_original.set_source(self.compare._left)
                self.still_adjusted.set_source(self.compare._right)
            self._compare_path = result["path"]
            self.player.setVideoSink(self.frame_sink)
            self.player.setSource(QUrl.fromLocalFile(result["path"]))
            self.player.setPosition(position)
            self.still_path = result["path"]
            self.playback_result = {"kind": "comparison", "proxy_path": result["path"], "limited": False}
            self.comparison_used_gyro = bool(result.get("gyro"))
            if gyro_on and not result.get("gyro"):
                note = self._gyro_message(clip)
            elif result.get("gyro"):
                note = "Both sides follow one clock. Colour and the look run once on the current gyro geometry."
            else:
                note = "Both sides follow one clock."
            self.preview_status.setText(" ".join(part for part in (result.get("message"), note) if part))
            self.viewer_stack.setCurrentIndex(0)
            self._retire_owned_previews()

        self._start(work, done, key="compare", on_finished=lambda: self._release_geometry(owner))

    def _current_geometry(self, clip: dict) -> str | None:
        record = self._gyro_geometry.get(clip["id"]) or {}
        path = str(record.get("path") or "")
        if not path or not Path(path).is_file():
            return None
        from ..prepare import gyro_geometry_key

        described = describe_clip(clip)
        if record.get("key") != gyro_geometry_key(clip, described["recipe"]):
            return None
        return path

    def _gyro_message(self, clip: dict) -> str:
        from ..prepare import gyro_geometry_key

        described = describe_clip(clip)
        recipe = described["recipe"]
        if not recipe.get("gyro_enabled"):
            return "Gyro is off."
        record = self._gyro_geometry.get(clip["id"]) or {}
        key = gyro_geometry_key(clip, recipe)
        if record.get("path") and Path(str(record["path"])).is_file() and record.get("key") != key:
            return (
                "Stabilization is stale. Strength, field of view, horizon, or rotation changed. "
                "The previous file is not the current geometry."
            )
        if record.get("key") == key and record.get("path") and Path(str(record["path"])).is_file():
            return "Stabilization geometry is current."
        return "Stabilization preview has not been rendered."

    def _show_gyro_state(self, clip: dict | None) -> None:
        if clip is None or not hasattr(self, "gyro_status"):
            return
        self.gyro_status.setText(self._gyro_message(clip))

    def refresh_selected_metadata(self) -> None:
        if not self._flush_pending_edit():
            return
        ids = self.selected_ids() or ([self.focused_id()] if self.focused_id() else [])
        if not ids:
            self.statusBar().showMessage("Select at least one clip.", 5000)
            return
        self._metadata_token += 1
        token, generation = self._metadata_token, self._project_gen
        self.statusBar().showMessage(f"Refreshing metadata for {len(ids)} clip(s)…")
        self.metadata_cancel_button.setEnabled(True)

        def done(report):
            if generation != self._project_gen or token != self._metadata_token:
                return
            self.reload_all()
            messages = []
            for item in report.get("clips") or []:
                if item.get("status") == "missing":
                    messages.append("A selected source is missing. Its recipe was kept.")
                messages.extend(str(issue) for issue in (item.get("assessment") or {}).get("issues") or [])
            self.statusBar().showMessage(" ".join(messages) or "Metadata refreshed. Recipes were kept.", 8000)

        def finished():
            if token == self._metadata_token:
                if self._metadata_cancel is not None and self._metadata_cancel.is_set():
                    self.statusBar().showMessage("Metadata refresh cancelled.", 5000)
                self._metadata_cancel = None
                self.metadata_cancel_button.setEnabled(False)

        self._metadata_cancel = self._start(
            lambda cancel: self.service.refresh_metadata(ids, cancel=cancel), done,
            key="metadata", on_finished=finished,
        )

    def cancel_metadata_refresh(self) -> None:
        if self._metadata_cancel is not None:
            self._metadata_cancel.set()
            self.statusBar().showMessage("Stopping metadata refresh…")

    def _retire_owned_previews(self) -> None:
        """Drop old owned previews. Never the file the player has open, and never a user export."""
        active = Path(self._compare_path).resolve() if getattr(self, "_compare_path", "") else None
        import re

        held = {Path(item).resolve() for item in self._geometry_users.values() if item}
        current = self._gyro_geometry.get(self.focused_id()) or {}
        if current.get("path"):
            held.add(Path(str(current["path"])).resolve())
        geometry_dir = self.service.data_dir / "gyro-geometry"
        if geometry_dir.is_dir():
            files = sorted(
                (path for path in geometry_dir.glob("*.mov") if re.fullmatch(r"[0-9a-f]{24}-[0-9a-f]{8}\.mov", path.name)),
                key=lambda path: path.stat().st_mtime, reverse=True,
            )
            spare = [path for path in files if path.resolve() not in held]
            remaining = max(0, 4 - sum(path.resolve() in held for path in files))
            for path in spare[remaining:]:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue  # A player/tool may still be releasing it. Retry next time.
            self._gyro_geometry = {
                key: record for key, record in self._gyro_geometry.items()
                if record.get("path") and Path(str(record["path"])).is_file()
            }
        compares = sorted(self.service.data_dir.glob("compare-*.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in compares[6:]:
            if active is not None and path.resolve() == active:
                continue
            try:
                path.unlink(missing_ok=True)
                path.with_suffix(".png").unlink(missing_ok=True)
            except OSError:
                continue

    def _release_geometry(self, owner: tuple[str, int]) -> None:
        self._geometry_users.pop(owner, None)
        self._retire_owned_previews()

    def update_stabilization_preview(self) -> None:
        if not self._flush_pending_edit():
            return
        clip = self.focused_clip()
        if clip is None:
            return
        if not self.gyro_enabled.isChecked():
            self.gyro_status.setText("Gyro is off, so there is no stabilization preview to render.")
            return
        from ..prepare import gyro_geometry_key

        described = describe_clip(clip)
        recipe = dict(described["recipe"])
        recipe["gyro_enabled"] = True
        key = gyro_geometry_key(clip, recipe)
        self._gyro_token += 1
        token = self._gyro_token
        clip_id = clip["id"]
        generation = self._project_gen
        import uuid

        directory = self.service.data_dir / "gyro-geometry"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / f"{key}-{uuid.uuid4().hex[:8]}.mov"
        owner = ("gyro", token)
        self._geometry_users[owner] = str(output)
        self.gyro_status.setText("Rendering gyro geometry in the original colour space. Colour is not applied in this stage.")

        def work(cancel):
            from ..gyroflow_cli import run_gyroflow
            from ..probe import display_dimensions, normalize_rotation

            _ffmpeg, ffprobe = self.service._tools()
            fresh = next(item for item in self.service.clips() if item["id"] == clip_id)
            video = ((fresh.get("probe") or {}).get("video") or [{}])[0]
            width, height = display_dimensions(video)
            rotation = normalize_rotation(video.get("rotation"))
            try:
                result = run_gyroflow(
                    settings=self.service.settings,
                    media=Path(fresh["path"]),
                    project=None,
                    gyro_file=None,
                    preset=None,
                    output=output,
                    ffprobe=ffprobe,
                    cancel=cancel,
                    on_progress=lambda _value: None,
                    automatic=True,
                    output_settings={
                        "format": "prores_422",
                        "audio": False,
                        "width": width,
                        "height": height,
                        "rotation": rotation,
                    },
                    stabilization={
                        "smoothness": recipe["gyro_smoothness"],
                        "fov": recipe["gyro_fov"],
                        "horizon_lock_amount": recipe["gyro_horizon"],
                    },
                )
            except CraftsError as exc:
                return {"error": exc.message, "token": token, "clip_id": clip_id}
            return {"path": str(result.get("output_path") or output), "key": key, "token": token, "clip_id": clip_id}

        def done(result: dict) -> None:
            if result.get("token") != self._gyro_token or generation != self._project_gen:
                return
            if result.get("error"):
                if self.focused_id() == result["clip_id"]:
                    self.gyro_status.setText(str(result["error"]))
                return
            path = str(result.get("path") or "")
            self._gyro_geometry[result["clip_id"]] = {"key": result.get("key"), "path": path}
            if self.focused_id() == result["clip_id"]:
                self.playback_result = {"kind": "gyro-geometry", "proxy_path": path, "key": result.get("key")}
                self._show_gyro_state(self.focused_clip())
                self.request_comparison()
            self._retire_owned_previews()

        self._start(work, done, key="gyro-preview", on_finished=lambda: self._release_geometry(owner))

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
        self._frame_token = getattr(self, "_frame_token", 0) + 1
        self._paused_frame_target = None
        if getattr(self, "_frame_cancel", None) is not None:
            self._frame_cancel.set()
        self._proxy_fallback = False
        self.still_path = ""
        self.playback_result = None
        self.still_original.set_source(QPixmap())
        self.still_adjusted.set_source(QPixmap())
        self.compare.set_pair(QPixmap(), QPixmap(), 0.0)
        self.compare._paired = False
        self.compare.frames_presented = 0
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        if hasattr(self, "scrub"):
            self.scrub.setRange(0, 0)
            self.time_label.setText("0:00.0 / 0:00.0")

    def _compare_mode_changed(self) -> None:
        mode = str(self.compare_mode.currentData() or "split")
        self.compare.set_mode(mode if mode in {"split", "wipe", "original", "processed"} else "split")
        self.viewer_stack.setCurrentIndex(0)

    def _on_compare_frame(self, frame) -> None:
        image = frame.toImage() if hasattr(frame, "toImage") else None
        if image is None or image.isNull() or self.player is None:
            return
        if getattr(self, "_paused_frame_target", None) is not None:
            return
        timestamp = frame.startTime() if hasattr(frame, "startTime") else -1
        seconds = timestamp / 1_000_000 if timestamp >= 0 else self.player.position() / 1000.0
        self.compare.present_split(image, seconds)
        self.still_original.set_source(self.compare._left)
        self.still_adjusted.set_source(self.compare._right)
        self.still_path = getattr(self, "_compare_path", "") or self.still_path

    def _frame_ms(self) -> int:
        clip = self.focused_clip()
        video = (((clip or {}).get("probe") or {}).get("video") or [{}])[0] or {}
        num = int(video.get("frame_rate_num") or 25)
        den = int(video.get("frame_rate_den") or 1)
        if num <= 0:
            return 40
        return max(1, int(round(1000 * den / num)))

    def play(self) -> None:
        if self.player is not None:
            self._frame_token += 1
            self._paused_frame_target = None
            if self._frame_cancel is not None:
                self._frame_cancel.set()
            self.player.play()

    def pause(self) -> None:
        if self.player is not None:
            self.player.pause()

    def step_frame(self, direction: int) -> None:
        if self.player is None:
            return
        import math
        import re
        import uuid

        path = getattr(self, "_compare_path", "")
        if not path or not Path(path).is_file():
            return
        clip = self.focused_clip() or {}
        video = ((clip.get("probe") or {}).get("video") or [{}])[0] or {}
        num = int(video.get("frame_rate_num") or 25)
        den = max(1, int(video.get("frame_rate_den") or 1))
        fps = num / den if num > 0 else 25.0
        current = self._paused_frame_target if self._paused_frame_target is not None else self.compare.seconds
        index = max(0, int(round(current * fps)) + (1 if direction > 0 else -1))
        duration = self.player.duration() / 1000.0
        if duration > 0:
            index = min(index, max(0, math.ceil(duration * fps) - 1))
        target = index / fps
        self.player.pause()
        self._paused_frame_target = target
        self.player.setPosition(round(target * 1000))
        self._frame_token += 1
        token, generation = self._frame_token, self._project_gen
        image_path = self.service.data_dir / f".frame-step-{uuid.uuid4().hex}.png"

        def work(cancel):
            from ..process import run_captured

            ffmpeg, _ffprobe = self.service._tools()
            code, _out, error = run_captured(
                [ffmpeg, "-y", "-hide_banner", "-nostdin", "-loglevel", "info", "-copyts",
                 "-ss", f"{target:.9f}", "-i", path, "-vf", "showinfo", "-frames:v", "1",
                 "-fps_mode", "passthrough", str(image_path)], timeout=30, cancel=cancel,
            )
            timestamp = re.search(r"pts_time:([0-9.]+)", error)
            if code or not image_path.is_file() or timestamp is None:
                raise CraftsError("The requested frame could not be decoded.", code="preview_frame")
            return float(timestamp.group(1))

        def done(seconds):
            if token != self._frame_token or generation != self._project_gen or path != self._compare_path:
                return
            image = QPixmap(str(image_path))
            if image.isNull():
                return
            self.compare.present_split(image, seconds)
            self.still_original.set_source(self.compare._left)
            self.still_adjusted.set_source(self.compare._right)
            self._paused_frame_target = seconds
            self.playback_result = {"kind": "paused-frame", "proxy_path": path, "frame_time_seconds": seconds}

        self._frame_cancel = self._start(work, done, key="frame-step", on_finished=lambda: image_path.unlink(missing_ok=True))

    def _seek(self, position: int) -> None:
        if self.player is not None:
            self._frame_token += 1
            self._paused_frame_target = None
            if self._frame_cancel is not None:
                self._frame_cancel.set()
            self.player.setPosition(position)

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
            self._schedule_recipe()

    def is_unsaved(self) -> bool:
        if self._notes_dirty or self._pending_edit is not None:
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
        if not self._prepare_to_leave():
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
        if not self._prepare_to_leave():
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
        self._gyro_token += 1
        self._metadata_token += 1
        self._metadata_cancel = None
        self.metadata_cancel_button.setEnabled(False)
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
        self._gyro_geometry.clear()
        self._geometry_users.clear()
        self._retire_owned_previews()
        self.reload_all()

    def save_project_interactive(self) -> bool:
        if not self._flush_pending_edit() or not self.commit_editor():
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
        if not self._flush_pending_edit() or not self.commit_editor():
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
        if not self._prepare_to_leave(discard=self.force_close, allow_discard=True):
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
        if not self._runner.settle(timeout=8) and not self.force_close:
            self.statusBar().showMessage("A background tool is still stopping. The window stays open.", 8000)
            event.ignore()
            return
        self._refresh_timer.stop()
        self._import_timer.stop()
        if self.player is not None:
            self.player.stop()
            self.player.setVideoSink(None)
            self.player.setSource(QUrl())
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()
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

    def _discard_editor(self) -> None:
        self._recipe_timer.stop()
        self._pending_edit = None
        self._pending_markers = None
        self._notes_dirty = False
        self._fill_focus()

    def _prompt_discard_edit(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setText("This edit could not be saved.")
        box.setInformativeText((self.last_error or "Check the current settings.") + "\nDiscard this edit and continue?")
        discard = box.addButton("Discard edit", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is discard

    def _prepare_to_leave(self, *, discard: bool = False, allow_discard: bool = False) -> bool:
        if discard:
            self._discard_editor()
            return True
        if self._flush_pending_edit() and self.commit_editor():
            return True
        if not allow_discard or not self._prompt_discard_edit():
            return False
        self._discard_editor()
        return True

    def _start(self, fn, on_ok, *, key: str | None = None, on_finished=None):
        token = self._token
        self._token += 1
        self._inflight += 1
        self._callbacks[token] = on_ok
        if on_finished is not None:
            self._finalizers[token] = on_finished
        return self._runner.submit(token, fn, key)

    def _finish_task(self, token: int) -> None:
        finalizer = self._finalizers.pop(token, None)
        try:
            if finalizer is not None:
                finalizer()
        finally:
            self._note_finished()

    def _task_ok(self, token: int, value) -> None:
        callback = self._callbacks.pop(token, None)
        try:
            if callback and value is not None:
                callback(value)
        finally:
            self._finish_task(token)

    def _task_fail(self, token: int, message: str) -> None:
        self._callbacks.pop(token, None)
        if message != "cancelled":
            self.last_error = message
            self.statusBar().showMessage(message, 10000)
        self._finish_task(token)

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
