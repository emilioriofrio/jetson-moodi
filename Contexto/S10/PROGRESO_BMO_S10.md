# Reporte de Progreso - Proyecto BMO Unified (Moodi)
**Semana:** S10 (13 al 17 de julio, 2026)
**Archivo:** PROGRESO_BMO_S10.md
**Ubicación:** `/home/jetson/Contexto/`
**Detalle técnico completo:** `PROGRESO_BMO_UI_CONFIG_VOZ.md` (documento de fase; este reporte lo resume con referencias a sus secciones)

---

## 1. Contexto y objetivo de la semana

Punto de partida: `Contexto/S10/PROMPT_MOODI_FASE_UI_CONFIG_VOZ.md`, el prompt de fase que recoge
los pendientes dejados por `PROGRESO_BMO_S08_S09.md` (sección 7: voz de Moodi con volumen
configurable, más interactividad) y los convierte en una fase formal de **UI, Configuraciones,
Botones Remapeables, Voz y Cámara**. Objetivo: dotar a Moodi de una pantalla de Configuraciones
persistente (volumen / idioma / apodo / tamaño de texto), remapeo interactivo de los 10 botones
físicos sin reflashear firmware, voz sintetizada on-device que narre palabras y frases en
Oraciones, y un pulido visual general — todo sin tocar boot/overlays/CSI ni el pipeline de
`sistem_IA` (restricción no negociable vigente desde `PROGRESO_BMO_S06.md`).

Como en S08-S09, se respetó la regla de oro: antes de tocar código se verificó el estado real de la
Jetson (Fase 0 de memoria intacta, sistema en reposo, `/dev/video0` libre, `runtime.yaml` partiendo
del ajuste validado 640x480 @30fps MJPG). La semana cerró con una primera prueba física del usuario
en el robot que destapó tres problemas reales (audio del sistema, ícono roto, diagrama pobre) —
corregidos el mismo día en una ronda adicional (sección 4).

---

## 2. Resumen ejecutivo

| # | Cambio | Estado |
|---|---|---|
| 1 | Persistencia central `config/settings.json` (volumen/idioma/apodo/tamaño de fuente) con escritura **atómica** (tmp + `os.replace`) | ✅ Implementado y probado (round-trip, archivo corrupto, valores fuera de rango) |
| 2 | i18n centralizado (`core/i18n.py` + `strings_es.json`/`strings_en.json`, ~90 claves), **cero textos hardcodeados** en la UI, cambio de idioma en caliente | ✅ Implementado; paridad de claves es/en verificada por test |
| 3 | Escala de fuente centralizada (`ui/theme.py`, 4 niveles, "Normal" ya más grande que antes) aplicada en caliente a toda la UI | ✅ Implementado y probado offscreen en los 4 niveles |
| 4 | 6ª pantalla **Configuraciones** en la cinta fantasma (`ui/settings_panel.py`, 5 secciones) | ✅ Implementado |
| 5 | Remapeo interactivo de los 10 botones físicos con diagrama frontal de Moodi, GPIO13 bloqueado con candado, guardado atómico + **recarga en caliente** del `ButtonRouter` | ✅ Implementado y probado end-to-end offscreen |
| 6 | Apodo del usuario + saludos personalizados bilingües con franja horaria (mañana/tarde/noche) | ✅ Implementado y probado (400 sorteos, nunca un `{name}` roto) |
| 7 | Voz de Moodi: TTS on-device (speech-dispatcher/espeak-ng, ya instalado) narrando palabra apilada y frase corregida, con volumen/idioma de Configuraciones | ✅ Implementado; síntesis real verificada en es y en |
| 8 | Volumen de animaciones **sin reintroducir el congelamiento de loop** (volumen 0 = variante `_sin_audio`; nunca `setMuted`/`setVolume(0)` sobre clip con audio) | ✅ Implementado |
| 9 | Pulido visual: easing `OutBack` + micro-bounce en la cinta, targets táctiles ≥64px, leyenda/fuentes más grandes | ✅ Implementado |
| 10 | Anti-parpadeo de etiquetas A/B/C en el panel Video (mayoría móvil sobre 5 predicciones, solo presentación) | ✅ Implementado y probado |
| 11 | Leyenda de botones ahora **dinámica** (se compone del mapa real de `button_map.json`; sigue correcta tras remapear) | ✅ Implementado |
| 12 | Corridas reales del motor de visión A+B+C (3× de 90-150s, cámara física) + **A/B de cadencia del Módulo C**: fps estable 1.41→2.50 (+77%), cadencia nueva conservada en `runtime.yaml` | ✅ Ejecutadas y medidas |
| 13 | Ronda de corrección tras la primera prueba física: audio del sistema (sink/perfil PulseAudio + saturación), ícono de Configuraciones roto, rediseño completo del diagrama de Moodi | ✅ Corregidos el mismo día (17 de julio) |
| 14 | Pruebas físicas completas en pantalla táctil/botones/altavoz (checklist de 9 puntos) | ⏳ Pendientes de validación del usuario — ver sección 7 |

