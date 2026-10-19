# -*- mode: python ; coding: utf-8 -*-
"""Empaqueta la interfaz de Demucs Studio en una carpeta ejecutable."""

from pathlib import Path

PROJECT = Path(SPECPATH).resolve().parent
TOOLS = PROJECT / "tools" / "bin"

for tool in ("ffmpeg.exe", "ffprobe.exe", "uv.exe"):
    if not (TOOLS / tool).is_file():
        raise SystemExit(
            f"Falta {TOOLS / tool}. Ejecuta primero scripts/fetch-tools.ps1"
        )

datas = [
    # Copia en fuentes del paquete para que el Python del venv pueda ejecutar
    # `-m demucs_studio.worker` con PYTHONPATH=worker_src.
    (str(PROJECT / "demucs_studio"), "worker_src/demucs_studio"),
    (str(PROJECT / "packaging" / "icon.png"), "."),
    (str(TOOLS / "ffmpeg.exe"), "bin"),
    (str(TOOLS / "ffprobe.exe"), "bin"),
    (str(TOOLS / "uv.exe"), "bin"),
]

hiddenimports = []

excludes = [
    "tkinter",
    "matplotlib",
    "pandas",
    "scipy",
    "IPython",
    "notebook",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSql",
    "PySide6.QtTest",
]

a = Analysis(
    [str(PROJECT / "winapp" / "__main__.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DemucsStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT / "packaging" / "icon.ico"),
    version=str(PROJECT / "packaging" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DemucsStudio",
)
