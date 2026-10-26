#!/bin/sh
set -eu

# azd provision creates the registry and writes its outputs into this environment.
# Keep infrastructure creation explicit; never call azd provision recursively here.
if [ -z "${AZURE_CONTAINER_REGISTRY_ENDPOINT:-}" ]; then
  echo 'Falta la infraestructura del entorno. Ejecuta azd provision y después azd deploy.' >&2
  exit 1
fi

cd "$(git rev-parse --show-toplevel)"
if ! git diff --quiet HEAD -- Dockerfile .dockerignore pyproject.toml uv.lock video_checklist data/manifest.json; then
  echo 'Guarda en un commit los cambios de la aplicación antes de desplegar: la imagen identifica su código con el SHA de Git.' >&2
  exit 1
fi
if [ -n "$(git ls-files --others --exclude-standard -- video_checklist)" ]; then
  echo 'Hay archivos nuevos sin commit en video_checklist. Inclúyelos antes de desplegar.' >&2
  exit 1
fi
source_commit=$(git rev-parse HEAD)
mkdir -p .build
printf '{"commit":"%s"}\n' "$source_commit" > .build/source-manifest.json
echo "Imagen vinculada al commit $source_commit. La construcción se realizará en Azure Container Registry."
