# Despliegue con Azure Developer CLI

El [inicio del README](../README.md#desplegar-en-tu-azure) crea una instalación propia. `azd provision` despliega Bicep y las asignaciones de roles; `azd deploy` construye la imagen en Azure Container Registry y la publica en App Service. `azd deploy` por sí solo necesita una infraestructura aprovisionada previamente.

## Configuración del entorno

Selecciona tu suscripción y restringe el acceso web a tu IP pública. La cuenta que aprovisiona debe poder crear el grupo de recursos, los servicios y las asignaciones de roles. La identidad administrada de la aplicación recibe `AcrPull` en su registro y `Cognitive Services OpenAI User` en su cuenta de IA.

| Variable de `azd` | Uso |
|---|---|
| `AZURE_SUBSCRIPTION_ID` | Suscripción propia donde se crearán los recursos. |
| `AZURE_LOCATION` | Región del grupo de recursos y Container Registry. |
| `AZURE_APP_SERVICE_LOCATION` | Región del host; por defecto `westeurope`. |
| `AZURE_AI_LOCATION` | Región de la cuenta de IA; por defecto `swedencentral`. |
| `MVP_ALLOWED_CLIENT_CIDR` | IP pública autorizada, con `/32` para IPv4 o `/128` para IPv6. |
| `AZURE_OPENAI_MODEL_NAME` | Modelo; por defecto `gpt-6.1-sol`. |
| `AZURE_OPENAI_MODEL_VERSION` | Versión del modelo; por defecto `2026-09-29`. |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Nombre del deployment; por defecto `video-checklist`. |
| `AZURE_OPENAI_DEPLOYMENT_SKU` | SKU del deployment; por defecto `GlobalStandard`. |
| `AZURE_OPENAI_CAPACITY` | Unidades de capacidad; por defecto `1`. |
| `MVP_MAX_ANALYSES` | Límite acumulado de análisis; por defecto `1000`. |
| `MVP_MAX_TOTAL_MINUTES` | Límite acumulado de minutos; por defecto `1500`. |

Puedes cambiar una variable antes de `azd provision`:

```sh
azd env set AZURE_APP_SERVICE_LOCATION westeurope
azd env set AZURE_AI_LOCATION swedencentral
```

Comprueba que tu cuenta dispone de cuota para el modelo, versión y SKU elegidos en esa región. Los valores predeterminados son `gpt-6.1-sol`, versión `2026-09-29`, y `GlobalStandard`. El despliegue no cambia de modelo o región automáticamente. Si no están disponibles, configura valores compatibles y vuelve a aprovisionar. El modelo debe admitir imágenes, Responses v1, ejecución en segundo plano y salida JSON Schema; la compatibilidad y calidad de otro modelo requieren verificación propia. `GlobalStandard` no garantiza que el procesamiento quede limitado a la región de la cuenta.

El nombre histórico del repositorio incluye Content Understanding. El análisis actual utiliza Azure Foundry Responses v1.

## Publicar cambios

Los hooks obtienen la revisión de Git para incluirla en la imagen. Guarda los cambios de la aplicación en un commit antes de desplegar:

```sh
git add <archivos-modificados>
git commit -m "Describe el cambio"
azd deploy
```

El contenedor incluye las dependencias Python y FFmpeg; el lector no necesita prepararlos en su equipo para utilizar el alojamiento Azure. La construcción remota evita depender de la arquitectura de su ordenador. Si la suscripción bloquea ACR Tasks, revisa sus permisos y disponibilidad; la construcción local alternativa requiere Docker.

Al cambiar `infra/`, `azure.yaml` o los valores que consume la infraestructura, ejecuta:

```sh
azd provision
azd deploy
```

## Comprobar el despliegue

Después de `azd provision` la aplicación todavía necesita su imagen: ejecuta `azd deploy`, que muestra su dirección. El entorno conserva también la salida `SERVICE_WEB_ENDPOINT_URL`. Abre `/health` desde la IP autorizada y comprueba que devuelve JSON de la aplicación, `mode: azure` y la revisión de Git desplegada. Una página provisional de App Service con HTTP 200 no demuestra que el contenedor esté listo.

Prueba desde la interfaz un MP4 propio dentro de los límites: edita los pasos, inicia un análisis real, comprueba los saltos al vídeo, guarda una revisión y abre ambos informes. Recarga durante la consulta de una operación para comprobar que recupera el mismo trabajo. Verifica también que un archivo inválido se rechaza antes de invocar el modelo.

El despliegue no ejecuta análisis automáticamente. La nueva infraestructura y este recorrido necesitan comprobación en tu suscripción; la evidencia del ensayo original no los sustituye.

## Estado, consumo y datos

La aplicación mantiene el vídeo, los fotogramas, la respuesta y las revisiones en `/home/video-checklist`. Un ledger SQLite persistente reserva cada análisis antes de enviarlo y limita intentos y duración acumulada. Usa una instancia y un worker; escalar exige sustituir esta coordinación por almacenamiento compartido apropiado.

Los límites iniciales son 1000 análisis y 1500 minutos acumulados. Son controles de la aplicación, no un presupuesto monetario ni una garantía sobre la factura. Una operación incierta conserva su estado y su reserva. No borres el ledger para repetir una solicitud cuyo resultado desconoces.

La aplicación usa Responses con almacenamiento de respuesta en el servicio para recuperar operaciones. No elimina esas respuestas automáticamente. Revisa la [retención y eliminación de Responses](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/responses) para tu recurso, además de los archivos que conserva App Service. Los vídeos privados, tokens y archivos de configuración quedan fuera de Git.

## Retirar la instalación

Descarga primero los informes que quieras conservar y ejecuta en el entorno correcto:

```sh
azd down
```

Revisa los recursos enumerados antes de confirmar. Se elimina el alojamiento y su estado persistente, junto con los recursos del entorno que retire `azd`. Comprueba después los recursos y el consumo de tu suscripción. App Service, Container Registry y el uso del modelo tienen costes independientes.

Referencia: [flujo de trabajo de Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/azd-commands).
