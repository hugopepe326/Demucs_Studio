# Adjunta un instalador ya compilado al release de GitHub.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File scripts\attach-release.ps1 -File "dist\installer\Demucs_Studio 1.0.0.exe"
#
# Requiere GitHub CLI (`gh auth login`) y permisos sobre el repositorio.

param(
    [Parameter(Mandatory = $true)]
    [string]$File,
    [string]$Tag = "Demucs",
    [string]$Repo = "hugopepe326/demucs"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $File)) {
    throw "No existe el archivo: $File"
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "Falta GitHub CLI. Instálalo con: winget install GitHub.cli"
}

$full = (Resolve-Path $File).Path
Write-Host "Subiendo $full al release $Tag de $Repo…"
gh release upload $Tag $full --repo $Repo --clobber
Write-Host "Listo."
