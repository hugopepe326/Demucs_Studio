"""Formatos de audio de salida y cómo generarlos.

Demucs solo sabe escribir WAV, FLAC y MP3 (ver ``demucs.audio.save_audio``).
Para el resto de formatos guardamos un WAV intermedio y lo codificamos con
ffmpeg, que demucs ya necesita para leer entradas exóticas.

Cada formato declara si demucs puede escribirlo directamente (*native*) y qué
controles de calidad tienen sentido, de modo que la interfaz muestre solo los
que aplican al formato elegido.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Iterable

# Extensiones que aceptamos como entrada (las que ffmpeg sabe leer).
AUDIO_EXTENSIONS: tuple[str, ...] = (
    ".aac", ".ac3", ".aif", ".aifc", ".aiff", ".alac", ".amr", ".ape", ".au",
    ".caf", ".dts", ".flac", ".m4a", ".m4b", ".mka", ".mp2", ".mp3", ".mp4",
    ".mpc", ".oga", ".ogg", ".opus", ".spx", ".wav", ".webm", ".wma", ".wv",
)


@dataclass(frozen=True)
class FormatInfo:
    """Descripción de un formato de salida y sus controles de calidad."""

    key: str
    label: str
    ext: str
    kind: str
    """``pcm`` (sin comprimir), ``lossless`` o ``lossy``."""
    native: bool
    """``True`` si demucs puede escribir el archivo directamente."""
    bit_depths: tuple[int, ...] = ()
    float_option: bool = False
    bitrates: tuple[int, ...] = ()
    presets: tuple[int, ...] = ()
    """Presets de calidad de lameenc para MP3 (2 = mejor, 7 = más rápido)."""
    vbr_qualities: tuple[int, ...] = ()
    """Calidad VBR de Vorbis con ``-q:a``."""
    note: str = ""


FORMATS: tuple[FormatInfo, ...] = (
    FormatInfo(
        "wav",
        "WAV · sin comprimir (PCM)",
        "wav",
        kind="pcm",
        native=True,
        bit_depths=(16, 24),
        float_option=True,
        note="Máxima fidelidad para editar en un DAW. Archivos grandes.",
    ),
    FormatInfo(
        "flac",
        "FLAC · sin pérdidas comprimido",
        "flac",
        kind="lossless",
        native=True,
        bit_depths=(16, 24),
        note="Idéntico al WAV en calidad, ocupando la mitad de espacio.",
    ),
    FormatInfo(
        "mp3",
        "MP3 · compatible con todo",
        "mp3",
        kind="lossy",
        native=True,
        bitrates=(128, 192, 256, 320),
        # demucs acepta presets de 2 (mejor) a 7 (más rápido) para lameenc.
        presets=(2, 3, 4, 5, 6, 7),
        note="Se reproduce en cualquier reproductor, coche o móvil.",
    ),
    FormatInfo(
        "ogg",
        "OGG Vorbis · calidad por unidad",
        "ogg",
        kind="lossy",
        native=False,
        vbr_qualities=(3, 5, 6, 8, 10),
        note="Mejor que MP3 al mismo tamaño. Requiere ffmpeg.",
    ),
    FormatInfo(
        "opus",
        "Opus · el más eficiente",
        "opus",
        kind="lossy",
        native=False,
        bitrates=(64, 96, 128, 160, 192, 256),
        note="Excelente calidad con bitrates bajos. Requiere ffmpeg.",
    ),
    FormatInfo(
        "m4a",
        "M4A · AAC para Apple y móvil",
        "m4a",
        kind="lossy",
        native=False,
        bitrates=(128, 192, 256, 320),
        note="El estándar en iTunes/iPhone y en muchos reproductores. Requiere ffmpeg.",
    ),
)

FORMATS_BY_KEY = {fmt.key: fmt for fmt in FORMATS}
DEFAULT_FORMAT = "wav"


def format_info(key: str) -> FormatInfo:
    """Devuelve el formato pedido; si no existe, cae a WAV."""
    return FORMATS_BY_KEY.get(key, FORMATS_BY_KEY[DEFAULT_FORMAT])


def available_formats() -> tuple[FormatInfo, ...]:
    """Todos los formatos, incluidos los que necesitan ffmpeg.

    La interfaz decide cuáles marcar como no disponibles si falta ffmpeg.
    """
    return FORMATS


# --- Opciones de calidad -------------------------------------------------


@dataclass
class FormatOptions:
    """Opciones de calidad seleccionadas en la interfaz."""

    bit_depth: int = 16
    float32: bool = False
    bitrate_kbps: int = 320
    mp3_preset: int = 2
    vbr_quality: int = 6
    clip_mode: str = "rescale"
    """``rescale`` evita clipping reescalando; ``clamp`` recorta; ``none`` deja igual."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FormatOptions":
        """Construye las opciones ignorando claves desconocidas o inválidas."""
        valid = {f.name for f in fields(cls)}
        clean = {k: v for k, v in (data or {}).items() if k in valid}
        try:
            return cls(**clean)
        except TypeError:
            return cls()


CLIP_MODES = ("rescale", "clamp", "none")
CLIP_MODE_LABELS = {
    "rescale": "Evitar clipping reescalando (recomendado)",
    "clamp": "Recortar picos (clamp)",
    "none": "No tocar la señal",
}


