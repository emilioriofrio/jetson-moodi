# Reporte de Progreso - Proyecto BMO Unified (Moodi)
**Fase:** UI, Configuraciones, Botones Remapeables, Voz y Cámara (S10)
**Fecha:** 17 de julio, 2026
**Archivo:** PROGRESO_BMO_UI_CONFIG_VOZ.md
**Prompt de fase:** `Contexto/S10/PROMPT_MOODI_FASE_UI_CONFIG_VOZ.md`

---

## 1. Contexto y verificación previa (regla de oro)

Antes de tocar código se confirmó el estado real del sistema en la Jetson:

- **Fase 0 de memoria vigente:** `run.py` tiene `CUDA_VISIBLE_DEVICES='-1'` y
  `TF_FORCE_GPU_ALLOW_GROWTH='true'` como líneas 2-4, antes de cualquier import (verificado por
  lectura directa). `free -h` en reposo: 5.0 GB disponibles, swap 0B usado, sin `ENOMEM`/`nvmap`.
- Sistema en reposo al trabajar: sin `bmo_app`/`llama-server`/`ia_bridge` corriendo, `/dev/video0`
  libre.
- Cero cambios en `/boot`, overlays, scripts CSI o `runtime.yaml` en toda la fase. La app sigue sin
  usar `sudo` y sin `cv2.imshow()`.
- `runtime.yaml` partía del ajuste validado en S08-S09 (640x480 @30fps MJPG, `tick_size: 15`,
  `modulo_c.detect_every: 5`, `analyze_every: 2`); en esta fase **solo** se ajustó la cadencia del
  Módulo C con una prueba A/B medida (ver sección 6) — resolución/fps/tick intactos.

---

## 2. Resumen ejecutivo

| # | Bloque / Cambio | Estado |
|---|---|---|
| 1 | Persistencia central `config/settings.json` (volumen/idioma/apodo/tamaño de fuente) con escritura **atómica** (tmp + `os.replace`) | ✅ Implementado y probado (round-trip, archivo corrupto, valores fuera de rango) |
| 2 | i18n centralizado (`core/i18n.py` + `strings_es.json`/`strings_en.json`), **cero textos hardcodeados** en `ui/*.py` y `core/*.py` | ✅ Implementado; paridad de claves es/en verificada; cambio en caliente probado offscreen |
| 3 | Escala de fuente centralizada (`ui/theme.py`, 4 niveles; "Normal" ya más grande que antes) aplicada a toda la UI, en caliente | ✅ Implementado y probado offscreen en los 4 niveles |
| 4 | 6ª entrada **Configuraciones** en la cinta fantasma + pantalla `ui/settings_panel.py` (5 secciones) | ✅ Implementado |
| 5 | Remapeo interactivo de los 10 botones físicos con diagrama frontal de Moodi animado (pulso naranja), GPIO13 bloqueado, aviso de roles críticos, guardado explícito + **recarga en caliente** de `ButtonRouter` | ✅ Implementado y probado offscreen (guardar → releer → rol nuevo activo) |
| 6 | Apodo del usuario + saludos personalizados bilingües con franja horaria (mañana/tarde/noche) | ✅ Implementado y probado (200 sorteos por caso, nunca un `{name}` roto) |
| 7 | Voz de Moodi (TTS on-device, speech-dispatcher/espeak-ng ya instalado): narra palabra apilada y frase corregida, con volumen/idioma de Configuraciones | ✅ Implementado; síntesis real verificada en es y en |
| 8 | Volumen aplicado al audio de animaciones **sin reintroducir el congelamiento de loop** (nunca `setVolume(0)`/`setMuted` sobre clip con audio; volumen 0 = variante `_sin_audio`) | ✅ Implementado |
| 9 | Pulido visual: easing `OutBack` + micro-bounce en la cinta, targets táctiles ≥64px (flechas 48→64, botón detener 44→64, teclas ≥64), leyenda/fuentes más grandes | ✅ Implementado |
| 10 | Anti-parpadeo de etiquetas A/B/C en el panel Video (mayoría móvil sobre 5 predicciones, solo presentación) | ✅ Implementado y probado |
| 11 | Leyenda de botones ahora **dinámica**: se compone del mapa real de `button_map.json` (sigue correcta tras remapear) | ✅ Implementado |
| 12 | Corridas reales del motor de visión A+B+C (3× de 90-150s, cámara física) midiendo fps/RAM/residuales + **A/B de cadencia del Módulo C** (fps estable 1.41→2.50, cadencia nueva conservada) | ✅ Ejecutadas — ver sección 6 |
| 13 | Pruebas físicas en pantalla táctil/botones/altavoz | ⏳ Pendientes de validación del usuario — ver sección 7 |

