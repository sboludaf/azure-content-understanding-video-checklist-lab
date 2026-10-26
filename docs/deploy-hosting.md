# Alojar la aplicación y su ledger privado

`infra/main.bicep` prepara Container Registry y, con `deployHosting=true`, App Service Linux. Los nombres de `infra/parameters.example.json` son ejemplos neutros: sustitúyelos por nombres propios y globalmente únicos cuando Azure lo requiera. El ejemplo conserva `deployHosting=false` y modo `fixture`.

Copia parámetros fuera del repositorio y fija regiones, nombres, SKU, IP autorizada, imagen, SHA y fecha ISO de retirada. El marcador de `cleanupDueAt` debe reemplazarse por una fecha válida; no concede permiso de borrado. `ownerIpCidr` debe ser tu red autorizada. Revisa HTTPS y restricciones web/SCM: no es una aplicación multiusuario pública.

Guarda appsettings y `MVP_AUTHORIZATION_JSON` fuera de Git. El arranque escribe la autorización fuera de la imagen; no incluyas configuración ni tokens en notas o logs.

## Construcción y despliegue

Los comandos siguientes son plantillas de operaciones con coste, no comprobaciones offline. Revisa identidad, alcance y parámetros antes de ejecutarlos en tu propia suscripción. En el snapshot sin `.git`, el SHA original se documenta en `SOURCE_REVISION.md`; para una modificación de código usa su propia revisión comprobada.

```sh
MVP_SUB='<YOUR_SUBSCRIPTION_ID>'
MVP_RG='<YOUR_RESOURCE_GROUP>'
MVP_ACR='<YOUR_REGISTRY_NAME>'
MVP_APP='<YOUR_APP_NAME>'
MVP_PARAMETERS_FILE='/ruta/privada/hosting-parameters.json'
MVP_DEPLOYMENT_OUTPUT='/ruta/privada/hosting-deployment.json'
MVP_APP_SETTINGS_FILE='/ruta/privada/hosting-appsettings.json'
MVP_SOURCE_COMMIT='8ac4dd4a1a73ee9ba6fb0d4d46eb960998f50249'
MVP_IMAGE_REF="video-checklist:${MVP_SOURCE_COMMIT}"

az deployment group what-if --subscription "$MVP_SUB" --resource-group "$MVP_RG" \
  --template-file infra/main.bicep --parameters @"$MVP_PARAMETERS_FILE" deployHosting=false

az deployment group create --subscription "$MVP_SUB" --resource-group "$MVP_RG" \
  --name video-registry --template-file infra/main.bicep \
  --parameters @"$MVP_PARAMETERS_FILE" deployHosting=false \
  --output json > "$MVP_DEPLOYMENT_OUTPUT"

az acr update --subscription "$MVP_SUB" --resource-group "$MVP_RG" --name "$MVP_ACR" \
  --role-assignment-mode rbac --output none

az acr build --subscription "$MVP_SUB" --resource-group "$MVP_RG" --registry "$MVP_ACR" \
  --image "$MVP_IMAGE_REF" --platform linux/amd64 --file Dockerfile \
  --build-arg "MVP_SOURCE_COMMIT=$MVP_SOURCE_COMMIT" .

az deployment group what-if --subscription "$MVP_SUB" --resource-group "$MVP_RG" \
  --template-file infra/main.bicep --parameters @"$MVP_PARAMETERS_FILE" \
  deployHosting=true analysisMode=fixture

az deployment group create --subscription "$MVP_SUB" --resource-group "$MVP_RG" \
  --name video-hosting --template-file infra/main.bicep \
  --parameters @"$MVP_PARAMETERS_FILE" deployHosting=true analysisMode=fixture \
  --output json > "$MVP_DEPLOYMENT_OUTPUT"

az webapp config appsettings set --subscription "$MVP_SUB" --resource-group "$MVP_RG" \
  --name "$MVP_APP" --settings @"$MVP_APP_SETTINGS_FILE" --query '[].name' --output tsv
```

El modo fixture alojado no acredita integración. Para Azure utiliza proveedor, deployment e identidad propios según [azure-preflight.md](azure-preflight.md).

## Salud, persistencia y retirada

Durante un arranque en frío App Service puede mostrar una página provisional con HTTP 200. Comprueba JSON de `/health`, SHA de fuente igual al esperado y `/api/config` de la aplicación. El ensayo utilizó B1, una instancia y `/home` persistente; no impone el SKU de una instalación nueva.

El ledger SQLite persiste fuera de la imagen. Todos los clientes del mismo alcance usan el mismo saldo por el relay HTTPS del host, sin crear una copia local. `MVP_LEDGER_URL` y `shared_ledger_url` identifican el mismo relay; `MVP_LEDGER_PATH` y `shared_ledger_path` identifican su ruta canónica. El token llega por un archivo privado indicado por `MVP_LEDGER_RELAY_TOKEN_PATH` y viaja en header, nunca en URL.

Mantén una instancia/worker mientras dependas del SQLite y sus controles de concurrencia. Antes de retirar host, cuenta, registro o respuestas conserva estado/evidencia y revisa recursos exactos, permiso y costes. La aplicación no hace esa retirada por sí sola.
