# Descarga las herramientas que se incluyen en el instalador:
#   - ffmpeg.exe y ffprobe.exe (giro completo de ffmpeg, esencial)
#   - uv.exe (gestor que crea el entorno del motor en el primer arranque)
#
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\fetch-tools.ps1

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = Split-Path -Parent $PSScriptRoot
$bin = Join-Path $root "tools\bin"
$downloads = Join-Path $root "tools\downloads"
New-Item -ItemType Directory -Force -Path $bin, $downloads | Out-Null

function Get-RemoteFile {
    param([string]$Url, [string]$Destination)
    if (Test-Path $Destination) {
        Write-Host "Ya existe $Destination"
        return
    }
    Write-Host "Descargando $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
}

# --- ffmpeg ---------------------------------------------------------------
$ffmpegZip = Join-Path $downloads "ffmpeg.zip"
Get-RemoteFile "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $ffmpegZip

$ffmpegDir = Join-Path $downloads "ffmpeg"
if (Test-Path $ffmpegDir) { Remove-Item -Recurse -Force $ffmpegDir }
Expand-Archive -Path $ffmpegZip -DestinationPath $ffmpegDir -Force

$ffmpegExe = Get-ChildItem -Path $ffmpegDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
$ffprobeExe = Get-ChildItem -Path $ffmpegDir -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
if (-not $ffmpegExe) { throw "No se encontró ffmpeg.exe en el paquete descargado" }
if (-not $ffprobeExe) { throw "No se encontró ffprobe.exe en el paquete descargado" }
Copy-Item $ffmpegExe.FullName (Join-Path $bin "ffmpeg.exe") -Force
Copy-Item $ffprobeExe.FullName (Join-Path $bin "ffprobe.exe") -Force

# --- uv -------------------------------------------------------------------
$uvZip = Join-Path $downloads "uv.zip"
Get-RemoteFile "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" $uvZip

$uvDir = Join-Path $downloads "uv"
if (Test-Path $uvDir) { Remove-Item -Recurse -Force $uvDir }
Expand-Archive -Path $uvZip -DestinationPath $uvDir -Force

$uvExe = Get-ChildItem -Path $uvDir -Recurse -Filter "uv.exe" | Select-Object -First 1
if (-not $uvExe) { throw "No se encontró uv.exe en el paquete descargado" }
Copy-Item $uvExe.FullName (Join-Path $bin "uv.exe") -Force

Write-Host ""
Write-Host "Herramientas listas en $bin"
Get-ChildItem $bin | Select-Object Name, Length