Todo el trabajo de la fase vive en `bmo_unified/`; el único cambio fuera fue la cadencia del
Módulo C en `runtime.yaml` (dos números, con prueba A/B medida). Cero cambios en `/boot`, overlays,
scripts CSI, firmware ESP32 o `start_bmo.sh`.

---

## 3. Cambios principales (resumen por bloque)

El detalle completo, con rutas, líneas y decisiones, está en `PROGRESO_BMO_UI_CONFIG_VOZ.md`
(secciones 3.1-3.9); aquí el resumen semanal:

### 3.1 Configuraciones persistentes (`core/app_settings.py`, nuevo)

`AppSettings(QObject)` carga `config/settings.json` al arranque, sanea valores fuera de rango
(volumen 0-100, idioma es/en, apodo ≤16 letras, escala válida) y emite señales Qt por campo para
aplicar los cambios **en caliente**, sin reiniciar la app. La escritura es atómica
(`atomic_write_json()`: tmp en el mismo directorio + `fsync` + `os.replace`) — un corte de energía a
mitad de guardado nunca deja un JSON truncado; el mismo mecanismo se reutiliza para
`button_map.json`.

### 3.2 Idioma es/en de efecto inmediato (`core/i18n.py`, nuevo)

Todos los textos visibles de `ui/*.py` y `core/*.py` migraron a `t(clave)` con catálogos
`strings_es.json`/`strings_en.json` (~90 claves, paridad verificada por test; barrido con grep
confirmando que no queda ningún literal fuera del sistema). Incluye las etiquetas del motor de
visión (`i18n.label()`: dedos_boca→"Fingers in mouth", etc.) y los mensajes de Telegram. El inglés
es natural para AAC infantil, no traducción literal. Cambiar idioma dispara `retranslate()` en cada
panel; verificado offscreen recorriendo las 6 pantallas en ambos idiomas.

### 3.3 Tamaño de texto (`ui/theme.py`, nuevo)

`fs(base_px)` con 4 niveles (factores 0.85/1.00/1.15/1.30) y la paleta Moodi compartida. Las bases
del nivel "Normal" subieron respecto a la iteración anterior (leyenda 12→15, chips 19→22, saludo
30→34…) y el factor máximo se acotó para que "Muy grande" no desborde el panel de 1024x600. Cambio
en caliente; probado offscreen en los 4 niveles.

### 3.4 Pantalla Configuraciones (`ui/settings_panel.py`, nuevo, ~600 líneas)

6ª entrada en la cinta fantasma (insignia verde con engrane, entre Video y Salir; `MainWindow.VIEWS`
pasa de 5 a 6 pantallas, reafirmando el z-order en `_show_view()` — gotcha conocido del `raise_()`
acumulativo). Cinco secciones: **Volumen** (slider grande + botones −/+), **Idioma** (dos botones,
efecto inmediato), **Apodo** (teclado QWERTY en pantalla con Ñ y ⌫, guardado explícito), **Tamaño de
texto** (cada botón se previsualiza con su tamaño real) y **Botones** (3.5).

### 3.5 Remapeo interactivo de botones

