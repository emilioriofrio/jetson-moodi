# Reporte de Progreso - Proyecto BMO Unified (Moodi)
**Semana:** S08-S09 (29 de junio al 10 de Julio, 2026)
**Archivo:** PROGRESO_BMO_S08_S09.md
**Ubicación:** `/home/jetson/`

---

## 1. Contexto y objetivo de la sesión

Punto de partida: `CONTEXTO_MOODI_UI_INTERACTIVA_08072026.md`, el documento de especificación que
reemplaza el diseño anterior de `bmo_app.py` (3 vistas + fila fija de botones fantasma, ver
`PROGRESO_BMO_S07.md`). Objetivo: reconstruir la navegación como una **cinta fantasma infinita** de
5 pantallas (Home/Caras/Oraciones/Video/Salir), remapear los 10 botones físicos del ESP32 a roles de
cursor + acciones fijas, corregir la rotación de la cámara USB, y rediseñar la pantalla Oraciones —
todo sin tocar boot/overlays/CSI (restricción no negociable heredada de `PROGRESO_BMO_S06.md`).

Tras implementar y verificar el código, se hizo una prueba real en la Jetson (pantalla táctil,
cámara, ESP32 físico) que encontró **varios problemas reales** solo visibles con hardware corriendo
— cubiertos en la sección 4. Esta sesión terminó con el motor de visión mostrando en vivo, sobre
video real, los recuadros y etiquetas de los tres módulos (A/B/C) por primera vez.

---

## 2. Resumen ejecutivo

