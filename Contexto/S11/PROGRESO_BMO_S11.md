# Reporte de Progreso - Proyecto BMO Unified (Moodi)
**Semana:** S11 (20 al 24 de julio, 2026)
**Archivo:** PROGRESO_BMO_S11.md
**Ubicación:** `/home/jetson/Contexto/S11/`
**Punto de partida:** `Contexto/S11/Prompt_S11.md` + los pendientes de `PROGRESO_BMO_S10.md`

---

## 1. Contexto y objetivo de la semana

S10 cerró con la pantalla de Configuraciones, i18n es/en, remapeo de botones y voz TTS
funcionando, pero con tres frentes abiertos que el usuario detectó **usando el robot de verdad**:

1. La app tarda en arrancar y hay que lanzarla escribiendo un comando en una terminal.
2. El diagrama de botones de Configuraciones era un dibujo de figuras (placeholder explícito de S10).
3. **Lo más grave:** el monitor emocional "se ve recortado y se traba varios segundos", la
   detección se percibe imprecisa, y el LLM que corrige las oraciones es "muy impreciso".

El objetivo de S11 fue atacar esos tres frentes priorizando el tercero, que es el que degrada la
función central del producto. Se mantuvo la restricción no negociable vigente desde S06: **nada de
`/boot`, overlays de device tree ni scripts CSI**, y la app nunca corre como `sudo`.

Método: igual que en S08-S10, **medir antes de tocar**. Las decisiones de esta semana no salieron de
leer el código sino de reproducir cada síntoma con un experimento (colas de multiprocessing, campo
de visión real de la cámara, A/B del prompt del LLM). Eso cambió el diagnóstico en dos casos donde
la explicación "obvia" era la equivocada.

---

## 2. Resumen ejecutivo

| # | Cambio | Estado |
|---|---|---|
| 1 | **Causa raíz de los tirones de video**: las colas de frames bloqueaban al orquestador. fps a la UI **1.41–2.50 → 30.00**; gap máximo entre frames **varios segundos → 0.07 s** | ✅ Corregido y medido en corrida real |
| 2 | **Causa raíz del "video recortado"**: 640x480 era un **recorte central** del sensor. Cambiado a 640x360 (16:9) = campo de visión completo y 25% menos píxeles | ✅ Corregido y verificado con capturas |
| 3 | Regresión propia detectada y corregida: el Módulo C dejaba de emitir al descartarse frames | ✅ Corregido |
| 3b | Umbrales que estaban en **frames** (no en segundos) reescalados: al subir a 30 fps reales pasaban a significar <1 s | ✅ Reajustados |
| 3c | Ritmo de predicción del Módulo C (~2 cada 130 s) | ⏳ **No resuelto** — ver 3.5 |
| 4 | **Calidad del LLM de oraciones**: prompt few-shot con el vocabulario real + muestreo casi determinista, **sin tocar la RAM** (mismo modelo) | ✅ Implementado y validado con A/B de 10 casos |
| 5 | Respaldo determinista si `llama-server` cae: el mensaje del niño ya no se pierde | ✅ Implementado y probado con el servicio caído |
| 6 | Bug pendiente de S10: `_label_history` no se limpiaba entre sesiones del motor de visión | ✅ Corregido |
| 7 | Acceso directo en el Escritorio + entrada en el menú de aplicaciones | ✅ Instalado y validado |
| 8 | Pantalla de carga con LOGO, lluvia de piezas de rompecabezas y barra de progreso real | ✅ Implementada |
| 9 | Diagrama de remapeo reemplazado por el render real `MOODI_VIEW.png`, con selección tipo hover | ✅ Implementado, coordenadas medidas |
| 10 | Interruptor de voz de Moodi en la pantalla Oraciones (persistente) | ✅ Implementado |
| 11 | Suite headless de S11 (29 comprobaciones) + arnés de medición de fps, ambos versionados | ✅ 29/29 OK |
| 12 | Audio saturado por limitación física de las bocinas | ⏳ **Sigue pendiente** — ver sección 6 |
| 13 | Validación física en el robot | ⏳ Pendiente del usuario — ver sección 7 |

