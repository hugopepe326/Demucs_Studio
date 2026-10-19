"""Motor de separación para Windows: localizar demucs, planificar y ejecutar.

La interfaz corre dentro del ejecutable de PyInstaller, mientras que demucs y
PyTorch viven en un *virtualenv* aparte creado en ``%LOCALAPPDATA%``. Este
módulo tiende el puente: encuentra el intérprete del motor, le pasa el trabajo
en un JSON y traduce los eventos que el worker escribe en stdout.

Ningún módulo de este paquete depende de Qt, así que el worker puede importarlo
desde el venv sin arrastrar la interfaz.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import config as app_config
from .formats import (
    FormatOptions,
    format_info,
    sanitize_options,
)
from .presets import STEMS, model_info, relative_cost, stem_filename
from .util import sanitize_component, track_name, unique_directory

log = logging.getLogger(__name__)

#: Marca que el worker imprime al arrancar, para detectar versiones mezcladas.
PROTOCOL_VERSION = 1

#: Eventos que el worker puede emitir (ver ``worker.py``).
EVENT_STAGE = "stage"
EVENT_PROGRESS = "progress"
EVENT_STEM_START = "stem_start"
EVENT_STEM_DONE = "stem_done"
EVENT_MODELS = "models"
EVENT_DONE = "done"
EVENT_ERROR = "error"
EVENT_CANCELLED = "cancelled"

_STAGE_LABELS = {
    "start": "Preparando…",
    "model": "Cargando modelo (la primera vez se descarga)",
    "audio": "Leyendo el audio",
    "separate": "Separando pistas",
    "encode": "Codificando la salida",
    "cleanup": "Limpiando temporales",
}


def stage_label(stage: str) -> str:
    """Texto legible de una etapa del worker."""
    return _STAGE_LABELS.get(stage, stage or "Procesando…")


# ---------------------------------------------------------------------------
# Utilidades de Windows
# ---------------------------------------------------------------------------


def _no_window_flags() -> int:
    """Evita que los subprocesos abran una consola en Windows."""
    if os.name == "nt":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0


def _subprocess_env() -> dict[str, str]:
    """Entorno común para el motor: binarios incluidos y rutas de datos."""
    env = os.environ.copy()
    bin_dir = str(app_config.BIN_DIR)
    path = env.get("PATH", "")
    env["PATH"] = f"{bin_dir}{os.pathsep}{path}" if path else bin_dir
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["TORCH_HOME"] = str(app_config.MODELS_DIR)
    env["HF_HOME"] = str(app_config.DATA_DIR / "hf")
    return env


# ---------------------------------------------------------------------------
# Detección del motor
# ---------------------------------------------------------------------------

#: Comprueba que el intérprete puede separar de verdad.
#: Se importa ``demucs.api`` (y no solo ``demucs``) porque es lo que usa el
#: worker: importar el paquete a secas no detectaría que falta numpy.
_PROBE_CODE = """
import json
try:
    import numpy
    import torch
    from demucs.api import Separator, save_audio  # noqa: F401
    import importlib.metadata as md
    cuda = bool(torch.cuda.is_available())
    info = {
        "demucs": md.version("demucs"),
        "torch": torch.__version__,
        "numpy": numpy.__version__,
        "cuda": cuda,
        "device": torch.cuda.get_device_name(0) if cuda else None,
        "cuda_version": getattr(torch.version, "cuda", None),
    }
except Exception as error:
    info = {"error": f"{type(error).__name__}: {error}"}
