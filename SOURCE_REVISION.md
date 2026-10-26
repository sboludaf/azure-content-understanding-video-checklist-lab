# Procedencia y alcance de esta adaptación

Esta distribución parte del commit original `8ac4dd4a1a73ee9ba6fb0d4d46eb960998f50249`, revisión 5 aceptada el 15 de octubre de 2026. La primera publicación pública extrajo el árbol mediante `git archive`, sin historial privado, credenciales, configuración operativa, vídeos ni respuestas crudas.

La adaptación de Azure Developer CLI modifica la infraestructura, el arranque y la configuración de la aplicación. Añade un despliegue con `azd provision` y `azd deploy`, identidad administrada y recursos propios del lector. El análisis acepta entre 3 y 6 pasos por trabajo y siempre utiliza Azure Foundry Responses. Se retira la demostración sintética de la aplicación. El historial del repositorio público identifica los cambios y su revisión actual; el SHA original identifica únicamente la procedencia.

Los datos anotados de `data/` y los scripts de evaluación conservan la muestra real histórica y su licencia, descrita en [docs/data.md](docs/data.md). No forman un modo de uso de la aplicación ni aportan resultados para un vídeo nuevo.

## Pruebas históricas

La aceptación original incluyó 197 pruebas y 26 subpruebas locales, además de tres análisis nuevos en Azure sobre variantes de una sola grabación. La revisión independiente comprobó 11 eventos emparejados, 12 saltos al tiempo guardado y seis informes descargados. El recorrido de análisis se realizó desde una interfaz local contra Azure real; del alojamiento remoto se comprobó salud y commit.

Esos resultados corresponden al código original y a su entorno del 15 de octubre. No validan los cambios de esta adaptación, una nueva infraestructura `azd`, la disponibilidad actual del modelo ni la precisión sobre otros vídeos o pasos. Las pruebas locales actuales verifican el código sin consumir Azure. La validación de extremo a extremo requiere desplegar esta revisión y analizar un vídeo real en la suscripción del lector.

`SHA256SUMS` permite comprobar los archivos distribuidos y excluye el propio manifiesto para evitar una dependencia circular. No incluye historial Git, configuración de entorno ni archivos privados generados durante el uso.