---

## 3. Fluidez y precisión del monitor emocional (el bloque principal)

### 3.1 Los tirones: el orquestador estaba encadenado al módulo más lento

**Síntoma reportado:** "se traba varios segundos la captura y muestra de imagen, no es para nada
fluido".

**Diagnóstico.** El orquestador repartía cada frame con `qC.put(msg)` / `qB.put(msg)` sobre
`multiprocessing.SimpleQueue`, que **no tiene límite de tamaño**. Parece que nunca bloquearía, y por
eso el problema había pasado desapercibido: la cola es ilimitada, pero por debajo escribe en un
**pipe del sistema operativo cuyo buffer son 64 KB**, y un frame BGR de 640x480 pesa ~900 KB. Es
decir, *cada* `put()` se quedaba esperando a que el worker leyera.

Consecuencia: el bucle de captura corría al ritmo del módulo **más lento**. Durante la ráfaga de 10
pasadas secuenciales de VGG16 del Módulo C, la captura quedaba congelada segundos enteros y la UI
simplemente dejaba de recibir imágenes.

**Medición que lo confirmó** (12 frames de 640x480 hacia un consumidor lento):

| Cola | Tiempo total | `put()` más lento |
|---|---|---|
| `SimpleQueue` (como estaba) | **11.05 s** | 1.01 s |
| `Queue(maxsize=5)` + descartar si está llena | **0.01 s** | 0.00 s |

**Corrección.**
- `core/orchestrator.py`: nuevo helper `put_drop_if_full()`, que entrega el frame sin bloquear y lo
  **descarta** si el worker va atrasado. Se descarta el frame *nuevo* (y no se saca el viejo) a
  propósito, para que el orquestador nunca compita con el worker haciendo `get()` sobre su cola.
- `run.py` y `bmo_unified/vision/engine.py`: las colas `qA_in/qB_in/qC_in` pasan a
  `ctx.Queue(maxsize=queues_maxsize)`. **`queues_maxsize` ya existía en `runtime.yaml` desde
  siempre, pero no lo leía nadie**; ahora es real.
- `cancel_join_thread()` antes de `close()`: las colas acotadas tienen un hilo alimentador que puede
  colgar el cierre, y este motor se enciende y apaga varias veces por sesión.

Para predicción en tiempo real, descartar frames cuando un módulo va atrasado es lo correcto: es
preferible una predicción reciente a una cola de frames viejos. De hecho parte de la "imprecisión"
percibida era eso — los módulos estaban clasificando imágenes de varios segundos atrás y el recuadro
se dibujaba sobre el frame actual.

**Resultado en corrida real (A+B+C, cámara física, 130 s):**

| Métrica | S10 | S11 |
|---|---|---|
| fps entregados a la UI (estable) | 1.41 → 2.50 | **30.00** |
| Gap máximo entre frames | varios segundos | **0.07 s** |
| Gap mediano | — | 36 ms |
| Primer frame tras `start()` | tras cargar todos los modelos | **1.3 s** |
| Pausa única de ~16 s al cargar Detectron2 | presente | **desaparecida del video** |

El gap de ~16 s que S10 documentaba como "final de la carga de Detectron2+VGG16" ya no afecta al
video: la captura dejó de esperar a los workers, así que la carga de modelos ocurre en paralelo sin
congelar la imagen.

### 3.2 El "video recortado": era el campo de visión, no el layout

Primero se sospechó del layout (un `QLabel` con pixmap desborda su contenedor). Se midió la
geometría y **la hipótesis era falsa**: el widget de video quedaba en 748x486 terminando en y=579,
dentro de los 600 px del panel.

La causa real es la cámara: pedirle **640x480 (4:3) devuelve un recorte central del sensor**. Se
comprobó capturando la misma escena en ambos modos: en 16:9 se ven la bandera de la pared izquierda
y la estantería derecha; en 4:3 desaparecen y la imagen queda "acercada".

