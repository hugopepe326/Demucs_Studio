# Demucs Studio · instalador para Windows

Aplicación de escritorio nativa para **Windows 10/11** que separa una canción en
batería, bajo, voces y resto de instrumentos. Es la versión Windows de Demucs
Studio: interfaz oscura, presets de calidad, cola de trabajos, formatos de
salida y estimación de tiempo.

El instalador final se llama **`Demucs_Studio 1.0.0.exe`** y se genera con
PyInstaller + Inno Setup. Se adjunta al release:

<https://github.com/hugopepe326/demucs/releases/tag/Demucs>

---

## Por qué se compila en GitHub Actions

Un `.exe` de Windows **no se puede compilar desde Linux**: PyInstaller no
cross-compila, y el instalador usa herramientas de Windows (Inno Setup,
PowerShell, `taskkill`). Por eso este proyecto incluye un workflow de GitHub
Actions (`windows-latest`) que:

1. instala Python 3.12 y las dependencias de compilación;
2. descarga `ffmpeg.exe`, `ffprobe.exe` y `uv.exe`;
3. ejecuta las pruebas del núcleo;
4. empaqueta la app con PyInstaller;
5. genera el instalador con Inno Setup;
6. calcula el SHA-256;
7. lo publica como **artifact** descargable y, opcionalmente, lo adjunta al
   release de GitHub.

También puedes compilarlo en un PC Windows con `build.ps1`.

---

## Arquitectura

```
┌─────────────────────────────┐
│ DemucsStudio.exe (PySide6)  │  interfaz, cola, ajustes, estimación
│  · ffmpeg.exe/ffprobe.exe   │  binarios incluidos
│  · uv.exe                   │  instalador del motor
└──────────────┬──────────────┘
               │  subproceso: python -m demucs_studio.worker spec.json
┌──────────────▼──────────────┐
│ %LOCALAPPDATA%\Demucs Studio│
│  engine\  (venv Python 3.12)│  demucs + PyTorch (CPU o CUDA)
│  hf\      (pesos)           │  se descargan al usar cada modelo
└─────────────────────────────┘
```

La app **no** mete PyTorch dentro del instalador. En el primer arranque ofrece:

- **CPU**: unos 250 MB de descarga, funciona en cualquier PC.
- **NVIDIA GPU (CUDA 12.1)**: unos 2,5 GB, mucho más rápido.

Así el instalador ronda los 100–150 MB (por debajo del límite de 2 GB por
archivo de GitHub Releases) y se puede ofrecer CUDA sin subir un instalador de
varios gigabytes. Los modelos de demucs se descargan la primera vez que se usa
cada uno (unos 80 MB para `htdemucs` y 320 MB para `htdemucs_ft`) desde Hugging
Face y se conservan para siempre.

Nada de audio se sube a Internet: solo se descargan el motor y los modelos.

---

## Opción A · GitHub Actions (recomendada)

### 1. Sube este proyecto a un repositorio

Crea un repositorio vacío (por ejemplo `demucs-studio-windows`) y sube **el
contenido de esta carpeta** como raíz:

```bash
git init
git add .
git commit -m "Instalador Windows de Demucs Studio"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/demucs-studio-windows.git
git push -u origin main
```

> `tools/bin` y `dist` están en `.gitignore`: las herramientas se descargan
> durante la compilación.

### 2. Ejecuta el workflow

En GitHub: **Actions → Compilar instalador de Windows → Run workflow**.

- Deja `release_tag` vacío para obtener solo el artifact.
- Si quieres adjuntarlo automáticamente al release, escribe `Demucs` en
  `release_tag` y configura el secreto `RELEASE_REPO_TOKEN` (ver abajo).

El workflow tarda unos 5–10 minutos.

### 3. Descarga el instalador

Al terminar, entra en la ejecución del workflow y descarga el artifact
**`demucs-studio-windows`**. Dentro está:

- `Demucs_Studio 1.0.0.exe`
- `SHA256SUMS.txt`

### 4. Adjúntalo al release (si no lo hiciste automáticamente)

Con GitHub CLI:

```bash
gh release upload Demucs "Demucs_Studio 1.0.0.exe" \
  --repo hugopepe326/demucs --clobber
```

O desde la web: **Releases → Demucs → Edit → Attach binaries**.