Diagrama frontal de Moodi dibujado con QPainter (cara real con la paleta muestreada de los clips,
d-pad en cruz, grupos titulados, la acción asignada rotulada bajo cada botón — estilo pantalla de
remapeo de un videojuego, ver rediseño en 4.3). Al tocar un botón pulsa con glow naranja mientras se
elige su acción; **GPIO13 (central largo, `EMO_TOGGLE`) está bloqueado con candado** y además
`ButtonRouter` lo reimpone al cargar aunque el JSON se edite a mano (defensa en profundidad). Si
`PECS_SEND` u `HOME` quedan sin botón aparece un aviso rojo no bloqueante. "Guardar" escribe el mapa
(atómico, preservando el comentario) y llama a `ButtonRouter.reload()` — **recarga en caliente, sin
reiniciar la app ni reflashear el firmware**. La leyenda de botones ahora es dinámica: se compone
del mapa real vía `gpio_for_role()`, así que sigue siendo correcta después de remapear.

### 3.6 Apodo y saludos personalizados (`core/greetings.py`, nuevo)

`config/greetings.json` reestructurado a `{es|en} × {genéricos, con apodo, franja horaria}` (mañana
6-12, tarde 12-19, noche 19-6). Con apodo entran al sorteo las plantillas con `{name}`; sin apodo,
solo las genéricas — nunca un `{name}` vacío (verificado con 400 sorteos). El formato antiguo sigue
aceptándose como fallback.

### 3.7 Voz de Moodi (`core/voice.py`, nuevo)

Se evaluaron 4 motores TTS on-device contra el presupuesto de RAM de la Orin Nano (tabla en el doc
de fase): elegido **speech-dispatcher + espeak-ng** (ya instalado, <15 MB, <150 ms, voz robótica
coherente con el personaje; Piper queda documentado como upgrade futuro). `VoiceEngine` es un hilo
daemon con cola: la UI nunca se bloquea, las locuciones salen en orden, y volumen/idioma se leen de
`AppSettings` al hablar. Cableado: `PecsEngine.word_added` → narra cada palabra apilada;
`sentence_sent` → narra la frase corregida (interrumpiendo lo obsoleto). Hallazgo previo: la "voz
pregrabada" existente son las pistas AAC embebidas en los 4 clips de animación (no hay
`faces_audio.json` ni audios sueltos en el disco — decisión documentada, no omisión).

### 3.8 Volumen de animaciones seguro + pulido visual