**Corrección:** `runtime.yaml` pasa a `640x360`, que es **el sensor completo** (mismo encuadre que
1280x720) y además tiene **25% menos píxeles que 640x480** (230k vs 307k) — gana campo de visión y
baja el costo por frame. Ambos son modos MJPG nativos a 30 fps, respetando la regla conocida de que
esta cámara solo negocia 30 o 25 fps.

**Efecto secundario que hubo que compensar:** `person_ratio` del Módulo C es la fracción del *frame*
ocupada por la persona, así que al ampliar el campo de visión baja. Se midió con Detectron2 sobre la
misma escena en ambos modos: **0.0141 (4:3) → 0.0114 (16:9)**, factor 0.81 (coherente con el 0.75
teórico de recortar 4:3 sobre un sensor 16:9). Por eso `min_person_ratio` baja de **0.15 a 0.12**.

Además, en `ui/emo_monitor_panel.py` el video se escala ahora contra `contentsRect()` con
`QSizePolicy.Ignored`, para que el pixmap nunca pueda arrastrar al layout (endurecimiento, aunque no
era la causa).

### 3.3 Regresión propia: el Módulo C dejó de emitir

Tras el arreglo de las colas, la primera corrida real mostró un efecto colateral que **no estaba en
el reporte del usuario y que introduje yo**: el Módulo C emitió solo **2 predicciones en 130 s**.

Causa: C solo publicaba cuando le llegaba un frame con `fidx % tick == 0` (tick=15) teniendo ya la
ventana de 11 frames llena. Mientras la cola era bloqueante, C recibía **todos** los frames en orden
y esa condición se cumplía puntualmente. Al empezar a descartar frames, acertar un índice múltiplo
exacto de 15 se volvió una lotería.

**Corrección** (`modules/mod_c.py`): el disparo pasa de "índice exacto múltiplo de tick" a
**"distancia en frames desde la última predicción ≥ tick"**. Conserva la intención original (no
repetir la ráfaga de VGG16 más seguido que cada `tick` frames) sin depender de qué frames
sobrevivieron al descarte. Mismo cambio para el aviso de "aún calentando".

> Nota de método: esto salió de mirar el conteo de predicciones por módulo en la corrida real, no
> del fps. Un cambio de infraestructura como el de las colas puede mejorar espectacularmente una
> métrica y romper otra en silencio.

### 3.4 Efecto sistémico: los umbrales estaban en FRAMES, y ahora los frames vuelan

Éste es el hallazgo más sutil de la semana y conviene tenerlo presente en adelante.

Varios parámetros de `runtime.yaml` **no están en segundos sino en número de frames**, y se
calibraron cuando la captura entregaba ~2.5 fps. Al restaurar los 30 fps reales, el mismo número de
frames pasó a representar una ventana de tiempo **12 veces más corta**:

| Parámetro | Valor S10 | Significaba (a 2.5 fps) | Pasaba a significar (a 30 fps) | Valor S11 |
|---|---|---|---|---|
| `modulo_b.stale_frames` | 20 | ~8 s | **0.67 s** | **240** (~8 s) |
| `modulo_c.stale_ticks` | 30 | ~12 s | **1 s** | **360** (~12 s) |

Sin este reajuste, B se declaraba "obsoleto" a los dos tercios de segundo y C se marcaba "inseguro"
entre una predicción y la siguiente, contaminando la fusión. Los valores nuevos conservan el
comportamiento **en tiempo** que ya estaba validado, no un número bonito.

De paso se detectó **config muerta**: `thresholds.pred_conf`, `thresholds.quality_pose` y
`thresholds.stale_frames` no los lee ningún módulo (solo `thresholds.face_timeout_ticks` se usa, en
el Módulo A). Se dejan como están para no cambiar dos cosas a la vez, pero conviene limpiarlos.

### 3.5 Lo que NO quedó resuelto: la cadencia del Módulo C

**Se debe decir claro: el Módulo C sigue emitiendo poco — del orden de 2 predicciones cada 130 s.**

