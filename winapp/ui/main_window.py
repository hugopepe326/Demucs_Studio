"""Ventana principal de Demucs Studio para Windows."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from demucs_studio import config as app_config
from demucs_studio.engine import (
    EngineStatus,
    detect_engine,
    estimate_seconds,
    ffprobe_path,
    plan_summary,
    request_from_config,
)
from demucs_studio.formats import (
    AUDIO_EXTENSIONS,
    CLIP_MODES,
    CLIP_MODE_LABELS,
    FORMATS,
    FormatOptions,
    format_info,
    sanitize_options,
)
from demucs_studio.jobs import (
    CANCELLED,
    DONE,
    ERROR,
    PENDING,
    RUNNING,
    Job,
    QueueManager,
    apply_calibration,
)
from demucs_studio.presets import (
    CUSTOM_PRESET,
    CUSTOM_PRESET_LABEL,
    MODELS,
    PRESETS,
    PRESETS_BY_KEY,
)
from demucs_studio.util import collect_audio_files, human_duration
from winapp import bootstrap
from winapp.ui.setup_dialog import SetupDialog
from winapp.ui.widgets import Card, DropZone, HLine, LogView, Pill, file_pixmap

STATUS_COLORS = {
    PENDING: "#8d8d98",
    RUNNING: "#60a5fa",
    DONE: "#4ade80",
    ERROR: "#f87171",
    CANCELLED: "#d29922",
}

SEGMENT_CHOICES = (0, 7, 8, 10, 15, 20, 30, 44)


class Bridge(QObject):
    """Señales que cruzan del hilo del motor al hilo de Qt."""

    jobUpdated = Signal(object)
    queueChanged = Signal()
    logLine = Signal(str)
    engineDetected = Signal(object)
    jobCompleted = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = app_config.Config.load()
        app_config.ensure_dirs()

        self.engine = EngineStatus()
        self._detected = False
        self._detecting = False
        self._updating = False
        self._row_by_id: dict[int, int] = {}
        self._dark_title_done = False

        self.setWindowTitle("Demucs Studio")
        self.resize(self.config.window_width, self.config.window_height)
        self.setMinimumSize(1020, 720)

        icon_path = self._resource("icon.png")
        if icon_path is not None:
            self.setWindowIcon(QIcon(str(icon_path)))

        self.bridge = Bridge(self)
        self._wire_bridge()
        self._build_ui()
        self._build_queue()
        self._load_controls()
        self._sync_table()
        self._update_engine_ui()
        self._update_actions()
        self._update_estimate()

        QTimer.singleShot(40, self.detect_engine_async)

    # ------------------------------------------------------------------
    # Recursos
    # ------------------------------------------------------------------

    @staticmethod
    def _resource(name: str) -> Path | None:
        base = app_config.RESOURCE_DIR
        for candidate in (base / name, base / "packaging" / name):
            if candidate.is_file():
                return candidate
        return None

    def _wire_bridge(self) -> None:
        self.bridge.jobUpdated.connect(self._on_job_updated)
        self.bridge.queueChanged.connect(self._on_queue_changed)
        self.bridge.logLine.connect(self._log)
        self.bridge.engineDetected.connect(self._on_engine_detected)
        self.bridge.jobCompleted.connect(self._on_job_completed)

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        root.addLayout(self._build_header())

        self.drop_zone = DropZone()
        self.drop_zone.pathsDropped.connect(self._add_paths)
        self.drop_zone.browseRequested.connect(self._choose_files)
        self.drop_zone.folderRequested.connect(self._choose_folder)
        root.addWidget(self.drop_zone)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_queue_card())
        splitter.addWidget(self._build_settings_card())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([640, 440])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_footer())

        self.log_view = LogView()
        self.log_view.setVisible(False)
        self.log_view.setMaximumHeight(150)
        root.addWidget(self.log_view)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(12)

        icon_path = self._resource("icon.png")
        if icon_path is not None:
            logo = QLabel()
            pixmap = file_pixmap(icon_path, 42)
            if pixmap is not None:
                logo.setPixmap(pixmap)
            logo.setFixedSize(42, 42)
            header.addWidget(logo)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Demucs Studio")
        title.setObjectName("AppTitle")
        subtitle = QLabel("Separación de fuentes · Windows")
        subtitle.setObjectName("AppSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)

        self.engine_pill = Pill("Detectando motor…", "muted")
        header.addWidget(self.engine_pill)

        self.install_button = QPushButton("Instalar motor")
        self.install_button.setCursor(Qt.PointingHandCursor)
        self.install_button.clicked.connect(self._install_engine)
        header.addWidget(self.install_button)

        self.menu_button = QToolButton()
        self.menu_button.setText("⋯")
        self.menu_button.setToolTip("Más opciones")
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        self.menu_button.setMenu(self._build_menu())
        header.addWidget(self.menu_button)
        return header

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        install = menu.addAction("Instalar / reparar motor")
        install.triggered.connect(self._install_engine)
        menu.addSeparator()
        data_action = menu.addAction("Abrir carpeta de datos")
        data_action.triggered.connect(self._open_data_dir)
        models_action = menu.addAction("Abrir carpeta de modelos")
        models_action.triggered.connect(self._open_models_dir)
        output_action = menu.addAction("Abrir carpeta de salida")
        output_action.triggered.connect(self._open_output)
        menu.addSeparator()
        reset = menu.addAction("Restaurar ajustes")
        reset.triggered.connect(self._reset_settings)
        about = menu.addAction("Acerca de Demucs Studio")
        about.triggered.connect(self._about)
        return menu

    def _build_queue_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("Cola")
        title.setObjectName("SectionTitle")
        self.queue_summary = QLabel("Sin canciones")
        self.queue_summary.setObjectName("Muted")
        head.addWidget(title)
        head.addWidget(self.queue_summary)
        head.addStretch(1)
        self.clear_done_button = QPushButton("Vaciar terminados")
        self.clear_done_button.setObjectName("Ghost")
        self.clear_done_button.setCursor(Qt.PointingHandCursor)
        self.clear_done_button.clicked.connect(self._clear_finished)
        head.addWidget(self.clear_done_button)
        layout.addLayout(head)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Canción", "Duración", "Estado", "Avance", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.doubleClicked.connect(self._open_row)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column, width in ((1, 82), (2, 190), (3, 130), (4, 36)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.table.setColumnWidth(column, width)

        self.queue_stack = QStackedWidget()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_icon = QLabel("♫")
        empty_icon.setObjectName("DropIcon")
        empty_icon.setAlignment(Qt.AlignCenter)
        empty_title = QLabel("La cola está vacía")
        empty_title.setObjectName("EmptyTitle")
        empty_title.setAlignment(Qt.AlignCenter)
        empty_text = QLabel("Arrastra canciones o usa «Elegir archivos».")
        empty_text.setObjectName("Muted")
        empty_text.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_icon)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_text)

        table_page = QWidget()
        table_layout = QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.table)

        self.queue_stack.addWidget(empty)
        self.queue_stack.addWidget(table_page)
        layout.addWidget(self.queue_stack, 1)
        return card

    def _build_settings_card(self) -> QScrollArea:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = QLabel("Ajustes de separación")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        self.preset_combo = QComboBox()
        for preset in PRESETS:
            self.preset_combo.addItem(preset.label, preset.key)
        self.preset_combo.addItem(CUSTOM_PRESET_LABEL, CUSTOM_PRESET)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        layout.addWidget(self._field_row("Preset", self.preset_combo))

        self.model_combo = QComboBox()
        for model in MODELS:
            self.model_combo.addItem(model.label, model.name)
        self.model_combo.currentIndexChanged.connect(self._on_manual_change)
        layout.addWidget(self._field_row("Modelo", self.model_combo))

        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self._on_setting_change)
        layout.addWidget(self._field_row("Dispositivo", self.device_combo))

        layout.addWidget(HLine())

        self.format_combo = QComboBox()
        for fmt in FORMATS:
            self.format_combo.addItem(fmt.label, fmt.key)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        layout.addWidget(self._field_row("Formato", self.format_combo))

        self.bit_depth_combo = QComboBox()
        self.bit_depth_combo.currentIndexChanged.connect(self._on_setting_change)
        self.bit_depth_row = self._field_row("Profundidad", self.bit_depth_combo)
        layout.addWidget(self.bit_depth_row)

        self.float_check = QCheckBox("WAV de 32 bits en coma flotante")
        self.float_check.toggled.connect(self._on_setting_change)
        self.float_row = QWidget()
        float_layout = QVBoxLayout(self.float_row)
        float_layout.setContentsMargins(100, 0, 0, 0)
        float_layout.addWidget(self.float_check)
        layout.addWidget(self.float_row)

        self.bitrate_combo = QComboBox()
        self.bitrate_combo.currentIndexChanged.connect(self._on_setting_change)
        self.bitrate_row = self._field_row("Bitrate", self.bitrate_combo)
        layout.addWidget(self.bitrate_row)

        self.mp3_preset_combo = QComboBox()
        self.mp3_preset_combo.currentIndexChanged.connect(self._on_setting_change)
        self.mp3_preset_row = self._field_row("Preset MP3", self.mp3_preset_combo)
        layout.addWidget(self.mp3_preset_row)

        self.vbr_combo = QComboBox()
        self.vbr_combo.currentIndexChanged.connect(self._on_setting_change)
        self.vbr_row = self._field_row("Calidad VBR", self.vbr_combo)
        layout.addWidget(self.vbr_row)

        self.clip_combo = QComboBox()
        for key in CLIP_MODES:
            self.clip_combo.addItem(CLIP_MODE_LABELS[key], key)
        self.clip_combo.currentIndexChanged.connect(self._on_setting_change)
        layout.addWidget(self._field_row("Clipping", self.clip_combo))

        self.format_note = QLabel("")
        self.format_note.setObjectName("FormatNote")
        self.format_note.setWordWrap(True)
        layout.addWidget(self.format_note)

        layout.addWidget(HLine())

        output_widget = QWidget()
        output_layout = QHBoxLayout(output_widget)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(6)
        self.output_edit = QLineEdit()
        output_layout.addWidget(self.output_edit, 1)
        browse_button = QPushButton("…")
        browse_button.setFixedWidth(34)
        browse_button.clicked.connect(self._choose_output_dir)
        output_layout.addWidget(browse_button)
        open_button = QPushButton("Abrir")
        open_button.clicked.connect(self._open_output)
        output_layout.addWidget(open_button)
        layout.addWidget(self._field_row("Carpeta", output_widget))

        self.subfolder_check = QCheckBox("Crear una subcarpeta por canción")
        self.subfolder_check.toggled.connect(self._on_setting_change)
        layout.addWidget(self._indent(self.subfolder_check))

        self.open_when_done_check = QCheckBox("Abrir la carpeta al terminar")
        self.open_when_done_check.toggled.connect(self._on_setting_change)
        layout.addWidget(self._indent(self.open_when_done_check))

        self.naming_combo = QComboBox()
        self.naming_combo.addItem("Estándar (drums, bass, vocals, other)", "standard")
        self.naming_combo.addItem("Español (batería, bajo, voces, otros)", "spanish")
        self.naming_combo.currentIndexChanged.connect(self._on_setting_change)
        layout.addWidget(self._field_row("Nombres", self.naming_combo))

        layout.addWidget(HLine())

        advanced = QLabel("Avanzado")
        advanced.setObjectName("SectionTitle")
        layout.addWidget(advanced)

        self.shifts_spin = QSpinBox()
        self.shifts_spin.setRange(0, 10)
        self.shifts_spin.setMaximumWidth(110)
        self.shifts_spin.valueChanged.connect(self._on_manual_change)
        layout.addWidget(self._field_row("Shifts", self.shifts_spin))

        self.overlap_spin = QSpinBox()
        self.overlap_spin.setRange(0, 90)
        self.overlap_spin.setSuffix(" %")
        self.overlap_spin.setMaximumWidth(110)
        self.overlap_spin.valueChanged.connect(self._on_manual_change)
        layout.addWidget(self._field_row("Solape", self.overlap_spin))

        self.segment_combo = QComboBox()
        for seconds in SEGMENT_CHOICES:
            label = "Automático" if seconds == 0 else f"{seconds} s"
            self.segment_combo.addItem(label, seconds)
        self.segment_combo.currentIndexChanged.connect(self._on_manual_change)
        layout.addWidget(self._field_row("Segmento", self.segment_combo))

        self.jobs_spin = QSpinBox()
        self.jobs_spin.setRange(0, 8)
        self.jobs_spin.setSpecialValueText("Automático")
        self.jobs_spin.setMaximumWidth(110)
        self.jobs_spin.valueChanged.connect(self._on_manual_change)
        layout.addWidget(self._field_row("Jobs", self.jobs_spin))

        self.split_check = QCheckBox("Dividir la canción en segmentos (split)")
        self.split_check.toggled.connect(self._on_manual_change)
        layout.addWidget(self._indent(self.split_check))

        layout.addWidget(HLine())

        self.estimate_label = QLabel("")
        self.estimate_label.setObjectName("Estimate")
        self.estimate_label.setWordWrap(True)
        layout.addWidget(self.estimate_label)

        reset_button = QPushButton("Restaurar ajustes")
        reset_button.setObjectName("Ghost")
        reset_button.clicked.connect(self._reset_settings)
        layout.addWidget(reset_button)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(card)
        scroll.setMinimumWidth(380)
        return scroll

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.overall = QProgressBar()
        self.overall.setRange(0, 100)
        self.overall.setValue(0)
        self.overall.setTextVisible(True)
        self.overall.setFixedHeight(18)
        self.overall.setMinimumWidth(160)
        self.overall_label = QLabel("0 de 0")
        self.overall_label.setObjectName("Muted")

        self.log_toggle = QPushButton("Registro")
        self.log_toggle.setObjectName("Ghost")
        self.log_toggle.setCheckable(True)

        self.open_button = QPushButton("Abrir salida")
        self.open_button.setObjectName("Ghost")
        self.open_button.clicked.connect(self._open_output)

        self.cancel_button = QPushButton("Cancelar todo")
        self.cancel_button.clicked.connect(self._cancel_all)

        self.start_button = QPushButton("Separar")
        self.start_button.setObjectName("Primary")
        self.start_button.setMinimumHeight(38)
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.clicked.connect(self._start)

        layout.addWidget(self.overall, 1)
        layout.addWidget(self.overall_label)
        layout.addSpacing(6)
        layout.addWidget(self.log_toggle)
        layout.addWidget(self.open_button)
        layout.addWidget(self.cancel_button)
        layout.addWidget(self.start_button)
        return footer

    @staticmethod
    def _field_row(label: str, widget: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        text = QLabel(label)
        text.setObjectName("FieldLabel")
        text.setFixedWidth(92)
        text.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(text)
        layout.addWidget(widget, 1)
        return row

    @staticmethod
    def _indent(widget: QWidget) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(100, 0, 0, 0)
        layout.addWidget(widget)
        return holder

    # ------------------------------------------------------------------
    # Cola
    # ------------------------------------------------------------------

    def _build_queue(self) -> None:
        self.queue = QueueManager(
            engine_provider=lambda: self.engine,
            on_job=lambda job: self.bridge.jobUpdated.emit(job),
            on_queue=lambda: self.bridge.queueChanged.emit(),
            on_log=lambda line: self.bridge.logLine.emit(line),
            on_completed=lambda job: self.bridge.jobCompleted.emit(job),
            ffprobe=ffprobe_path(),
        )
        self.log_toggle.toggled.connect(self.log_view.setVisible)

    def _sync_table(self) -> None:
        jobs = self.queue.jobs
        self.table.setRowCount(0)
        self._row_by_id.clear()
        self.table.setRowCount(len(jobs))

        for row, job in enumerate(jobs):
            self._row_by_id[job.identifier] = row

            name = QTableWidgetItem(job.name)
            name.setData(Qt.UserRole, job.identifier)
            name.setToolTip(job.source)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem("--:--"))
            self.table.setItem(row, 2, QTableWidgetItem(""))
            self.table.setItem(row, 3, QTableWidgetItem(""))

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFixedHeight(18)
            bar.setTextVisible(True)
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(6, 0, 8, 0)
            holder_layout.addWidget(bar)
            self.table.setCellWidget(row, 3, holder)

            remove = QPushButton("✕")
            remove.setObjectName("RowButton")
            remove.setFixedSize(24, 24)
            remove.setToolTip("Quitar de la cola")
            remove.clicked.connect(
                lambda _checked=False, item=job: self.queue.remove(item)
            )
            self.table.setCellWidget(row, 4, remove)

            self._update_row(job)

        self.queue_stack.setCurrentIndex(1 if jobs else 0)
        counts = self.queue.counts()
        total = len(jobs)
        if total:
            if counts[RUNNING]:
                text = f"{total} canciones · {counts[RUNNING]} en proceso"
            elif counts[PENDING]:
                text = f"{total} canciones · {counts[PENDING]} en espera"
            else:
                text = f"{total} canciones · {counts[DONE]} completadas"
        else:
            text = "Sin canciones"
        self.queue_summary.setText(text)
        self.clear_done_button.setEnabled(
            counts[DONE] + counts[ERROR] + counts[CANCELLED] > 0
        )
        self._update_actions()
        self._update_estimate()

    def _update_row(self, job: Job) -> None:
        row = self._row_by_id.get(job.identifier)
        if row is None or row >= self.table.rowCount():
            return

        duration_item = self.table.item(row, 1)
        if duration_item is not None:
            duration_item.setText(
                human_duration(job.duration) if job.duration else "--:--"
            )

        status_item = self.table.item(row, 2)
        if status_item is not None:
            status_item.setText(job.summary())
            status_item.setForeground(QColor(STATUS_COLORS.get(job.status, "#8d8d98")))
            status_item.setToolTip(job.hint or job.error or job.summary())

        holder = self.table.cellWidget(row, 3)
        if holder is not None:
            bar = holder.findChild(QProgressBar)
            if bar is not None:
                bar.setValue(max(0, min(100, int(round(job.percent)))))

    def _on_job_updated(self, job: Job) -> None:
        self._update_row(job)
        self._update_actions()
        self._update_estimate()

    def _on_queue_changed(self) -> None:
        self._sync_table()

    def _on_job_completed(self, job: Job) -> None:
        from demucs_studio.engine import ffmpeg_path

        if job.status == DONE:
            device = job.request.effective_device(self.engine)
            self.config.calibration = apply_calibration(
                self.config.calibration, job, device
            )
            self.config.save()
            self._log(
                f"Completado: {job.name} · {len(job.outputs)} pistas · "
                f"{human_duration(job.elapsed)}"
            )
            if self.config.open_when_done and job.outdir:
                QDesktopServices.openUrl(QUrl.fromLocalFile(job.outdir))
        elif job.status == ERROR:
            self._log(f"Error en {job.name}: {job.error}")
        self._update_actions()

    def _clear_finished(self) -> None:
        self.queue.clear_finished()

    def _table_menu(self, position) -> None:
        row = self.table.rowAt(position.y())
        jobs = self.queue.jobs
        if row < 0 or row >= len(jobs):
            return
        job = jobs[row]

        menu = QMenu(self)
        if job.outdir:
            open_action = menu.addAction("Abrir carpeta de salida")
            open_action.triggered.connect(lambda: self._open_job(job))
        if job.status in (PENDING, RUNNING):
            cancel_action = menu.addAction("Cancelar")
            cancel_action.triggered.connect(lambda: self.queue.cancel(job))
        remove_action = menu.addAction("Quitar de la cola")
        remove_action.triggered.connect(lambda: self.queue.remove(job))
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _open_row(self, index) -> None:
        jobs = self.queue.jobs
        if 0 <= index.row() < len(jobs):
            self._open_job(jobs[index.row()])

    @staticmethod
    def _open_job(job: Job) -> None:
        if job.outdir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(job.outdir))

    # ------------------------------------------------------------------
    # Añadir archivos
    # ------------------------------------------------------------------

    def _choose_files(self) -> None:
        extensions = " ".join(f"*{ext}" for ext in AUDIO_EXTENSIONS)
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Añadir canciones",
            str(Path.home()),
            f"Archivos de audio ({extensions});;Todos los archivos (*)",
        )
        if paths:
            self._add_paths(paths)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Añadir una carpeta de música", str(Path.home())
        )
        if folder:
            self._add_paths([folder])

    def _add_paths(self, paths) -> None:
        accepted, rejected = collect_audio_files(paths)
        if not accepted and not rejected:
            return

        existing = set()
        for job in self.queue.jobs:
            try:
                existing.add(str(Path(job.source).resolve()).lower())
            except OSError:
                existing.add(str(job.source).lower())

        fresh = []
        for path in accepted:
            try:
                key = str(path.resolve()).lower()
            except OSError:
                key = str(path).lower()
            if key in existing:
                continue
            existing.add(key)
            fresh.append(path)

        if not fresh:
            self._log("No hay canciones nuevas que añadir.")
            return

        config = self._apply_config_from_controls()
        requests = [
            request_from_config(config, path, ffmpeg=self.engine.ffmpeg)
            for path in fresh
        ]
        self.queue.add(requests)
        self._log(f"Añadidas {len(fresh)} canciones a la cola.")
        if rejected:
            self._log(
                f"Descartados {len(rejected)} elementos que no son archivos de audio."
            )
        if not self.engine.available:
            self._log("El motor aún no está instalado: usa «Instalar motor».")
        self._update_actions()

    # ------------------------------------------------------------------
    # Ajustes
    # ------------------------------------------------------------------

    def _load_controls(self) -> None:
        self._updating = True
        try:
            config = self.config
            self.output_edit.setText(str(config.output_path()))
            self.subfolder_check.setChecked(config.create_subfolder)
            self.open_when_done_check.setChecked(config.open_when_done)
            self.naming_combo.setCurrentIndex(
                max(0, self.naming_combo.findData(config.stem_naming))
            )

            index = self.model_combo.findData(config.model)
            self.model_combo.setCurrentIndex(index if index >= 0 else 0)
            self.shifts_spin.setValue(config.shifts)
            self.overlap_spin.setValue(int(round(config.overlap * 100)))
            index = self.segment_combo.findData(config.segment or 0)
            self.segment_combo.setCurrentIndex(index if index >= 0 else 0)
            self.jobs_spin.setValue(config.jobs)
            self.split_check.setChecked(config.split)

            index = self.preset_combo.findData(config.preset)
            if index < 0:
                index = self.preset_combo.findData(CUSTOM_PRESET)
            self.preset_combo.setCurrentIndex(max(0, index))

            index = self.format_combo.findData(config.formatch)
            self.format_combo.setCurrentIndex(index if index >= 0 else 0)

            self._populate_devices()
            self._refresh_format_controls()
        finally:
            self._updating = False

    def _populate_devices(self) -> None:
        current = self.config.device
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem("Automático", "auto")
        self.device_combo.addItem("CPU", "cpu")
        if self.engine.available and self.engine.cuda:
            name = self.engine.device_name or "NVIDIA"
            self.device_combo.addItem(f"NVIDIA · {name}", "cuda")
        self.device_combo.blockSignals(False)

        index = self.device_combo.findData(current)
        self.device_combo.setCurrentIndex(index if index >= 0 else 0)

    def _refresh_format_controls(self) -> None:
        key = self.format_combo.currentData() or "wav"
        fmt = format_info(str(key))
        options = self._options_from_controls()
        options = sanitize_options(fmt, options)
        self._populate_quality_combos(fmt)
        self._apply_quality_options(fmt, options)
        self._update_estimate()

    def _populate_quality_combos(self, fmt) -> None:
        self.bit_depth_combo.blockSignals(True)
        self.bit_depth_combo.clear()
        for depth in fmt.bit_depths or (16,):
            self.bit_depth_combo.addItem(f"{depth} bits", depth)
        self.bit_depth_combo.blockSignals(False)

        self.bitrate_combo.blockSignals(True)
        self.bitrate_combo.clear()
        for bitrate in fmt.bitrates or (320,):
            self.bitrate_combo.addItem(f"{bitrate} kbps", bitrate)
        self.bitrate_combo.blockSignals(False)

        self.mp3_preset_combo.blockSignals(True)
        self.mp3_preset_combo.clear()
        for preset in fmt.presets or (2,):
            self.mp3_preset_combo.addItem(f"Preset {preset}", preset)
        self.mp3_preset_combo.blockSignals(False)

        self.vbr_combo.blockSignals(True)
        self.vbr_combo.clear()
        for quality in fmt.vbr_qualities or (6,):
            self.vbr_combo.addItem(f"Calidad {quality}", quality)
        self.vbr_combo.blockSignals(False)

    def _apply_quality_options(self, fmt, options: FormatOptions) -> None:
        self.bit_depth_combo.blockSignals(True)
        index = self.bit_depth_combo.findData(options.bit_depth)
        self.bit_depth_combo.setCurrentIndex(index if index >= 0 else 0)
        self.bit_depth_combo.blockSignals(False)

        self.bitrate_combo.blockSignals(True)
        index = self.bitrate_combo.findData(options.bitrate_kbps)
        self.bitrate_combo.setCurrentIndex(index if index >= 0 else 0)
        self.bitrate_combo.blockSignals(False)

        self.mp3_preset_combo.blockSignals(True)
        index = self.mp3_preset_combo.findData(options.mp3_preset)
        self.mp3_preset_combo.setCurrentIndex(index if index >= 0 else 0)
        self.mp3_preset_combo.blockSignals(False)

        self.vbr_combo.blockSignals(True)
        index = self.vbr_combo.findData(options.vbr_quality)
        self.vbr_combo.setCurrentIndex(index if index >= 0 else 0)
        self.vbr_combo.blockSignals(False)

        self.clip_combo.blockSignals(True)
        index = self.clip_combo.findData(options.clip_mode)
        self.clip_combo.setCurrentIndex(index if index >= 0 else 0)
        self.clip_combo.blockSignals(False)

        self.float_check.blockSignals(True)
        self.float_check.setChecked(bool(options.float32))
        self.float_check.blockSignals(False)

        self.bit_depth_row.setVisible(bool(fmt.bit_depths))
        self.float_row.setVisible(bool(fmt.float_option))
        self.bitrate_row.setVisible(bool(fmt.bitrates))
        self.mp3_preset_row.setVisible(bool(fmt.presets))
        self.vbr_row.setVisible(bool(fmt.vbr_qualities))
        self.format_note.setText(fmt.note)

    def _options_from_controls(self) -> FormatOptions:
        def data(combo: QComboBox, default):
            value = combo.currentData()
            return default if value is None else value

        return FormatOptions(
            bit_depth=int(data(self.bit_depth_combo, 16)),
            float32=self.float_check.isChecked(),
            bitrate_kbps=int(data(self.bitrate_combo, 320)),
            mp3_preset=int(data(self.mp3_preset_combo, 2)),
            vbr_quality=int(data(self.vbr_combo, 6)),
            clip_mode=str(data(self.clip_combo, "rescale")),
        )

    def _apply_config_from_controls(self, *, save: bool = False) -> app_config.Config:
        config = self.config
        config.output_dir = self.output_edit.text().strip()
        config.create_subfolder = self.subfolder_check.isChecked()
        config.open_when_done = self.open_when_done_check.isChecked()
        config.stem_naming = self.naming_combo.currentData() or "standard"
        config.preset = self.preset_combo.currentData() or CUSTOM_PRESET
        config.model = self.model_combo.currentData() or "htdemucs"
        config.shifts = self.shifts_spin.value()
        config.overlap = self.overlap_spin.value() / 100.0
        config.segment = int(self.segment_combo.currentData() or 0)
        config.jobs = self.jobs_spin.value()
        config.device = self.device_combo.currentData() or "auto"
        config.split = self.split_check.isChecked()
        config.formatch = self.format_combo.currentData() or "wav"
        config.options = self._options_from_controls().to_dict()
        if save:
            config.save()
        return config

    def _on_preset_changed(self) -> None:
        if self._updating:
            return
        key = self.preset_combo.currentData()
        preset = PRESETS_BY_KEY.get(str(key))
        if preset is None:
            return
        self._updating = True
        try:
            index = self.model_combo.findData(preset.model)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
            self.shifts_spin.setValue(preset.shifts)
            self.overlap_spin.setValue(int(round(preset.overlap * 100)))
        finally:
            self._updating = False
        self._update_estimate()

    def _mark_custom(self) -> None:
        index = self.preset_combo.findData(CUSTOM_PRESET)
        if index >= 0 and self.preset_combo.currentIndex() != index:
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(index)
            self.preset_combo.blockSignals(False)

    def _on_manual_change(self) -> None:
        if self._updating:
            return
        self._mark_custom()
        self._update_estimate()

    def _on_setting_change(self) -> None:
        if self._updating:
            return
        self._update_estimate()

    def _on_format_changed(self) -> None:
        if self._updating:
            return
        self._refresh_format_controls()

    def _choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Elegir carpeta de salida", self.output_edit.text()
        )
        if folder:
            self.output_edit.setText(folder)

    def _reset_settings(self) -> None:
        answer = QMessageBox.question(
            self,
            "Restaurar ajustes",
            "¿Volver a los ajustes de fábrica? La cola no se modificará.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.config = app_config.Config()
        self._load_controls()
        self.config.save()
        self._log("Ajustes restaurados.")
        self._update_estimate()

    # ------------------------------------------------------------------
    # Motor
    # ------------------------------------------------------------------

    def detect_engine_async(self) -> None:
        if self._detecting:
            return
        self._detecting = True
        self.engine_pill.setText("Detectando motor…")
        self.engine_pill.set_tone("muted")
        self.start_button.setEnabled(False)

        thread = threading.Thread(
            target=self._detect_worker, name="demucs-engine-detect", daemon=True
        )
        thread.start()

    def _detect_worker(self) -> None:
        try:
            status = detect_engine(timeout=240)
        except Exception as error:  # noqa: BLE001 - se informa en la interfaz
            status = EngineStatus(error=f"{type(error).__name__}: {error}")
        self.bridge.engineDetected.emit(status.to_dict())

    def _on_engine_detected(self, payload: dict) -> None:
        self._detecting = False
        self._detected = True
        try:
            self.engine = EngineStatus(**payload)
        except TypeError:
            self.engine = EngineStatus(error="Respuesta inválida al detectar el motor")

        if self.config.device == "cuda" and not self.engine.cuda:
            self.config.device = "auto"
            self.config.save()

        self._populate_devices()
        self._update_engine_ui()
        if self.engine.available:
            self._log(f"Motor detectado: {self.engine.summary}")
        else:
            self._log(f"Motor no disponible: {self.engine.error or 'sin detalle'}")
        self._update_actions()
        self._update_estimate()

    def _update_engine_ui(self) -> None:
        if self.engine.available:
            device = "CUDA" if self.engine.cuda else "CPU"
            self.engine_pill.setText(f"Motor listo · {device}")
            self.engine_pill.set_tone("ok")
            self.engine_pill.setToolTip(self.engine.summary)
            self.install_button.setVisible(False)
        else:
            self.engine_pill.setText("Motor no instalado")
            self.engine_pill.set_tone("warn")
            self.engine_pill.setToolTip(self.engine.error or "")
            self.install_button.setVisible(True)

    def _install_engine(self) -> None:
        device = self.config.setup_device or bootstrap.recommended_device()
        dialog = SetupDialog(
            self, device=device, repair=self.engine.available
        )
        if dialog.exec() == QDialog.Accepted:
            self.config.setup_device = dialog.device
            self.config.save()
            self._log(
                f"Motor instalado con la opción {dialog.device.upper()}. "
                "Detectando de nuevo…"
            )
            self.detect_engine_async()

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------

    def _start(self) -> None:
        if not self.engine.available:
            self._install_engine()
            return

        config = self._apply_config_from_controls(save=True)
        pending = self.queue.pending_jobs()
        if not pending:
            self._log("No hay canciones pendientes en la cola.")
            return

        device = config.device
        if device == "auto":
            device = "cuda" if self.engine.cuda else "cpu"
        if device == "cuda" and not self.engine.cuda:
            QMessageBox.warning(
                self,
                "GPU no disponible",
                "El motor se instaló sin CUDA o no se detecta la GPU. "
                "Cambia el dispositivo a CPU o reinstala el motor con CUDA.",
            )
            return

        try:
            config.output_path().mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QMessageBox.critical(
                self,
                "Carpeta de salida no válida",
                f"No se pudo crear la carpeta de salida:\n{error}",
            )
            return

        for job in pending:
            job.request = request_from_config(
                config, job.source, ffmpeg=self.engine.ffmpeg
            )

        self._log(f"Iniciando la cola · {plan_summary(pending[0].request)}")
        self.queue.start_all()
        self._update_actions()

    def _cancel_all(self) -> None:
        if not self.queue.jobs:
            return
        self._log("Cancelando la cola…")
        self.queue.cancel_all()
        self._update_actions()

    def _update_actions(self) -> None:
        if not hasattr(self, "queue"):
            return
        busy = self.queue.busy
        pending = len(self.queue.pending_jobs())
        self.start_button.setEnabled(self.engine.available and pending > 0 and not busy)
        if busy:
            self.start_button.setText("Separando…")
        elif pending:
            self.start_button.setText(f"Separar ({pending})")
        else:
            self.start_button.setText("Separar")
        self.cancel_button.setEnabled(busy or pending > 0)
        self.install_button.setVisible(not self.engine.available)

        done, total, fraction = self.queue.overall_progress()
        self.overall.setValue(int(round(fraction * 100)))
        self.overall_label.setText(f"{done} de {total}" if total else "0 de 0")

    def _update_estimate(self) -> None:
        if not hasattr(self, "queue"):
            return
        config = self._apply_config_from_controls(save=False)
        device = config.device
        if device == "auto":
            device = "cuda" if self.engine.cuda else "cpu"

        jobs = [job for job in self.queue.jobs if job.status in (PENDING, RUNNING)]
        if not jobs:
            self.estimate_label.setText(
                "Añade canciones para ver la duración y el tiempo estimado."
            )
            return

        total_duration = sum(job.duration or 0 for job in jobs)
        estimate = 0.0
        missing_duration = False
        for job in jobs:
            if job.status == RUNNING:
                model = job.request.model
                shifts = job.request.shifts
            else:
                model = config.model
                shifts = config.shifts
            seconds = estimate_seconds(
                duration=job.duration,
                model=model,
                shifts=shifts,
                device=device,
                calibration=config.calibration,
            )
            if seconds is None:
                missing_duration = True
            else:
                estimate += seconds

        parts = [
            f"{len(jobs)} canción" if len(jobs) == 1 else f"{len(jobs)} canciones"
        ]
        if total_duration:
            parts.append(f"duración {human_duration(total_duration)}")
        if missing_duration:
            parts.append("calculando duración…")
        elif estimate:
            parts.append(f"estimado ~{human_duration(estimate)}")
        parts.append("GPU" if device == "cuda" else "CPU")
        self.estimate_label.setText(" · ".join(parts))

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def _log(self, line: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        for part in (line.splitlines() or [""]):
            self.log_view.append_line(f"{timestamp}  {part}")

    def _open_output(self) -> None:
        path = self._apply_config_from_controls(save=False).output_path()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_config.DATA_DIR)))

    def _open_models_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_config.MODELS_DIR)))

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "Acerca de Demucs Studio",
            (
                f"<b>Demucs Studio {app_config.APP_VERSION}</b><br><br>"
                "Separación de batería, bajo, voces y resto de instrumentos "
                "con demucs y PyTorch.<br><br>"
                "Todo se procesa en tu equipo: no se sube audio a ningún "
                "servidor.<br><br>"
                f"<span style='color:#8d8d98'>Datos: {app_config.DATA_DIR}</span>"
            ),
        )

    # ------------------------------------------------------------------
    # Eventos de ventana
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - API de Qt
        super().showEvent(event)
        if not self._dark_title_done:
            self._dark_title_done = True
            self._enable_dark_title_bar()

    def _enable_dark_title_bar(self) -> None:
        if os.name != "nt":
            return
        try:
            import ctypes

            value = ctypes.c_int(1)
            hwnd = int(self.winId())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:  # pragma: no cover - solo estética en Windows
            pass

    def closeEvent(self, event) -> None:  # noqa: N802 - API de Qt
        if self.queue.busy:
            answer = QMessageBox.question(
                self,
                "Separación en curso",
                "Hay una separación en curso. ¿Quieres cancelarla y salir?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.queue.cancel_all()

        config = self._apply_config_from_controls(save=False)
        config.window_width = self.width()
        config.window_height = self.height()
        config.save()
        super().closeEvent(event)
