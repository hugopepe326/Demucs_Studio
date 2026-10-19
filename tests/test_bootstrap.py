"""Pruebas del instalador del motor (sin ejecutar descargas reales)."""

from __future__ import annotations

from pathlib import Path

from winapp import bootstrap


def test_build_steps_cpu(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bootstrap, "uv_path", lambda: tmp_path / "uv.exe")
    steps = bootstrap.build_steps("cpu")
    commands = [" ".join(step.args) for step in steps]

    assert steps[0].args[1] == "venv"
    assert any("--python" in step.args for step in steps)
    assert any("download.pytorch.org/whl/cpu" in command for command in commands)
    assert any("demucs==4.1.0" in command for command in commands)
    assert any("--no-deps" in step.args for step in steps)
    # demucs 4.1.0 necesita sphn/safetensors/huggingface-hub, no soundfile ni openunmix.
    assert any("sphn>=0.1.12" in command for command in commands)
    assert any("safetensors" in command for command in commands)
    assert any("huggingface-hub" in command for command in commands)
    assert not any("openunmix" in command for command in commands)
    assert not any("soundfile" in command for command in commands)


def test_build_steps_cuda(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bootstrap, "uv_path", lambda: tmp_path / "uv.exe")
    steps = bootstrap.build_steps("cuda")
    commands = [" ".join(step.args) for step in steps]
    assert any("download.pytorch.org/whl/cu121" in command for command in commands)


def test_recommended_device_without_powershell() -> None:
    # En Linux no hay PowerShell: siempre CPU.
    assert bootstrap.recommended_device() == "cpu"


def test_uv_path_raises_without_binary(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap.app_config, "bundled_tool", lambda name: None)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: None)
    try:
        bootstrap.uv_path()
    except bootstrap.BootstrapError:
        pass
    else:  # pragma: no cover
        raise AssertionError("uv_path debería fallar sin uv.exe")


def test_no_window_flags_on_linux() -> None:
    from demucs_studio.engine import _no_window_flags

    assert _no_window_flags() == 0