Se corrigió su regresión de emisión (3.3), pero eso no cambió el ritmo: el cuello de botella no es
el disparo sino **rellenar su ventana de 11 frames**. Se mantiene `detect_every: 8` /
`analyze_every: 3` de S10, cuyo motivo original (bajar el costo de C para que no ahogara la captura)
**ya no aplica**, porque la captura dejó de depender de C.

No se tocó esa cadencia en esta semana por método: ya se hicieron cambios grandes y medidos en el
pipeline, y ajustar además el ritmo de C sin poder validarlo con una persona real delante de la
cámara sería cambiar demasiadas variables a la vez. Queda como el punto 4 de la sección 9, que es
ahora el trabajo más valioso pendiente sobre el motor de visión.

---

## 4. Calidad del LLM de las oraciones

**Restricción:** mejorar sin comprometer la RAM que necesita la predicción emocional.

**Decisión: no se cambia de modelo.** Se mantiene **Qwen2.5-1.5B-Instruct Q4_K_M** (~1 GB). Las
alternativas que hay en `/home/jetson/models` empeoran el cuadro: `DeepSeek-R1-0528-Qwen3-8B` pesa
**5 GB** y dejaría sin memoria al motor de visión, y `DeepSeek-R1-Distill-Qwen-1.5B` es un modelo de
razonamiento (gasta tokens en `<think>` y es más lento para una tarea que no requiere razonar).

Toda la mejora se consiguió **a coste cero de RAM**, en `apps/llm/ia_bridge.py`:

1. **Prompt few-shot con el vocabulario real** de `config/rfid_vocab.json` (8 ejemplos con las
   tarjetas que el niño tiene de verdad: YO, MAMÁ, PAPÁ, QUIERO, AGUA, BAÑO, PARQUE…). Éste era el
   defecto principal: un modelo de 1.5B con solo una instrucción abstracta divaga o inventa.
2. **Reglas explícitas** contra inventar palabras y, sobre todo, contra perder la negación.
3. **Muestreo casi determinista**: `temperature` 0.3 → 0.15, `top_p` 0.85, `repeat_penalty` 1.05,
   `max_tokens` 96 → 48 y `stop` en el salto de línea. Corregir gramática no es una tarea creativa;
   a 0.3 el mismo conjunto de tarjetas daba frases distintas en cada intento.
4. Se corrigió el campo `model`, que decía `deepseek-r1-0528-qwen3-8b` — un modelo que **no** es el
   que `start_bmo.sh` carga. `llama.cpp` lo ignora, pero despistaba al depurar.

**A/B real contra el modelo cargado** (mismo modelo, mismas tarjetas, solo cambia prompt+muestreo):

| Tarjetas | ANTES (S10) | DESPUÉS (S11) |
|---|---|---|
| `YO QUIERO COMIDA` | `QUERO COMER COMIDA` | Quiero comida. |
| `NO QUIERO DORMIR` | `DEJO LA LUZ Y SUELO AQUÍ.` | No quiero dormir. |
| `MAMÁ YO IR COLEGIO` | Mamá y yo vamos al colegio. | Mamá, quiero ir al colegio. |
| `YO NECESITO AYUDA BAÑO` | `Tengo que hacer un bañito.` | Necesito ayuda para ir al baño. |
| `PAPÁ QUIERO MÁS AGUA` | Papá, quiero más agua. | Papá, quiero más agua. |
| `YO ESTOY BIEN` | `ESTOY BIEN.` | Estoy bien. |
| `HERMANO JUGAR PARQUE YO` | Yo juego en el parque con mi hermano. | Jugaré en el parque con mi hermano. |
| `NO NECESITO AYUDA` | `TÚ NO NECESITAS AYUDA.` | No necesito ayuda. |
| `Marina COMER COCINA` | `Marina COME COCINA` | Marina, quiero comer en la cocina. |
| `YO QUIERO IR DORMIR` | Quiero dormir. | Quiero ir a dormir. |

