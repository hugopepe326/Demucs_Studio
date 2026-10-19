"""Pruebas del núcleo compartido (independientes de Qt y de Windows)."""

from __future__ import annotations

from pathlib import Path

from demucs_studio import config as app_config
from demucs_studio.engine import SeparationRequest, estimate_seconds, record_calibration
from demucs_studio.formats import (
    FormatOptions,
    demucs_save_kwargs,
    format_info,
    sanitize_options,
)
from demucs_studio.presets import PRESETS, relative_cost


def test_relative_cost_scales_with_bag_and_shifts() -> None:
    assert relative_cost("htdemucs", 1) == 1.0
    assert relative_cost("htdemucs", 0) == 1.0
    assert relative_cost("htdemucs_ft", 3) == 12.0
    assert relative_cost("mdx_extra", 1) == 4.0


def test_estimate_uses_cpu_defaults() -> None:
    seconds = estimate_seconds(
        duration=180.0, model="htdemucs", shifts=1, device="cpu"
    )
    assert seconds is not None
    # 180 s × 1 × 0,55 × 1,05
    assert abs(seconds - 103.95) < 0.01


def test_estimate_returns_none_without_duration() -> None:
    assert estimate_seconds(duration=None, model="htdemucs", shifts=1, device="cpu") is None
    assert estimate_seconds(duration=0, model="htdemucs", shifts=1, device="gpu") is None


def test_calibration_smooths_measurements() -> None:
    table = record_calibration(
        {}, device="cpu", model="htdemucs", shifts=1, duration=100.0, elapsed=50.0
    )
    assert abs(table["cpu:htdemucs"] - 0.5) < 1e-9
    table = record_calibration(
        table, device="cpu", model="htdemucs", shifts=1, duration=100.0, elapsed=100.0
    )
    assert abs(table["cpu:htdemucs"] - 0.7) < 1e-9


def test_sanitize_options_fixes_bitrate() -> None:
    fmt = format_info("mp3")
    clean = sanitize_options(fmt, FormatOptions(bitrate_kbps=300))
    assert clean.bitrate_kbps == 256


def test_wav_float_save_kwargs() -> None:
    fmt = format_info("wav")
    options = sanitize_options(fmt, FormatOptions(bit_depth=24, float32=True))
    kwargs = demucs_save_kwargs(fmt, options)
    assert kwargs["bits_per_sample"] == 32
    assert kwargs["as_float"] is True


def test_request_uses_windows_style_output_tree(tmp_path: Path) -> None:
    song = tmp_path / "Mi Canción.mp3"
    song.write_bytes(b"fake")
    request = SeparationRequest(
        input_path=str(song),
        output_dir=str(tmp_path),
        model="htdemucs",
        format_key="flac",
        create_subfolder=True,
    )
    assert request.planned_output_dir() == tmp_path / "Mi Canción"
    names = [path.name for path in request.expected_files()]
    assert names == ["drums.flac", "bass.flac", "vocals.flac", "other.flac"]


def test_request_dict_is_serializable(tmp_path: Path) -> None:
    song = tmp_path / "track.wav"
    song.write_bytes(b"fake")
    request = SeparationRequest(input_path=str(song), output_dir=str(tmp_path))
    payload = request.to_dict()
    assert payload["track"] == "track"
    assert payload["protocol"] == 1
    assert payload["device"] == "auto"


def test_config_roundtrip(tmp_path: Path) -> None:
    config = app_config.Config(model="mdx_extra_q", shifts=2, overlap=0.4, jobs=3)
    path = tmp_path / "config.json"
    config.save(path)
    loaded = app_config.Config.load(path)
    assert loaded.model == "mdx_extra_q"
    assert loaded.shifts == 2
    assert abs(loaded.overlap - 0.4) < 1e-9
    assert loaded.jobs == 3


def test_presets_have_expected_keys() -> None:
    keys = {preset.key for preset in PRESETS}
    assert {"borrador", "equilibrada", "mdx", "alta", "maxima"} <= keys
