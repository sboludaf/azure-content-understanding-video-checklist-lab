# Procedencia de esta distribución

Commit original: `8ac4dd4a1a73ee9ba6fb0d4d46eb960998f50249` (revisión 5 del código aceptado el 15/10/2026).

Este snapshot se extrajo mediante `git archive` del árbol de ese commit. No contiene historial Git privado, `.git`, credenciales, configuración operativa, vídeos, respuestas crudas o entornos virtuales.

El código de `video_checklist/`, `scripts/`, `tests/`, el lock y los datos/etiquetas versionados son idénticos byte a byte al árbol original. Sólo se reescribieron o sanearon estos archivos de documentación/configuración:

- `README.md`
- `docs/analysis-budget.md`
- `docs/azure-preflight.md`
- `docs/deploy-foundry.md`
- `docs/deploy-hosting.md`
- `infra/parameters.example.json`

Los cambios documentales eliminan referencias al entorno privado y preparan instrucciones para una instalación propia. No constituyen una nueva ejecución Azure ni transfieren autorizaciones del ensayo original. El SHA original identifica la procedencia, no un commit Git nuevo de este snapshot sin historial.

Se añadieron este documento, `SHA256SUMS` y `docs/architecture.svg`. El diagrama SVG estático reproduce la arquitectura documentada del ensayo y el README incorpora el enlace de clonación al repositorio público. Son cambios de documentación: no modifican el código, las pruebas ni los datos del commit original. El manifiesto SHA-256 cubre todos los archivos de la distribución excepto el propio manifiesto, para evitar una dependencia circular. El inventario externo `FILE_INVENTORY.json` compara cada archivo original con el snapshot y permite comprobar que la fuente permanece intacta.
