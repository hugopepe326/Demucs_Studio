"""Demucs Studio (edición Windows).

Este paquete se importa desde dos intérpretes distintos:

* la interfaz, dentro del ejecutable de PyInstaller;
* el worker, con el Python del *virtualenv* donde viven demucs y PyTorch.

Por eso ``__init__`` no importa Qt ni nada pesado: los submódulos se cargan
bajo demanda y solo ``demucs_studio.formats``, ``presets`` y ``util`` (stdlib
puro) se usan desde el worker.
"""

__all__ = ["__version__", "APP_NAME"]

__version__ = "1.0.0"
APP_NAME = "Demucs Studio"
