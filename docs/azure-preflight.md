# Preflight de una instalación Azure propia

La integración aceptada el 15/10/2026 utilizó Azure Foundry Responses v1 con `gpt-6.1-sol`, versión `2026-09-29`. Esa evidencia no acredita disponibilidad actual. La distribución no incluye acceso, identidades, autorización o endpoints del entorno original.

## Configuración que debe coincidir

- Proveedor `azure_foundry_responses`, API `v1`, endpoint HTTPS propio, deployment y modelo/versión disponibles para tu cuenta/región/cuota.
- Identidad y tenant explícitos: `AzureCliCredential` para el operador local o `ManagedIdentityCredential` en el alojamiento. El scope de Responses es `https://ai.azure.com/.default`. Verifica permisos de datos efectivos sobre la cuenta; un rol ARM no prueba acceso al modelo.
- Lab/hash, SHA, esquema, resource ID y allowlist del paquete privado, sin copiar recursos o saldo de otro entorno.
- Autorización vigente y ledger canónico compartido, con reservas anteriores al envío. La plantilla [cloud-authorization.template.json](cloud-authorization.template.json) parte desactivada; cambiar un booleano no satisface los demás validadores.

`MVP_AZURE_PROVIDER=auto` toma el proveedor de la autorización; `azure_foundry_responses` exige esa ruta. `MVP_AZURE_DEPLOYMENT_NAME` debe coincidir con la binding privada. La ruta `azure_content_understanding` conserva un baseline histórico y no configura Responses.

## Preparar el esquema sin llamar a Azure

Crea un archivo privado con los cuatro IDs/etiquetas congelados, sin gold ni orden temporal anotado:

```sh
uv run --frozen python -m video_checklist.foundry prepare-schema \
  --steps /ruta/privada/steps.json --model gpt-6.1-sol \
  --output /ruta/privada/candidate.json
```

El comando calcula definición/ID/hash, sin obtener token ni crear recursos. El muestreo recorre todo el vídeo a frecuencia adaptativa, hasta 2 FPS, 49 imágenes y 1280 píxeles de lado. Conserva PTS reales. Son límites de la aplicación, no cuotas acreditadas del proveedor; los tiempos son `model_estimate` y deben contrastarse con anotaciones.

## Arranque privado

Guarda configuración, manifest y autorización fuera de Git. `.env.example` enumera variables, pero arranca la demostración sintética y no concede acceso Azure.

```sh
uv run --frozen --env-file /ruta/privada/azure.env uvicorn video_checklist.main:app --host 127.0.0.1 --port 8765
```

Si tu versión de uv no carga una ruta con espacios, carga un archivo de entorno de confianza en un subshell y usa `--no-env-file`. Cargar un archivo con shell puede ejecutar su contenido; usa únicamente archivos propios revisados.

## Envío, recuperación y aceptación

El cliente usa `POST /openai/v1/responses` con imágenes privadas `input_image`, `background=true`, `store=true` y el nombre exacto del deployment. Persiste el response ID y sólo consulta GET de la misma operación. No reenvía por polling, recarga o timeout; una operación incierta sin ID requiere reconciliación antes de otro análisis.

Revisa retención y eliminación en [Responses](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/responses). Conserva IDs y alcance de retirada propios; la aplicación no elimina respuestas automáticamente. La factura y las reservas del ledger son medidas distintas.

Reproduce interfaz, tres clips, revisión, exportaciones, saltos y rechazos desde una instalación independiente. Un deployment preparado, fixtures o una página provisional HTTP 200 no acreditan integración. En alojamiento, `/health` debe devolver JSON de la aplicación y el SHA esperado, junto a `/api/config` propio. Sigue [deploy-hosting.md](deploy-hosting.md), [analysis-budget.md](analysis-budget.md) y [data.md](data.md).
