#!/usr/bin/env bash
# Adjunta un instalador ya compilado al release de GitHub.
#
# Uso:
#   ./scripts/attach-release.sh "dist/installer/Demucs_Studio 1.0.0.exe"
#
# Requiere GitHub CLI (`gh auth login`) y permisos sobre el repositorio.

set -euo pipefail

FILE="${1:?Uso: $0 <ruta-del-exe> [tag] [owner/repo]}"
TAG="${2:-Demucs}"
REPO="${3:-hugopepe326/demucs}"

if [[ ! -f "$FILE" ]]; then
  echo "No existe el archivo: $FILE" >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "Falta GitHub CLI. Instálalo desde https://cli.github.com/" >&2
  exit 1
fi

echo "Subiendo $FILE al release $TAG de $REPO…"
gh release upload "$TAG" "$FILE" --repo "$REPO" --clobber
echo "Listo."
