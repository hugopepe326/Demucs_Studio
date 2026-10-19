"""Instalación y reparación del motor de separación.

El instalador de Demucs Studio es deliberadamente pequeño: incluye la interfaz,
ffmpeg y ``uv``, pero **no** PyTorch. En el primer arranque (o al pulsar
«Reinstalar motor») se crea un entorno aislado en ``%LOCALAPPDATA%`` con
Python 3.12, PyTorch y demucs. Así se puede ofrecer CPU o CUDA sin superar el
límite de 2 GB por archivo de GitHub Releases.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from demucs_studio import config as app_config

LogCallback = Callable[[str], None]

#: Versiones probadas. Se pueden subir más adelante sin tocar la interfaz.
TORCH_VERSION = "2.5.1"
TORCHAUDIO_VERSION = "2.5.1"
DEMUCS_VERSION = "4.1.0"
NUMPY_VERSION = "1.26.4"

#: Dependencias de demucs 4.1.0 (se instalan sin arrastrar otro PyTorch).
DEMUCS_DEPENDENCIES = (
    "einops",
    "huggingface-hub",
    "julius>=0.2.3",
    "lameenc>=1.2",
    "pyyaml",
    "safetensors",
    "sphn>=0.1.12",
    "tqdm",
)

#: Índices oficiales de PyTorch para Windows.
PYTORCH_INDEX = {
    "cpu": "https://download.pytorch.org/whl/cpu",
    "cuda": "https://download.pytorch.org/whl/cu121",
}

DEVICE_LABELS = {
    "cpu": "CPU · funciona en cualquier PC",
    "cuda": "NVIDIA GPU · CUDA 12.1 (mucho más rápido)",
}


class BootstrapError(RuntimeError):
    """Fallo controlado durante la instalación del motor."""


class BootstrapCancelled(BootstrapError):
    """El usuario canceló la instalación."""


@dataclass(frozen=True)
class Step:
    """Un comando de instalación con su descripción."""

    title: str
    args: tuple[str, ...]
    timeout: float = 3600.0


def _no_window() -> int:
    if os.name == "nt":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0


def uv_path() -> Path:
    """Localiza ``uv.exe`` (incluido en el instalador o en el PATH)."""
    bundled = app_config.bundled_tool("uv")
    if bundled is not None:
        return bundled
    found = shutil.which("uv")
    if found:
        return Path(found)
    raise BootstrapError(
        "No se encontró uv.exe. Reinstala Demucs Studio para recuperar el "
        "instalador del motor."
    )


def nvidia_gpu() -> str | None:
    """Nombre de la GPU NVIDIA detectada por Windows, si hay alguna."""
    if os.name != "nt":
        return None
    command = (
        "(Get-CimInstance Win32_VideoController | "
        "Select-Object -ExpandProperty Name) -join \"`n\""
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (result.stdout or "").splitlines():
        name = line.strip()
        if name and "nvidia" in name.lower():
            return name
    return None


def recommended_device() -> str:
    """``cuda`` si hay una GPU NVIDIA; ``cpu`` en caso contrario."""
    return "cuda" if nvidia_gpu() else "cpu"


def build_steps(device: str) -> list[Step]:
    """Comandos necesarios para dejar el motor listo."""
    uv = str(uv_path())
    python = str(app_config.engine_python())
    index = PYTORCH_INDEX.get(device, PYTORCH_INDEX["cpu"])
    return [
        Step(
            "Preparando Python 3.12…",
            (
                uv,
                "venv",
                "--python",
                "3.12",
                str(app_config.ENGINE_DIR),
            ),
        ),
        Step(
            f"Instalando numpy {NUMPY_VERSION}…",
            (
                uv,
                "pip",
                "install",
                "--python",
                python,
                f"numpy=={NUMPY_VERSION}",
            ),
        ),
        Step(
            (
                "Descargando PyTorch para NVIDIA (CUDA 12.1)…"
                if device == "cuda"
                else "Descargando PyTorch para CPU…"
            ),
            (
                uv,
                "pip",
                "install",
                "--python",
                python,
                f"torch=={TORCH_VERSION}",
                f"torchaudio=={TORCHAUDIO_VERSION}",
                "--index-url",
                index,
            ),
        ),
        Step(
            f"Instalando demucs {DEMUCS_VERSION}…",
            (
                uv,
                "pip",
                "install",
                "--python",
                python,
                f"demucs=={DEMUCS_VERSION}",
                "--no-deps",
            ),
        ),
        Step(
            "Instalando dependencias de demucs…",
            (
                uv,
                "pip",
                "install",
                "--python",
                python,
                *DEMUCS_DEPENDENCIES,
            ),
        ),
    ]


def _environment() -> dict[str, str]:
    app_config.ensure_dirs()
    env = os.environ.copy()
    env["UV_CACHE_DIR"] = str(app_config.UV_CACHE_DIR)
    env["UV_PYTHON_INSTALL_DIR"] = str(app_config.PYTHON_INSTALL_DIR)
    env["UV_LINK_MODE"] = "copy"
    env["UV_NO_PROGRESS"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class EngineInstaller:
    """Ejecuta la instalación del motor y permite cancelarla."""

    def __init__(
        self,
        device: str = "cpu",
        *,
        on_log: LogCallback | None = None,
        on_step: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.device = device if device in PYTORCH_INDEX else "cpu"
        self.on_log = on_log or (lambda line: None)
        self.on_step = on_step or (lambda index, total, title: None)
        self._cancelled = threading.Event()
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    # --- Control ---------------------------------------------------------

    def cancel(self) -> None:
        """Pide cancelar y mata el comando en curso."""
        self._cancelled.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=_no_window(),
                        timeout=10,
                    )
                else:
                    process.terminate()
            except (OSError, subprocess.SubprocessError):
                pass

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    # --- Instalación -----------------------------------------------------

    def run(self) -> str:
        """Instala el motor y devuelve la ruta del intérprete creado."""
        if self._cancelled.is_set():
            raise BootstrapCancelled("Instalación cancelada.")

        app_config.ensure_dirs()
        uv_path()  # valida que uv está disponible antes de borrar nada

        # Instalación limpia: evita venvs a medias de intentos anteriores.
        if app_config.ENGINE_DIR.exists():
            self.on_log("Eliminando el motor anterior…")
            shutil.rmtree(app_config.ENGINE_DIR, ignore_errors=True)

        steps = build_steps(self.device)
        total = len(steps)
        env = _environment()
        for index, step in enumerate(steps, start=1):
            if self._cancelled.is_set():
                raise BootstrapCancelled("Instalación cancelada.")
            self.on_step(index, total, step.title)
            self.on_log("")
            self.on_log(f"== {index}/{total} · {step.title}")
            self.on_log("$ " + " ".join(step.args))
            self._run_step(step, env)

        python = app_config.engine_python()
        if not python.is_file():
            raise BootstrapError(
                "La instalación terminó pero no se creó el intérprete del motor."
            )

        self.on_log("")
        self.on_log("Comprobando que el motor puede separar audio…")
        from demucs_studio.engine import probe_interpreter

        status = probe_interpreter(python, timeout=240)
        if not status.available:
            raise BootstrapError(
                "El motor se instaló pero no superó la comprobación: "
                f"{status.error or 'error desconocido'}"
            )

        marker = app_config.ENGINE_DIR / "install.json"
        try:
            marker.write_text(
                json.dumps(
                    {
                        "device": self.device,
                        "torch": status.torch_version,
                        "demucs": status.demucs_version,
                        "cuda": status.cuda,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

        self.on_log(
            f"Motor listo: demucs {status.demucs_version} · "
            f"torch {status.torch_version} · {status.device_summary}"
        )
        return str(python)

    def _run_step(self, step: Step, env: dict[str, str]) -> None:
        try:
            process = subprocess.Popen(
                list(step.args),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=_no_window(),
            )
        except OSError as error:
            raise BootstrapError(f"No se pudo ejecutar uv: {error}") from error

        with self._lock:
            self._process = process

        try:
            if process.stdout is not None:
                for line in process.stdout:
                    if self._cancelled.is_set():
                        try:
                            process.kill()
                        except OSError:
                            pass
                        raise BootstrapCancelled("Instalación cancelada.")
                    text = line.rstrip()
                    if text:
                        self.on_log(text)
            code = process.wait()
        finally:
            with self._lock:
                self._process = None

        if self._cancelled.is_set():
            raise BootstrapCancelled("Instalación cancelada.")
        if code != 0:
            raise BootstrapError(
                f"El paso «{step.title}» falló con código {code}. "
                "Revisa el registro y comprueba la conexión a Internet."
            )


def remove_engine() -> None:
    """Borra el venv del motor (los modelos descargados se conservan)."""
    shutil.rmtree(app_config.ENGINE_DIR, ignore_errors=True)


def engine_marker() -> dict:
    """Lee el marcador de instalación, si existe."""
    path = app_config.ENGINE_DIR / "install.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
