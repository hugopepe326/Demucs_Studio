"""Worker que ejecuta una separación dentro del venv con demucs y PyTorch.

Se lanza como ``python -m demucs_studio.worker <spec.json>`` desde el proceso
de la interfaz y se comunica por **stdout con líneas JSON** (un evento por
línea). stderr queda libre para avisos de torch, que la interfaz vuelca al
registro.

Eventos emitidos
----------------
``{"event": "stage", "stage": ..., "message": ...}``
    Cambio de fase (carga de modelo, lectura, separación, codificación).
``{"event": "progress", "percent": 0-100, "detail": ...}``
    Avance global del trabajo.
``{"event": "stem_start"/"stem_done", "stem": ..., "path": ...}``
    Cada pista empieza o termina de escribirse.
``{"event": "done", "outdir": ..., "files": [...]}``
    Trabajo completado.
``{"event": "cancelled"}`` / ``{"event": "error", "message", "hint"}``
    Cancelación pedida por el usuario o fallo controlado.

La cancelación funciona en dos capas: SIGTERM marca una bandera y el callback
de demucs lanza ``KeyboardInterrupt`` (el mecanismo documentado por demucs
para abortar); además la interfaz mata el grupo de procesos completo, con lo
que también caen los ffmpeg hijos.
"""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

# El runner añade la raíz del proyecto al PYTHONPATH, así que estos imports
# funcionan aunque el worker se ejecute desde el venv.
from demucs_studio.formats import (
    INTERMEDIATE_BIT_DEPTH,
    ffmpeg_command,
    format_info,
    parse_progress_line,
    sanitize_options,
)
from demucs_studio.presets import STEMS, stem_filename
from demucs_studio.util import human_duration, sanitize_component, unique_directory

# Reparto del 0-100 % entre las fases del proceso.
SEPARATE_START = 4.0
SEPARATE_SPAN = 74.0
ENCODE_START = 78.0
ENCODE_SPAN = 22.0

PROGRESS_MIN_INTERVAL = 0.15


def emit(payload: dict[str, Any]) -> None:
    """Escribe un evento como una línea JSON y vacía el búfer."""
    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        # La interfaz se cerró; el proceso morirá en breve.
        pass


