"""Diálogo de instalación del motor (CPU o CUDA)."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from winapp import bootstrap


class InstallWorker(QObject):
    """Ejecuta ``EngineInstaller`` fuera del hilo de la interfaz."""

    log = Signal(str)
    step = Signal(int, int, str)
    finished = Signal(bool, str)

    def __init__(self, device: str) -> None:
        super().__init__()
        self.installer = bootstrap.EngineInstaller(
            device,
            on_log=lambda line: self.log.emit(line),
            on_step=lambda index, total, title: self.step.emit(index, total, title),
        )

    @Slot()
    def run(self) -> None:
        try:
            path = self.installer.run()
        except bootstrap.BootstrapCancelled:
            self.finished.emit(False, "__cancelled__")
        except Exception as error:  # noqa: BLE001 - se muestra al usuario
            self.finished.emit(False, str(error))
        else:
            self.finished.emit(True, path)

    def cancel(self) -> None:
        self.installer.cancel()


class SetupDialog(QDialog):
    """Permite elegir CPU/CUDA y muestra el progreso de la descarga."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        device: str = "cpu",
        repair: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            ("Reparar motor · Demucs Studio" if repair else "Instalar motor · Demucs Studio")
        )
        self.setModal(True)
        self.resize(640, 580)
        self.setMinimumSize(560, 480)

        self.device = device if device in ("cpu", "cuda") else "cpu"
        self._repair = repair
        self._gpu: str | None = None
        self._thread: QThread | None = None
        self._worker: InstallWorker | None = None
        self._running = False
        self._close_after = False
        self._cancelled = False

        self._build_ui()
        self._detect_hardware()

    # --- Construcción ----------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        title = QLabel(
            "Reparar el motor de separación"
            if self._repair
            else "Instalar el motor de separación"
        )
        title.setObjectName("AppTitle")
        layout.addWidget(title)

        intro = QLabel(
            (
                "Se volverá a crear el entorno del motor desde cero. Los modelos "
                "ya descargados se conservan."
                if self._repair
                else "Demucs Studio descarga el motor (demucs + PyTorch) una sola vez. "
                "Elige el tipo de instalación según tu equipo."
            )
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.group = QButtonGroup(self)
        self.cpu_radio = QRadioButton("CPU · funciona en cualquier PC (~250 MB)")
        self.cuda_radio = QRadioButton(
            "NVIDIA GPU · CUDA 12.1, mucho más rápido (~2,5 GB)"
        )
        self.group.addButton(self.cpu_radio, 0)
        self.group.addButton(self.cuda_radio, 1)
        layout.addWidget(self.cpu_radio)
        layout.addWidget(self.cuda_radio)

        self.hardware_label = QLabel("")
        self.hardware_label.setObjectName("FormatNote")
        self.hardware_label.setWordWrap(True)
        layout.addWidget(self.hardware_label)

        note = QLabel(
            "La descarga se hace desde pytorch.org y PyPI; necesita conexión a "
            "Internet. Puedes cancelar y retomarla más tarde: al reinstalar se "
            "empieza de cero, pero los modelos descargados se conservan."
        )
        note.setObjectName("FormatNote")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.step_label = QLabel("")
        self.step_label.setObjectName("Muted")
        layout.addWidget(self.step_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setFont(QFont("Cascadia Mono", 9))
        self.log.setPlaceholderText("El progreso de la descarga aparecerá aquí…")
        self.log.setMinimumHeight(180)
        layout.addWidget(self.log, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.install_button = QPushButton("Instalar motor")
        self.install_button.setObjectName("Primary")
        self.install_button.clicked.connect(self._start)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

    def _detect_hardware(self) -> None:
        gpu = bootstrap.nvidia_gpu()
        self._gpu = gpu
        if gpu:
            self.hardware_label.setText(f"GPU detectada: {gpu}")
            if self.device == "cuda":
                self.cuda_radio.setChecked(True)
            else:
                self.cpu_radio.setChecked(True)
        else:
            self.cuda_radio.setEnabled(False)
            self.cpu_radio.setChecked(True)
            self.device = "cpu"
            self.hardware_label.setText(
                "No se detectó una GPU NVIDIA. La opción CUDA queda desactivada; "
                "puedes instalar la versión CPU y funciona igual, solo más lenta."
            )

    # --- Instalación -----------------------------------------------------

    def _start(self) -> None:
        if self._running:
            return
        self.device = "cuda" if self.cuda_radio.isChecked() else "cpu"
        self._running = True
        self._cancelled = False
        self._close_after = False

        self.cpu_radio.setEnabled(False)
        self.cuda_radio.setEnabled(False)
        self.install_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Cancelar instalación")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.step_label.setText("Iniciando…")
        self.log.clear()
        self._append("Iniciando la instalación del motor…")

        self._thread = QThread(self)
        self._worker = InstallWorker(self.device)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._append)
        self._worker.step.connect(self._on_step)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    @Slot(str)
    def _append(self, line: str) -> None:
        self.log.appendPlainText(line)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    @Slot(int, int, str)
    def _on_step(self, index: int, total: int, title: str) -> None:
        self.step_label.setText(f"Paso {index} de {total} · {title}")

    @Slot(bool, str)
    def _on_finished(self, ok: bool, message: str) -> None:
        self._running = False
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if ok else 0)

        if ok:
            self._append("")
            self._append("Instalación completada correctamente.")
            self._append(message)
            super().accept()
            return

        self._reset_controls()

        if message == "__cancelled__":
            self._append("Instalación cancelada por el usuario.")
            self.step_label.setText("Instalación cancelada")
            if self._close_after:
                super().reject()
            return

        self._append("")
        self._append(f"Error: {message}")
        self.step_label.setText("No se pudo instalar el motor")
        QMessageBox.critical(
            self,
            "No se pudo instalar el motor",
            f"{message}\n\nRevisa el registro para ver el detalle y comprueba "
            "la conexión a Internet.",
        )
        if self._close_after:
            super().reject()

    @Slot()
    def _on_thread_finished(self) -> None:
        self._worker = None
        self._thread = None

    def _reset_controls(self) -> None:
        self.cpu_radio.setEnabled(True)
        self.cuda_radio.setEnabled(self._gpu is not None)
        self.install_button.setEnabled(True)
        self.cancel_button.setText("Cancelar")
        self.cancel_button.setEnabled(True)
        self.progress.setVisible(False)
        self.step_label.setText("")

    def _on_cancel(self) -> None:
        if not self._running:
            self.reject()
            return
        if self._cancelled:
            return
        self._cancelled = True
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancelando…")
        self.step_label.setText("Cancelando la instalación…")
        if self._worker is not None:
            self._worker.cancel()

    # --- Cierre ----------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - API de Qt
        if not self._running:
            super().closeEvent(event)
            return

        answer = QMessageBox.question(
            self,
            "Instalación en curso",
            "La instalación está en curso. ¿Quieres cancelarla y cerrar?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            event.ignore()
            return

        self._close_after = True
        self._on_cancel()
        event.ignore()

    def accept(self) -> None:  # noqa: D401 - API de Qt
        if self._running:
            return
        super().accept()