print(json.dumps(info))
"""


@dataclass
class EngineStatus:
    """Estado del motor de separación localizado."""

    available: bool = False
    python: str | None = None
    demucs_version: str | None = None
    torch_version: str | None = None
    numpy_version: str | None = None
    cuda: bool = False
    device_name: str | None = None
    ffmpeg: str | None = None
    error: str | None = None

    @property
    def device_summary(self) -> str:
        if self.cuda and self.device_name:
            return f"GPU · {self.device_name}"
        return "CPU"

    @property
    def summary(self) -> str:
        if not self.available:
            return "Motor no instalado"
        parts = [f"demucs {self.demucs_version}", f"torch {self.torch_version}"]
        parts.append(self.device_summary)
        if not self.ffmpeg:
            parts.append("sin ffmpeg")
        return " · ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "python": self.python,
            "demucs_version": self.demucs_version,
            "torch_version": self.torch_version,
            "numpy_version": self.numpy_version,
            "cuda": self.cuda,
            "device_name": self.device_name,
            "ffmpeg": self.ffmpeg,
            "error": self.error,
        }


def ffmpeg_path() -> str | None:
    """Ruta de ffmpeg: primero el incluido en el instalador, luego el PATH."""
    bundled = app_config.bundled_tool("ffmpeg")
    return str(bundled) if bundled else shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    """Ruta de ffprobe: primero el incluido, luego el PATH."""
    bundled = app_config.bundled_tool("ffprobe")
    return str(bundled) if bundled else shutil.which("ffprobe")


def engine_python() -> Path:
    """Intérprete del venv del motor."""
    return app_config.engine_python()


def candidate_pythons() -> list[Path]:
    """Intérpretes donde puede vivir demucs, en orden de preferencia."""
    candidates: list[Path] = []
    override = os.environ.get(app_config.PYTHON_ENV_VAR)
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(engine_python())
    ordered: list[Path] = []
    for candidate in candidates:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def probe_interpreter(python: str | Path, *, timeout: float = 180.0) -> EngineStatus:
    """Pregunta a un intérprete si puede separar audio.

    Importar torch es lento la primera vez (varios segundos), por eso el
    timeout es holgado; conviene llamar a esto desde un hilo aparte.
    """
    python = str(python)
    status = EngineStatus(python=python, ffmpeg=ffmpeg_path())
    try:
        result = subprocess.run(
            [python, "-c", _PROBE_CODE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_subprocess_env(),
            creationflags=_no_window_flags(),
        )
    except FileNotFoundError:
        status.error = "El intérprete del motor no existe"
        return status
    except subprocess.TimeoutExpired:
        status.error = "El intérprete no respondió a tiempo"
        return status
    except OSError as error:
        status.error = str(error)
        return status

    payload: dict[str, Any] = {}
    for line in reversed((result.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    if "error" in payload:
        status.error = payload["error"]
        return status
    if not payload:
        tail = (result.stderr or "").strip().splitlines()
        status.error = tail[-1] if tail else "Fallo desconocido al probar el motor"
        return status

    status.available = True
    status.demucs_version = payload.get("demucs")
    status.torch_version = payload.get("torch")
    status.numpy_version = payload.get("numpy")
    status.cuda = bool(payload.get("cuda"))
    status.device_name = payload.get("device")
    return status


def detect_engine(*, timeout: float = 180.0) -> EngineStatus:
    """Prueba el venv del motor y devuelve su estado."""
    last: EngineStatus | None = None
    for python in candidate_pythons():
        if python.is_file() or shutil.which(str(python)):
            status = probe_interpreter(python, timeout=timeout)
            if status.available:
                return status
            last = status
    if last is not None:
        return last
    return EngineStatus(
        error=(
            "El motor todavía no está instalado. Usa «Instalar motor» para "
            "descargar demucs y PyTorch."
        ),
        ffmpeg=ffmpeg_path(),
    )


# ---------------------------------------------------------------------------
# Descripción del trabajo
# ---------------------------------------------------------------------------


@dataclass
class SeparationRequest:
    """Todo lo necesario para separar una canción."""

    input_path: str
    output_dir: str
    model: str = "htdemucs"
    shifts: int = 1
    overlap: float = 0.25
    segment: int | None = None
    jobs: int = 0
    device: str = "auto"
    split: bool = True
    format_key: str = "wav"
    options: dict[str, Any] = field(default_factory=FormatOptions().to_dict)
    create_subfolder: bool = True
    stem_naming: str = "standard"
    keep_intermediate: bool = False
    temp_dir: str = ""
    ffmpeg: str | None = None

    def format_info(self):
        return format_info(self.format_key)

    def options_obj(self) -> FormatOptions:
        return self.options_from_dict(self.options)

    @staticmethod
    def options_from_dict(data: dict[str, Any] | None) -> FormatOptions:
        return FormatOptions.from_dict(data)

    def sanitized_options(self) -> FormatOptions:
        return sanitize_options(self.format_info(), self.options_obj())

    def effective_device(self, engine: EngineStatus | None) -> str:
        """Resuelve ``auto`` según el motor disponible."""
        if self.device in ("cuda", "cpu"):
            return self.device
        if engine is not None and engine.cuda:
            return "cuda"
        return "cpu"

    def to_dict(self) -> dict[str, Any]:
        return {
            "input": self.input_path,
            "track": self.track_label(),
            "output_dir": self.output_dir,
            "model": self.model,
            "shifts": int(self.shifts),
            "overlap": float(self.overlap),
            "segment": int(self.segment) if self.segment else None,
            "jobs": int(self.jobs),
            "device": self.device,
            "split": bool(self.split),
            "format": self.format_key,
            "options": self.sanitized_options().to_dict(),
            "create_subfolder": bool(self.create_subfolder),
            "stem_naming": self.stem_naming,
            "keep_intermediate": bool(self.keep_intermediate),
            "temp_dir": self.temp_dir or str(app_config.default_temp_dir()),
            "ffmpeg": self.ffmpeg or ffmpeg_path(),
            "protocol": PROTOCOL_VERSION,
        }

    def track_label(self) -> str:
        """Nombre de la canción, sin extensión."""
        return track_name(Path(self.input_path))

    def planned_stems(self) -> list[str]:
        """Pistas que se van a escribir, en el orden en que se muestran."""
        return list(STEMS)

    def planned_output_dir(self) -> Path:
        """Carpeta donde quedarán los resultados (según los ajustes actuales)."""
        base = Path(self.output_dir).expanduser()
        return (
            base / sanitize_component(self.track_label())
            if self.create_subfolder
            else base
        )

    def expected_files(self) -> list[Path]:
        """Rutas probables de salida, para previsualizar antes de ejecutar."""
        fmt = self.format_info()
        base = self.planned_output_dir()
        if not self.create_subfolder:
            prefix = sanitize_component(self.track_label())
            return [
                base / f"{prefix} - {stem_filename(stem, self.stem_naming)}.{fmt.ext}"
                for stem in self.planned_stems()
            ]
        return [
            base / f"{stem_filename(stem, self.stem_naming)}.{fmt.ext}"
            for stem in self.planned_stems()
        ]


def request_from_config(
    config: "app_config.Config",
    input_path: str | Path,
    *,
    ffmpeg: str | None = None,
) -> SeparationRequest:
    """Construye la petición de una canción a partir de los ajustes actuales."""
    return SeparationRequest(
        input_path=str(input_path),
        output_dir=str(config.output_path()),
        model=config.model,
        shifts=config.shifts,
        overlap=config.overlap,
        segment=config.segment or None,
        jobs=config.jobs,
        device=config.device,
        format_key=config.formatch,
        options=config.options,
        create_subfolder=config.create_subfolder,
        stem_naming=config.stem_naming,
        ffmpeg=ffmpeg or ffmpeg_path(),
    )


# ---------------------------------------------------------------------------
# Estimación de tiempo (autocalibrada)
# ---------------------------------------------------------------------------

#: Segundos de cómputo por segundo de audio y por unidad de coste, antes de
#: haber medido nada en este equipo. En cuanto se separa algo, la tabla de
#: calibración los sustituye por los de esta máquina concreta.
_DEFAULT_UNIT_COST = {"cuda": 0.10, "cpu": 0.55}


def calibration_key(device: str, model: str) -> str:
    return f"{device}:{model}"


def estimate_seconds(
    *,
    duration: float | None,
    model: str,
    shifts: int,
    device: str,
    calibration: dict[str, float] | None = None,
) -> float | None:
    """Estimación de segundos de proceso, o ``None`` si no hay duración."""
    if not duration or duration <= 0:
        return None
    cost = relative_cost(model, shifts)
    table = calibration or {}
    unit = table.get(calibration_key(device, model))
    if unit is None:
        unit = _DEFAULT_UNIT_COST.get(device, _DEFAULT_UNIT_COST["cpu"])
    # 5 % de margen para carga del modelo y codificación.
    return duration * cost * unit * 1.05


def record_calibration(
    calibration: dict[str, float],
    *,
    device: str,
    model: str,
    shifts: int,
    duration: float | None,
    elapsed: float,
    smoothing: float = 0.4,
) -> dict[str, float]:
    """Actualiza la tabla de calibración con una medición real."""
    table = dict(calibration or {})
    if not duration or duration <= 0 or elapsed <= 0:
        return table
    cost = relative_cost(model, shifts)
    unit = elapsed / (duration * cost)
    key = calibration_key(device, model)
    previous = table.get(key)
    table[key] = (
        unit if previous is None else (1 - smoothing) * previous + smoothing * unit
    )
    return table


# ---------------------------------------------------------------------------
# Ejecución del worker
# ---------------------------------------------------------------------------


class WorkerError(RuntimeError):
    """Error controlado al lanzar o comunicarse con el worker."""


class JobRunner:
    """Lanza ``demucs_studio.worker`` y traduce sus eventos.

    El worker es un proceso aparte por dos motivos: mantiene la ventana
    respondiendo y permite cancelar de verdad (en Windows se mata el árbol de
    procesos con ``taskkill /T``, que incluye los ffmpeg hijos).
    """

    def __init__(
        self,
        engine: EngineStatus,
        request: SeparationRequest,
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_finished: Callable[["JobRunner"], None] | None = None,
        worker_root: Path | None = None,
    ) -> None:
        self.engine = engine
        self.request = request
        self.on_event = on_event or (lambda event: None)
        self.on_log = on_log or (lambda line: None)
        self.on_finished = on_finished or (lambda runner: None)
        self.worker_root = Path(worker_root or app_config.WORKER_SRC)

        self.process: subprocess.Popen[str] | None = None
        self.spec_path: Path | None = None
        self.returncode: int | None = None
        self.error: str | None = None
        self.error_hint: str | None = None
        self.outputs: list[str] = []
        self.result_dir: str | None = None
        self.cancelled = False
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    # --- API pública ------------------------------------------------------

    @property
    def running(self) -> bool:
        with self._lock:
            return self.process is not None and self.process.poll() is None

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)

    def start(self) -> None:
        """Arranca el proceso y empieza a leer sus salidas."""
        if self.process is not None:
            raise WorkerError("Este trabajo ya se ha lanzado.")

        app_config.ensure_dirs()
        spec_path = Path(tempfile.mkstemp(prefix="demucs-studio-", suffix=".json")[1])
        spec_path.write_text(
            json.dumps(self.request.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self.spec_path = spec_path

        python = self.engine.python
        if not python:
            raise WorkerError("No hay ningún intérprete con demucs configurado.")

        env = _subprocess_env()
        # El worker importa `demucs_studio.*`, que vive en worker_src.
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{self.worker_root}{os.pathsep}{existing}"
            if existing
            else str(self.worker_root)
        )
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("OMP_NUM_THREADS", str(min(8, os.cpu_count() or 4)))

        creationflags = _no_window_flags()
        if os.name == "nt":
            creationflags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

        popen_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = creationflags
        else:
            popen_kwargs["start_new_session"] = True

        try:
            self.process = subprocess.Popen(
                [python, "-m", "demucs_studio.worker", str(spec_path)],
                cwd=str(self.worker_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **popen_kwargs,
            )
        except OSError as error:
            self._cleanup_spec()
            raise WorkerError(f"No se pudo lanzar el motor: {error}") from error

        self.started_at = time.monotonic()
        self._spawn_reader(self.process.stdout, self._handle_stdout)
        self._spawn_reader(self.process.stderr, self._handle_stderr)
        waiter = threading.Thread(target=self._wait, name="demucs-studio-wait", daemon=True)
        waiter.start()
        self._threads.append(waiter)

    def cancel(self) -> None:
        """Pide al worker que pare; si no obedece, lo mata."""
        self.cancelled = True
        with self._lock:
            process = self.process
        if process is None or process.poll() is not None:
            return

        if os.name == "nt":
            # /T mata también los ffmpeg que haya lanzado el worker.
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=_no_window_flags(),
                    timeout=10,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
            return

        # POSIX (desarrollo): grupo de procesos completo.
        self._signal_group(signal.SIGTERM)
        deadline = time.monotonic() + 5.0
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            self._signal_group(signal.SIGKILL)

    def _signal_group(self, sig: int) -> None:
        with self._lock:
            process = self.process
        if process is None:
            return
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.send_signal(sig)
            except (ProcessLookupError, OSError):
                pass

    # --- Internos ---------------------------------------------------------

    def _spawn_reader(self, stream, handler: Callable[[str], None]) -> None:
        if stream is None:
            return

        def pump() -> None:
            try:
                for line in iter(stream.readline, ""):
                    handler(line)
            except (ValueError, OSError):
                pass
            finally:
                try:
                    stream.close()
                except OSError:
                    pass

        thread = threading.Thread(target=pump, name="demucs-studio-pipe", daemon=True)
        thread.start()
        self._threads.append(thread)

    def _handle_stdout(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if not line.startswith("{"):
            self.on_log(line)
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            self.on_log(line)
            return
        if not isinstance(event, dict):
            self.on_log(line)
            return
        self._absorb(event)
        try:
            self.on_event(event)
        except Exception:  # pragma: no cover - la interfaz no debe tumbar el runner
            log.exception("Fallo procesando un evento del motor")

    def _absorb(self, event: dict[str, Any]) -> None:
        kind = event.get("event")
        if kind == EVENT_DONE:
            self.outputs = [str(path) for path in event.get("files", [])]
            self.result_dir = event.get("outdir")
        elif kind == EVENT_ERROR:
            self.error = str(event.get("message") or "Error desconocido del motor")
            self.error_hint = event.get("hint")
        elif kind == EVENT_CANCELLED:
            self.cancelled = True

    def _handle_stderr(self, line: str) -> None:
        stripped = line.strip()
        if stripped:
            self.on_log(stripped)

    def _wait(self) -> None:
        process = self.process
        if process is None:
            return
        returncode = process.wait()
        for thread in list(self._threads):
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)
        self.returncode = returncode
        self.finished_at = time.monotonic()
        if returncode not in (0, None):
            if self.error is None and not self.cancelled:
                self.error = f"El motor terminó con código {returncode}"
        self._cleanup_spec()
        try:
            self.on_finished(self)
        except Exception:  # pragma: no cover
            log.exception("Fallo en el callback de finalización")

    def _cleanup_spec(self) -> None:
        if self.spec_path is not None:
            try:
                self.spec_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.spec_path = None


def run_request(
    engine: EngineStatus,
    request: SeparationRequest,
    *,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> tuple[bool, str | None, list[str]]:
    """Ejecuta una separación de forma síncrona (para pruebas y diagnóstico)."""
    done = threading.Event()
    runner = JobRunner(
        engine,
        request,
        on_event=on_event,
        on_log=on_log,
        on_finished=lambda _runner: done.set(),
    )
    runner.start()
    done.wait()
    ok = runner.returncode == 0 and runner.error is None
    return ok, runner.error, runner.outputs


def worker_command(engine: EngineStatus, spec_path: Path) -> list[str]:
    """Comando exacto del worker (útil para depurar y para el registro)."""
    return [engine.python or "python", "-m", "demucs_studio.worker", str(spec_path)]


def plan_summary(request: SeparationRequest) -> str:
    """Línea corta que describe lo que se va a hacer."""
    info = model_info(request.model)
    fmt = request.format_info()
    return (
        f"{info.label.split(' · ')[0]} · shifts {request.shifts} · "
        f"solape {request.overlap:.0%} · {fmt.label.split(' · ')[0]}"
    )


def iter_stem_pairs(stems: Iterable[str]) -> list[tuple[str, str]]:
    """Empareja cada pista con el nombre de archivo que le tocará."""
    return [(stem, stem_filename(stem, "standard")) for stem in stems]