Los dos fallos marcados en negrita conceptual son los graves y ambos desaparecen: **`NO NECESITO
AYUDA` se convertía en "TÚ NO NECESITAS AYUDA"** (cambia de persona: deja de ser el niño quien
habla) y **`NO QUIERO DORMIR` en "DEJO LA LUZ Y SUELO AQUÍ"** (alucinación completa, se pierde el
mensaje). De 10 casos, antes 5 eran claramente defectuosos; después los 10 son correctos, naturales
y conservan la negación.

**Respaldo si el LLM no responde.** Antes, un fallo de `llama-server` devolvía HTTP 500 y **la frase
del niño se perdía**. En un dispositivo de comunicación eso es inaceptable: ahora se devuelve una
frase armada por reglas y marcada como `degraded`. Probado apagando `llama-server`: `YO QUIERO AGUA`
→ `"Yo quiero agua."` en vez de un error. Se añadió también un endpoint `/health`.

---

## 5. Cambios de experiencia de uso

### 5.1 Acceso directo (ya no hace falta la terminal)

- `bmo_unified/moodi.desktop` — entrada de escritorio válida (pasa `desktop-file-validate`), con
  icono propio generado del render de Moodi (`assets/moodi_icon.png`).
- `bmo_unified/lanzar_moodi.sh` — lanzador real. Existe como script aparte porque la especificación
  Desktop Entry **no admite redirecciones ni comillas de shell en `Exec`**. Además deja log en
  `logs/lanzador.log` (al lanzar desde un icono no hay terminal donde ver un fallo temprano) y
  **evita abrir una segunda instancia**, que pelearía por la cámara y por `/dev/ttyUSB0`.
- `bmo_unified/instalar_acceso_directo.sh` — instala el icono en el Escritorio y en el menú de
  aplicaciones. Idempotente, sin `sudo`, todo bajo `$HOME`. Marca el `.desktop` como *de confianza*
  con `gio`, que GNOME exige para no tratarlo como un archivo de texto.

### 5.2 Pantalla de carga

`bmo_unified/ui/splash.py`, **proceso aparte** y no un widget dentro de `bmo_app.py`. La razón es
concreta: el tramo más largo del arranque (los ~12 s que `start_bmo.sh` espera a `llama-server`, más
gunicorn y el arranque del intérprete con PyQt) ocurre **antes de que exista un `QApplication`**. Un
splash interno solo podría aparecer justo cuando ya no hace falta.

- Fondo con degradado de la paleta Moodi + **lluvia de piezas de rompecabezas** cayendo con
  rotación, escala y paralaje (las piezas grandes caen más rápido y más opacas).
  Las 9 piezas completas se localizaron una vez por componentes conexas sobre el canal alfa y sus
  recortes van fijos en el código: un corte en rejilla ciego (primer intento) partía piezas por la
  mitad y las mitades se leían como escombros.
- LOGO centrado con una respiración suave de ±2%.
- Barra de progreso real que **persigue** el objetivo en vez de saltar (los pasos de arranque son
  escalones grandes y espaciados) + mensaje del paso actual, traducido (es/en).
- Comunicación por archivo de texto `/tmp/moodi_boot.status` con formato `PORCENTAJE|CLAVE_I18N`,
  porque lo escriben tanto bash (`start_bmo.sh`) como Python (`bmo_app.py`).
- `bmo_app.py` escribe `READY` **después** de que Qt pintó la ventana (`processEvents()`), para que
  no se vea un parpadeo de escritorio entre que se va el splash y aparece la app.
- Redes de seguridad: `trap` en `start_bmo.sh` si el arranque muere a medias, y temporizador de
  120 s en el propio splash para que nunca se quede pegado en pantalla.

### 5.3 Diagrama de remapeo con el render real + hover

`MoodiDiagram` ahora dibuja `assets/MOODI_VIEW.png` en vez de figuras. Los 10 botones se ubican con
**coordenadas normalizadas medidas** sobre el PNG (detección de color de cada control: cruz del
d-pad, isla de 3 puntos, barra inferior), no estimadas a ojo, así que siguen cuadrando a cualquier
escala. Verificado: los 10 centros caen dentro de la imagen y cada uno resuelve a su propio botón.

