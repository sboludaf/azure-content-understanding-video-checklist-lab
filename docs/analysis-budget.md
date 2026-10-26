# Autorización y consumo de una instalación propia

La distribución no concede acceso ni permiso de gasto. El alcance usado en el ensayo de octubre ha vencido; una instalación nueva requiere autorización, recursos y configuración propios vigentes. No copies identidades, allowlists o saldos de otro entorno.

## Paquete privado y validadores

[cloud-authorization.template.json](cloud-authorization.template.json) describe los campos esperados y parte desactivada. Un booleano `authorized: true` no sustituye vigencia, procedencia, retirada, lab/hash, recurso, proveedor, identidad, esquema, modelo o SHA.

Para Responses deben coincidir `provider=azure_foundry_responses`, `api_version=v1`, modelo/versión, `deployment_name`, endpoint y configuración privada. `provider_data` conserva la verificación del recurso y deployment, hijo de esa misma cuenta. La forma del JSON no prueba por sí sola identidad, RBAC o disponibilidad; verifica esos hechos antes de marcar el paquete como válido.

El código conserva contratos finitos de revisión 5 y dos cupos técnicos, `implementer` y `tester`. Las cifras y ampliaciones de aquel ensayo no son permisos transferibles. Revisa `video_checklist/authorization.py` y sus tests antes de adaptar un contrato; no se promete que cualquier plantilla arbitraria satisfaga sus validadores. Consulta [azure-preflight.md](azure-preflight.md).

## Reservas y operaciones inciertas

El ledger reserva intento, duración y coste conservador antes del POST. Todos los procesos del mismo alcance usan el mismo saldo canónico. Los fallos, retries e incertidumbre conservan reservas según el contrato; no reinicies ni copies un ledger para continuar una ejecución.

Sólo puede existir una operación activa o incierta dentro del alcance. Un timeout no demuestra rechazo por el proveedor. Conserva request/response ID y consulta la operación ya enviada. Polling y recargas no crean otra reserva o POST. La autorización económica no elimina límites finitos de intentos, minutos y concurrencia.

## Coste y retirada

Una reserva no es factura, precio del proveedor ni hard cap de facturación Azure. El uso de tokens se conserva separado de euros estimados; no vuelvas a sumar tokens cacheados o de razonamiento ya incluidos. Uso desconocido no significa gasto cero.

Considera App Service, Container Registry, builds, almacenamiento y tráfico cuando correspondan. Verifica precios aplicables a tu cuenta y región antes de crear recursos. Define recursos y retirada propios antes del despliegue: la aplicación no aprovisiona ni elimina recursos o respuestas automáticamente.

Mantén configuración, tokens y manifiestos fuera de Git; sanea informes y logs de distribución. El consentimiento humano aplicable y los permisos técnicos son comprobaciones separadas.