El volumen configurado se aplica **al cargar el media** (`setVolume(max(1, v))`), nunca 0 y nunca
sobre el pipeline en vivo; volumen 0 se resuelve cargando la variante `_sin_audio` — exactamente el
mecanismo anti-congelamiento validado en S08-S09 (root cause #10 de aquel reporte). Además:
transiciones de la cinta con `OutBack` + micro-bounce al aterrizar (sin `QGraphicsEffect`, la
precaución de la ventana sigue vigente), y todos los targets táctiles subidos a ≥64px (flechas
48→64, botón detener 44→64, teclado y acciones a 64px+).

### 3.9 Cámara y detección: A/B medido de la cadencia del Módulo C

Tres corridas reales standalone (90-150s, cámara física, A+B+C, misma topología de procesos que
`VisionEngine`), midiendo el fps estable **solo desde t=60s** (excluye el warm-up de
TF/Detectron2):

| Corrida | Config Módulo C | fps a UI (estable) | RAM mín. disponible | Residuales |
|---|---|---|---|---|
| 2 (150s) | `detect_every: 5`, `analyze_every: 2` | **1.41** | 1.90 GB | 0 |
| 3 (150s) | `detect_every: 8`, `analyze_every: 3` | **2.50 (+77%)** | 1.93 GB | 0 |

Se conserva la cadencia nueva (solo `runtime.yaml`, cero código). **Trade-off documentado a
propósito:** las predicciones de movimiento repetitivo de C se refrescan ~50% más lento a cambio de
fluidez general; la fusión no se resiente porque `required_modules: 2` permite emitir nivel con A+B
mientras C reporta a su ritmo. El gap único de ~16s presente en ambas configuraciones es el final de
la carga de Detectron2+VGG16 (pausa única de arranque, no un stall recurrente); excluyéndolo, la
corrida 3 fluye a ~2.9 fps — en línea con el benchmark de S08-S09. A esto se suma el
**anti-parpadeo** del panel Video: la etiqueta mostrada por módulo es la mayoría de las últimas 5
predicciones (suavizado solo de presentación; no toca la fusión ni los módulos).

---

## 4. Ronda de corrección tras la primera prueba física (17 de julio)

El usuario probó la app en el robot (dos sesiones reales; botones físicos, Salir-hold y cierre
limpio sin residuales funcionando en ambas) y reportó tres problemas — corregidos el mismo día
(detalle en `PROGRESO_BMO_UI_CONFIG_VOZ.md` sección 9):

### 4.1 Sin audio en toda la Jetson — configuración del sistema, no de la app

El sink por defecto de PulseAudio era la salida analógica (sin nada conectado) y la tarjeta HDMI
estaba en perfil Surround 7.1 contra una pantalla estéreo — combinación que produce silencio.
Corregido a nivel de usuario con `pactl` (perfil `hdmi-stereo` + sink por defecto HDMI; persiste en
`~/.config/pulse`, no toca nada de sistema/boot). Segundo ajuste el mismo día: el sonido salía
**saturado** — el sink estaba al 96% (señal casi a escala completa contra los altavoces pequeños del
panel); bajado a 65% (−11 dB de margen) y suavizada la curva de amplitud del TTS en `core/voice.py`
(espeak-ng clipea por sí solo a amplitudes altas). Nota operativa: si se cambia pantalla/cable HDMI,
PulseAudio puede volver a elegir mal perfil/sink — repetir los comandos documentados.

### 4.2 Ícono de Configuraciones roto en la cinta

`⚙️` no existe como glifo a color en la Noto Color Emoji de esta Jetson y Qt lo montaba mal sobre la
insignia. Reemplazado por un **engrane vectorial dibujado con QPainter** (que además es lo que pedía
la especificación: ícono propio, sin depender de fuentes/assets). Verificado con render offscreen a
PNG.

### 4.3 Rediseño del diagrama de Moodi

La primera versión se veía pobre (y un render a PNG destapó un `NameError` que abortaba el
`paintEvent` a la mitad — el dibujo salía incompleto de verdad). Rediseño completo: cara real de
Moodi con la paleta muestreada de los clips, d-pad en cruz con flechas vectoriales, grupos titulados,
la acción asignada rotulada bajo cada botón, y candado vectorial en el central largo (el emoji 🔒
también renderizaba mal). Verificado con render offscreen en ambos idiomas; suite headless completa
re-ejecutada tras la ronda: **TODO OK**.

> 📸 **Captura recomendada:** la sección "Botones" de Configuraciones con el diagrama rediseñado y
> un botón pulsando en naranja — es el cambio más visible de la semana junto con la pantalla nueva.

---

## 5. Mapa de archivos nuevos/modificados

```
bmo_unified/
├── bmo_app.py                    # + carga de settings/i18n/tema ANTES de la primera pantalla
├── config/
│   ├── settings.json             # NUEVO -- volumen/idioma/apodo/tamaño de fuente
│   ├── strings_es.json           # NUEVO -- ~90 claves i18n
│   ├── strings_en.json           # NUEVO -- paridad verificada con es
│   └── greetings.json            # Reescrito -- es/en × genéricos/apodo/franja horaria
├── core/
│   ├── app_settings.py           # NUEVO -- AppSettings + atomic_write_json
│   ├── i18n.py                   # NUEVO -- t() / label() / set_language()
│   ├── greetings.py              # NUEVO -- pick_greeting(idioma, apodo, hora)
│   ├── voice.py                  # NUEVO -- VoiceEngine (spd-say/espeak-ng, no bloqueante)
│   ├── button_router.py          # + reload() en caliente, blindaje GPIO13, señal map_reloaded
│   └── pecs_engine.py            # + señal word_added; mensajes vía i18n
└── ui/
    ├── theme.py                  # NUEVO -- fs() escala de fuente + paleta compartida
    ├── settings_panel.py         # NUEVO -- pantalla Configuraciones + MoodiDiagram
    ├── main_window.py            # Reescrito -- 6 vistas, leyenda dinámica, voz, hot-reload
    ├── ghost_ribbon.py           # + entrada CONFIG (engrane QPainter), OutBack+bounce, 64px
    ├── pecs_panel.py             # Reescrito -- i18n + fs()
    ├── emo_monitor_panel.py      # Reescrito -- i18n + fs(), mayoría móvil anti-parpadeo
    ├── animation_player.py       # + set_volume() seguro (variante _sin_audio para volumen 0)
    └── calibration_overlay.py    # i18n + fs()

integradora/model_ia/sistem_IA/
└── config/runtime.yaml           # modulo_c.detect_every 5→8, analyze_every 2→3 (A/B medido)
```

Sin cambios en: resto de `sistem_IA/`, firmware ESP32, `start_bmo.sh`, scripts de cámara.

---

## 6. Pruebas realizadas

- **Suite headless en la Jetson real** (`QT_QPA_PLATFORM=offscreen`, venv `pruebas_mod`, widgets
  reales): **38/38 OK**, re-ejecutada completa tras la ronda de correcciones físicas. Cubre: JSONs y
  paridad es/en, round-trip y saneo de `AppSettings` (archivo corrupto → defaults sin crash),
  400 sorteos de saludos sin `{name}` roto, recarga en caliente del `ButtonRouter` (incluido GPIO13
  editado a mano → reimpuesto), flujo end-to-end de la pantalla de remapeo (diagrama → rol →
  Guardar → archivo → router releído), los 4 niveles de fuente en caliente, `MainWindow` completo
  recorriendo las 6 vistas con cambio de idioma/escala en vivo y cierre limpio.
- **Voz:** `spd-say` sintetizó en español e inglés con volumen mapeado (retorno OK); tras el fix de
  PulseAudio se reprodujo una frase TTS y un WAV de prueba por el sink nuevo.
- **Motor de visión real:** 3 corridas standalone de 90-150s con cámara física y A+B+C, midiendo
  fps estable/gaps/RAM/residuales (tabla en 3.9); `/dev/video0` liberado tras cada corrida
  (verificado con `fuser`), cero procesos residuales.
- **Prueba física del usuario (primera ronda):** dos sesiones reales en el robot — botones físicos,
  Salir-hold y cierre limpio confirmados; destapó los 3 problemas de la sección 4.
- **Renders offscreen a PNG** del ícono nuevo y del diagrama rediseñado, en ambos idiomas.

---

## 7. Pendiente (checklist de validación física para el usuario)

Lo implementado ya pasó las pruebas headless; falta el recorrido físico completo en el robot:

1. Cambiar idioma en caliente y recorrer las 6 pantallas confirmando que no queda texto sin traducir.
2. Reasignar ≥3 botones distintos, Guardar, y confirmar que el mapeo nuevo responde sin reiniciar.
3. Confirmar que el central largo aparece con candado y no es editable.
4. Registrar un apodo y verlo en ≥2 saludos distintos de Oraciones, en ambos idiomas.
5. Recorrer los 4 tamaños de fuente confirmando que nada desborda en la pantalla real.
6. Monitor emocional ≥2 min con A+B+C: fluidez percibida y `free -h` estable con la app completa.
7. Voz de Moodi audible por el altavoz en es y en, con el volumen del slider (ya sin saturación).
8. Estabilidad larga con stack completo (LLM + monitor on/off varias veces + voz) sin degradación.
9. Cero residuales y cámara liberada tras salir (el chequeo de cortesía de `bmo_app.py` lo reporta
   en el log al cerrar).

Además: confirmar que el easing `OutBack`/micro-bounce se sienten bien en el panel real (parámetros
de una línea si se quieren suavizar), y que el video de fondo sigue sin parpadeos.

---

## 8. Recomendaciones para la siguiente iteración

1. **Animaciones reactivas al estado emocional** (pendiente heredado de S08-S09, sección 7): el
   punto de entrada natural ya existe (`MainWindow._on_stress_stats()`, donde vive la alerta de
   Telegram); requiere decidir/generar clips nuevos antes de implementarla (hoy solo hay 4).
2. **Piper TTS** como upgrade de calidad de voz cuando haya margen de RAM; `VoiceEngine.speak()`
   está diseñado para cambiar de motor sin tocar la UI.
3. El diagrama de Moodi usa un dibujo QPainter documentado como placeholder de asset: si se produce
   un render/foto frontal del robot, solo hay que reemplazar `MoodiDiagram.paintEvent`.
4. Si en el uso real la cadencia del Módulo C se siente insuficiente, revertir es un cambio de dos
   números en `runtime.yaml` (trade-off documentado en 3.9).
5. Del pendiente de S08-S09 queda vivo: confirmar con `--calibrate` los 10 GPIO tras el recableado,
   e identificar la causa física del evento espurio de `EMO_TOGGLE` (GPIO13).
