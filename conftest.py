"""Configuración común de pytest.

Fuerza rutas de datos temporales para que las pruebas nunca escriban en
`%APPDATA%` / `%LOCALAPPDATA%` reales. Debe ejecutarse antes de importar
`demucs_studio.config`, que calcula las constantes al importarse.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_sandbox = Path(tempfile.mkdtemp(prefix="demucs-tests-"))
os.environ["APPDATA"] = str(_sandbox / "Roaming")
os.environ["LOCALAPPDATA"] = str(_sandbox / "Local")