Nada de esta fase toca `/boot`, overlays, scripts CSI, ni el pipeline de `sistem_IA` (el único
cambio fuera de `bmo_unified/` es… ninguno: todo vive en `bmo_unified/`).

---

## 3. Detalle por bloque

### 3.1 Bloque 5 primero: persistencia (`config/settings.json`)

**Nuevo** [`core/app_settings.py`](file:///home/jetson/bmo_unified/core/app_settings.py) —
`AppSettings(QObject)`: carga al arranque, saneo defensivo (volumen 0-100, idioma es/en, apodo ≤16,
escala válida), señales Qt por campo (`volume_changed`, `language_changed`, `nickname_changed`,
`font_scale_changed`) para aplicar en caliente. `atomic_write_json()` (tmp en el mismo directorio +
`fsync` + `os.replace`) se reutiliza también para `button_map.json` — un corte de energía a mitad de
escritura nunca deja un JSON truncado.

[`bmo_app.py:82-118`](file:///home/jetson/bmo_unified/bmo_app.py) carga settings + i18n + escala de
fuente **antes de construir la primera pantalla**. Los imports nuevos son diferidos dentro de
`main()` a propósito (mismo gotcha de `spawn`/`__mp_main__` documentado en el propio archivo).

### 3.2 Bloque 2.2: idioma es/en con efecto inmediato

**Nuevos** [`core/i18n.py`](file:///home/jetson/bmo_unified/core/i18n.py) (función `t(clave)` +
`i18n.label(cruda)` para etiquetas del motor de visión, con fallback clave→es→cruda),
[`config/strings_es.json`](file:///home/jetson/bmo_unified/config/strings_es.json) y
[`config/strings_en.json`](file:///home/jetson/bmo_unified/config/strings_en.json) (~90 claves,
paridad verificada por test). Traducción al inglés natural para AAC infantil, no literal.

Migrados a `t()`: `main_window.py` (leyendas), `pecs_panel.py` (instrucción, avisos "tarjeta no
reconocida"/"procesando…"/"enviado"/errores), `ghost_ribbon.py` (nombres de pantalla,
"Manteniendo…"), `emo_monitor_panel.py` (títulos, módulos, etiquetas de estrés y de los
vocabularios B/C: dedos_boca→"Fingers in mouth", etc.), `calibration_overlay.py`,
`settings_panel.py`, `core/pecs_engine.py` (mensajes de error y el mensaje de Telegram) y la alerta
de estrés ALTO de Telegram. Barrido con grep: no queda ningún literal visible fuera del sistema.

El cambio de idioma dispara `MainWindow._retranslate_all()` → `retranslate()` en cada panel; el
saludo de Oraciones se rehace si esa vista está activa. Verificado offscreen recorriendo las 6
pantallas en ambos idiomas.

### 3.3 Bloque 2.5/3.5: tamaño de fuente

**Nuevo** [`ui/theme.py`](file:///home/jetson/bmo_unified/ui/theme.py): `fs(base_px)` con factores
0.85 / 1.00 / 1.15 / 1.30 y la paleta Moodi compartida. Todos los `font-size` de `ui/*.py` migrados
a `fs()`; las bases del nivel "Normal" subieron respecto a la iteración anterior (leyenda 12→15,
instrucción 14→17, chips 19→22, saludo 30→34, tarjetas de módulo 13/15→14/17, título de clip
20→24). El factor máximo 1.30 se eligió acotado para que "Muy grande" no desborde 1024x600 (los
contenedores de leyenda y título escalan su alto con `fs()`). Cambio en caliente vía
`_restyle_all()`; probado offscreen en los 4 niveles sin excepción.

### 3.4 Bloque 2: pantalla Configuraciones

[`ui/ghost_ribbon.py`](file:///home/jetson/bmo_unified/ui/ghost_ribbon.py) — 6ª entrada `CONFIG`
(insignia verde `#4E9B6E` + glifo ⚙️, mismo estilo QPainter/emoji del resto), colocada entre Video
y Salir. `MainWindow.VIEWS` ahora tiene 6 pantallas; `_show_view()` reafirma el z-order de
`_settings_panel` igual que hace con Video (gotcha conocido de `raise_()` acumulativo).

**Nuevo** [`ui/settings_panel.py`](file:///home/jetson/bmo_unified/ui/settings_panel.py) (~600
líneas): pantalla completa con la paleta teal de Moodi, navegación por secciones a la izquierda
(botones de 66px) y contenido a la derecha:

- **Volumen:** slider grande (handle 48px) + botones −/+ de 76px; persiste al soltar; aplica a
  animaciones (ver 3.7) y voz.
- **Idioma:** dos botones grandes Español/English; efecto inmediato en toda la UI.
- **Apodo:** teclado QWERTY en pantalla (teclas 64px, con Ñ y ⌫), máximo 16 letras, guardado
  explícito, capitalización amable ("PEPE"→"Pepe").
- **Tamaño de texto:** 4 niveles, cada botón se previsualiza con su propio tamaño real.
- **Botones:** ver 3.5.

### 3.5 Bloque 3.3: remapeo interactivo de botones

- **`MoodiDiagram`** (dentro de `settings_panel.py`): vista frontal de Moodi dibujada con QPainter —
  cuerpo + pantalla con carita (ojos/sonrisa) + los 10 botones en su posición aproximada (d-pad de
  cursores, 3 de isla, panel L/C/R con el central largo). **Es un placeholder documentado**: se
  buscó un asset de imagen frontal en el proyecto (`integradora/animaciones`, config, docs) y no
  existe ninguno; cuando haya un asset final solo hay que reemplazar el `paintEvent`.
- Al tocar un botón del dibujo, **pulsa con glow naranja animado** (QTimer + QPainter, 2 Hz — sin
  `QGraphicsEffect`, precaución vigente de esta ventana) mientras se elige su acción, como en la
  reasignación de controles de un videojuego.
- Acciones disponibles: 4 cursores, Enviar, Borrar palabra, Borrar todo, Home, Dinámica, Monitoreo
  on/off y "Sin función". Lista desplazable táctil (`QScroller`, botones de 64px).
- **GPIO13 (`PANEL_C`, central largo) bloqueado:** se dibuja gris con candado 🔒; al tocarlo se
  muestra la explicación y las acciones se deshabilitan. Además
  [`core/button_router.py`](file:///home/jetson/bmo_unified/core/button_router.py) **reimpone**
  `13: EMO_TOGGLE` al cargar/recargar aunque el JSON se edite a mano (defensa en profundidad).
- **Aviso de conflictos, sin bloquear:** si `PECS_SEND` u `HOME` quedan sin ningún botón, aparece
  el aviso en rojo (se puede guardar igual, decisión informada del adulto).
- **Guardar explícito:** el botón "Guardar" escribe `button_map.json` (atómico, preservando el
  `_comentario` original) y llama a `ButtonRouter.reload()` — **recarga en caliente**, sin
  reiniciar app ni reflashear firmware. Al guardar, la leyenda de botones se refresca sola
  (señal nueva `map_reloaded`).
- Entrar/salir de la sección descarta ediciones a medias (estado siempre coherente con el archivo).

**Leyenda dinámica** ([`main_window.py`](file:///home/jetson/bmo_unified/ui/main_window.py)
`_legend_text()`): la barra "qué hace cada botón" ya no es texto fijo — se compone consultando
`gpio_for_role()` sobre el mapa real (p. ej. si Enviar se mueve a Isla derecha, la leyenda de
Oraciones lo dice). Si un rol queda sin botón, su segmento desaparece de la leyenda.

### 3.6 Bloque 3.4: apodo y saludos personalizados

**Nuevo** [`core/greetings.py`](file:///home/jetson/bmo_unified/core/greetings.py) +
[`config/greetings.json`](file:///home/jetson/bmo_unified/config/greetings.json) reestructurado:
`{es|en} × {generic, named, morning[_named], afternoon[_named], evening[_named]}` (mañana 6-12,
tarde 12-19, noche 19-6, reloj local). Con apodo entran al sorteo las plantillas con `{name}` y las
de franja; sin apodo, solo las genéricas — nunca un `{name}` vacío (verificado con 400 sorteos).
Formato antiguo (`{"greetings": [...]}`) sigue aceptándose como fallback. El inglés es natural
("Good morning, {name}! How did you sleep?"), no traducción literal.

### 3.7 Bloque 4: voz de Moodi

**Investigación previa (lo que ya existía):** no hay `config/faces_audio.json` ni archivos de audio
sueltos ni `Reporte_final_unido.pdf` en el disco de la Jetson (buscado). La "voz pregrabada"
existente son las **pistas AAC embebidas** en los 4 clips de `~/integradora/animaciones` (Sonreir,
Perro, Gato, Color Manzana — verificado con ffprobe), que ya suenan en Caras. Eso cubre el audio por
cara; lo que no puede estar pregrabado (frases RFID y corrección del LLM) es lo que resuelve el TTS.

**Evaluación de TTS on-device (Orin Nano 8 GB, ~5 GB ya comprometidos por llama-server):**

| Opción | Estado en la Jetson | RAM aprox. | Latencia | Calidad | Veredicto |
|---|---|---|---|---|---|
| speech-dispatcher + espeak-ng | **Ya instalado** (`spd-say`, voces es/en) | ~10-15 MB daemon, síntesis en proceso efímero | <150 ms | Robótica (coherente con el personaje robot) | **Elegido** |
| pico2wave (libttspico) | No instalado (requiere apt + internet) | ~30 MB | baja | Mejor prosodia | Alternativa si se quiere mejor voz con costo bajo |
| Piper TTS (neural) | No instalado (pip + descarga de modelos) | ~60-100 MB por voz residente | ~0.5-1.5 s CPU | La mejor | Upgrade futuro; riesgo innecesario hoy contra la estabilidad de Fase 0 |
| flite / festival | Parcial | bajo | baja | Español pobre | Descartado |

**Nuevo** [`core/voice.py`](file:///home/jetson/bmo_unified/core/voice.py) — `VoiceEngine`: un hilo
trabajador daemon consume una cola de frases y ejecuta `spd-say -l <idioma> -i <volumen> -r -15 -w`;
la UI nunca se bloquea y las locuciones salen en orden. Volumen e idioma se leen de `AppSettings`
**al hablar** (el slider aplica también a la voz; volumen 0 = voz muda). `interrupt=True` cancela
(`spd-say -C`) lo pendiente. `shutdown()` en el cierre limpio de MainWindow (sin residuales).

**Cableado** ([`main_window.py`](file:///home/jetson/bmo_unified/ui/main_window.py)
`_wire_signals()`): señal nueva `PecsEngine.word_added` → narra cada palabra apilada;
`sentence_sent` → narra la frase corregida (interrumpiendo palabras sueltas obsoletas). Síntesis
real verificada en la Jetson en es y en (`spd-say` retornó OK; la salida audible por el altavoz
queda en la lista física, sección 7).

**Exploración adicional (animaciones reactivas al estado emocional)** — no implementada en esta
fase (no bloqueante según el prompt): queda para la siguiente iteración; la señal necesaria ya
existe (`VisionEngine.stats_ready` → `_on_stress_stats` en MainWindow, donde hoy vive la alerta de
Telegram — es el punto natural para disparar una cara/frase reactiva).

### 3.8 Bloque 1: pulido visual

- **Textos junto a la carita más grandes y con jerarquía** — vía las bases nuevas de `fs()` (3.3).
- **Transiciones:** el reel de la cinta pasa de `OutCubic` 260ms a **`OutBack` 320ms** (llega, se
  pasa ~10% y asienta — más "vivo"); al aterrizar en una pantalla la insignia central hace un
  **micro-bounce** (crece 12% y vuelve, 240ms, `QVariantAnimation` + repintado propio). El pulso
  del diagrama de botones (3.5) usa el mismo lenguaje. Todo sin `QGraphicsEffect` (la precaución de
  la ventana sigue vigente) y sin tocar `AnimationPlayer`/loop seamless — el fondo de video no se
  altera.
- **Targets táctiles ≥64px:** flechas de la cinta 48→64px, botón detener del monitor 44→64px,
  teclado y acciones de Configuraciones a 64px+, botones táctiles del diagrama con área inflada a
  ≥64px.
- **Volumen de animaciones seguro**
  ([`ui/animation_player.py`](file:///home/jetson/bmo_unified/ui/animation_player.py)
  `set_volume()`): el volumen configurado se aplica **al cargar el media** (`setVolume(max(1, v))`),
  nunca 0 y nunca sobre el pipeline en vivo; volumen 0 se resuelve cargando la variante
  `_sin_audio`, exactamente el mismo mecanismo anti-congelamiento ya validado de `set_muted()`.

### 3.9 Bloque 3: cámara y detección (estado real verificado)

- Tres corridas reales standalone (90-150s) con la cámara física y los 3 módulos (sección 6):
  pipeline funcional de punta a punta, misma topología de procesos que usa `VisionEngine`.
- **Anti-parpadeo implementado** en
  [`ui/emo_monitor_panel.py`](file:///home/jetson/bmo_unified/ui/emo_monitor_panel.py): la etiqueta
  mostrada por módulo (tarjeta lateral y overlay sobre el video) es la **mayoría de las últimas 5
  predicciones** (empate → la más reciente). Es suavizado solo de presentación: no toca la fusión
  (que ya tiene histéresis/cooldown) ni los módulos (EMA/SMOOTH_WINDOW propios aguas arriba).
- **Cadencia del Módulo C reducida con prueba A/B medida** (sección 6): la primera corrida de
  estado estable mostró que C activo seguía limitando el throughput global (1.41 fps entregados a
  la UI). Se probó `modulo_c.detect_every: 5→8` y `analyze_every: 2→3` (solo `runtime.yaml`, cero
  código) y el fps estable subió a **2.50 (+77%)** con la misma memoria mínima. Se conserva el
  valor nuevo. **Trade-off elegido (documentado a propósito):** el Módulo C detecta persona cada 8
  frames que le llegan (antes 5) y corre su análisis de secuencia cada 3 ticks (antes 2) — sus
  predicciones de movimiento repetitivo se refrescan más lento (~50% menos frecuentes), a cambio
  de fluidez general del video; la fusión no se resiente porque `required_modules: 2` ya permite
  emitir nivel con A+B mientras C reporta a su ritmo, y `stale_ticks: 30` sigue cubriendo la
  caducidad de su última lectura.
- Sin cambios en `orchestrator.py` ni en los módulos: fluidez = configuración medida + suavizado
  de presentación.

---

## 4. Mapa de archivos nuevos/modificados

```
bmo_unified/
├── bmo_app.py                    # + carga de settings/i18n/tema ANTES de la primera pantalla
├── config/
│   ├── settings.json             # NUEVO -- volumen/idioma/apodo/tamaño de fuente
│   ├── strings_es.json           # NUEVO -- ~90 claves i18n
│   ├── strings_en.json           # NUEVO -- paridad verificada con es
│   ├── greetings.json            # Reescrito -- es/en × genéricos/apodo/franja horaria
│   └── button_map.json           # (sin cambios de contenido; ahora también lo escribe la UI)
├── core/
│   ├── app_settings.py           # NUEVO -- AppSettings + atomic_write_json
│   ├── i18n.py                   # NUEVO -- t() / label() / set_language()
│   ├── greetings.py              # NUEVO -- pick_greeting(idioma, apodo, hora)
│   ├── voice.py                  # NUEVO -- VoiceEngine (spd-say/espeak-ng, no bloqueante)
│   ├── button_router.py          # + reload() en caliente, GPIO_PHYS, blindaje GPIO13, map_reloaded
│   └── pecs_engine.py            # + señal word_added; mensajes vía i18n
└── ui/
    ├── theme.py                  # NUEVO -- fs() escala de fuente + paleta compartida
    ├── settings_panel.py         # NUEVO -- pantalla Configuraciones completa + MoodiDiagram
    ├── main_window.py            # Reescrito -- 6 vistas, leyenda dinámica, propagación en caliente, voz
    ├── ghost_ribbon.py           # + entrada CONFIG, i18n, OutBack+bounce, flechas 64px, retranslate/restyle
    ├── pecs_panel.py             # Reescrito -- i18n + fs(); new_greeting(str) recibe el saludo ya elegido
    ├── emo_monitor_panel.py      # Reescrito -- i18n + fs(), mayoría móvil anti-parpadeo, botón 64px
    ├── animation_player.py       # + set_volume() seguro (variante _sin_audio para volumen 0)
    └── calibration_overlay.py    # i18n + fs()
```

Sin cambios en: `sistem_IA/` completo, firmware ESP32, `start_bmo.sh`, scripts de cámara.

---

## 5. Pruebas realizadas (esta sesión, en la Jetson real)

Suite headless (`QT_QPA_PLATFORM=offscreen`, venv `pruebas_mod`, widgets reales) — **38/38 OK**:

- JSONs nuevos válidos; **paridad de claves es/en** exacta.
- i18n: `t()` en ambos idiomas, `label()` con fallback a etiqueta cruda, formato con placeholders.
- `AppSettings`: round-trip completo, archivo corrupto → defaults sin crash, saneo de valores fuera
  de rango (volumen 400→100, idioma "fr"→es, apodo 99→16 chars).
- Saludos: 400 sorteos (es+apodo mañana / en sin apodo noche) sin un solo `{name}` roto; plantillas
  con nombre aparecen ~55% cuando hay apodo; formato antiguo aceptado.
- `ButtonRouter`: recarga en caliente aplica el mapa nuevo; GPIO13 editado a mano → reimpuesto
  `EMO_TOGGLE`.
- `SettingsPanel` end-to-end offscreen: seleccionar botón en el diagrama → asignar rol → Guardar →
  archivo escrito (comentario preservado, GPIO13 intacto) → router releído con el rol nuevo activo;
  aviso al dejar `PECS_SEND` sin botón; GPIO13 no editable; teclado en pantalla + guardar apodo.
- Los 4 niveles de escala de fuente aplicados en caliente sobre todos los paneles, sin excepciones.
- `MainWindow` completo instanciado offscreen: recorrido de las 6 vistas, leyenda dinámica
  compuesta del mapa real, cambio de idioma y de escala en caliente vía las señales reales de
  `AppSettings`, cierre limpio (`shutdown()`).
- Voz: `spd-say` sintetizó en español e inglés con volumen mapeado (retorno OK en ambos).
- Motor de visión real (90s, cámara física, A+B+C): ver sección 6.

---

## 6. Corrida real del motor de visión (Bloque 3)

Harness standalone (misma topología de procesos que `vision/engine.py`, sin Qt), cámara física,
los 3 módulos habilitados, venv `pruebas_mod`. El "fps estable" se mide **solo desde t=60s**
(excluye el warm-up de TF/Detectron2); tres corridas:

| Corrida | Config Módulo C | fps a UI (estable) | Gap máx. | RAM mín. disponible | Residuales |
|---|---|---|---|---|---|
| 1 (90s, promedio bruto con warm-up) | `detect_every: 5`, `analyze_every: 2` | 0.86 (bruto) | 19.2s (warm-up) | 1.72 GB | 0 reales* |
| 2 (150s, ventana 60-150s) | `detect_every: 5`, `analyze_every: 2` | **1.41** | 16.8s | 1.90 GB | 0 |
| 3 (150s, ventana 60-150s) | `detect_every: 8`, `analyze_every: 3` | **2.50 (+77%)** | 16.5s | 1.93 GB | 0 |

Lecturas:

- **Pipeline funcional de punta a punta:** las tres corridas recibieron predicciones de A, B y C y
  ~30 `stats` del Reporter (fusión emitiendo nivel global).
- **Memoria estable:** piso de ~1.7-1.9 GB *disponibles* durante la corrida (sin `ENOMEM`/`nvmap`
  en la salida), recuperación total (~4.9 GB) al detener. Nota: sin `llama-server` corriendo (~5 GB
  cuando está activo el stack completo — el margen con LLM activo lo cubre la prueba física 8 de la
  sección 7).
- **El gap de ~16s aparece una sola vez y con igual magnitud en ambas configuraciones** → es el
  final de la inicialización de Detectron2+VGG16 cayendo dentro de la ventana medida (una pausa
  única por arranque del monitor), no un stall recurrente. Excluyéndolo, la corrida 3 fluye a
  ~2.9 fps — en línea con el benchmark de S08-S09 (~3.0-3.46), que probablemente se midió antes de
  que C terminara de cargar (corridas de 35-40s).
- **Decisión tomada:** conservar `detect_every: 8` / `analyze_every: 3` (ver trade-off en 3.9). Si
  en la prueba física la cadencia de C se siente insuficiente, revertir es un cambio de dos números
  en `runtime.yaml`.
- \*El "residual" único de la corrida 1 era el `resource_tracker` de multiprocessing (benigno,
  muere con el padre); las corridas 2-3 filtran por nombre igual que el chequeo de cortesía de
  `bmo_app.py` y confirman **cero procesos del motor** sobreviviendo, con `/dev/video0` liberado
  (verificado con `fuser` tras cada corrida).

---

## 7. Pendiente de validación física (checklist para el usuario)

El estándar de cierre de esta fase exige pruebas con hardware físico (pantalla táctil, botones
ESP32, altavoz). Lo implementado ya pasó las pruebas headless de la sección 5; falta el recorrido
físico (yo no relanzo la app en el robot — lo pruebas tú, como acordamos):

1. Cambiar idioma en caliente y recorrer las 6 pantallas confirmando que no queda texto sin
   traducir (la cobertura por código ya está verificada; falta el ojo humano sobre el panel).
2. Reasignar ≥3 botones distintos (uno de cursor y uno de acción incluidos), Guardar, y confirmar
   en el robot que el mapeo nuevo responde sin reiniciar.
3. Confirmar que el central largo aparece con candado y no es editable.
4. Registrar un apodo y verlo en ≥2 saludos distintos de Oraciones, en ambos idiomas.
5. Recorrer los 4 tamaños de fuente confirmando que nada desborda en la pantalla real.
6. Monitor emocional ≥2 min con A+B+C: fluidez percibida y `free -h` estable (la corrida de 90s de
   la sección 6 ya lo midió sin UI; falta con la app completa).
7. Voz de Moodi audible por el altavoz narrando una frase en es y en en Oraciones, con el volumen
   del slider (la síntesis ya retorna OK; falta confirmar la salida física de audio — ojo: la
   Jetson lista sinks HDMI/APE; si el altavoz está en HDMI, verificar el sink por defecto de
   PulseAudio).
8. Estabilidad larga con stack completo (LLM + monitor on/off varias veces + voz) sin degradación.
9. Cero residuales y cámara liberada tras salir (`psutil` — el chequeo de cortesía de `bmo_app.py`
   ya lo reporta en el log al cerrar).

Además del checklist del prompt:
- El easing `OutBack` de la cinta y el micro-bounce: confirmar que se sienten bien en el panel real
  (son parámetros de una línea si se quieren suavizar).
- El video de fondo debe seguir sin parpadeos tras el pulido (no se tocó `AnimationPlayer` más allá
  de `set_volume()`, que reutiliza el mecanismo ya validado).

## 8. Decisiones y notas para la siguiente iteración

- **`faces_audio.json` no existe y no se creó:** el audio por cara ya vive embebido en los clips y
  la pantalla Caras lo reproduce; crear el mapa explícito solo tiene sentido si se quieren audios
  distintos al del propio clip. Documentado como decisión, no como omisión.
- **Piper TTS** queda como upgrade de calidad de voz cuando haya margen de RAM (p. ej. si el LLM
  baja a 1.5B); la interfaz `VoiceEngine.speak()` está pensada para cambiar de motor sin tocar la UI.
- **Animaciones reactivas al estado emocional** (curiosidad/preocupación con frase hablada): punto
  de entrada natural en `MainWindow._on_stress_stats()`; requiere decidir qué clips usar (hoy solo
  hay 4) — sugerido generar/grabar clips nuevos antes de implementarla.
- La sección "Botones" muestra el diagrama placeholder de QPainter; si se produce un render/foto
  frontal del robot, reemplazar solo `MoodiDiagram.paintEvent`.
- Los cambios de esta fase están en el working tree, **sin commitear** (no se pidió); `git add`
  sugerido: `bmo_unified/` completo (los JSON nuevos de config no contienen secretos;
  `telegram.json` sigue fuera del repo).

---

## 9. Ronda de correcciones tras la primera prueba física (17 de julio)

El usuario probó la app en el robot (dos sesiones reales, botones físicos y Salir-hold funcionando,
cierre limpio sin residuales en ambas) y reportó tres problemas:

### 9.1 Sin audio en toda la Jetson (ni YouTube) — configuración del sistema, no de la app

Diagnóstico con `pactl`: el sink por defecto de PulseAudio era la **salida analógica**
(`alsa_output.platform-sound.analog-stereo`, sin nada conectado) y la tarjeta HDMI estaba en perfil
**Surround 7.1** (la pantalla es estéreo — combinación que produce silencio). Corrección a nivel de
usuario (persiste en `~/.config/pulse`, no toca nada del sistema/boot):

```
pactl set-card-profile alsa_card.platform-3510000.hda output:hdmi-stereo
pactl set-default-sink alsa_output.platform-3510000.hda.hdmi-stereo   # + unmute, volumen 90%
```

Se reprodujo una frase TTS y un WAV de prueba por el sink nuevo. **Segundo ajuste (mismo día):** el
usuario reportó sonido saturado — el sink HDMI estaba al 96% (−1.19 dB), señal casi a escala
completa contra los altavoces pequeños del panel (en HDMI no hay ganancia analógica; la saturación
era física). Bajado a **65% (−11 dB de margen)** con `pactl set-sink-volume ... 65%`; el volumen
fino lo pone cada fuente (slider de Configuraciones para Moodi). Además se suavizó la curva de
amplitud del TTS en `core/voice.py` (0..100 → −100..+50 de `spd-say -i`): espeak-ng clipea por sí
solo a amplitudes altas. Nota: si se cambia la pantalla/cable HDMI, PulseAudio puede volver a
elegir mal perfil/sink — repetir los comandos de arriba.

### 9.2 Ícono de Configuraciones roto en la cinta

`⚙️` (U+2699 + selector de variante emoji) no existe como glifo a color en la Noto Color Emoji de
esta Jetson y Qt lo montaba mal sobre la insignia. Reemplazado por un **engrane vectorial dibujado
con QPainter** (`_RibbonIcon._draw_gear()` en `ui/ghost_ribbon.py`: anillo + 8 dientes + eje), que
además es lo que pedía la especificación de la fase (ícono propio, sin depender de assets/fuentes).
Verificado con render offscreen a PNG: nítido y centrado. El aparente "doble texto" en el primer
render era un artefacto del harness offscreen (deleteLater sin event loop), no ocurre en la app viva.

### 9.3 Rediseño del diagrama de Moodi (remapeo de botones)

La primera versión se veía pobre y poco intuitiva (y un render a PNG destapó además un `NameError`
de import — `CORAL` — que abortaba el `paintEvent` a la mitad: el dibujo salía incompleto de
verdad, no solo "feo"). Rediseño completo de `MoodiDiagram`:

- **Cara real de Moodi** con la paleta muestreada de los clips: ojos navy con brillo, mejillas
  coral, sonrisa rosa.
- **D-pad en cruz de verdad** (base navy + flechas vectoriales); si un brazo se remapea a algo que
  no es su cursor, el brazo muestra el nombre corto de la acción en vez de la flecha.
- **Grupos titulados** (Cursores / Isla) y **la acción asignada rotulada bajo cada botón**
  (claves i18n nuevas `actionshort.*`) — estilo pantalla de remapeo de un control de videojuego,
  se ve el mapeo completo de un vistazo.
- **Candado vectorial** en el central largo (el emoji 🔒 también renderizaba mal sobre QPainter).
- Ritmo vertical recalculado para que títulos y rótulos no colisionen; verificado con render
  offscreen a PNG en ambos idiomas de nuevo tras el ajuste.

Suite headless completa re-ejecutada tras esta ronda: **TODO OK**.
