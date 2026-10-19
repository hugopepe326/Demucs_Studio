"""Modelos de demucs, presets de calidad y nombres de pistas.

Los nombres de modelo y el tamaño de cada *bag* están verificados contra
``demucs/remote/*.yaml`` de demucs 4.1.0: el número de submodelos determina
cuánto tarda una separación (cada submodelo hace su propia pasada completa).
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Pistas (stems) -------------------------------------------------------

#: Orden canónico en el que queremos mostrar/guardar las pistas. demucs usa
#: ``("drums", "bass", "other", "vocals")``; aquí adelantamos los stems.
STEMS = ("drums", "bass", "vocals", "other")

STEM_LABELS_ES = {
    "drums": "Batería",
    "bass": "Bajo",
    "vocals": "Voces",
    "other": "Otros instrumentos",
}

STEM_LABELS_EN = {
    "drums": "Drums",
    "bass": "Bass",
    "vocals": "Vocals",
    "other": "Other",
}

#: ``"standard"`` mantiene los nombres de demucs (compatibles con DAWs);
#: ``"spanish"`` escribe bateria/bajo/voces/otros.
STEM_NAMING = {
    "standard": {stem: stem for stem in STEMS},
    "spanish": {
        "drums": "bateria",
        "bass": "bajo",
        "vocals": "voces",
        "other": "otros",
    },
}


def stem_label(stem: str, language: str = "es") -> str:
    """Etiqueta legible de una pista, con respaldo al nombre crudo."""
    table = STEM_LABELS_ES if language == "es" else STEM_LABELS_EN
    return table.get(stem, stem)


def stem_filename(stem: str, naming: str = "standard") -> str:
    """Nombre de archivo (sin extensión) de una pista."""
    return STEM_NAMING.get(naming, STEM_NAMING["standard"]).get(stem, stem)


# --- Modelos -------------------------------------------------------------


@dataclass(frozen=True)
class ModelInfo:
    """Un modelo preentrenado de demucs con metadatos para la interfaz."""

    name: str
    label: str
    bag: int
    """Número de submodelos que se ejecutan (coste relativo de tiempo)."""
    segment: int | None
    """Duración de segmento impuesta por el modelo, si la define."""
    quality: int
    """Calidad esperada, de 1 a 5 estrellas."""
    speed: int
    """Velocidad relativa, de 1 a 5 (5 = la más rápida)."""
    note: str


MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        "htdemucs",
        "htdemucs · Transformer híbrido",
        bag=1,
        segment=None,
        quality=4,
        speed=5,
        note="Mejor relación calidad/tiempo. Es el modelo por defecto de demucs.",
    ),
    ModelInfo(
        "htdemucs_ft",
        "htdemucs_ft · Fine-tuned",
        bag=4,
        segment=None,
        quality=5,
        speed=2,
        note="La mejor calidad para voces y batería; tarda ~4 veces más.",
    ),
    ModelInfo(
        "hdemucs_mmi",
        "hdemucs_mmi · Híbrido v3",
        bag=1,
        segment=44,
        quality=3,
        speed=4,
        note="Modelo clásico de la v3, muy estable en mezclas densas.",
    ),
    ModelInfo(
        "mdx_extra_q",
        "mdx_extra_q · MDX extra cuantizado",
        bag=4,
        segment=44,
        quality=4,
        speed=3,
        note="Familia MDX comprimida: buena calidad usando menos memoria.",
    ),
    ModelInfo(
        "mdx_extra",
        "mdx_extra · MDX extra",
        bag=4,
        segment=44,
        quality=5,
        speed=1,
        note="Muy buena calidad, consume bastante VRAM y es el más lento.",
    ),
    ModelInfo(
        "mdx_q",
        "mdx_q · MDX cuantizado",
        bag=4,
        segment=44,
        quality=3,
        speed=3,
        note="Versión reducida de MDX v2; buen equilibrio en equipos modestos.",
    ),
    ModelInfo(
        "mdx",
        "mdx · MDX v2",
        bag=4,
        segment=44,
        quality=4,
        speed=2,
        note="Modelo MDX original de v2. Sonido distinto a los Transformer.",
    ),
)

MODELS_BY_NAME = {model.name: model for model in MODELS}
DEFAULT_MODEL = "htdemucs"


def model_info(name: str) -> ModelInfo:
    """Devuelve el :class:`ModelInfo` de ``name`` o un marcador genérico."""
    info = MODELS_BY_NAME.get(name)
    if info is not None:
        return info
    return ModelInfo(name, name, bag=1, segment=None, quality=0, speed=0, note="")


def relative_cost(model: str, shifts: int) -> float:
    """Coste relativo de tiempo frente a ``htdemucs`` con un shift.

    Cada submodelo del *bag* y cada *shift* implican una pasada completa por la
    canción, así que el coste es proporcional a ``bag × max(1, shifts)``.
    """
    bag = model_info(model).bag
    return float(bag * max(1, int(shifts or 0)))


# --- Presets de calidad --------------------------------------------------


@dataclass(frozen=True)
class QualityPreset:
    """Combinación de modelo, shifts y solape lista para usar."""

    key: str
    label: str
    model: str
    shifts: int
    overlap: float
    detail: str

    @property
    def cost(self) -> float:
        return relative_cost(self.model, self.shifts)


PRESETS: tuple[QualityPreset, ...] = (
    QualityPreset(
        "borrador",
        "Borrador · lo más rápido",
        model="htdemucs",
        shifts=0,
        overlap=0.10,
        detail="Para escuchar rápido y decidir si la mezcla sirve.",
    ),
    QualityPreset(
        "equilibrada",
        "Equilibrada (recomendada)",
        model="htdemucs",
        shifts=1,
        overlap=0.25,
        detail="Ajustes por defecto de demucs. Buen equilibrio calidad/tiempo.",
    ),
    QualityPreset(
        "mdx",
        "MDX",
        model="mdx_extra_q",
        shifts=1,
        overlap=0.25,
        detail="Otro sabor sonoro, útil si htdemucs deja restos de voz en otros.",
    ),
    QualityPreset(
        "alta",
        "Alta",
        model="htdemucs_ft",
        shifts=3,
        overlap=0.40,
        detail="Pistas limpias para mezclar o remezclar. Tarda bastante más.",
    ),
    QualityPreset(
        "maxima",
        "Máxima",
        model="htdemucs_ft",
        shifts=8,
        overlap=0.50,
        detail="Lo mejor que puede dar demucs. Reservado para dejar el equipo horas.",
    ),
)

PRESETS_BY_KEY = {preset.key: preset for preset in PRESETS}
DEFAULT_PRESET = "equilibrada"

#: Clave que usa la interfaz cuando el usuario toca los ajustes a mano.
CUSTOM_PRESET = "personalizada"
CUSTOM_PRESET_LABEL = "Personalizada"

#: Límites de los controles avanzados.
SHIFTS_RANGE = (0, 10)
OVERLAP_RANGE = (0.0, 0.9)
SEGMENT_CHOICES = (0, 7, 8, 10, 15, 20, 30, 44)
"""0 significa "automático" (deja que demucs elija)."""

JOBS_MAX = 8
"""``jobs`` reparte la inferencia en varios hilos/procesos."""


def preset_matching(model: str, shifts: int, overlap: float) -> str:
    """Clave del preset que coincide exactamente, o ``personalizada``."""
    for preset in PRESETS:
        if (
            preset.model == model
            and preset.shifts == shifts
            and abs(preset.overlap - overlap) < 1e-9
        ):
            return preset.key
    return CUSTOM_PRESET
