"""Pruebas del puente con el worker (subproceso y protocolo JSON).

Se usa un `demucs_studio.worker` falso colocado en un PYTHONPATH temporal, de
modo que no hace falta demucs ni PyTorch.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from demucs_studio.engine import EngineStatus, JobRunner, SeparationRequest

FAKE_WORKER = """
import json
import sys

spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
print(json.dumps({"event": "stage", "stage": "start"}))
print(json.dumps({"event": "progress", "percent": 42.0}))
print(json.dumps({"event": "stem_done", "stem": "drums"}))
print(json.dumps({
    "event": "done",
    "outdir": spec["output_dir"],
    "files": ["out1.wav", "out2.wav"],
}))
"""

FAKE_SLEEPER = """
import json
import sys
import time

json.loads(open(sys.argv[1], encoding="utf-8").read())
print(json.dumps({"event": "stage", "stage": "start"}))
sys.stdout.flush()
time.sleep(120)
"""


def _fake_root(tmp_path: Path, source: str) -> Path:
    package = tmp_path / "demucs_studio"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "worker.py").write_text(source, encoding="utf-8")
    return tmp_path


def _engine() -> EngineStatus:
    return EngineStatus(available=True, python=sys.executable)


def test_runner_parses_worker_events(tmp_path: Path) -> None:
    root = _fake_root(tmp_path, FAKE_WORKER)
    request = SeparationRequest(
        input_path=str(tmp_path / "song.wav"), output_dir=str(tmp_path)
    )

    events = []
    done = threading.Event()
    runner = JobRunner(
        _engine(),
        request,
        on_event=events.append,
        on_finished=lambda _runner: done.set(),
        worker_root=root,
    )
    runner.start()
    assert done.wait(30), "el worker falso no terminó"
    runner._threads and [thread.join(timeout=5) for thread in runner._threads]

    kinds = [event.get("event") for event in events]
    assert "stage" in kinds
    assert "progress" in kinds
    assert "done" in kinds
    assert runner.returncode == 0
    assert runner.error is None
    assert runner.outputs == ["out1.wav", "out2.wav"]
    assert runner.result_dir == str(tmp_path)


def test_runner_cancels_a_stuck_worker(tmp_path: Path) -> None:
    root = _fake_root(tmp_path, FAKE_SLEEPER)
    request = SeparationRequest(
        input_path=str(tmp_path / "song.wav"), output_dir=str(tmp_path)
    )

    done = threading.Event()
    runner = JobRunner(
        _engine(),
        request,
        on_finished=lambda _runner: done.set(),
        worker_root=root,
    )
    runner.start()
    time.sleep(1.5)
    assert runner.running
    runner.cancel()
    assert done.wait(20), "el worker cancelado no terminó"
    assert runner.cancelled
    assert not runner.running


def test_spec_file_is_removed_after_run(tmp_path: Path) -> None:
    root = _fake_root(tmp_path, FAKE_WORKER)
    request = SeparationRequest(
        input_path=str(tmp_path / "song.wav"), output_dir=str(tmp_path)
    )
    done = threading.Event()
    runner = JobRunner(
        _engine(),
        request,
        on_finished=lambda _runner: done.set(),
        worker_root=root,
    )
    runner.start()
    assert done.wait(30)
    assert runner.spec_path is None