def sanitize_options(fmt: FormatInfo, options: FormatOptions) -> FormatOptions:
    """Ajusta las opciones a valores válidos para ``fmt``."""
    out = FormatOptions.from_dict(options.to_dict())
    if fmt.bit_depths and out.bit_depth not in fmt.bit_depths:
        out.bit_depth = fmt.bit_depths[0]
    if not fmt.bit_depths:
        out.bit_depth = 16
    if not fmt.float_option:
        out.float32 = False
    if fmt.bitrates and out.bitrate_kbps not in fmt.bitrates:
        # Elige el bitrate más cercano por debajo si el guardado no aplica.
        lower = [b for b in fmt.bitrates if b <= out.bitrate_kbps]
        out.bitrate_kbps = max(lower) if lower else fmt.bitrates[0]
    if fmt.presets and out.mp3_preset not in fmt.presets:
        out.mp3_preset = fmt.presets[0]
    if fmt.vbr_qualities and out.vbr_quality not in fmt.vbr_qualities:
        out.vbr_quality = fmt.vbr_qualities[len(fmt.vbr_qualities) // 2]
    if out.clip_mode not in CLIP_MODES:
        out.clip_mode = "rescale"
    return out


def demucs_save_kwargs(fmt: FormatInfo, options: FormatOptions) -> dict[str, Any]:
    """Argumentos para ``demucs.api.save_audio`` cuando el formato es nativo.

    Solo se incluyen las claves que tienen sentido para el formato: pasar
    ``bitrate`` a un WAV o ``bits_per_sample`` a un MP3 no aporta nada.
    """
    opts = sanitize_options(fmt, options)
    kwargs: dict[str, Any] = {"clip": opts.clip_mode}
    if fmt.key == "wav":
        kwargs["as_float"] = bool(opts.float32)
        kwargs["bits_per_sample"] = 32 if opts.float32 else opts.bit_depth
    elif fmt.key == "flac":
        kwargs["bits_per_sample"] = opts.bit_depth
    elif fmt.key == "mp3":
        kwargs["bitrate"] = opts.bitrate_kbps
        kwargs["preset"] = opts.mp3_preset
    return kwargs


#: Bits por muestra del WAV intermedio que se pasa a ffmpeg. 24 bits evita
#: perder resolución antes de codificar, a cambio de algo más de disco temporal.
INTERMEDIATE_BIT_DEPTH = 24


def needs_transcode(fmt: FormatInfo) -> bool:
    """``True`` si hay que pasar por un WAV intermedio y ffmpeg."""
    return not fmt.native


def ffmpeg_encode_args(fmt: FormatInfo, options: FormatOptions) -> list[str]:
    """Argumentos de códec/calidad para ffmpeg (sin entrada ni salida)."""
    opts = sanitize_options(fmt, options)
    if fmt.key == "ogg":
        return ["-c:a", "libvorbis", "-q:a", str(opts.vbr_quality)]
    if fmt.key == "opus":
        return ["-c:a", "libopus", "-b:a", f"{opts.bitrate_kbps}k", "-vbr", "on"]
    if fmt.key == "m4a":
        return ["-c:a", "aac", "-b:a", f"{opts.bitrate_kbps}k"]
    # Los formatos nativos también pueden re-codificarse con ffmpeg; se ofrecen
    # por si en el futuro se expone un control que demucs no soporte.
    if fmt.key == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", f"{opts.bitrate_kbps}k"]
    if fmt.key == "flac":
        return ["-c:a", "flac", "-sample_fmt", "s32" if opts.bit_depth == 24 else "s16"]
    if fmt.key == "wav":
        return ["-c:a", "pcm_s24le" if opts.bit_depth == 24 else "pcm_s16le"]
    raise ValueError(f"Formato sin codificador ffmpeg definido: {fmt.key}")


def ffmpeg_command(
    ffmpeg: str,
    src: str,
    dst: str,
    fmt: FormatInfo,
    options: FormatOptions,
    *,
    with_progress: bool = True,
) -> list[str]:
    """Comando ffmpeg completo para codificar ``src`` (WAV) a ``dst``."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if with_progress:
        # out_time_us=<microsegundos> va a stdout mientras el log va a stderr.
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += ["-i", src, "-map_metadata", "-1"]
    cmd += ffmpeg_encode_args(fmt, options)
    cmd += [dst]
    return cmd


def parse_progress_line(line: str) -> float | None:
    """Extrae segundos procesados de una línea ``-progress`` de ffmpeg."""
    line = line.strip()
    for key in ("out_time_us=", "out_time_ms="):
        if line.startswith(key):
            raw = line[len(key):]
            if raw in ("N/A", ""):
                return None
            try:
                value = int(raw)
            except ValueError:
                return None
            # ffmpeg reporta microsegundos en ambos campos.
            return value / 1_000_000.0
    return None


def describe_options(fmt: FormatInfo, options: FormatOptions) -> str:
    """Resumen corto de calidad, para mostrar junto a la cola."""
    opts = sanitize_options(fmt, options)
    if fmt.key == "wav":
        detail = "32-bit float" if opts.float32 else f"{opts.bit_depth} bits"
    elif fmt.key == "flac":
        detail = f"{opts.bit_depth} bits"
    elif fmt.key == "ogg":
        detail = f"calidad {opts.vbr_quality}"
    elif fmt.key == "mp3":
        detail = f"{opts.bitrate_kbps} kbps · preset {opts.mp3_preset}"
    else:
        detail = f"{opts.bitrate_kbps} kbps"
    return f"{fmt.label.split(' · ')[0]} {detail}"


def format_summary(fmt: FormatInfo) -> str:
    """Etiqueta corta del formato para combos y listas."""
    return fmt.label


def iter_extension_labels(fmt: FormatInfo) -> Iterable[str]:
    return (fmt.ext,)
