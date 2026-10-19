"""Widgets reutilizables de la interfaz."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def repolish(widget: QWidget) -> None:
    """Vuelve a aplicar el estilo tras cambiar una propiedad dinámica."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class Card(QFrame):
    """Panel con fondo elevado y borde fino."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFrameShape(QFrame.NoFrame)


class HLine(QFrame):
    """Separador horizontal de un píxel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HLine")
        self.setFixedHeight(1)


class Pill(QLabel):
    """Etiqueta de estado con color semántico."""

    def __init__(self, text: str = "", tone: str = "muted") -> None:
        super().__init__(text)
        self.setObjectName("Pill")
        self.setAlignment(Qt.AlignCenter)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        repolish(self)


class DropZone(QFrame):
    """Zona para arrastrar canciones o carpetas."""

    pathsDropped = Signal(list)
    browseRequested = Signal()
    folderRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(116)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setProperty("dragActive", "false")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignCenter)

        icon = QLabel("♫")
        icon.setObjectName("DropIcon")
        icon.setAlignment(Qt.AlignCenter)

        title = QLabel("Arrastra tus canciones aquí")
        title.setObjectName("DropTitle")
        title.setAlignment(Qt.AlignCenter)

        subtitle = QLabel(
            "WAV, MP3, FLAC, M4A, OGG, Opus… · también carpetas completas"
        )
        subtitle.setObjectName("Muted")
        subtitle.setAlignment(Qt.AlignCenter)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.setAlignment(Qt.AlignCenter)
        browse = QPushButton("Elegir archivos")
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self.browseRequested.emit)
        folder = QPushButton("Añadir carpeta")
        folder.setObjectName("Ghost")
        folder.setCursor(Qt.PointingHandCursor)
        folder.clicked.connect(self.folderRequested.emit)
        buttons.addWidget(browse)
        buttons.addWidget(folder)

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(4)
        layout.addLayout(buttons)

    def _set_active(self, active: bool) -> None:
        self.setProperty("dragActive", "true" if active else "false")
        repolish(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_active(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - API de Qt
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_active(False)
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            event.acceptProposedAction()
            self.pathsDropped.emit(paths)
        else:
            event.ignore()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.browseRequested.emit()
        super().mousePressEvent(event)


class LogView(QPlainTextEdit):
    """Registro de solo lectura con tipografía monoespaciada."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(4000)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setFont(QFont("Cascadia Mono", 9))
        self.setPlaceholderText("Aquí aparecerá el registro del motor…")
        self.setMinimumHeight(72)

    def append_line(self, line: str) -> None:
        self.appendPlainText(line)
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


def file_pixmap(path: Path, size: int):
    """Carga una imagen escalada, si existe."""
    from PySide6.QtGui import QPixmap

    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return None
    return pixmap.scaled(
        size,
        size,
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )
