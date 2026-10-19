# Compila Demucs Studio y genera el instalador en Windows.
#
# Requisitos: Python 3.12, Inno Setup 6 (winget install JRSoftware.InnoSetup)
#
# Uso:  powershell -ExecutionPolicy Bypass -File build.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = "python"
if (-not (Get-Command $python -ErrorAction SilentlyContinue)) {
    throw "No se encontró Python en el PATH. Instala Python 3.12 x64."
}

if (-not (Test-Path ".venv-build")) {
    Write-Host "Creando el entorno de compilación…"
    & $python -m venv .venv-build
}

$venvPython = ".\.venv-build\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements-build.txt

Write-Host "Comprobaciones…"
& $venvPython -m pytest -q

Write-Host "Descargando herramientas (ffmpeg, uv)…"
& ".\scripts\fetch-tools.ps1"

Write-Host "Empaquetando la aplicación…"
& ".\.venv-build\Scripts\pyinstaller.exe" packaging\demucs-studio.spec --noconfirm --clean

Write-Host "Generando el instalador…"
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    $iscc = "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $iscc)) {
    throw "No se encontró Inno Setup 6. Instálalo con: winget install JRSoftware.InnoSetup"
}
& $iscc "packaging\installer.iss"

Write-Host "Calculando SHA-256…"
Get-ChildItem "dist\installer\*.exe" | ForEach-Object {
    $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
    "$hash  $($_.Name)" | Out-File -Encoding ascii (Join-Path $_.DirectoryName "SHA256SUMS.txt")
}

Write-Host ""
Write-Host "Listo. Resultado en dist\installer:"
Get-ChildItem "dist\installer" | Select-Object Name, Length
