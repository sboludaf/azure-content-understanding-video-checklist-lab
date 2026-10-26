# W42 — despliegue con Azure Developer CLI

Status: Ready for Validation

## 1. Encargo y alcance
Preparar y publicar el repositorio para que cada lector despliegue la aplicación y su infraestructura en su propia suscripción Azure. Sin modo de análisis sintético. No se ejecuta un despliegue Azure durante esta adaptación.

## 2. Receta
Azure Developer CLI con Bicep. Crear recursos con `azd provision`; construir y publicar el contenedor con `azd deploy`.

## 3. Arquitectura
Grupo de recursos propio, Container Registry Basic, App Service Linux B1, cuenta AIServices y deployment de modelo compatible con visión y Responses. Identidad administrada del host con AcrPull sobre su registro y Cognitive Services OpenAI User sobre su cuenta de IA. Una instancia y un worker; estado persistente en /home/video-checklist.

## 4. Configuración del lector
El lector configura suscripción, regiones, modelo y versión, cuota disponible y su IP pública /32 o /128. No se fijan identificadores del propietario ni claves. La restricción web deniega el resto de direcciones. Los límites locales de consumo no constituyen un presupuesto de Azure.

## 5. Preparación completada
azure.yaml, módulos Bicep, parámetros, hooks de empaquetado y configuración del host. El hook vincula la imagen a un commit limpio de Git. El runtime sólo utiliza Azure real, con checklist de 3–6 pasos. README y guía de despliegue actualizados.

## 6. All validation checks pass
- [x] AZD instalado: 1.34.2.
- [x] azure.yaml validado frente al esquema oficial y sus referencias.
- [ ] Entorno, autenticación, suscripción y ubicación del lector.
- [ ] Cuota, disponibilidad del modelo y políticas de esa suscripción.
- [ ] Provision preview contra la suscripción del lector.
- [x] Pruebas de código, lock de dependencias y sintaxis JavaScript.
- [x] Contexto Docker revisado; sólo código, lock, licencia, manifiesto real histórico y SHA de fuente.
- [ ] Empaquetado remoto en ACR y despliegue Azure.
- [x] Asignaciones de roles verificadas estáticamente.
- [ ] Recorrido web completo con un vídeo real en Azure.
- Aspire: no aplica.

## 7. Validation Proof
- `az bicep build --file infra/main.bicep --outfile /tmp/w42-final-arm.json`: compilación correcta.
- Validación JSON Schema oficial de azure.yaml: correcta, 16 documentos de esquema resueltos.
- `MVP_RUNTIME_DIR=/tmp/video-checklist-tests .venv/bin/pytest -q`: 212 pruebas y 26 subpruebas correctas.
- `node --check video_checklist/static/app.js`: correcto.
- `uv lock --check --offline`: correcto.
- Hooks: manifiesto de SHA correcto desde Git limpio; rechazan fuente modificada y ausencia de infraestructura.
- Revisión independiente del contrato infraestructura/runtime/Docker: sin bloqueos detectados en los valores predeterminados.

El estado permanece Ready for Validation: no se ha validado esta nueva infraestructura en una suscripción Azure. La aceptación histórica de la revisión 5 no se atribuye a esta adaptación. El coste de App Service, Registry y modelo corresponde a la suscripción donde el lector decida desplegar.