def log_line(message: str) -> None:
    """Aviso para el registro de la interfaz (fuera del protocolo JSON)."""
    print(message, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Cancelación
# ---------------------------------------------------------------------------


class Aborted(Exception):
    """El usuario pidió cancelar el trabajo."""


class Cancellation:
    """Bandera de cancelación compartida con los manejadores de señal."""

    def __init__(self) -> None:
        self._flag = False

    def request(self, *_args: Any) -> None:
        self._flag = True

    @property
    def requested(self) -> bool:
        return self._flag

    def check(self) -> None:
        """Lanza :class:`Aborted` si se pidió cancelar."""
        if self._flag:
            raise Aborted()


# ---------------------------------------------------------------------------
# Progreso
# ---------------------------------------------------------------------------


class PassTracker:
    """Traduce los callback de demucs a una fracción 0-1 por pasada.

    demucs avisa al empezar y al terminar cada segmento con su desplazamiento
    en muestras. Como los segmentos van en orden creciente, usar el
    desplazamiento sobre la duración total da una barra monótona dentro de
    cada pasada (``models × shifts`` pasadas por canción).
    """

    def __init__(self) -> None:
        self.pass_key: tuple[int, int] | None = None
        self.last_offset = 0
        self.delta = 0

    def fraction(self, info: dict[str, Any]) -> float:
        key = (int(info.get("model_idx_in_bag") or 0), int(info.get("shift_idx") or 0))
        offset = int(info.get("segment_offset") or 0)
        if key != self.pass_key:
            self.pass_key = key
            self.last_offset = 0
            self.delta = 0
        delta = offset - self.last_offset
        if delta > 0:
            self.delta = delta
        self.last_offset = offset
        length = int(info.get("audio_length") or 0)
        if length <= 0:
            return 0.0
        progressed = offset + (self.delta if info.get("state") == "end" else 0)
        return max(0.0, min(1.0, progressed / length))


class Reporter:
    """Emite eventos de progreso con límite de frecuencia."""

    def __init__(self) -> None:
        self._last_time = 0.0
        self._last_percent = -1.0

    def stage(self, stage: str, message: str, *, percent: float | None = None) -> None:
        payload: dict[str, Any] = {"event": "stage", "stage": stage, "message": message}
        if percent is not None:
            payload["percent"] = percent
            self._last_percent = percent
        emit(payload)

    def progress(self, percent: float, detail: str = "", *, force: bool = False) -> None:
        now = time.monotonic()
        if not force:
            if now - self._last_time < PROGRESS_MIN_INTERVAL and abs(percent - self._last_percent) < 0.5:
                return
        self._last_time = now
        self._last_percent = percent
        emit({"event": "progress", "percent": round(percent, 2), "detail": detail})


# ---------------------------------------------------------------------------
# Guardado y transcodificación
# ---------------------------------------------------------------------------


def transcode(
    ffmpeg: str,
    src: Path,
    dst: Path,
    fmt,
    options,
    duration: float | None,
    *,
    on_progress: Callable[[float], None],
    cancellation: Cancellation,
) -> None:
    """Convierte ``src`` (WAV) a ``dst`` con ffmpeg, informando del avance."""
    command = ffmpeg_command(ffmpeg, str(src), str(dst), fmt, options)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    errors: list[str] = []

    def drain_stderr() -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            line = line.strip()
            if line:
                errors.append(line)

    drainer = threading.Thread(target=drain_stderr, daemon=True)
    drainer.start()

    try:
        if process.stdout is not None:
            for line in process.stdout:
                if cancellation.requested:
                    process.terminate()
                    raise Aborted
                seconds = parse_progress_line(line)
                if seconds is not None and duration and duration > 0:
                    on_progress(max(0.0, min(1.0, seconds / duration)))
        returncode = process.wait()
    except Aborted:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        raise
    finally:
        drainer.join(timeout=2.0)

    if returncode != 0:
        tail = errors[-1] if errors else "ffmpeg falló"
        raise RuntimeError(f"No se pudo codificar {dst.name}: {tail}")


def save_native(save_audio, tensor, path: Path, samplerate: int, fmt, options) -> None:
    """Escribe una pista en un formato que demucs ya sabe generar."""
    from demucs_studio.formats import demucs_save_kwargs

    kwargs = demucs_save_kwargs(fmt, options)
    save_audio(tensor, str(path), samplerate=samplerate, **kwargs)


# ---------------------------------------------------------------------------
# Trabajo principal
# ---------------------------------------------------------------------------


def resolve_device(requested: str, torch) -> str:
    """Convierte ``auto`` en el dispositivo real disponible."""
    if requested in ("cuda", "cpu"):
        return requested
    return "cuda" if torch.cuda.is_available() else "cpu"


def model_max_segment(model) -> float:
    """Segmento máximo admitido por el modelo (igual que hace demucs CLI)."""
    try:
        from demucs.apply import BagOfModels
        from demucs.htdemucs import HTDemucs

        if isinstance(model, HTDemucs):
            return float(model.segment)
        if isinstance(model, BagOfModels):
            return float(model.max_allowed_segment)
    except Exception:  # pragma: no cover - según versión de demucs
        pass
    return float("inf")


def hint_for_error(error: BaseException) -> str | None:
    """Sugerencia accionable según el fallo."""
    name = type(error).__name__
    text = str(error).lower()
    if "outofmemory" in name.lower() or "out of memory" in text:
        return (
            "Se agotó la memoria de la GPU. Prueba un modelo más ligero, baja el "
            "segmento o cambia el dispositivo a CPU."
        )
    if "loadaudioerror" in name.lower() or "could not read the file" in text:
        return "No se pudo leer el audio: revisa que el archivo no esté dañado o conviértelo a WAV."
    if "loadmodelerror" in name.lower():
        return "No se pudo cargar el modelo. Comprueba la conexión: la primera vez se descarga."
    if "urlerror" in name.lower() or "connection" in text or "timed out" in text:
        return "Parece un problema de red al descargar el modelo de demucs."
    if "ffmpeg" in text and "not" in text:
        return (
            "Falta ffmpeg. Reinstala Demucs Studio (el instalador lo incluye) "
            "o elige un formato WAV, FLAC o MP3."
        )
    if "no space left" in text or "not enough space" in text:
        return "No queda espacio en disco para los archivos temporales o la salida."
    if "filenotfounderror" in name.lower():
        return "El archivo de entrada desapareció o no se puede leer."
    return None


def list_models_and_exit() -> int:
    """Modo de diagnóstico: imprime los modelos que ofrece este demucs."""
    try:
        from demucs.api import list_models
    except Exception as error:
        emit({"event": "error", "message": f"No se pudo importar demucs: {error}"})
        return 1
    try:
        models = list_models()
    except Exception as error:
        emit({"event": "error", "message": f"No se pudo listar los modelos: {error}"})
        return 1
    emit({"event": "models", "bag": list(models.get("bag", [])), "single": list(models.get("single", []))})
    return 0


def run(spec: dict[str, Any], cancellation: Cancellation) -> int:
    """Ejecuta el trabajo descrito en ``spec`` y devuelve el código de salida."""
    reporter = Reporter()
    input_path = Path(spec["input"]).expanduser()
    output_root = Path(spec["output_dir"]).expanduser()
    fmt = format_info(spec.get("format", "wav"))
    options = sanitize_options(fmt, _options(spec.get("options") or {}))
    ffmpeg = spec.get("ffmpeg") or shutil.which("ffmpeg")

    if not input_path.is_file():
        emit({
            "event": "error",
            "message": f"No existe el archivo de entrada: {input_path}",
            "hint": "Comprueba que la canción siga en su sitio.",
        })
        return 2

    if not output_root.is_dir():
        try:
            output_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            emit({
                "event": "error",
                "message": f"No se pudo crear la carpeta de salida: {error}",
                "hint": "Elige otra carpeta donde tengas permiso de escritura.",
            })
            return 2

    if not fmt.native and not ffmpeg:
        emit({
            "event": "error",
            "message": f"El formato {fmt.label} necesita ffmpeg y no está disponible.",
            "hint": (
                "Reinstala Demucs Studio (incluye ffmpeg) o elige WAV, FLAC o MP3."
            ),
        })
        return 2

    # Importamos demucs aquí para que los errores de importación se informen
    # como un evento y no como un traceback suelto en stderr.
    reporter.stage("start", "Preparando el motor…", percent=0.0)
    try:
        import torch
        from demucs.api import Separator, save_audio
    except Exception as error:
        message = f"No se pudo importar demucs o PyTorch: {type(error).__name__}: {error}"
        emit({"event": "error", "message": message, "hint": hint_for_error(error)})
        return 3

    device = resolve_device(spec.get("device", "auto"), torch)
    shifts = max(0, int(spec.get("shifts") or 0))
    overlap = float(spec.get("overlap") or 0.25)
    segment = spec.get("segment") or None
    jobs = int(spec.get("jobs") or 0)
    model_name = spec.get("model") or "htdemucs"

    # Carpeta temporal para los WAV intermedios de los formatos no nativos.
    work_dir = Path(spec.get("temp_dir") or tempfile.gettempdir()) / "demucs-studio"
    work_dir.mkdir(parents=True, exist_ok=True)
    session_dir = Path(tempfile.mkdtemp(prefix=f"{input_path.stem[:40]}-", dir=str(work_dir)))
    keep_intermediate = bool(spec.get("keep_intermediate"))

    reporter.stage(
        "model",
        f"Cargando el modelo {model_name} en {device.upper()}…",
        percent=1.0,
    )
    if cancellation.requested:
        raise Aborted

    try:
        separator = Separator(
            model=model_name,
            device=device,
            shifts=shifts,
            overlap=overlap,
            split=bool(spec.get("split", True)),
            segment=segment,
            jobs=jobs,
            progress=False,
            callback=None,
        )
    except Exception as error:
        emit({
            "event": "error",
            "message": f"No se pudo cargar el modelo {model_name}: {type(error).__name__}: {error}",
            "hint": hint_for_error(error),
        })
        shutil.rmtree(session_dir, ignore_errors=True)
        return 4

    # Si el modelo impone un segmento máximo, avisamos antes de fallar a medias.
    max_segment = model_max_segment(separator.model)
    if segment is not None and segment > max_segment:
        emit({
            "event": "error",
            "message": f"El modelo {model_name} no admite segmentos de {segment} s (máximo {max_segment:g} s).",
            "hint": "Baja el segmento en las opciones avanzadas o elige otro modelo.",
        })
        shutil.rmtree(session_dir, ignore_errors=True)
        return 4

    tracker = PassTracker()

    def callback(info: dict[str, Any]) -> None:
        # Mecanismo documentado por demucs para abortar una separación.
        if cancellation.requested:
            raise KeyboardInterrupt
        models_in_bag = int(info.get("models") or 1)
        shift_index = int(info.get("shift_idx") or 0)
        model_index = int(info.get("model_idx_in_bag") or 0)
        shifts_total = max(1, shifts)
        passes = max(1, models_in_bag) * shifts_total
        overall = (model_index * shifts_total + shift_index + tracker.fraction(info)) / passes
        percent = SEPARATE_START + SEPARATE_SPAN * max(0.0, min(1.0, overall))
        detail = f"submodelo {model_index + 1}/{models_in_bag}, pasada {shift_index + 1}/{shifts_total}"
        reporter.progress(percent, detail)

    try:
        separator.update_parameter(callback=callback)
    except Exception:
        # Si la versión de demucs no permite actualizar el callback, seguimos
        # sin progreso fino en vez de abortar.
        log_line("Aviso: no se pudo instalar el callback de progreso.")

    stem_names = list(STEMS)
    files: list[str] = []
    target_dir: Path | None = None

    try:
        reporter.stage("audio", f"Leyendo {input_path.name}…", percent=2.0)
        _origin, separated = separator.separate_audio_file(input_path)
        cancellation.check()

        samplerate = int(separator.samplerate)
        duration = None
        try:
            duration = float(separated[stem_names[0]].shape[-1]) / samplerate
        except Exception:
            duration = None
        log_line(
            f"Separación lista: {len(separated)} pistas, {samplerate} Hz, "
            f"{human_duration(duration)}"
        )

        # Orden de salida: drums, bass, vocals, other (el que pide la interfaz).
        ordered = [stem for stem in stem_names if stem in separated]
        ordered += [stem for stem in separated if stem not in ordered]

        track = sanitize_component(spec.get("track") or input_path.stem)
        if spec.get("create_subfolder", True):
            target_dir = unique_directory(output_root / track)
        else:
            target_dir = output_root
        target_dir.mkdir(parents=True, exist_ok=True)

        total_stems = max(1, len(ordered))
        reporter.stage("encode", "Escribiendo las pistas…", percent=ENCODE_START)

        for index, stem in enumerate(ordered):
            cancellation.check()
            tensor = separated[stem]
            stored = stem_filename(stem, spec.get("stem_naming", "standard"))
            prefix = "" if spec.get("create_subfolder", True) else f"{track} - "
            final = target_dir / f"{prefix}{stored}.{fmt.ext}"

            emit({
                "event": "stem_start",
                "stem": stem,
                "index": index,
                "total": total_stems,
                "path": str(final),
            })

            if fmt.native:
                save_native(save_audio, tensor, final, samplerate, fmt, options)
            else:
                # WAV intermedio a 24 bits: se conserva si el usuario lo pidió.
                if keep_intermediate:
                    temp_wav = target_dir / f"{prefix}{stored}.wav"
                else:
                    temp_wav = session_dir / f"{index:02d}-{stored}.wav"
                save_audio(tensor, str(temp_wav), samplerate=samplerate,
                           bits_per_sample=INTERMEDIATE_BIT_DEPTH, clip=options.clip_mode)
                cancellation.check()

                def stem_progress(fraction: float, index: int = index) -> None:
                    percent = ENCODE_START + ENCODE_SPAN * ((index + fraction) / total_stems)
                    reporter.progress(percent, f"Convirtiendo {stored} a {fmt.ext.upper()}…")

                transcode(
                    ffmpeg,
                    temp_wav,
                    final,
                    fmt,
                    options,
                    duration,
                    on_progress=stem_progress,
                    cancellation=cancellation,
                )
                if not keep_intermediate:
                    try:
                        temp_wav.unlink()
                    except OSError:
                        pass

            files.append(str(final))
            emit({"event": "stem_done", "stem": stem, "path": str(final)})
            reporter.progress(
                ENCODE_START + ENCODE_SPAN * ((index + 1) / total_stems),
                f"{stored}.{fmt.ext} listo",
                force=True,
            )

        results_dir = str(target_dir)
    except KeyboardInterrupt:
        raise Aborted
    except Aborted:
        raise
    except Exception as error:
        emit({
            "event": "error",
            "message": f"{type(error).__name__}: {error}",
            "hint": hint_for_error(error),
            "traceback": traceback.format_exc(),
        })
        shutil.rmtree(session_dir, ignore_errors=True)
        return 5

    shutil.rmtree(session_dir, ignore_errors=True)
    reporter.stage("cleanup", "Limpiando archivos temporales…", percent=99.0)
    emit({"event": "done", "outdir": results_dir, "files": files})
    return 0


def _options(data: dict[str, Any]):
    from demucs_studio.formats import FormatOptions

    return FormatOptions.from_dict(data)


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del worker."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--list-models":
        return list_models_and_exit()
    if not argv:
        print("uso: python -m demucs_studio.worker <spec.json> | --list-models", file=sys.stderr)
        return 64

    cancellation = Cancellation()
    signal.signal(signal.SIGTERM, cancellation.request)
    signal.signal(signal.SIGINT, cancellation.request)

    spec_path = Path(argv[0])
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        emit({"event": "error", "message": f"No se pudo leer el trabajo: {error}"})
        return 65

    try:
        return run(spec, cancellation)
    except Aborted:
        emit({"event": "cancelled"})
        return 0
    except KeyboardInterrupt:
        emit({"event": "cancelled"})
        return 0
    except Exception as error:  # pragma: no cover - red de seguridad
        emit({
            "event": "error",
            "message": f"Fallo inesperado: {type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        })
        return 70


if __name__ == "__main__":
    sys.exit(main())
