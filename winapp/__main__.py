"""Punto de entrada del ejecutable de Windows."""

from __future__ import annotations

import multiprocessing
import os
import sys
import tempfile
import traceback
from pathlib import Path

APP_VERSION = "1.0.0"


def resource_path(name: str) -> Path | None:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for candidate in (base / name, base / "packaging" / name):
        if candidate.is_file():
            return candidate
    return None


def _selftest() -> int:
    """Comprueba imports y construcción de la ventana sin abrir nada."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sandbox = Path(tempfile.gettempdir()) / "demucs-studio-selftest"
    os.environ.setdefault("APPDATA", str(sandbox / "Roaming"))
    os.environ.setdefault("LOCALAPPDATA", str(sandbox / "Local"))

    try:
        from PySide6.QtWidgets import QApplication

        from demucs_studio.formats import FormatOptions, format_info, sanitize_options
        from demucs_studio.presets import relative_cost
        from winapp.ui.main_window import MainWindow
        from winapp.ui.theme import STYLESHEET

        assert relative_cost("htdemucs_ft", 3) == 12.0
        assert sanitize_options(format_info("mp3"), FormatOptions()).bitrate_kbps == 320

        app = QApplication([])
        app.setStyle("Fusion")
        app.setStyleSheet(STYLESHEET)
        window = MainWindow()
        window.resize(1180, 860)
        window.close()
        print("SELFTEST OK")
        print(f"Python {sys.version.split()[0]}")
        print(f"Recursos: {Path(getattr(sys, '_MEIPASS', Path.cwd()))}")
        return 0
    except Exception:  # noqa: BLE001 - diagnóstico
        traceback.print_exc()
        return 1


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--version" in argv:
        print(f"Demucs Studio {APP_VERSION}")
        return 0
    if "--selftest" in argv:
        return _selftest()

    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from winapp.ui.main_window import MainWindow
    from winapp.ui.theme import STYLESHEET

    QApplication.setApplicationName("Demucs Studio")
    QApplication.setOrganizationName("Demucs Studio")
    QApplication.setApplicationVersion(APP_VERSION)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLESHEET)

    icon = resource_path("icon.png")
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))

    def handle_exception(exc_type, exc_value, exc_tb) -> None:
        traceback.print_exception(exc_type, exc_value, exc_tb)
        try:
            QMessageBox.critical(
                None,
                "Error inesperado",
                f"Se produjo un error inesperado:\n\n{exc_value}\n\n"
                "El registro completo está en la consola de Windows.",
            )
        except Exception:  # pragma: no cover - la propia UI falló
            pass

    sys.excepthook = handle_exception

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
