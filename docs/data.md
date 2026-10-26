# Muestra real de evaluación histórica

La aplicación desplegada recibe un MP4 que aporta el usuario. No descarga esta muestra, no incorpora resultados precalculados y no ofrece un modo sintético.

`data/manifest.json` y `data/gold.json` conservan la procedencia y las anotaciones manuales de la muestra usada en el ensayo original. No se envía el gold al modelo. Los vídeos y las respuestas de ese ensayo no se distribuyen en el repositorio.

## Fuente y licencia

Fuente: [Tying off onto the middle of a rope, de Slashme (2021)](https://commons.wikimedia.org/w/index.php?title=File:Tying_off_onto_the_middle_of_a_rope.webm&oldid=1120719840), bajo [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). El vídeo original dura 441256 ms y tiene SHA-256 `94fa9fff509b1d7c535309e57118a1c60ba52ab43baed63253c32d3a724d028f`.

Se prepararon tres extractos reales del mismo original, sin audio, convertidos a H.264, 1280×720 y 25 FPS:

| Clip | Cambio sobre el original | Duración | SHA-256 |
|---|---|---:|---|
| `base` | Segundos 0 a 37. | 37 s | `2b039dd448a97e3f734168505a84bc3a6982254e1f7decd5a4914737dbd27846` |
| `omitted` | Segundos 13 a 37; elimina la primera acción anotada. | 24 s | `b4c2ce2165b7a245053e2791addbd70c89fd2ec13fe211474e9fa7b5720d72e2` |
| `reordered` | Tramos 13 a 15, 0 a 13 y 15 a 37, en ese orden. | 37 s | `b676509729bd57c05cea82982cd3ccca8049fd337e16066cc347c93fc5f47be3` |

Son montajes controlados de una sola grabación; no son tres muestras independientes ni fallos reales de una persona. Las etiquetas describen acciones visibles de manos y cuerda. No certifican seguridad, capacidad de carga ni lo ocurrido fuera de cámara.

El gold se congeló el 12 de octubre de 2026 antes de las llamadas IA, con hash `0f93af6237385e258102055561d0be3d8eddbded481594573ae3ad94c66bb687`. Sus cuatro acciones y tiempos están en `data/gold.json`. El ensayo histórico no mide la precisión de los nuevos pasos que escriba otro usuario.

## Atribución de los derivados

> Tying off onto the middle of a rope, Slashme (2021), Wikimedia Commons, CC BY-SA 4.0. Cambios: extracto continuo 0 a 37 s, eliminación de audio y conversión H.264; `omitted` elimina 0 a 13 s; `reordered` intercambia 0 a 13 s con 13 a 15 s. El autor no avala esta aplicación.

Al distribuir derivados, conserva autor, fuente, licencia y cambios, y aplica CC BY-SA 4.0 al derivado. La licencia MIT del código no cambia la licencia del vídeo. `scripts/prepare_media.py` conserva las transformaciones históricas para evaluación, fuera del recorrido de despliegue y uso normal.