### Adjuntar automáticamente desde otro repositorio

El `GITHUB_TOKEN` de un repositorio no puede escribir en otro. Si el workflow
vive en un repo distinto al del release:

1. Crea un token clásico con permiso `repo` (o fine-grained con
   `Contents: read and write` sobre `hugopepe326/demucs`).
2. En el repo del instalador: **Settings → Secrets and variables → Actions →
   New repository secret** → nombre `RELEASE_REPO_TOKEN`.
3. Lanza el workflow con `release_tag = Demucs` y
   `target_repo = hugopepe326/demucs`.

Si el workflow está en el mismo repo del release, basta con `release_tag` y el
token por defecto.

---

## Opción B · Compilar en un PC Windows

Requisitos:

- Windows 10/11 x64
- Python 3.12 x64 en el `PATH`
- [Inno Setup 6](https://jrsoftware.org/isinfo.php)
  (`winget install JRSoftware.InnoSetup`)
- Git (opcional)

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

El script crea `.venv-build`, instala dependencias, descarga ffmpeg/uv, ejecuta
las pruebas, empaqueta con PyInstaller y genera:

```
dist\installer\Demucs_Studio 1.0.0.exe
dist\installer\SHA256SUMS.txt
```

Para compilar solo la app (sin instalador):

```powershell
pyinstaller packaging\demucs-studio.spec --noconfirm --clean
```

El resultado queda en `dist\DemucsStudio\DemucsStudio.exe`.

---

## Primer arranque

1. Abre **Demucs Studio** desde el menú Inicio.
2. Pulsa **Instalar motor** y elige CPU o NVIDIA GPU.
3. Espera a que termine la descarga (se muestra el progreso en el registro).
4. Arrastra canciones o carpetas y pulsa **Separar**.

La primera separación de cada modelo descarga sus pesos desde Hugging Face
(unos 80 MB para `htdemucs`, 320 MB para `htdemucs_ft`). Las siguientes son
offline.

### Dónde se guarda todo

| Qué | Ruta |
| --- | --- |
| Programa | `%LOCALAPPDATA%\Programs\Demucs Studio` |
| Motor (venv) | `%LOCALAPPDATA%\Demucs Studio\engine` |
| Modelos | `%LOCALAPPDATA%\Demucs Studio\hf\hub` |
| Caché temporal | `%LOCALAPPDATA%\Demucs Studio\cache` |
| Ajustes | `%APPDATA%\Demucs Studio\config.json` |
| Salida por defecto | `%USERPROFILE%\Music\Separaciones` |

Desinstalar desde **Configuración → Aplicaciones** elimina el programa, pero
conserva el motor y los modelos para que una reinstalación sea instantánea. Si
quieres liberar esos GB, borra `%LOCALAPPDATA%\Demucs Studio` a mano.

---

## Activar la descarga en la web

El sitio Windows (`Fede_Demucs_Windows`) ya apunta al asset:

```
https://github.com/hugopepe326/demucs/releases/download/Demucs/Demucs_Studio%201.0.0.exe
```

Cuando el `.exe` esté adjunto al release, edita
`src/data/platform.ts`:

```ts
export const assetUploaded = true;
```

y vuelve a desplegar. El botón **Descargar para Windows** pasará de
«Publicación pendiente» a descargar el instalador, y aparecerá el enlace
secundario al release.

> El nombre del archivo debe ser exactamente `Demucs_Studio 1.0.0.exe`
> (espacio incluido). Si cambias `OutputBaseFilename` en `installer.iss`,
> actualiza también `installerName` y `downloadUrl`.

---

## Desarrollo y pruebas

```bash
python -m pytest -q
python -m winapp --selftest   # construye la ventana en modo offscreen
```

El núcleo (`demucs_studio/`) no depende de Qt ni de Windows, así que las pruebas
se pueden ejecutar en Linux/macOS. La interfaz se prueba con
`QT_QPA_PLATFORM=offscreen`.

### Estructura

```
Fede_Demucs_Installer/
├── demucs_studio/          núcleo compartido con el worker
│   ├── config.py           rutas %APPDATA% / %LOCALAPPDATA%, Config
│   ├── engine.py           detección del motor, JobRunner, estimaciones
│   ├── formats.py          WAV/FLAC/MP3/OGG/Opus/M4A y calidad
│   ├── jobs.py             cola de trabajos y callbacks
│   ├── presets.py          modelos, presets y nombres de pistas
│   ├── util.py             ffprobe, nombres seguros, apertura de carpetas
│   └── worker.py           proceso que ejecuta demucs (venv)
├── winapp/
│   ├── __main__.py         entrada, --selftest, --version
│   ├── bootstrap.py        instalación del motor con uv
│   └── ui/
│       ├── main_window.py  ventana, cola, ajustes, estimación
│       ├── setup_dialog.py diálogo CPU/CUDA y progreso
│       ├── theme.py        hoja de estilos oscura
│       └── widgets.py      zona de arrastre, tarjetas, píldoras, log
├── packaging/
│   ├── demucs-studio.spec  PyInstaller
│   ├── installer.iss       Inno Setup
│   ├── version_info.txt    propiedades del .exe
│   └── icon.ico / icon.png
├── scripts/
│   ├── fetch-tools.ps1     descarga ffmpeg + uv
│   └── attach-release.*    sube el .exe al release
├── tests/
├── .github/workflows/build-windows.yml
├── requirements-build.txt
├── build.ps1
└── README.md
```

### Versiones fijadas

| Componente | Versión |
| --- | --- |
| Python del motor | 3.12 (gestionado por uv) |
| Torch / torchaudio | 2.5.1 |
| demucs | 4.1.0 |
| numpy | 1.26.4 |
| PySide6 | 6.8.1 |
| PyInstaller | 6.11.1 |

Si actualizas alguna, cambia la constante correspondiente en
`winapp/bootstrap.py` (motor) o `requirements-build.txt` (interfaz) y vuelve a
compilar.

---

## Problemas frecuentes

**SmartScreen bloquea el instalador.**
El `.exe` no está firmado con un certificado de código (cuestan cientos de
euros al año). Es normal en un proyecto independiente: pulsa **Más información
→ Ejecutar de todas formas**. El código fuente está en este repositorio y el
workflow es reproducible.

**«No se detectó una GPU NVIDIA».**
La opción CUDA solo aparece si Windows ve una GPU NVIDIA. Si la tienes y no
aparece, actualiza los drivers. También puedes instalar CPU y cambiar después
con **Instalar / reparar motor**.

**La descarga del motor falla.**
Comprueba la conexión y el espacio libre (necesitas ~3 GB para CPU y ~8 GB para
CUDA). Puedes cancelar y reintentar; la instalación se hace limpia. El registro
muestra el comando exacto de `uv` que falló.

**Antivirus marca el `.exe`.**
Los ejecutables de PyInstaller a veces reciben falsos positivos. Añade una
excepción o compila tú mismo con `build.ps1`.

**El motor ocupa mucho.**
PyTorch CUDA ocupa varios GB. Puedes borrar
`%LOCALAPPDATA%\Demucs Studio\engine` y reinstalar la versión CPU.

**Quiero ver el registro.**
Pulsa **Registro** en la barra inferior. Ahí aparecen los mensajes del worker,
los avisos de torch y los errores con su causa.

---

## Licencia y créditos

Demucs Studio es una interfaz sobre [demucs](https://github.com/facebookresearch/demucs)
(MIT), [PyTorch](https://pytorch.org/) y [ffmpeg](https://ffmpeg.org/).
Los modelos se descargan de sus repositorios oficiales. Este proyecto no
redistribuye los pesos.

### Componentes de terceros incluidos en el instalador

| Componente | Licencia | Nota |
| --- | --- | --- |
| [ffmpeg](https://ffmpeg.org/legal.html) / ffprobe | GPL (build de gyan.dev) | Se descarga del build «essentials»; si redistribuyes el instalador, cumple la GPL (enlaza al código fuente de ffmpeg). |
| [uv](https://github.com/astral-sh/uv) | MIT / Apache-2.0 | Gestor que prepara el entorno del motor. |
| [PySide6](https://www.qt.io/licensing) | LGPL-3.0 | Interfaz; se enlaza dinámicamente. |
| demucs | MIT | Se instala en el equipo del usuario, no se redistribuye. |
| PyTorch / torchaudio | BSD-3-Clause | Se instalan en el equipo del usuario. |

El código de este repositorio se ofrece tal cual, sin garantías.

