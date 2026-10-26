$ErrorActionPreference = 'Stop'
if (-not $env:AZURE_CONTAINER_REGISTRY_ENDPOINT) {
    throw 'Falta la infraestructura del entorno. Ejecuta azd provision y después azd deploy.'
}
$repositoryRoot = git rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0) { throw 'No se encontró el repositorio Git.' }
Set-Location $repositoryRoot
git diff --quiet HEAD -- Dockerfile .dockerignore pyproject.toml uv.lock video_checklist data/manifest.json
if ($LASTEXITCODE -ne 0) { throw 'Guarda en un commit los cambios de la aplicación antes de desplegar: la imagen identifica su código con el SHA de Git.' }
$untracked = git ls-files --others --exclude-standard -- video_checklist
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron comprobar los archivos nuevos.' }
if ($untracked) { throw 'Hay archivos nuevos sin commit en video_checklist. Inclúyelos antes de desplegar.' }
$sourceCommit = git rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'No se pudo obtener el commit de origen.' }
New-Item -ItemType Directory -Force -Path .build | Out-Null
[System.IO.File]::WriteAllText((Join-Path $repositoryRoot '.build/source-manifest.json'), ('{"commit":"' + $sourceCommit + '"}' + "`n"), [System.Text.UTF8Encoding]::new($false))
Write-Host "Imagen vinculada al commit $sourceCommit. La construcción se realizará en Azure Container Registry."
