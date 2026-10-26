# Preparar Azure Foundry para una instalación propia

La revisión aceptada utilizó Azure Foundry Responses v1. Esta guía no apunta al entorno original ni despliega nada al abrirla. El adaptador Content Understanding conserva un baseline anterior; su preparador no configura Responses.

## Cuenta, modelo e identidad

Prepara una cuenta propia de IA y deployment compatible. La revisión 5 fue comprobada con `gpt-6.1-sol`, versión `2026-09-29`; verifica disponibilidad, cuota, tipo de deployment, región y precio efectivos para tu cuenta. Un SKU no acredita residencia exclusiva ni calidad del resultado.

Fija en tu manifest privado suscripción, tenant, resource ID, endpoint, deployment, modelo/versión e identidad. El operador local usa `AzureCliCredential` con tenant/principal explícitos; el host usa `ManagedIdentityCredential`. El backend comprueba scope `https://ai.azure.com/.default` y claims antes de HTTP, sin fallback automático a claves u otra identidad.

Verifica permisos de datos sobre la cuenta propia. Un rol ARM o de Content Understanding no demuestra automáticamente permiso de Responses. Consulta las definiciones vigentes y el alcance efectivo antes de asignar permisos; un 403 exige revisar la causa, no ampliar a suscripción por defecto.

## Preparación local y ejecución

Prepara cuatro IDs/etiquetas congelados, sin gold ni orden temporal anotado:

```sh
uv run --frozen python -m video_checklist.foundry prepare-schema \
  --steps /ruta/privada/steps.json --model gpt-6.1-sol \
  --output /ruta/privada/candidate.json
```

Este comando local produce definición y hash; no obtiene tokens, despliega el modelo o autoriza análisis. El muestreo adaptativo cubre todo el vídeo con hasta 2 FPS, 49 imágenes y 1280 píxeles de lado. Los tiempos son estimaciones evaluables; su formato válido no acredita interpretación correcta.

Guarda autorización, manifest y archivo de entorno fuera de Git. Configura endpoint y `MVP_AZURE_DEPLOYMENT_NAME` con el deployment verificado. `MVP_AZURE_PROVIDER=auto` toma el proveedor de la autorización; `azure_foundry_responses` exige esta ruta. Los bindings y SHA deben coincidir antes del envío.

El cliente envía `POST /openai/v1/responses`, conserva ID/rejilla temporal y recupera sólo GET de esa respuesta. No modifica defaults de Content Understanding ni vuelve a enviar por recargar. Una operación incierta requiere reconciliación. Conserva IDs y alcance de retirada propios; la aplicación no elimina respuestas.

Sigue [azure-preflight.md](azure-preflight.md), [analysis-budget.md](analysis-budget.md) y [deploy-hosting.md](deploy-hosting.md). Comprueba identidad, deployment y ledger antes de analizar. Reproduce tres clips desde UI, abre HTML/JSON, verifica saltos y revisión tras recarga y prueba entradas inválidas sin llamadas pagadas.

La existencia de un recurso no acredita aceptación. La evidencia original corresponde al 15/10/2026 y no demuestra disponibilidad actual de recursos ajenos.

Documentación: [Responses](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/responses) y [autenticación Entra v1](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle).
