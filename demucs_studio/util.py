"""Utilidades compartidas: rutas, archivos de audio y formateo de datos."""

from __future__ import annotations

import os
import shutil
import subprocess
import unicodedata
from pathlib import Path

from .formats import AUDIO_EXTENSIONS

#: Tope de archivos que escaneamos al soltar una carpeta, para no colgar la
#: interfaz si alguien arrastra media biblioteca de música.
MAX_FILES_PER_DROP = 200

#: Carpetas que nunca vale la pena recorrer buscando música.
_SKIP_DIRS = {".git", "node_modules", ".cache", "__pycache__", ".venv", "venv"}


def is_audio_file(path: Path) -> bool:
    """``True`` si la ruta parece un archivo de audio soportado."""
    return path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS


def audio_files_in_dir(directory: Path, *, recursive: bool = True) -> list[Path]:
    """Lista los archivos de audio de una carpeta, ordenados por ruta."""
    found: list[Path] = []
    if recursive:
        for root, dirs, files in os.walk(directory):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS)
            for name in sorted(files):
                candidate = Path(root) / name
                if candidate.suffix.lower() in AUDIO_EXTENSIONS:
                    found.append(candidate)
                    if len(found) >= MAX_FILES_PER_DROP:
                        return found
    else:
        for candidate in sorted(directory.iterdir()):
            if candidate.suffix.lower() in AUDIO_EXTENSIONS:
                found.append(candidate)
    return found


def collect_audio_files(paths, *, recursive: bool = True) -> tuple[list[Path], list[Path]]:
    """Normaliza lo que se suelte en la ventana.

    Acepta archivos de audio, carpetas (se recorren) y descarta lo demás.

    Returns:
        ``(aceptados, rechazados)``: rutas de audio listas para separar y rutas
        que no se pudieron usar, para poder avisar al usuario.
    """
    accepted: list[Path] = []
    rejected: list[Path] = []
    seen: set[str] = set()

    for raw in paths:
        path = Path(raw).expanduser()
        candidates: list[Path] = []
        if path.is_dir():
            candidates = audio_files_in_dir(path, recursive=recursive)
            if not candidates:
                rejected.append(path)
        elif is_audio_file(path):
            candidates = [path]
        else:
            rejected.append(path)

        for candidate in candidates:
            try:
                key = str(candidate.resolve())
            except OSError:
                key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            accepted.append(candidate)
            if len(accepted) >= MAX_FILES_PER_DROP:
                return accepted, rejected

    return accepted, rejected


def track_name(path: Path) -> str:
    """Nombre de la canción sin extensión (lo que demucs llama ``{track}``)."""
    return Path(path).name.rsplit(".", 1)[0]


def sanitize_component(name: str) -> str:
    """Convierte texto libre en algo seguro para un nombre de archivo."""
    normalized = unicodedata.normalize("NFC", name)
    cleaned = "".join(
        ch if ch.isalnum() or ch in " .-_()[]&'" else "_" for ch in normalized
    )
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned or "pista"


def unique_directory(base: Path) -> Path:
    """Devuelve ``base`` o ``base (2)``, ``base (3)``… si ya existe."""
    if not base.exists():
        return base
    for index in range(2, 1000):
        candidate = base.with_name(f"{base.name} ({index})")
        if not candidate.exists():
            return candidate
    return base.with_name(f"{base.name} ({os.getpid()})")


def human_duration(seconds: float | None) -> str:
    """Formatea segundos como ``3:45`` o ``1:02:33``."""
    if seconds is None or seconds <= 0:
        return "--:--"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def human_size(num_bytes: float | None) -> str:
    """Formatea bytes en unidades legibles."""
    if not num_bytes:
        return "0 B"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def which(*names: str) -> str | None:
    """Primer ejecutable disponible de la lista."""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


#: Emuladores de terminal probados en Linux, para desarrollo.
_TERMINALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("konsole", ("konsole", "--hold", "-e")),
    ("gnome-terminal", ("gnome-terminal", "--")),
    ("kgx", ("kgx", "--")),
    ("xfce4-terminal", ("xfce4-terminal", "--hold", "-e")),
    ("alacritty", ("alacritty", "-e")),
    ("kitty", ("kitty",)),
    ("foot", ("foot",)),
    ("wezterm", ("wezterm", "start", "--")),
    ("xterm", ("xterm", "-e")),
)


def open_in_terminal(command: str) -> bool:
    """Ejecuta ``command`` en una terminal visible (diagnóstico)."""
    if os.name == "nt":
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", "cmd", "/k", command],
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            return True
        except OSError:
            return False
    for name, prefix in _TERMINALS:
        if shutil.which(name):
            try:
                subprocess.Popen(
                    [
                        *prefix,
                        "bash",
                        "-lc",
                        f"{command}; echo; echo '--- pulsa Enter para cerrar ---'; read",
                    ],
                    start_new_session=True,
                )
                return True
            except OSError:
                continue
    return False


def reveal_in_file_manager(path: Path) -> bool:
    """Abre la carpeta (y selecciona el archivo) en el explorador."""
    target = Path(path)
    if os.name == "nt":
        try:
            if target.is_file():
                subprocess.Popen(["explorer", f"/select,{target}"])
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
            return True
        except OSError:
            return False

    text = str(target)
    for command in (
        ("xdg-open", text),
        ("dolphin", text),
        ("nautilus", text),
        ("thunar", text),
        ("nemo", text),
    ):
        if shutil.which(command[0]):
            try:
                subprocess.Popen(list(command), start_new_session=True)
                return True
            except OSError:
                continue
    return False


def ffprobe_duration(ffprobe: str, path: Path, *, timeout: float = 20.0) -> float | None:
    """Duración en segundos de un archivo de audio, o ``None`` si falla."""
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None