- **Hover interactivo**: el botón bajo el puntero se ilumina y aparece una etiqueta flotante con el
  nombre físico y su acción. En la pantalla táctil no hay hover real, así que el toque también lo
  fija, manteniendo el mismo recorrido visual.
- El **hover manda sobre la selección** en la etiqueta: es el afordance de "¿qué hace este botón?" y
  debe seguir al dedo (al revés, seguía nombrando al ya seleccionado mientras se exploraban otros).
- Se rotula **solo** el botón activo. Rotular los 10 a la vez saturaba el dibujo y los textos se
  pisaban entre sí — defecto visible en la versión de S10.
- GPIO13 sigue bloqueado con candado vectorial, ahora dibujado sobre el óvalo central real.

### 5.4 Interruptor de voz en Oraciones

Botón circular de 68 px arriba a la derecha de la pantalla Oraciones. La voz TTS todavía es tosca
(espeak-ng) y debía poder callarse **sin entrar a Configuraciones ni bajar el volumen general de las
animaciones**, por eso `voice_enabled` es una preferencia propia y separada del volumen.

- Persistente en `settings.json` (escritura atómica, como el resto).
- `VoiceEngine` lo consulta **al hablar**, no al encolar: si la voz se apagó mientras una frase
  esperaba en la cola, ya no suena.
- Al apagar se llama `silence_now()`, que corta lo que esté sonando; si no, la frase en curso seguía
  narrándose tras pulsar y el botón se sentía roto.
- **El icono se dibuja con QPainter** (altavoz con ondas / altavoz tachado). La primera versión usó
  los glifos U+1F56A / U+1F568 y salían como **cuadro vacío (tofu)**: se verificó después que
  *ninguna* fuente instalada en esta Jetson los tiene. Es el mismo tropiezo que S10 tuvo con ⚙️ y 🔒,
  y refuerza la regla ya conocida aquí: **en este equipo, iconos dibujados, nunca glifos de fuente.**

### 5.5 Bug pendiente de S10 corregido

`EmoMonitorPanel._label_history` y `_latest_preds` sobrevivían al `stop()` del motor de visión, así
que al reentrar a VIDEO las primeras predicciones nuevas se votaban junto con etiquetas de la sesión
anterior. Ahora `reset_session()` se dispara con las señales `started` y `stopped` del `VisionEngine`.

---

## 6. Audio saturado: sigue pendiente (limitación física)

**Estado: NO resuelto.** Es una limitación de las bocinas del panel ElecLab, no un defecto de
software, y el margen que queda por software ya se agotó en S10:

- El sink de PulseAudio ya está a **65%** (−11 dB de margen) desde S10, aplicado automáticamente en
  `start_bmo.sh`.
- La curva de amplitud del TTS ya está suavizada en `core/voice.py` (mapeo 0–100 → −100…+50, porque
  espeak-ng clipea por su cuenta a amplitudes altas).

**Pendiente explícito de comprobar (pedido del usuario):** si **subir el volumen de la pantalla**
ayuda en algo. La hipótesis a verificar es que el volumen del panel HDMI y el del sink de PulseAudio
se multiplican, y que subir el del panel permitiría **bajar** el del sink (menos saturación digital)
manteniendo el mismo volumen percibido. Esto **requiere el hardware físico y el oído del usuario**:
no se puede validar desde aquí, porque la saturación es audible, no medible en el log.

Si esa vía no alcanza, la salida realista es hardware: un pequeño amplificador o bocinas mejores.

---

## 7. Pruebas realizadas

- **Suite headless S11** (`bmo_unified/tests/test_s11.py`, widgets reales, `QT_QPA_PLATFORM=offscreen`):
  **29/29 OK**. Cubre `voice_enabled` (round-trip, persistencia, no re-emitir, archivo corrupto →
  defaults), `VoiceEngine` respetando el interruptor, el botón de Oraciones y su target ≥64 px, las
  10 posiciones del diagrama sobre el PNG (dentro de la imagen y cada centro resolviendo a su
  botón), el splash (recorte de piezas, lectura de estado, cierre con READY) y el `reset_session()`
  del bug de S10.