| # | Cambio | Estado |
|---|---|---|
| 1 | Corrección de rotación de cámara (config + helper + integración en `orchestrator.py`) | ✅ Implementado y verificado con video real |
| 2 | Remapeo de 10 GPIO + firmware ESP32 reescrito + `button_map.json` actualizado | ✅ Compilado, **flasheado a la placa física**, `BOOT:OK` limpio |
| 3 | Cinta fantasma infinita (`ui/ghost_ribbon.py`, nuevo) — 5 pantallas, wrap-around, iconos propios | ✅ Implementado |
| 4 | Máquina de estados de 5 pantallas en `main_window.py` (cursores dependientes de la vista activa) | ✅ Implementado |
| 5 | Pantalla Oraciones rediseñada: saludo variable, instrucción, selección por cursor, borrado palabra por palabra | ✅ Implementado y verificado con test automatizado |
| 6 | 3 bugs reales encontrados y corregidos en pruebas con hardware físico (botón Salir, video no-seamless, crash de Módulo C) | ✅ Corregidos y reverificados |
| 7 | Diagnóstico y ajuste de rendimiento del motor de visión (A+B+C saturaban CPU/GPU) | ✅ Medido antes/después, mejora de ~3x |
| 8 | Marcas visuales por módulo (recuadro + etiqueta de A/B/C) en el panel de Video | ✅ Implementado y grabado en video real |
| 9 | Leyenda de botones activos por pantalla | ✅ Implementado |
| 10 | Root cause real del video congelado tras un loop: `QMediaPlayer.setMuted()`/`setVolume(0)` sobre un clip CON audio bloquea el pipeline GStreamer en el reinicio, en esta Jetson | ✅ Encontrado con 9+ pasos de descarte y corregido (variantes de clip sin audio cacheadas) |
| 11 | Loop de animación sin corte visible (seek a 0 antes del EOS real, en vez de esperar `EndOfMedia`) | ✅ Implementado y verificado (3 ciclos limpios, sin pasar nunca por `StoppedState`) |
| 12 | Bug de z-order: la animación quedaba permanentemente encima de todo tras visitar Oraciones, tapando el panel de Video (botón incluido) | ✅ Corregido (reafirmación explícita del orden de apilado en cada `_show_view()`) |
| 13 | Fuga de la leyenda de título de clip fuera de la pantalla Caras (efecto secundario del fix #10) | ✅ Corregido |
| 14 | Root cause real de "botones/RFID no responden": `/dev/ttyUSB0` no accesible (usuario `jetson` no estaba en el grupo `dialout`) | ✅ Diagnosticado y corregido (`usermod -aG dialout` + reinicio); verificado end-to-end |
| 15 | Rediseño visual: cinta fantasma como carrusel deslizante de verdad (antes solo resaltaba, no se movía) + insignias con emoji a color | ✅ Implementado |
| 16 | Rediseño del panel de Video: botón "Detener" (redundante, ya que salir por la cinta detiene la visión) reducido a icono circular; tarjetas por módulo en vez de texto suelto | ✅ Implementado |
| 17 | Leyenda de botones ahora se autooculta en sintonía con la cinta fantasma (antes permanente) | ✅ Implementado |
| 18 | Bot de Telegram (`@TEAmoodi_bot`) configurado y verificado end-to-end (PECS→LLM→Telegram) | ✅ Token recuperado del historial de git, verificado, y `chat_id` apuntado a la cuenta del usuario |

---

## 3. Cambios por fase (especificación nueva)

### 3.1 Corrección de rotación de cámara

**Nuevo** [`bmo_unified/config/camera.json`](file:///home/jetson/bmo_unified/config/camera.json) y
[`integradora/model_ia/sistem_IA/core/frame_utils.py`](file:///home/jetson/integradora/model_ia/sistem_IA/core/frame_utils.py)
(`load_rotation`/`apply_rotation`, mapa `NONE`/`CW`/`CCW`/`180` → constantes `cv2.ROTATE_*`).
Integrado en el bucle de captura de
[`orchestrator.py:160-173`](file:///home/jetson/integradora/model_ia/sistem_IA/core/orchestrator.py#L160):
la rotación se aplica **inmediatamente después de `cap.read()`**, antes de repartir el frame a los
Módulos A/B/C y a la UI — así todos los consumidores reciben ya el frame corregido.

> 📸 **Captura recomendada:** el frame crudo de la cámara junto al mismo frame tras `apply_rotation`,
> para documentar visualmente la corrección (o su ausencia — ver hallazgo en 4.7).

### 3.2 Remapeo de 10 GPIO + firmware ESP32

[`integradora/Oraciones_interpret/src/main.cpp`](file:///home/jetson/integradora/Oraciones_interpret/src/main.cpp#L41):

```cpp
constexpr uint8_t BUTTON_PINS[NUM_BUTTONS] = {4, 16, 32, 33, 25, 26, 27, 17, 14, 13};
```

Reemplaza el mapa anterior (`{4,5,32,33,25,26,27,14,12,13}`, de una iteración intermedia) por el
definitivo de roles cursor + isla + panel: `CURSOR_UP=4, CURSOR_DOWN=16, CURSOR_LEFT=32,
CURSOR_RIGHT=33, ISLA_R=25, ISLA_M=26, ISLA_L=27, PANEL_R=17, PANEL_L=14, PANEL_C=13`. El protocolo
serie no cambia (`BTN:<gpio>:DOWN|UP`); el firmware sigue mandando GPIO crudo, la traducción a rol
vive solo en
[`bmo_unified/config/button_map.json`](file:///home/jetson/bmo_unified/config/button_map.json).

Compilado con PlatformIO (RAM 6.6%, Flash 21.2%) y **flasheado a la ESP32 física** vía
`pio run -t upload` (`esptool.py`, chip `ESP32-D0WD-V3`, MAC `08:d1:f9:dc:cd:e8`) — `SUCCESS` en
9.93s. Verificado leyendo el puerto serie directamente tras el reset: solo `BOOT:OK`, sin eventos
espurios.

> 📸 **Captura recomendada:** la salida de terminal de `pio run -t upload` con `[SUCCESS]`, y/o la
> lectura serie mostrando `BOOT:OK` — evidencia de que el firmware nuevo quedó grabado en la placa
> real (no solo compilado).

### 3.3 Cinta fantasma infinita (`ui/ghost_ribbon.py`, nuevo)

Reemplaza [`ui/ghost_controls.py`](file:///home/jetson/bmo_unified/ui/ghost_controls.py) (fila fija
de 5 botones de acción: Borrar/Enviar/Cámara/Siguiente Cara/Cerrar) por un carrusel de **pantallas**:
Home, Caras, Oraciones, Video, Salir. Invisible por defecto, aparece con fundido al tocar la
pantalla, autooculta a los 5s (mismo patrón `QGraphicsOpacityEffect`/`QPropertyAnimation` que el
componente anterior). Selección resaltada en naranja; wrap-around cíclico vía flechas difuminadas en
los bordes. Iconos dibujados con `QPainter` (casa, cara, globo de diálogo, cámara, power) — sin
depender de assets externos.

[`ui/ghost_ribbon.py:79`](file:///home/jetson/bmo_unified/ui/ghost_ribbon.py#L79) — el icono
"Salir" no cambia de pantalla al toque directo: exige **mantener presionado 3s**
(`SALIR_HOLD_MS`) antes de emitir `exit_requested`, para evitar un cierre accidental por el niño
(ver bug relacionado en 4.1).

> 📸 **Captura recomendada:** la cinta abierta mostrando los 5 iconos con el resaltado naranja en
> la opción activa — es el cambio de navegación más visible de toda la sesión.

### 3.4 Máquina de estados de 5 pantallas (`ui/main_window.py`)

[`ui/main_window.py:38`](file:///home/jetson/bmo_unified/ui/main_window.py#L38) — `VIEWS` pasa de
`("CARA","PECS","MONITOR")` a `("HOME","CARAS","ORACIONES","VIDEO","SALIR")`.
[`_show_view()` (línea 147)](file:///home/jetson/bmo_unified/ui/main_window.py#L147) centraliza:
detener la visión al salir de "Video", iniciarla al entrar, elegir saludo nuevo al entrar a
"Oraciones", y sincronizar el resaltado de la cinta.
[`_handle_cursor()` (línea 191)](file:///home/jetson/bmo_unified/ui/main_window.py#L191) enruta los
roles `CURSOR_*` según la pantalla activa: en Caras cambian de clip de cara, en Oraciones mueven la
selección de palabra, en el resto no tienen efecto (reposo).

### 3.5 Pantalla Oraciones rediseñada (`ui/pecs_panel.py`, `core/pecs_engine.py`)

`PecsPanel` pasa de ser una franja semitransparente en la parte baja a una **pantalla completa de
fondo plano** (sin video), con saludo grande elegido al azar de
[`config/greetings.json`](file:///home/jetson/bmo_unified/config/greetings.json) (nuevo), texto de
instrucción fijo, y palabras apiladas como chips con **selección por cursor** (borde naranja en la
palabra seleccionada). [`core/pecs_engine.py`](file:///home/jetson/bmo_unified/core/pecs_engine.py)
gana `delete_at(index)`, que borra la palabra seleccionada sin afectar al resto — antes solo existía
`delete_last()`/`clear()` (todo o lo último).

Verificado con test automatizado: apilar 3 palabras, mover el cursor a la del medio, invocar
`PECS_DELETE`, confirmar que solo esa palabra desaparece y las otras dos quedan intactas.

> 📸 **Captura recomendada:** la pantalla Oraciones con el saludo, los chips apilados y uno
> resaltado en naranja — muestra de un vistazo el rediseño completo de esta vista.

---

## 4. Ronda de corrección con hardware real (feedback directo del usuario)

Tras lanzar `start_bmo.sh` completo (stack real: `llama-server` + `ia_bridge` + `bmo_app.py`) y
probarlo en la pantalla táctil física, surgieron los siguientes problemas — todos corregidos en la
misma sesión.

### 4.1 Bug: el botón "Salir" no cerraba la app

**Causa:** la cinta se autoculta a los 5s de inactividad, pero iniciar el hold de 3s sobre "Salir"
no reiniciaba ese temporizador. Si el usuario tocaba la cinta, dudaba un par de segundos y luego
mantenía presionado "Salir", la cinta podía empezar a desvanecerse a mitad de la espera, cancelando
el gesto silenciosamente.

**Corrección:**
[`ui/ghost_ribbon.py:128`](file:///home/jetson/bmo_unified/ui/ghost_ribbon.py#L128) — el press sobre
"Salir" ahora emite `press_started`, conectado a `notify_touch()` del padre (línea 187), reiniciando
el auto-hide. Se añadió además feedback visual inmediato ("Manteniendo…" en naranja) para que el
gesto se perciba como activo mientras se sostiene.

### 4.2 Bug: el video de "Caras" no era seamless (corte visible al hacer loop)

**Causa:**
[`ui/animation_player.py`](file:///home/jetson/bmo_unified/ui/animation_player.py) usaba
`QMediaPlaylist.CurrentItemInLoop`; el backend GStreamer recarga el elemento de la playlist en cada
vuelta, produciendo un parpadeo/corte visible.

**Corrección:** loop manual —
[`_on_media_status_changed()` (línea 65)](file:///home/jetson/bmo_unified/ui/animation_player.py#L65)
detecta `QMediaPlayer.EndOfMedia` y hace `setPosition(0)` + `play()` sobre el mismo `QMediaPlayer`
(línea 44), sin recargar el pipeline.

### 4.3 Diagnóstico de rendimiento: A+B+C saturaban CPU/GPU

El usuario reportó que la pantalla "Video" iba muy lenta, "cuando ya se debió solucionar" (en
referencia al fix de memoria RAM/GPU de sesiones anteriores). Se midió el motor de visión de forma
aislada (script standalone, sin `llama-server` de por medio) para descartar que el LLM fuera la
causa:

| Métrica | Antes | Después del ajuste |
|---|---|---|
| FPS promedio entregados a la UI | ~1.17 | ~3.0–3.46 (≈3x) |
| Hueco máximo entre frames | 4.6s | 2.7–3.4s |
| CPU (6 núcleos, Orin Nano) | saturada (varios núcleos al 100%) | — |
| GPU (`GR3D_FREQ`) | picos de 99% | — |

**Conclusión:** el cuello de botella real es que los 3 módulos (A: MediaPipe+DeepFace, B:
MediaPipe+TF, C: Detectron2+VGG16+BiLSTM) compiten a la vez por los 6 núcleos + GPU de la Orin Nano
— no el conflicto de memoria con el LLM (ese ya estaba resuelto). Ajuste aplicado, solo en
[`config/runtime.yaml`](file:///home/jetson/integradora/model_ia/sistem_IA/config/runtime.yaml)
(sin tocar lógica de los módulos): resolución 960×720→640×480 (línea 73), `tick_size` 10→15 (línea
68), `modulo_c.detect_every` 3→5 (línea 42), `modulo_c.analyze_every` 1→2 (línea 40).

> 📸 **Captura recomendada:** el bloque de `runtime.yaml` con los valores nuevos, junto a la tabla
> de medición antes/después — evidencia concreta de una optimización basada en datos, no en
> intuición.

### 4.4 Regresión propia: bajar `fps` a 20 rompió la cámara

Como parte del ajuste anterior se bajó también `fps` de 30 a 20. Esto **rompió la cámara**: esta
webcam USB solo negocia MJPG a 30 o 25 fps (confirmado probando 5/10/15/20/25/30 directo contra
`/dev/video0` con `gst-launch-1.0`; a cualquier valor distinto de 30/25 la negociación falla con
`streaming stopped, reason not-negotiated (-4)`), forzando una caída silenciosa al *fallback* V4L2
crudo (sin pipeline GStreamer), mucho menos confiable. Es la explicación real de que "la cámara
seguía sin funcionar como debería" tras el primer ajuste de rendimiento.

**Corrección:**
[`runtime.yaml:8`](file:///home/jetson/integradora/model_ia/sistem_IA/config/runtime.yaml#L8) —
`fps` revertido a `30`. Reverificado abriendo la pipeline MJPG real: `Cámara USB abierta (MJPG,
GStreamer)` (antes caía a `Cámara USB abierta (V4L2)`).

### 4.5 Marcas por módulo (A/B/C) en el panel de Video

El usuario pidió ver, además del color global de estrés, **qué detecta cada módulo** en vivo.

- Módulo A ya incluía `meta.region` (bbox facial) — sin cambios.
- **Nuevo** [`pose_bbox_px()` en `mod_b.py:74`](file:///home/jetson/integradora/model_ia/sistem_IA/modules/mod_b.py#L74):
  calcula el recuadro de los landmarks de pose visibles (MediaPipe Holistic) y lo agrega a
  `meta.region` (línea 246) — solo para visualización, no participa en la clasificación.
- `mod_c.py`: `person_mask_from_detectron()` descartaba la caja de Detectron2 y solo devolvía la
  máscara; ahora también devuelve `region` (bbox en píxeles), reenviado en el `meta` del `PredMsg`
  de cada tick.
- [`ui/emo_monitor_panel.py`](file:///home/jetson/bmo_unified/ui/emo_monitor_panel.py) — nuevo
  método `on_pred()` guarda la última predicción por módulo; `_draw_module_overlays()` dibuja un
  recuadro de color (A=cian, B=magenta, C=amarillo) + etiqueta (`"A: MEDIO 0.72"`) sobre el frame,
  antes de escalarlo al `QLabel`. Señal `pred_ready` conectada en
  [`main_window.py:110`](file:///home/jetson/bmo_unified/ui/main_window.py#L110) (antes no estaba
  cableada a ningún elemento visual — quedaba pendiente desde `PROGRESO_BMO_S07.md`, sección 8,
  punto 2).

Se grabó un video de 40s del motor corriendo standalone (A+B+C reales, cámara real) mostrando los
tres recuadros con etiqueta en tiempo real, para validar el resultado sin necesitar la pantalla
táctil física.

> 🎥 **Video recomendado:** la grabación de 40s del panel con los 3 recuadros por módulo — es la
> evidencia más directa de esta funcionalidad nueva y la más solicitada por el usuario.

### 4.6 Bug en Detectron2: indexado de `Boxes` con tensor en vez de `int`

Al probar el cambio de la sección 4.5 en vivo, el Módulo C crasheó a los ~30s con
`AssertionError: Indexing on Boxes with 2 failed to return a matrix!`.

**Causa:**
[`mod_c.py:72`](file:///home/jetson/integradora/model_ia/sistem_IA/modules/mod_c.py#L72) indexaba
`inst.pred_boxes[best]` donde `best` es un tensor 0-dimensional de PyTorch (resultado de
`scores.argmax()`). `Tensor.__getitem__` (usado por `pred_masks`/`scores`, que sí funcionaban)
acepta un tensor 0-d sin problema, pero la clase `Boxes` de Detectron2 sobreescribe `__getitem__`
exigiendo específicamente un `int` de Python para el camino rápido de "una sola caja" — con un
tensor, cae al camino genérico y la aserción de dimensión falla.

**Corrección:** `inst.pred_boxes[int(best)]` — conversión explícita a `int` antes de indexar.
Reproducido el escenario exacto (40s de ejecución real) tras el fix: sin errores.

### 4.7 Hallazgo: la cámara no estaba físicamente rotada

Se probaron `"CW"` y `"CCW"` en `camera.json` — ambos dejaban la imagen visiblemente de lado. Se
capturó el frame **crudo** (sin ninguna rotación) directamente de `/dev/video0` y resultó estar ya
perfectamente vertical (puerta, persona y estantería en orientación normal). Conclusión: la cámara
actual **no** está montada rotada 90°, a diferencia de lo que asumía
`CONTEXTO_MOODI_UI_INTERACTIVA_08072026.md` (posiblemente se remontó correctamente desde entonces, o
el documento describía un montaje distinto al actual).

**Corrección:** [`camera.json`](file:///home/jetson/bmo_unified/config/camera.json) → `{"rotate":
"NONE"}`. Verificado con dos videos reales grabados directo del dispositivo (sin pasar por
`bmo_app.py`): uno de la cámara cruda, otro del motor de visión completo con overlays — ambos
confirmando orientación correcta.

### 4.8 Leyenda de botones activos por pantalla

El usuario notó que no había ninguna indicación en pantalla de qué hace cada botón físico según la
vista activa. Se agregó una franja semitransparente en la parte superior
([`main_window.py`](file:///home/jetson/bmo_unified/ui/main_window.py), diccionario `LEGEND_TEXT`)
que cambia de texto en cada `_show_view()` — p. ej. en Oraciones: *"Cursores: mover selección · Isla-
Medio: borrar palabra · Isla-Izq: limpiar todo · Panel-Der: enviar"*.

### 4.9 Verificación: borrado palabra por palabra en Oraciones

El usuario preguntó si en Oraciones solo se podía "borrar todo y enviar", o si existía borrado
individual. Se confirmó con un test automatizado (ver sección 3.5) que **sí existe** desde el
rediseño de esta misma sesión: el cursor selecciona una palabra específica y `PECS_DELETE` (Isla-
Medio) borra solo esa, dejando el resto — `PECS_CLEAR` (borrado total) es una acción aparte.

---

## 5. Mapa de archivos nuevos/modificados

```
bmo_unified/
├── config/
│   ├── camera.json                 # NUEVO -- {"rotate": "NONE"} (ver 4.7)
│   ├── greetings.json              # NUEVO -- saludos variables de Oraciones
│   └── button_map.json             # Reescrito -- roles CURSOR_*/PECS_*/HOME/EMO_TOGGLE/DYNAMIC_PLAY
├── core/
│   └── pecs_engine.py               # + delete_at(index)
└── ui/
    ├── ghost_ribbon.py               # NUEVO -- cinta infinita de 5 pantallas (reemplaza ghost_controls.py)
    ├── main_window.py                # Reescrito -- VIEWS de 5 pantallas, cursores por vista, leyenda
    ├── pecs_panel.py                 # Reescrito -- pantalla completa, saludo, selección por cursor
    ├── emo_monitor_panel.py          # Reescrito -- botón circular, tarjetas por módulo (ver 9.7)
    ├── animation_player.py           # Reescrito -- variantes sin audio + loop por seek (ver 9.2/9.3)
    └── ghost_ribbon.py               # Reescrito -- carrusel deslizante + insignias emoji (ver 9.7)

bmo_unified/
├── bmo_app.py                        # + fuente Ubuntu a nivel de QApplication (ver 9.7)
├── vision/engine.py                  # + MIN_RESTART_INTERVAL_S, cooldown anti-rebote (ver 9.8)
└── config/telegram.json              # NUEVO contenido -- bot_token/chat_id configurados (ver 9.9, NO versionado)

integradora/animaciones/
└── _sin_audio/                       # NUEVO -- caché de variantes sin pista de audio (ver 9.2)

integradora/model_ia/sistem_IA/
├── core/
│   ├── frame_utils.py                # NUEVO -- load_rotation()/apply_rotation()
│   └── orchestrator.py               # + aplicación de rotación en el bucle de captura
├── modules/
│   ├── mod_b.py                       # + pose_bbox_px(), region en meta (ver 4.5)
│   └── mod_c.py                       # + bbox de Detectron2 en meta, fix indexado (ver 4.5/4.6)
└── config/runtime.yaml                # width/height/tick_size/detect_every/analyze_every (ver 4.3/4.4)

integradora/Oraciones_interpret/
└── src/main.cpp                       # BUTTON_PINS reasignado a 10 GPIO definitivos (ver 3.2)
```

---

## 6. Pruebas realizadas

- **Firmware:** compilado y **flasheado a la placa física** dos veces en total en el proyecto (ver
  histórico en `PROGRESO_BMO_S07.md`); esta sesión: mapa de 10 GPIO nuevo, `SUCCESS` en 9.93s,
  lectura serie real confirmando `BOOT:OK` limpio tras el reset.
- **Cámara:** enumeración de formatos reales soportados (`ffmpeg -f v4l2 -list_formats all`),
  prueba de negociación de framerate directo contra `/dev/video0` con `gst-launch-1.0` (detectó el
  bug de 4.4), dos videos de 5-40s grabados directo del dispositivo para confirmar orientación y
  overlays sin depender de la pantalla táctil.
- **Motor de visión:** ejecutado standalone (fuera de `bmo_app.py`) durante 35-40s en múltiples
  corridas, con y sin el ajuste de rendimiento, midiendo fps/latencia reales y confirmando cero
  procesos residuales al detener.
- **Stack completo (`start_bmo.sh`):** `llama-server` + `ia_bridge` + `bmo_app.py` lanzados dos
  veces en esta sesión sobre la Jetson real; navegación por la cinta, cambio de pantallas,
  PECS-RFID, y activación del monitor probados por el usuario en la pantalla táctil física.
- **Oraciones (borrado selectivo):** test automatizado apilando 3 palabras, moviendo el cursor y
  confirmando que `PECS_DELETE` borra solo la seleccionada (sección 3.5/4.9).
- **Congelamiento de video (segunda ronda):** 10 arneses de prueba aislados descartando variables una
  por una (efectos gráficos, layouts, anidado de widgets, tamaño fijo, mute/volumen) hasta confirmar
  la causa real y su fix, con clips de audio real extraídos del proyecto (sección 9.2/9.3).
- **Serie/RFID:** confirmado con `getfacl`/lectura directa de `/dev/ttyUSB0` que el permiso era la
  causa antes de tocar código; reverificado end-to-end tras el fix con logs reales de botones y una
  oración completa formada por tarjetas RFID (sección 9.6).
- **Stack completo (segunda ronda):** `start_bmo.sh` completo lanzado de nuevo, con prueba real del
  usuario de principio a fin: RFID → apilado → `PECS_SEND` → corrección LLM → Telegram, cierre limpio
  sin procesos residuales.
- **Telegram:** verificado en tres niveles — API cruda (`curl`/`getMe`/`sendMessage`), código real del
  proyecto (`core/telegram_sender.send_message_async()` invocado directamente), y flujo completo
  dentro de la app (`PECS_SEND` real, sección 9.9).

---

## 7. Pendiente

- ~~Verificar en la pantalla táctil física los puntos que solo se probaron por script/video~~ —
  hecho en la segunda ronda (sección 9): recorrido táctil, cambio de cara con audio, flujo
  PECS-RFID→LLM→Telegram, todo confirmado con el stack completo real.
- Confirmar con `--calibrate` que los 10 botones físicos llegan con el GPIO/rol esperado tras el
  recableado de la sección 3.2 (sigue sin confirmarse explícitamente con ese modo).
- Evaluar si el rendimiento del Módulo C (~3 fps con los ajustes actuales) es suficiente para el
  caso de uso real, o si se necesita un ajuste adicional (p. ej. reducir aún más `detect_every`, o
  evaluar correr el Módulo C bajo demanda en vez de junto con A+B).
- Evaluar la posibilidad de generar la voz esperada de las animaciones para reproducir la voz de Moodi en sintonía con su animación. Asimismo, considerar generar más animaciones que permitan cierta interactividad con el usuario. Por ejemplo que en la pantalla de Oraciones, Moodi dicte las palabras que vaya recitando. Para eso tambien deberia incluirse un apartado de configuracion que permita calibrar el volumen. Otras nuevas animaciones reaccionarian al estado general de la respuesta emocional que se esta captando. Ejemplo, si hay signos de felicidad Moodi preguntaría "Qué te tiene tan feliz?" o si identifica altos niveles de estres, pondría una cara preocupada diciendo "Cuéntame, qué te hace sentir así?"
- Identificar la causa física del evento espurio de `EMO_TOGGLE` (sección 9.8) — el cooldown evita
  que degenere en cascada, pero no explica *por qué* se dispara solo; revisar el botón físico/cableado
  de ese GPIO (13) en la placa.
- Decidir si se quiere revocar/regenerar el token de Telegram (sección 9.9) dado que quedó expuesto
  en el historial de git — el usuario decidió mantenerlo por ahora.
- `telegram_sender.py` solo admite un `chat_id` (un destinatario); evaluar si se necesita enviar a
  varios en el futuro.

---

## 8. Recomendaciones para la siguiente iteración

1. **Persistir los videos/capturas de verificación como artefactos del proyecto.** Esta sesión
   generó evidencia visual real (cámara cruda, motor de visión con overlays) fuera del repositorio
   (carpeta de trabajo temporal). Vale la pena decidir un lugar fijo (p. ej.
   `bmo_unified/docs/verificacion/`) si se quiere conservar esta evidencia junto al código.
2. **Automatizar la detección de framerates válidos de la cámara** en `orchestrator.py` (probar
   30→25→fallback) en vez de asumir un valor fijo en `runtime.yaml` — habría evitado la regresión
   de la sección 4.4 de forma preventiva.
3. Los tres puntos pendientes de `PROGRESO_BMO_S07.md` sección 8 quedan así: el punto 2 (marcas por
   módulo) se completó esta sesión (4.5); los puntos 1 (controles fantasma más inmersivos) y 3
   (persistencia de reportes por sesión) siguen pendientes.

---

## 9. Segunda ronda: pulido visual + debugging profundo de hardware (10 de julio)

### 9.1 Contexto

Con la interfaz de 5 pantallas ya en uso real, el usuario reportó una lista de problemas de UI
(video congelado, cara "aplastada" en Oraciones, iconos planos de la cinta, fondo oscuro en
Oraciones, leyenda permanente) más dos fallas funcionales graves: **la cinta de Caras no cambiaba de
cara** y **Oraciones no detectaba palabras**. La investigación de estas dos últimas terminó revelando
causas raíz completamente ajenas al código de UI.

### 9.2 Root cause real del video congelado: `setMuted()`/`setVolume()` con audio, no el
double-buffer ni los `QGraphicsEffect`

Antes de esta sesión ya existía (sin commitear) una variante de doble reproductor para precargar el
siguiente clip; se confirmó que rompía el video (congelado y "pegado" sobre toda la app) y se
revirtió al reproductor único. El usuario reportó que **incluso así** el video seguía congelándose
tras el primer loop, con la formación de oraciones sin funcionar. Se armó un banco de 10 scripts de
diagnóstico aislados (`diag_video.py` … `diag_video9.py`, fuera del repo) para descartar, uno por
uno: `QGraphicsOpacityEffect` de la cinta, el `QVBoxLayout` interno de `AnimationPlayer`, el anidado
extra de widgets (`central → AnimationPlayer → QVideoWidget`), y `setFixedSize` vs. `resize()` — 
ninguno de estos reproducía el congelamiento de forma aislada al compararlos contra el mismo arnés
de prueba. La variable real, aislada recién en el 8º-9º paso: **`player.setMuted(True)` (o
`setVolume(0)`) sobre un `QMediaPlayer` cuyo clip tiene pista de audio dejaba el pipeline de
GStreamer trabado en el primer reinicio de loop en esta Jetson**, aunque el reproductor siguiera
reportando `PlayingState`. Un clip sin pista de audio (`ffmpeg -an -c:v copy`) hace el mismo loop sin
problema, con o sin `setMuted(True)`.

**Corrección** en
[`ui/animation_player.py`](file:///home/jetson/bmo_unified/ui/animation_player.py): "silenciar" ya
no controla el volumen del reproductor en caliente — carga una variante del mismo clip **sin pista de
audio**, generada una sola vez con `ffmpeg` y cacheada en
`integradora/animaciones/_sin_audio/` (`_ensure_muted_variant()`, línea 130; `_playback_path()`,
línea 120). `set_muted()` (línea 182) recarga el clip actual con o sin audio según corresponda, y
deliberadamente **no emite `clip_changed`** (ver 9.4).

### 9.3 Loop sin corte visible (seek antes del EOS real)

Una vez resuelto el congelamiento, seguía siendo visible un "salto" duro en cada vuelta del loop —
esperar a `EndOfMedia` real dispara el manejo interno de fin de stream de GStreamer (flush + posible
frame congelado momentáneo). **Corrección:**
[`_check_seamless_loop()` (línea 166)](file:///home/jetson/bmo_unified/ui/animation_player.py#L166),
sondea la posición cada 40ms (`LOOP_POLL_MS`) y reinicia a 0 cuando faltan ≤120ms
(`LOOP_LEAD_MS`, línea 54) para el final real, **mientras el pipeline sigue en `PlayingState`** — el
salto a frame 0 (siempre keyframe) desde plena reproducción es una operación mucho más liviana que la
transición de fin de stream. `EndOfMedia` se conserva solo como red de seguridad. Verificado con
diagnóstico: los tres reinicios de loop ocurrieron a 115-132ms del final real, sin que el reproductor
pasara nunca por `StoppedState`.

### 9.4 Bug: leyenda de título de cara filtrándose a otras pantallas

Efecto secundario del fix de 9.2: como `set_muted()` ahora recarga el clip (para cambiar entre
variante con/sin audio), y antes emitía `clip_changed` en cada recarga, la leyenda temporal de título
(pensada solo para "Caras") aparecía también al entrar a Video o cualquier otra pantalla, cada vez
que cambiaba el estado de silencio. **Corrección:** `set_muted()` ya no emite `clip_changed` (solo lo
hace un cambio de cara real vía `next_clip()`/`prev_clip()`), más una guarda defensiva en
[`_show_caption()` (línea 239)](file:///home/jetson/bmo_unified/ui/main_window.py#L239) de
`main_window.py` que ignora la señal si la vista activa no es "CARAS".

### 9.5 Bug de z-order: la animación se quedaba pegada encima de todo

El usuario reportó que, tras usar la app un rato (visitar Oraciones), la animación de fondo aparecía
años más tarde flotando sobre la pantalla de Video, tapando el panel completo — incluido su botón. 
**Causa:** al entrar a Oraciones, `_animation.raise_()` la sube al frente de **todos** sus hermanos
(incluido `_monitor_panel`), y nada la baja de nuevo; al volver a pantalla completa para otra vista,
sigue por encima. **Corrección:** cada rama de
[`_show_view()` (línea 196)](file:///home/jetson/bmo_unified/ui/main_window.py#L196) reafirma
explícitamente el orden correcto para su propia pantalla en vez de asumir el orden de creación:
`_pecs_panel.raise_()` + `_animation.raise_()` en Oraciones (línea 218, para que la cara flote sobre
el fondo plano), `_monitor_panel.raise_()` en Video (línea 228, para que el panel cubra el fondo
animado).

### 9.6 Root cause real de "no cambian las caras" / "no detecta palabras": permisos de
`/dev/ttyUSB0`

Un diagnóstico aislado confirmó que `next_clip()`/`prev_clip()` (cambio de cara en Caras) funcionaba
sin problema por sí solo (5/5 cambios limpios) — descartando un bug de `AnimationPlayer`. La causa
real: el usuario `jetson` **no pertenecía al grupo `dialout`**, dueño de `/dev/ttyUSB0`
(`crw-rw---- root:dialout`), y no hay ninguna regla `NOPASSWD` de sudo configurada — por lo que el
paso `sudo -n chmod 666 /dev/ttyUSB0 || true` de `start_bmo.sh` fallaba en silencio. Sin el puerto
serie abierto, **ni los botones físicos ni las tarjetas RFID llegaban nunca a la app** —
independiente de cualquier bug de interfaz. **Corrección:** `sudo usermod -aG dialout jetson` +
reinicio de la Jetson. Verificado end-to-end tras el reinicio: `Puerto serie abierto: /dev/ttyUSB0`
en el log, botones físicos (`CURSOR_*`, `PECS_DELETE`, `PECS_CLEAR`) y lectura RFID (formación de
oración completa) funcionando.

### 9.7 Rediseño visual: paleta Moodi, tipografía, cinta fantasma y panel de Video

- **Paleta y tipografía:** colores de Oraciones muestreados directamente de un frame real de clip
  (`#93CDD6` turquesa de fondo, `#2A3C4B` navy de ojos, `#EFA082` coral de mejillas — ver
  [`ui/pecs_panel.py:16`](file:///home/jetson/bmo_unified/ui/pecs_panel.py#L16)) en vez del fondo
  oscuro genérico anterior; fuente `Ubuntu` (preinstalada, sin descargas) aplicada a nivel de
  `QApplication` en `bmo_app.py` en vez de la sans-serif por defecto de Qt.
- **Proporción de la cara en Oraciones:** `ORACIONES_FACE_RECT` pasa de un recuadro cuadrado
  (180×180, forzaba recorte/escala pareja contra clips de 16:9, se percibía "aplastada") a 320×180
  (misma proporción que los clips fuente).
- **Cinta fantasma rediseñada**
  ([`ui/ghost_ribbon.py`](file:///home/jetson/bmo_unified/ui/ghost_ribbon.py)): antes solo
  re-resaltaba un icono en una fila estática de 5; ahora es un carrusel de verdad — solo se ven 3
  posiciones (anterior/actual/siguiente) y cambiar de pantalla **desliza** el reel lateralmente
  (`_step()`, línea 303), con wraparound infinito real. Los iconos SVG monocromos planos se
  reemplazan por insignias circulares con gradiente de color propio por pantalla (`BADGE_COLORS`,
  línea 65) + glifo emoji a color (`EMOJI`, línea 58; fuente "Noto Color Emoji", también
  preinstalada).
- **Panel de Video rediseñado**
  ([`ui/emo_monitor_panel.py`](file:///home/jetson/bmo_unified/ui/emo_monitor_panel.py)): el botón
  "Detener reconocimiento" — casi redundante, ya que salir de Video por la cinta ya detiene la
  visión (ver `_show_view`) — pasa de una pastilla ancha con texto a un botón circular pequeño (✕,
  línea 107); las lecturas de los módulos A/B/C pasan de texto plano suelto a tarjetas
  (`_ModuleCard`, línea 54) con punto de color + nombre + valor.
- **Leyenda de botones activos:** antes permanente (ruido visual constante); ahora oculta por
  defecto y sincronizada con la visibilidad de la cinta fantasma vía la nueva señal
  `GhostRibbon.visibility_changed` (línea 196), conectada en
  [`main_window.py:183`](file:///home/jetson/bmo_unified/ui/main_window.py#L183).

### 9.8 Endurecimiento: cooldown anti-rebote del motor de visión

En los logs de prueba se observó el motor de visión (TensorFlow+Detectron2+MediaPipe+cámara, muy
pesado) arrancando y deteniéndose repetidamente en segundos sin que el usuario lo pidiera —
consistente con un evento espurio en el botón físico `EMO_TOGGLE` (el debounce de 50ms del firmware
ya es correcto; más probable una conexión intermitente en ese botón, no un bug de firmware). Ese
ciclo, por sí solo, es capaz de saturar los 6 núcleos de la Orin Nano lo suficiente como para
explicar congelamientos generales.
**Corrección:** [`vision/engine.py:45`](file:///home/jetson/bmo_unified/vision/engine.py#L45),
`MIN_RESTART_INTERVAL_S = 2.0` — un arranque pedido a menos de 2s del último apagado se ignora con
advertencia en el log, en vez de encadenar arranque/apagado en cascada.

### 9.9 Bot de Telegram: recuperación del token y verificación end-to-end

El usuario pidió conectar el bot ya existente (`@TEAmoodi_bot`, creado por desarrolladores
anteriores) sin tener ya el token a mano.
[`config/telegram.json`](file:///home/jetson/bmo_unified/config/telegram.json) tenía
`bot_token`/`chat_id` vacíos a propósito (nunca se versionó el secreto, ver
`PROGRESO_BMO_S07.md` sección 4.C). El token se recuperó del **historial de git** del firmware
antiguo (commit inicial `a460cac`, antes de la refactorización que lo movió a config): se verificó
que seguía activo (`getMe` → `TEAmoodi_bot` ok) y se completó `telegram.json` con él. Probado
end-to-end lanzando el stack completo (`start_bmo.sh`): `PECS_SEND` → corrección gramatical real vía
`ia_bridge`/`llama-server` (`"PUEDO AYUDARTE AGUA"` → `"Puedo ayudarte con agua."`) → mensaje
recibido en Telegram. El `chat_id` original pertenecía a otra persona (recuperado vía la API,
`first_name`/`last_name` en la respuesta); a pedido del usuario se cambió a su propio `chat_id`,
obtenido haciéndole enviar `/start` al bot y leyendo `getUpdates`.

> ⚠️ Nota de seguridad: el token quedó expuesto en texto plano en el historial de git desde el primer
> commit del proyecto. Sigue activo y se decidió mantenerlo así por ahora (decisión del usuario); si
> este repositorio llega a subirse a un remoto compartido, conviene revocarlo/regenerarlo vía
> BotFather antes de eso.

### 9.10 Metodología de verificación de esta ronda

A diferencia de sesiones anteriores, buena parte de esta ronda se verificó **sin** relanzar
repetidamente la app completa sobre la pantalla física (el usuario probaba directamente y reportaba
resultados) — en su lugar, cada hipótesis de causa raíz se aisló con un arnés de prueba mínimo
(`QMainWindow` + los widgets reales importados desde `bmo_unified`, sin cámara/serie/visión) corrido
en background, comparando resultado contra una variable a la vez. Esto permitió descartar 8 teorías
plausibles pero incorrectas antes de confirmar la real (9.2), y confirmar el fix de z-order/leyenda
por lectura de código antes de pedir una verificación física final.
