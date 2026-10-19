"""Rutas de la aplicación y persistencia de la configuración.

Edición Windows: la configuración vive en ``%APPDATA%\\Demucs Studio`` y el
motor (venv con demucs y PyTorch), los modelos y la caché en
``%LOCALAPPDATA%\\Demucs Studio``. Nada se escribe junto al ejecutable, así que
la app funciona sin permisos de administrador.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from .formats import DEFAULT_FORMAT, FormatOptions
from .presets import DEFAULT_MODEL, DEFAULT_PRESET

log = logging.getLogger(__name__)

APP_ID = "io.github.demucsstudio.DemucsStudio"
APP_NAME = "Demucs Studio"
APP_VERSION = "1.0.0"
APP_SUMMARY = (
    "Separa cualquier canción en batería, bajo, voces y resto de instrumentos."
)

#: ``True`` cuando corremos dentro del ejecutable de PyInstaller.
FROZEN = bool(getattr(sys, "frozen", False))

#: Carpeta donde PyInstaller deja los recursos (``_internal``) o la raíz del
#: proyecto cuando se ejecuta desde el código fuente.
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Raíz que se añade al ``PYTHONPATH`` del worker. En el ejecutable contiene
#: una copia en fuentes de este paquete; en desarrollo es el propio proyecto.
WORKER_SRC = RESOURCE_DIR / "worker_src" if FROZEN else PROJECT_ROOT

#: Binarios incluidos en el instalador (ffmpeg, ffprobe y uv).
BIN_DIR = RESOURCE_DIR / "bin" if FROZEN else PROJECT_ROOT / "tools" / "bin"


def _base(env: str, fallback: Path) -> Path:
    raw = os.environ.get(env)
    return Path(raw).expanduser() if raw else fallback


#: ``%APPDATA%`` y ``%LOCALAPPDATA%`` con respaldo para pruebas en otros SO.
APPDATA_DIR = _base("APPDATA", Path.home() / "AppData" / "Roaming")
LOCALAPPDATA_DIR = _base("LOCALAPPDATA", Path.home() / "AppData" / "Local")

CONFIG_DIR = APPDATA_DIR / "Demucs Studio"
DATA_DIR = LOCALAPPDATA_DIR / "Demucs Studio"
CACHE_DIR = DATA_DIR / "cache"
MODELS_DIR = DATA_DIR / "models"
ENGINE_DIR = DATA_DIR / "engine"
UV_CACHE_DIR = DATA_DIR / "uv-cache"
PYTHON_INSTALL_DIR = DATA_DIR / "python"

CONFIG_FILE = CONFIG_DIR / "config.json"
VENV_PYTHON = ENGINE_DIR / "Scripts" / "python.exe"
VENV_PYTHON_POSIX = ENGINE_DIR / "bin" / "python"

#: Permite apuntar a otro intérprete con demucs instalado (útil para depurar).
PYTHON_ENV_VAR = "DEMUCS_STUDIO_PYTHON"


def default_output_dir() -> Path:
    """Carpeta de salida sugerida: ``~/Music/Separaciones`` en Windows."""
    for candidate in (
        Path.home() / "Music",
        Path.home() / "Música",
        Path.home() / "Downloads",
        Path.home() / "Descargas",
    ):
        if candidate.is_dir():
            return candidate / "Separaciones"
    return Path.home() / "Separaciones"


def default_temp_dir() -> Path:
    """Directorio de trabajo para los WAV intermedios."""
    return CACHE_DIR / "tmp"


def engine_python() -> Path:
    """Intérprete del motor, según la plataforma."""
    if os.name == "nt":
        return VENV_PYTHON
    # Solo para desarrollo/pruebas del proyecto en Linux o macOS.
    return VENV_PYTHON_POSIX


def bundled_tool(name: str) -> Path | None:
    """Ruta de un binario incluido (``ffmpeg``, ``ffprobe``, ``uv``)."""
    candidates = [BIN_DIR / name]
    if os.name == "nt":
        candidates.insert(0, BIN_DIR / f"{name}.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def ensure_dirs() -> None:
    """Crea las carpetas de datos que la app necesita."""
    for directory in (CONFIG_DIR, DATA_DIR, CACHE_DIR, MODELS_DIR, default_temp_dir()):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:  # pragma: no cover - disco lleno/permisos
            log.warning("No se pudo crear %s: %s", directory, error)


def sanitize_calibration(raw: Any) -> dict[str, float]:
    """Deja la tabla de calibración con claves y números usables."""
    if not isinstance(raw, dict):
        return {}
    table: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or ":" not in key:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if 0 < number < 10:
            table[key] = number
    return table


def _clamp_int(value: Any, *, default: int, low: int, high: int) -> int:
    """Convierte a entero dentro de rango, cayendo al valor por defecto."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clamp_float(value: Any, *, default: float, low: float, high: float) -> float:
    """Convierte a número decimal dentro de rango, cayendo al valor por defecto."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


@dataclass
class Config:
    """Ajustes persistentes de la aplicación."""

    # Salida
    output_dir: str = ""
    create_subfolder: bool = True
    open_when_done: bool = False
    stem_naming: str = "standard"

    # Separación
    preset: str = DEFAULT_PRESET
    model: str = DEFAULT_MODEL
    shifts: int = 1
    overlap: float = 0.25
    segment: int = 0
    jobs: int = 0
    device: str = "auto"
    split: bool = True

    # Formato
    formatch: str = DEFAULT_FORMAT
    options: dict[str, Any] = field(default_factory=FormatOptions().to_dict)

    # Motor
    setup_device: str = "cpu"
    """``cpu`` o ``cuda``: lo elegido la última vez que se instaló el motor."""

    # Interfaz
    window_width: int = 1180
    window_height: int = 860

    #: Segundos de cómputo por segundo de audio y unidad de coste, medidos en
    #: esta máquina. Clave ``"cuda:htdemucs"``. Se rellena sola al separar.
    calibration: dict[str, float] = field(default_factory=dict)

    def output_path(self) -> Path:
        """Carpeta de salida efectiva."""
        raw = self.output_dir.strip() if self.output_dir else ""
        return Path(raw).expanduser() if raw else default_output_dir()

    def format_options(self) -> FormatOptions:
        """Opciones de formato ya validadas."""
        return FormatOptions.from_dict(self.options)

    def set_format_options(self, options: FormatOptions) -> None:
        self.options = options.to_dict()

    # --- Persistencia ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Config":
        """Carga tolerante: ignora claves y valores que no encajen."""
        fallback = cls()
        if not isinstance(data, dict):
            return fallback
        valid = {f.name for f in fields(cls)}
        accepted = {key: value for key, value in data.items() if key in valid}
        try:
            config = replace(fallback, **accepted)
        except TypeError:
            log.warning(
                "Configuración parcialmente inválida; se usan los valores por defecto."
            )
            return fallback

        config.shifts = _clamp_int(config.shifts, default=fallback.shifts, low=0, high=10)
        config.overlap = _clamp_float(
            config.overlap, default=fallback.overlap, low=0.0, high=0.9
        )
        config.segment = _clamp_int(config.segment, default=0, low=0, high=600)
        config.jobs = _clamp_int(config.jobs, default=0, low=0, high=8)
        if config.device not in ("auto", "cuda", "cpu"):
            config.device = "auto"
        if config.stem_naming not in ("standard", "spanish"):
            config.stem_naming = "standard"
        if config.setup_device not in ("cpu", "cuda"):
            config.setup_device = "cpu"
        if not isinstance(config.options, dict):
            config.options = FormatOptions().to_dict()
        if not isinstance(config.output_dir, str):
            config.output_dir = ""
        config.calibration = sanitize_calibration(config.calibration)
        return config

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Lee la configuración del disco; si falta o está rota, usa la de defecto."""
        config_path = path or CONFIG_FILE
        try:
            raw = config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls()
        except OSError as error:
            log.warning("No se pudo leer %s: %s", config_path, error)
            return cls()
        try:
            return cls.from_dict(json.loads(raw))
        except json.JSONDecodeError as error:
            log.warning(
                "Configuración corrupta en %s (%s); se ignorará.", config_path, error
            )
            return cls()

    def save(self, path: Path | None = None) -> None:
        """Escribe la configuración, ignorando fallos de disco."""
        config_path = path or CONFIG_FILE
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = config_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tmp.replace(config_path)
        except OSError as error:
            log.warning("No se pudo guardar la configuración en %s: %s", config_path, error)
