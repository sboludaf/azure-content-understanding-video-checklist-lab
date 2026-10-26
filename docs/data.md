# Dataset visual congelado — revisión 1

Se prepararon tres vídeos reales del mismo procedimiento, con licencia y gold visual antes de cualquier llamada IA. La inspección del Implementer permite usar esta muestra para una demostración controlada; la revisión independiente del Tester y la precisión de Azure están pendientes. Las etiquetas describen acciones visibles de manos y cuerda. No son instrucciones de uso del nudo ni certifican seguridad, capacidad de carga o ejecución fuera de cámara.

Fuente: [Tying off onto the middle of a rope — Slashme](https://commons.wikimedia.org/wiki/File:Tying_off_onto_the_middle_of_a_rope.webm), obra propia del 20/03/2021. Se verificó la ficha y su [revisión 1120719840](https://commons.wikimedia.org/w/index.php?title=File:Tying_off_onto_the_middle_of_a_rope.webm&oldid=1120719840). La ficha declara [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). El SHA-1 descargado coincide con el publicado por Commons. Original: 441256 ms, 190825781 bytes, SHA-256 `94fa9fff509b1d7c535309e57118a1c60ba52ab43baed63253c32d3a724d028f`.

El extracto base es continuo: segundos 0–37 del original. Los otros dos casos son montajes registrados de esa misma fuente; no son fallos reales de una persona ni muestras independientes. Se eliminó el audio, se convirtió a MP4 H.264/yuv420p, 1280×720, 25 FPS, y se retiraron metadatos de contenedor. Solo aparecen manos y antebrazos en el tramo seleccionado; no se observó un rostro. Los archivos y las planchas de fotogramas están fuera del checkout y del vault, en `/private/tmp/mvp-labs/2026-10-15-content-understanding-video-checklist/media/` con permisos de directorio 0700. No se ha autorizado subirlos públicamente.

| Clip | Transformación sobre original | Duración | Bytes | SHA-256 |
|---|---|---:|---:|---|
| base | [0,37000) ms | 37000 ms | 6579884 | `2b039dd448a97e3f734168505a84bc3a6982254e1f7decd5a4914737dbd27846` |
| omitted | [13000,37000) ms; se elimina S1 | 24000 ms | 4267432 | `b4c2ce2165b7a245053e2791addbd70c89fd2ec13fe211474e9fa7b5720d72e2` |
| reordered | [13000,15000) + [0,13000) + [15000,37000) ms; se intercambian S1 y S2 | 37000 ms | 6627302 | `b676509729bd57c05cea82982cd3ccca8049fd337e16066cc347c93fc5f47be3` |

El gold quedó congelado el **12/10/2026 19:11:57.282658 UTC**, con SHA-256 `0f93af6237385e258102055561d0be3d8eddbded481594573ae3ad94c66bb687`. Se observaron fotogramas de la fuente a 2 FPS, la frontera de paso por la anilla entre 11–18 s a 5 FPS, y todos los derivados a 2 FPS. Las etiquetas temporales se estimaron manualmente con incertidumbre de hasta 500 ms; no se atribuyen al grounding del servicio. Hay cero llamadas IA antes de este gold.

| ID | Acción visible en base | Intervalo en base | Intervalo en omitted | Intervalo en reordered |
|---|---|---|---|---|
| S1 | Preparar un doble amarillo entre las manos, lejos de la anilla | 5200–12800 ms | No observado | 7200–14800 ms |
| S2 | Introducir el doble por la anilla metálica izquierda | 13300–14800 ms | 300–1800 ms | 300–1800 ms |
| S3 | Manipular y entrelazar el doble junto a la anilla hasta un nudo visible | 15000–32700 ms | 2000–19700 ms | 15000–32700 ms |
| S4 | Tirar del ramal largo hacia la derecha y dejarlo extendido | 33000–36500 ms | 20000–23500 ms | 33000–36500 ms |

S2 dura aproximadamente **1,5 segundos** y la anilla es un objeto pequeño. Esto supone un riesgo para el muestreo aproximado a 1 FPS de Content Understanding: el paso se ve en el material, pero no se promete que el servicio lo detectará. En el clip omitido hay un doble de cuerda ya formado; su existencia no demuestra que la preparación S1 se vea en ese vídeo. El Tester debe revisar las etiquetas antes de evaluar y registrar discrepancias; el gold no se modifica para mejorar un resultado IA.

Desde la raíz de un clon limpio, con Python y FFmpeg/FFprobe con libx264 disponibles:

```sh
python3 scripts/prepare_media.py --inspect
export MVP_SAMPLE_PATH=/private/tmp/mvp-labs/2026-10-15-content-understanding-video-checklist/media/base.mp4
```

La preparación descarga el original público solo si falta, comprueba su hash/tamaño, construye los tres clips y comprueba sus hashes, codec, duración y tamaño. No hace llamadas Azure ni modifica el gold. `--output-dir` permite otra carpeta temporal fuera del checkout. El manifest contiene los argumentos exactos de FFmpeg y el mapeo de tiempos de fuente a derivado. La codificación congelada usa **FFmpeg 8.1.1, libx264, CRF 23, preset medium**; diferencias de compilación/encoder pueden producir hashes distintos. El script se detiene ante esa diferencia: corresponde recuperar los bytes congelados o reconciliar la revisión del dataset antes de QA, sin retocar etiquetas por el resultado IA.

Para evaluar un informe JSON normalizado obtenido realmente por Azure:

```sh
python3 scripts/evaluate.py --gold data/gold.json --clip base --result /ruta/privada/informe-base.json
python3 scripts/evaluate.py --gold data/gold.json --clip omitted --result /ruta/privada/informe-omitted.json
python3 scripts/evaluate.py --gold data/gold.json --clip reordered --result /ruta/privada/informe-reordered.json
```

Cada ejecución imprime JSON saneado con TP/FP/FN, precisión, recall, mediana/máximo de error de inicio, emparejamientos y motivos de fallo. Empareja uno a uno por ID, intervalo válido e inicio a ±3000 ms, sin contar dos veces un gold. Para ese clip exige precisión ≥0,90, recall ≥0,80, denominadores distintos de cero, intervalos válidos y cero afirmaciones observadas de la acción eliminada. Una salida `source=fixture` u otra fuente no Azure se rechaza para métricas de aceptación; el hash y la duración de entrada deben coincidir con el gold congelado. Código de salida 0 solo significa que esas métricas de un clip cumplen los umbrales: **no concede PASS de QA**, ni prueba saltos UI, presupuesto, retirada o independencia del Tester. Hay que conservar y reunir los tres informes y completar A1–A8.

Las pruebas unitarias del evaluador usan payloads sintéticos explícitos y comprueban duplicados, matching, frontera ±3 s, intervalos, entrada equivocada, omisión y rechazo de fixture. No son análisis Azure ni evidencia de aceptación del laboratorio.

Verificación propia del Implementer el 12/10/2026: se ejecutó `prepare_media.py` en una segunda carpeta temporal sin derivados, reutilizando por hard link el original ya comprobado. Produjo los tres hashes, tamaños y duraciones exactamente iguales al manifest, sin volver a descargar la fuente. Los 12 tests de `tests/test_evaluation.py` pasaron; una ejecución CLI con payload unitario `source=fixture` devolvió código 1 y ninguna métrica de aceptación. Se releyó el gold y se comprobó que su hash seguía siendo el congelado. Estos resultados acreditan preparación/reproducción local y el evaluador; no acreditan análisis Azure, A1–A8 ni QA independiente.

Atribución que acompaña cada derivado/informe:

> Tying off onto the middle of a rope — Slashme (2021), Wikimedia Commons, CC BY-SA 4.0. Cambios: extracto continuo 0–37 s, eliminación de audio y conversión H.264; el clip omitted elimina 0–13 s; el clip reordered intercambia 0–13 s con 13–15 s. El autor no avala este laboratorio.

Si se distribuye un derivado, conservar esa atribución, enlace de licencia y cambios, aplicando CC BY-SA 4.0 al derivado. El código queda separado de la licencia del vídeo. El manifest mantiene fuente, revisión, hashes, licencia y transformaciones para auditoría. El gold nunca se envía al analizador: el cliente solo puede enviar el vocabulario de IDs/descripciones sin el orden esperado y sin tiempos gold.

Consumo local observado: una descarga de 190825781 bytes y tres derivados de 17474618 bytes, sin Azure, facturación, publicación o recursos nuevos. Una suite de los tres clips suma 98 segundos de vídeo; cualquier envío futuro necesita autorización y reserva en el ledger conjunto.
