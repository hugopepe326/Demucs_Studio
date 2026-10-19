"""Modelo de trabajo y cola de ejecución.

Los trabajos se ejecutan **de uno en uno**: demucs es intensivo en CPU, GPU y
memoria, así que lanzar varios a la vez solo hace que todos tarden más. Esta
cola es independiente de GTK; los observadores pueden ser llamados desde hilos
del worker, de modo que quien pinte la interfaz debe saltar al hilo principal
con ``GLib.idle_add``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .engine import (
    EVENT_CANCELLED,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_PROGRESS,
    EVENT_STAGE,
    EVENT_STEM_DONE,
    EVENT_STEM_START,
    EngineStatus,
    JobRunner,
    SeparationRequest,
    WorkerError,
    record_calibration,
    stage_label,
)
from .util import ffprobe_duration, human_duration

log = logging.getLogger(__name__)

#: Estados posibles de un trabajo.
PENDING = "pending"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"
FINISHED_STATES = (DONE, ERROR, CANCELLED)

STATUS_LABELS = {
    PENDING: "En espera",
    RUNNING: "En proceso",
    DONE: "Completado",
    ERROR: "Con errores",
    CANCELLED: "Cancelado",
}


@dataclass
class Job:
    """Una canción en la cola, con su estado y sus resultados."""

    identifier: int
    request: SeparationRequest
    status: str = PENDING
    percent: float = 0.0
    message: str = STATUS_LABELS[PENDING]
    detail: str = ""
    error: str | None = None
    hint: str | None = None
    outputs: list[str] = field(default_factory=list)
    outdir: str | None = None
    duration: float | None = None
    current_stem: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    runner: JobRunner | None = None

    @property
    def name(self) -> str:
        """Nombre de la canción."""
        return self.request.track_label()

    @property
    def source(self) -> str:
        return self.request.input_path

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED_STATES

    @property
    def is_running(self) -> bool:
        return self.status == RUNNING

    def eta_text(self, *, device: str = "cpu", calibration: dict[str, float] | None = None) -> str:
        """Texto de tiempo restante estimado, si hay datos suficientes."""
        from .engine import estimate_seconds

        if self.status != RUNNING or not self.duration:
            return ""
        total = estimate_seconds(
            duration=self.duration,
            model=self.request.model,
            shifts=self.request.shifts,
            device=device,
            calibration=calibration,
        )
        if not total:
            return ""
        remaining = max(0.0, total - self.elapsed)
        if remaining < 5:
            return "terminando…"
        return f"~{human_duration(remaining)} restante"

    def summary(self) -> str:
        """Línea de estado para la interfaz."""
        if self.status == RUNNING:
            parts = [self.message or stage_label("start")]
            if self.detail:
                parts.append(self.detail)
            return " · ".join(part for part in parts if part)
        if self.status == DONE:
            count = len(self.outputs)
            extra = f"{count} pistas" if count else "listo"
            if self.elapsed <= 0:
                return f"{STATUS_LABELS[DONE]} · {extra}"
            return f"{STATUS_LABELS[DONE]} · {extra} · {human_duration(self.elapsed)}"
        if self.status == ERROR:
            return self.error or STATUS_LABELS[ERROR]
        if self.status == CANCELLED:
            return STATUS_LABELS[CANCELLED]
        return STATUS_LABELS[PENDING]


class QueueManager:
    """Orquesta la ejecución secuencial de los trabajos."""

    def __init__(
        self,
        engine_provider: Callable[[], EngineStatus],
        *,
        on_job: Callable[[Job], None] | None = None,
        on_queue: Callable[[], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_completed: Callable[[Job], None] | None = None,
        ffprobe: str | None = None,
    ) -> None:
        self.engine_provider = engine_provider
        self.on_job = on_job or (lambda job: None)
        self.on_queue = on_queue or (lambda: None)
        self.on_log = on_log or (lambda line: None)
        self.on_completed = on_completed or (lambda job: None)
        self.ffprobe = ffprobe

        self._jobs: list[Job] = []
        self._runner: JobRunner | None = None
        self._current: Job | None = None
        self._counter = 0
        self._lock = threading.Lock()
        self._stop_requested = False

    # --- Consultas -------------------------------------------------------

    @property
    def jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._runner is not None

    def pending_jobs(self) -> list[Job]:
        with self._lock:
            return [job for job in self._jobs if job.status == PENDING]

    def finished_jobs(self) -> list[Job]:
        with self._lock:
            return [job for job in self._jobs if job.is_finished]

    def counts(self) -> dict[str, int]:
        with self._lock:
            counts = {PENDING: 0, RUNNING: 0, DONE: 0, ERROR: 0, CANCELLED: 0}
            for job in self._jobs:
                counts[job.status] = counts.get(job.status, 0) + 1
            return counts

    def overall_progress(self) -> tuple[int, int, float]:
        """``(terminados, total, fracción 0-1)`` para la barra global."""
        with self._lock:
            jobs = list(self._jobs)
        total = len(jobs)
        if not total:
            return 0, 0, 0.0
        done = sum(1 for job in jobs if job.is_finished)
        partial = sum(job.percent / 100.0 for job in jobs if job.is_running)
        return done, total, max(0.0, min(1.0, (done + partial) / total))

    # --- Mutaciones ------------------------------------------------------

    def add(self, requests: Iterable[SeparationRequest]) -> list[Job]:
        """Añade uno o varios trabajos a la cola y devuelve los creados."""
        created: list[Job] = []
        with self._lock:
            for request in requests:
                self._counter += 1
                job = Job(identifier=self._counter, request=request)
                self._jobs.append(job)
                created.append(job)
        if created:
            self._notify_queue()
            self._notify_many(created)
            self._probe_durations(created)
        return created

    def start(self) -> bool:
        """Arranca el siguiente trabajo si la cola está libre."""
        with self._lock:
            if self._runner is not None:
                return False
            pending = [job for job in self._jobs if job.status == PENDING]
            if not pending:
                return False
            job = pending[0]

        engine = self.engine_provider()
        if not engine.available:
            job.status = ERROR
            job.error = engine.error or "El motor de demucs no está instalado."
            job.hint = "Instala el motor desde el botón de la barra superior."
            job.finished_at = time.monotonic()
            self._notify(job)
            self._notify_queue()
            return False

        job.status = RUNNING
        job.percent = 0.0
        job.message = "Arrancando el motor…"
        job.error = None
        job.hint = None
        job.started_at = time.monotonic()
        job.finished_at = None

        runner = JobRunner(
            engine,
            job.request,
            on_event=lambda event, job=job: self._handle_event(job, event),
            on_log=self._safe_log,
            on_finished=self._handle_finished,
        )
        job.runner = runner
        with self._lock:
            self._runner = runner
            self._current = job
        self._notify(job)
        self._notify_queue()

        try:
            runner.start()
        except WorkerError as error:
            with self._lock:
                self._runner = None
                self._current = None
            job.status = ERROR
            job.error = str(error)
            job.finished_at = time.monotonic()
            self._notify(job)
            return False
        return True

    def start_all(self) -> None:
        """Pone en marcha la cola."""
        self._stop_requested = False
        self.start()

    def cancel(self, job: Job) -> None:
        """Cancela un trabajo concreto."""
        if job.is_finished:
            return
        if job.status == PENDING:
            job.status = CANCELLED
            job.message = STATUS_LABELS[CANCELLED]
            job.finished_at = time.monotonic()
            self._notify(job)
            self._notify_queue()
            return
        if job.runner is not None:
            job.message = "Cancelando…"
            self._notify(job)
            job.runner.cancel()

    def cancel_all(self) -> None:
        """Cancela el trabajo en curso y todos los pendientes."""
        self._stop_requested = True
        with self._lock:
            jobs = list(self._jobs)
        for job in jobs:
            if job.status in (PENDING, RUNNING):
                self.cancel(job)

    def remove(self, job: Job) -> None:
        """Quita un trabajo de la lista (los activos se cancelan antes)."""
        if not job.is_finished:
            self.cancel(job)
        with self._lock:
            if job in self._jobs:
                self._jobs.remove(job)
        self._notify_queue()

    def clear_finished(self) -> None:
        """Vacía los trabajos ya terminados."""
        with self._lock:
            self._jobs = [job for job in self._jobs if not job.is_finished]
        self._notify_queue()

    def clear_all(self) -> None:
        """Vacía la cola entera, cancelando lo que esté activo."""
        self.cancel_all()
        with self._lock:
            self._jobs = []
        self._notify_queue()

    # --- Callbacks del runner -------------------------------------------

    def _handle_event(self, job: Job, event: dict[str, Any]) -> None:
        kind = event.get("event")
        if kind == EVENT_STAGE:
            job.message = event.get("message") or stage_label(event.get("stage", ""))
            percent = event.get("percent")
            if isinstance(percent, (int, float)):
                job.percent = float(percent)
        elif kind == EVENT_PROGRESS:
            percent = event.get("percent")
            if isinstance(percent, (int, float)):
                job.percent = max(0.0, min(100.0, float(percent)))
            job.detail = str(event.get("detail") or "")
            if job.message in ("", STATUS_LABELS[PENDING]):
                job.message = stage_label("separate")
        elif kind == EVENT_STEM_START:
            stem = str(event.get("stem") or "")
            job.current_stem = stem
            index = event.get("index")
            total = event.get("total")
            if isinstance(index, int) and isinstance(total, int):
                job.detail = f"pista {index + 1}/{total}"
        elif kind == EVENT_STEM_DONE:
            job.detail = f"{event.get('stem')} escrito"
        elif kind == EVENT_DONE:
            job.outdir = event.get("outdir")
            job.outputs = [str(path) for path in event.get("files", [])]
        elif kind == EVENT_ERROR:
            job.error = str(event.get("message") or "Error desconocido")
            job.hint = event.get("hint")
        elif kind == EVENT_CANCELLED:
            job.status = CANCELLED
        self._notify(job)

    def _handle_finished(self, runner: JobRunner) -> None:
        job: Job | None = None
        with self._lock:
            if self._current is not None and self._current.runner is runner:
                job = self._current
            self._runner = None
            self._current = None

        if job is None:
            return

        job.finished_at = time.monotonic()
        if runner.cancelled:
            job.status = CANCELLED
            job.message = STATUS_LABELS[CANCELLED]
        elif runner.error:
            job.status = ERROR
            job.error = runner.error
            job.hint = runner.error_hint
            job.message = STATUS_LABELS[ERROR]
        elif runner.returncode == 0:
            job.status = DONE
            job.percent = 100.0
            job.message = STATUS_LABELS[DONE]
            job.outputs = runner.outputs or job.outputs
            job.outdir = runner.result_dir or job.outdir
        else:
            job.status = ERROR
            job.error = job.error or f"El motor terminó con código {runner.returncode}"
            job.message = STATUS_LABELS[ERROR]

        job.runner = None
        self._notify(job)
        try:
            self.on_completed(job)
        except Exception:
            log.exception("Fallo en el callback de finalización")
        self._notify_queue()

        if not self._stop_requested:
            self.start()

    # --- Utilidades ------------------------------------------------------

    def _probe_durations(self, jobs: list[Job]) -> None:
        """Averigua la duración de cada canción sin bloquear la interfaz."""
        if not self.ffprobe:
            return

        def worker() -> None:
            from pathlib import Path

            for job in jobs:
                try:
                    duration = ffprobe_duration(self.ffprobe, Path(job.source))
                except Exception:
                    duration = None
                if duration:
                    job.duration = duration
                    self._notify(job)

        thread = threading.Thread(target=worker, name="demucs-studio-probe", daemon=True)
        thread.start()

    def _notify(self, job: Job) -> None:
        try:
            self.on_job(job)
        except Exception:
            log.exception("Fallo al notificar el estado de un trabajo")

    def _notify_queue(self) -> None:
        try:
            self.on_queue()
        except Exception:
            log.exception("Fallo al notificar la cola")

    def _notify_many(self, jobs: list[Job]) -> None:
        for job in jobs:
            self._notify(job)

    def _safe_log(self, line: str) -> None:
        try:
            self.on_log(line)
        except Exception:
            log.exception("Fallo al escribir en el registro")


def apply_calibration(
    calibration: dict[str, float],
    job: Job,
    device: str,
) -> dict[str, float]:
    """Actualiza la tabla de calibración con lo que tardó este trabajo."""
    if job.status != DONE:
        return calibration
    return record_calibration(
        calibration,
        device=device,
        model=job.request.model,
        shifts=job.request.shifts,
        duration=job.duration,
        elapsed=job.elapsed,
    )