- **Corridas reales del motor de visión** (A+B+C, cámara física, 130 s cada una), midiendo fps
  estable, gaps, predicciones por módulo y residuales. Cero procesos residuales y `/dev/video0`
  liberada tras cada corrida (verificado).
- **Experimento aislado de colas** (12 frames de 640x480 a un consumidor lento): la medición que
  identificó la causa raíz.
- **Compatibilidad de los workers con las colas nuevas**: se verificó que el patrón real
  `_reader.poll() + get()` de `mod_{a,b,c}` sigue funcionando con `Queue(maxsize=N)` y que el
  productor nunca se bloquea (60 frames en 2.01 s, `put` más lento 0.6 ms).
- **Campo de visión**: capturas reales de la misma escena en 640x480, 640x360 y 1280x720, más
  verificación de que la pipeline GStreamer del orquestador negocia efectivamente 640x360.
- **`person_ratio` con Detectron2** sobre la misma escena en ambos modos, para calibrar
  `min_person_ratio` con un número medido y no con una estimación.
- **A/B del LLM**: 10 conjuntos de tarjetas contra el modelo real, prompt viejo vs nuevo.
- **Respaldo del bridge**: probado con `llama-server` apagado (devuelve la frase, no un 500) y
  `/health` reportando 503.
- **Capturas offscreen** de todas las pantallas en es/en regeneradas (`capturas_moodi/`), incluida
  una nueva del hover del diagrama.
- **Validación de la entrada de escritorio** con `desktop-file-validate` (pasa limpio).

---

## 8. Mapa de archivos nuevos/modificados

```
bmo_unified/
├── bmo_app.py                     # + progreso de arranque y READY tras pintar la ventana
├── moodi.desktop                  # NUEVO -- entrada de escritorio
├── lanzar_moodi.sh                # NUEVO -- lanzador (log + una sola instancia)
├── instalar_acceso_directo.sh     # NUEVO -- instalador idempotente del icono
├── assets/                        # NUEVO
│   ├── MOODI_VIEW.png             #   render frontal para el remapeo
│   ├── LOGO.png                   #   logo del splash
│   ├── lluvia_rompecabezas.png    #   piezas del splash
│   └── moodi_icon.png             #   icono del acceso directo (generado)
├── tests/                         # NUEVO
│   ├── test_s11.py                #   suite headless de S11 (29 comprobaciones)
│   └── medir_fps_vision.py        #   arnés de medición de fps/gaps del motor
├── config/
│   ├── settings.json              # + voice_enabled
│   ├── strings_es.json            # + 8 claves (voz + arranque) -> 118
│   └── strings_en.json            # + 8 claves, paridad verificada -> 118
├── core/
│   ├── app_settings.py            # + voice_enabled y su señal
│   └── voice.py                   # + respeta el interruptor, + silence_now()
├── ui/
│   ├── splash.py                  # NUEVO -- pantalla de carga
│   ├── settings_panel.py          # MoodiDiagram reescrito sobre el PNG + hover
│   ├── pecs_panel.py              # + interruptor de voz
│   ├── emo_monitor_panel.py       # + reset_session(), escalado contra contentsRect()
│   └── main_window.py             # + cableado de voz y de reset de sesión
└── vision/
    └── engine.py                  # colas acotadas + cancel_join_thread()

integradora/model_ia/sistem_IA/
├── run.py                         # colas acotadas (usa queues_maxsize, antes muerto)
├── core/orchestrator.py           # NUEVO put_drop_if_full(): no bloquear la captura
├── modules/mod_c.py               # emisión por distancia en frames, no por índice exacto
└── config/runtime.yaml            # 640x480 -> 640x360; min_person_ratio 0.15 -> 0.12;
                                   # stale_frames 20 -> 240; stale_ticks 30 -> 360

apps/llm/ia_bridge.py              # prompt few-shot, muestreo determinista, respaldo, /health
start_bmo.sh                       # + lanza el splash y publica el progreso de arranque
capturas_moodi/capturas_ui.py      # + captura de hover; selección por el flujo real
```

Sin cambios en: firmware ESP32, scripts de cámara, `/boot` ni overlays.

> ⚠️ **`start_bmo.sh` NO está versionado.** El `.gitignore` ignora todo el directorio raíz (`/*`) y
> su lista blanca no incluye `start_bmo.sh`, aunque `CLAUDE.md` lo describe como archivo del
> proyecto. Como ahora además lanza la pantalla de carga y publica el progreso de arranque, conviene
> añadir `!/start_bmo.sh` a la lista blanca del `.gitignore` para que sus cambios no se pierdan.
> No se hizo en esta semana por ser una decisión de estructura del repositorio.

---

## 9. Pendiente para S12

1. **Validación física completa en el robot** (nada de esto se ha probado con el hardware en mano):
   - Confirmar que el video del monitor emocional se ve **fluido y con el encuadre ancho**.
   - Confirmar que la detección se siente más precisa con una persona sentada frente a Moodi.
   - Recorrer Oraciones enviando frases y comprobar la calidad real del LLM corregido.
   - Probar el icono del Escritorio y ver la pantalla de carga de principio a fin.
   - Tocar los 10 botones del diagrama nuevo y comprobar que el hover/selección responde bien en
     táctil (las zonas táctiles se ampliaron a 56 px porque los controles del render son pequeños).
   - Encender y apagar la voz desde Oraciones.
2. **Audio saturado** (sección 6): comprobar si subir el volumen de la pantalla permite bajar el del
   sink. Si no, evaluar amplificador/bocinas.
3. **Recalibrar `min_person_ratio` con una persona real** frente a la cámara. El 0.12 sale de un
   factor medido (0.81) sobre una escena de laboratorio, no del caso de uso real.
4. **Subir la cadencia del Módulo C — el pendiente más valioso del motor de visión** (ver 3.5). Hoy
   emite ~2 predicciones cada 130 s. `detect_every: 8` / `analyze_every: 3` se bajaron en S10 para
   que C no ahogara la captura, y **ese motivo ya no existe**. El cuello de botella medido es
   rellenar su ventana de 11 frames, así que hay que atacar `analyze_every` y `detect_every` y
   medir de nuevo, idealmente con una persona sentada frente al robot.
5. **Frames uniformemente espaciados para el Módulo C.** El BiLSTM se entrenó con secuencias de
   frames regulares; con el descarte, la secuencia que ve C ya no está uniformemente espaciada en el
   tiempo. Lo correcto sería que el orquestador le mande a C solo los frames que va a usar (como ya
   hace con A y su `tick`), en vez de mandarle todos y que C filtre. Es un cambio con impacto en la
   precisión de C y merece medirse aparte.
6. **Voz de Moodi**: sigue siendo espeak-ng y suena tosca. Piper TTS como upgrade cuando haya margen
   de RAM (`VoiceEngine.speak()` está diseñado para cambiar de motor sin tocar la UI). El
   interruptor nuevo es un parche de convivencia, no la solución.
7. **Animaciones reactivas al estado emocional** (heredado de S08-S09 y S10): el punto de entrada
   (`MainWindow._on_stress_stats()`) existe, pero **hacen falta clips nuevos** — hoy solo hay 4.
8. **Causa física del evento espurio de `EMO_TOGGLE` (GPIO13)** y confirmar los 10 GPIO con
   `--calibrate` tras el recableado (heredado de S10).
9. **Contención de GPU**: `llama-server` corre con `-ngl 999` (todas las capas en GPU) y compite con
   Detectron2/VGG16 del Módulo C. No se tocó porque el LLM solo calcula unos segundos por frase,
   pero convendría medir si moverlo a CPU mejora la visión sin penalizar demasiado las oraciones.
