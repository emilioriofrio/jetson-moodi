# PROMPT PARA CLAUDE CLI — MOODI: Interfaz, Configuraciones, Botones Remapeables, Voz y Cámara Funcional

## 0. Regla de oro (no negociable, léela primero)

Antes de tocar una sola línea de código, confirma el estado real del sistema en la Jetson (no
asumas nada de sesiones anteriores). Está terminantemente **prohibido**:

- Escribir en `/boot`, `extlinux.conf`, device tree overlays, o cualquier script relacionado con la
  cámara CSI.
- Usar `cv2.imshow()` en cualquier parte de la app PyQt5.
- Abrir `/dev/video0` desde más de un proceso a la vez.
- Elevar privilegios (`sudo`) dentro de la app para acceder a la cámara.

La cámara es exclusivamente USB vía OpenCV/V4L2 sobre `/dev/video0`. Cualquier cambio de
configuración de cámara se hace en `config/camera.json`, nunca hardcodeado ni a nivel de sistema.

Antes de continuar, lee y respeta lo ya definido en el proyecto:
- `CONTEXTO_MOODI_UI_INTERACTIVA_08072026.md` (arquitectura de cinta fantasma, 5 pantallas,
  mapa GPIO definitivo, ciclo de vida de procesos).
- `REQUERIMIENTOS_APP_MOODI.md`
- `config/button_map.json`, `config/greetings.json`, `config/camera.json`, `config/runtime.yaml`
- `core/button_router.py`, `ui/main_window.py`, `ui/ghost_ribbon.py`, `ui/pecs_panel.py`,
  `core/pecs_engine.py`

Si al implementar algo de este documento hay ambigüedad, contextualiza con los archivos del
proyecto primero. Si sigue abierta, elige la opción más segura (que nunca comprometa el arranque
ni la estabilidad de memoria RAM/GPU) y déjala registrada en un `.md` de progreso nuevo
(`PROGRESO_BMO_UI_CONFIG_VOZ.md`) para validación posterior.

**Bloqueante previo:** confirma que la Fase 0 de memoria (`CUDA_VISIBLE_DEVICES='-1'` y
`TF_FORCE_GPU_ALLOW_GROWTH='true'` como primeras líneas ejecutables de `run.py`, antes de
cualquier import) sigue aplicada y verificada (`free -h` estable, sin `ENOMEM`/`nvmap` en logs) antes
de avanzar con el punto 6 (cámara). Si no está confirmada, verifícala primero.

---

## 1. Objetivo de esta fase

Implementar, de manera completa y verificada con pruebas reales en la Jetson (no solo en teoría),
los siguientes siete bloques de trabajo. Cada bloque debe quedar documentado en
`PROGRESO_BMO_UI_CONFIG_VOZ.md` con lo implementado, lo probado y lo pendiente, siguiendo el
formato ya usado en los reportes `PROGRESO_BMO_*` anteriores.

---

## 2. Bloque 1 — Pulido visual y de interacción de la cara principal

- Aumentar el tamaño y la jerarquía visual de cualquier texto que aparezca junto a la carita
  (nombre de animación, leyenda de botones, etiquetas), sin romper la composición ni tapar la
  cara de forma permanente.
- Pulir las transiciones y animaciones existentes (cinta fantasma, cambio de cara, aparición de
  botones fantasma) para que se sientan más suaves y "amigables" — piensa en curvas de
  aceleración/desaceleración (`QEasingCurve`), fundes en vez de cortes duros, y micro-feedback al
  tocar (por ejemplo, un leve "bounce" al seleccionar en la cinta).
- No se debe sacrificar la fluidez del video de fondo (`.mp4` en loop seamless) por estos cambios;
  verificar que sigue sin parpadeos ni ventanas negras tras el pulido.
- Aumentar el tamaño de los targets táctiles si alguno queda por debajo de 64 px.

---

## 3. Bloque 2 — Apartado de Configuraciones en la cinta fantasma

Añadir una sexta entrada a la cinta (`ui/ghost_ribbon.py`): **Configuraciones**, con su propio
ícono dibujado con `QPainter` (consistente con el resto: sin depender de assets externos).

Nueva pantalla `ui/settings_panel.py` que incluya, como mínimo:

### 3.1 Volumen
- Control deslizante (slider) que ajuste el volumen de todo audio reproducido por la app
  (animaciones, futura voz de Moodi). Persistir el valor en `config/settings.json`.

### 3.2 Idioma (español / inglés)
- Selector de idioma con efecto inmediato sobre toda la interfaz: cinta, pantallas (Home, Caras,
  Oraciones, Video, Configuraciones), textos de instrucción, saludos, avisos ("tarjeta no
  reconocida", "procesando…", confirmaciones), y leyenda de botones.
- Implementar como un módulo de internacionalización centralizado, por ejemplo
  `core/i18n.py` + `config/strings_es.json` + `config/strings_en.json`, con una función tipo
  `t("key")` usada en todos los módulos de UI. **No dejar ningún texto hardcodeado fuera del
  sistema de traducción** — recorre `ui/*.py` y `config/greetings.json` para asegurar cobertura
  completa, incluyendo los saludos variables de Oraciones (deben tener su lista equivalente en
  inglés, no una traducción literal palabra por palabra sino natural).
- Traducción al inglés debe ser coherente, natural y apropiada para el contexto (comunicación
  aumentativa con niños), no una traducción mecánica.
- Persistir el idioma elegido en `config/settings.json` y aplicarlo al reiniciar la app.

### 3.3 Reasignación de botones físicos (remapeo interactivo)
- Dentro de Configuraciones, una subsección donde se pueda reasignar el rol lógico de **cada uno
  de los 10 botones físicos** (los roles ya definidos en `config/button_map.json`: `CURSOR_UP`,
  `CURSOR_DOWN`, `CURSOR_LEFT`, `CURSOR_RIGHT`, `ISLA_L`, `ISLA_M`, `ISLA_R`, `PANEL_L`, `PANEL_C`,
  `PANEL_R`) a cualquier acción disponible según el contexto (volver a Home, borrar todo, borrar
  palabra seleccionada, enviar, desplazar cursor, activar/desactivar monitoreo, reproducir
  dinámica, etc.).
- **Restricción heredada que se mantiene fija y no editable:** el botón central largo
  (`PANEL_C` / GPIO13) permanece como `EMO_TOGGLE`. Indícalo visualmente como bloqueado/no
  editable en la UI, con una explicación breve de por qué.
- Vista frontal de Moodi (imagen estática del robot con los 10 botones ubicados en su posición
  real) donde, al seleccionar un botón físico para reasignar, **ese botón se anime** (resaltado,
  pulso o glow) para indicar cuál se está configurando — igual que la reasignación de botones de
  un control de videojuego. Si no existe todavía un asset de imagen de la vista frontal de Moodi,
  búscalo en el proyecto (carpetas de animaciones/imágenes ya existentes) antes de crear uno
  nuevo; si no existe ninguno, genera un diagrama simple con `QPainter` que ubique los 10 puntos
  de botón sobre una silueta básica, dejando claro en el progreso que es un placeholder hasta que
  haya un asset gráfico final.
- Al confirmar una reasignación, escribir el cambio en `config/button_map.json` (la fuente de
  verdad ya usada por `core/button_router.py`) sin necesidad de reflashear el firmware — esto ya
  es soportado por el diseño actual (el firmware manda GPIO crudo, el mapeo a rol vive en Python).
  Verifica que `ButtonRouter` recargue el mapa sin reiniciar la app, o si no lo soporta, implementa
  la recarga en caliente.
- Prevenir/advertir sobre conflictos: dos roles críticos no deberían quedar sin ningún botón
  asignado (por ejemplo, si el usuario quita `PECS_SEND` de todos lados). Mostrar aviso, no
  bloquear silenciosamente.
- Guardar cambios con confirmación explícita (botón "Guardar"), no autoguardado silencioso, para
  evitar reasignaciones accidentales por toques del niño.

### 3.4 Apodo del usuario
- Campo para registrar el apodo/nombre del usuario, persistido en `config/settings.json`.
- Usarlo para personalizar el saludo variable de la pantalla Oraciones (`config/greetings.json`).
  Amplía el sistema de saludos para que algunas plantillas incluyan el nombre de forma natural
  (ej. `"Hola {nombre}, ¿qué deseas contarme hoy?"`, `"¿Qué me quieres decir, {nombre}?"`), tanto
  en español como en inglés, y sigue eligiendo uno al azar cada vez que se abre la pantalla —
  igual que el patrón de saludo variable de una app conocida al abrirse. Si no hay apodo
  registrado, usar las plantillas genéricas sin nombre (no dejar un `{nombre}` vacío o roto en
  pantalla).
- Evalúa e implementa formas adicionales de personalización razonables sin sobrecargar el
  documento de configuración: por ejemplo, variar el saludo según franja horaria (mañana/tarde/
  noche) combinado con el apodo, si el reloj del sistema está disponible y es confiable.

### 3.5 Tamaño de fuente
- Selector con 4 niveles: Pequeño, Normal, Grande, Muy grande — aplicado a los textos relevantes
  mostrados junto a Moodi (nombre de animación, leyenda de botones, saludo e instrucción de
  Oraciones, chips de palabras).
- Implementar como un factor de escala centralizado (por ejemplo, una constante base en un tema/
  estilo QSS o un helper de tamaños de fuente) que todos los widgets de texto consulten, en vez de
  tamaños de fuente hardcodeados dispersos por los archivos de `ui/`. Recorre `ui/*.py` para
  identificar fuentes hardcodeadas y migrarlas a este sistema.
- El tamaño base "Normal" debe quedar más grande que el actual (el usuario indica que hoy se ve
  pequeño), pero cada nivel debe escalar proporcionalmente sin romper el layout (sin overflow,
  sin solapar la cara, sin recortar texto) en la resolución fija 1024x600.
- Persistir en `config/settings.json` y aplicar sin necesidad de reiniciar la app.

---

## 4. Bloque 3 — Cámara y detección de emociones (Módulos A, B, C) completamente funcional

- Punto de partida: revisar el estado real actual de `orchestrator.py` y los módulos de visión
  (Módulo A: MediaPipe detección facial; Módulo B: MediaPipe Holistic + BiLSTM gestual; Módulo C:
  Detectron2 + VGG16 + BiLSTM de flujo óptico) — no asumir que están al mismo nivel de madurez que
  el reporte de la sesión anterior; confirmar con una corrida real en la Jetson.
- Objetivo: video del panel embebido de Video **fluido** (sin freezes, sin caídas de FPS visibles
  al usuario) y predicciones de los tres módulos **consistentes** (sin parpadeo de etiquetas entre
  frames consecutivos por ruido, con algún criterio de suavizado/histéresis temporal si hace
  falta, por ejemplo mayoría móvil sobre una ventana corta de frames).
- Reutilizar y validar de nuevo el ajuste de rendimiento ya documentado (reducción de `detect_every`,
  carga perezosa, `CUDA_VISIBLE_DEVICES='-1'` para visión) — si el Módulo C sigue limitando el
  throughput global, evalúa correrlo a una cadencia menor que A/B (no necesariamente en cada
  frame) en vez de forzar los tres a la misma tasa, dejando claro en el progreso el trade-off de
  precisión vs fluidez elegido.
- Mantener las marcas visuales por módulo (recuadro + etiqueta) en el panel de Video.
- Verificar de nuevo, con hardware real, que activar/desactivar el monitor repetidamente no
  degrada memoria ni dispara `ENOMEM`/`nvmap`, y que el cierre libera la cámara y termina el
  subproceso sin residuales (`psutil`).
- No modificar nada de `/boot` ni scripts CSI para lograr esto — cualquier mejora de fluidez debe
  venir de configuración de captura V4L2 (resolución, formato, FPS negociado), del pipeline de
  inferencia, o del pipeline Qt de renderizado del panel embebido.

---

## 5. Bloque 4 — Voz de Moodi

- Investiga primero qué ya existe en el proyecto: revisa `~/integradora/animaciones` y las seis
  categorías de audio documentadas en el paper (`Reporte_final_unido.pdf`, sección 3.3.2) y
  `config/faces_audio.json` — hay audios pregrabados asociados a caras/frases específicas que ya
  cumplen parte de esta función. No dupliques trabajo si ya existe una voz grabada usable.
- Para contenido dinámico que no puede estar pregrabado (frases formadas en Oraciones vía RFID, y
  la frase corregida que devuelve el LLM), investiga y evalúa opciones de texto-a-voz (TTS)
  viables **on-device** en una Jetson Orin Nano con 8 GB de RAM unificada, ya comprometida por
  `llama-server` (~5 GB): motores ligeros de TTS en español y en inglés (coherente con el idioma
  seleccionado en Configuraciones), con footprint de memoria y latencia compatibles con no romper
  el conflicto de memoria ya resuelto en la Fase 0. Documenta las opciones evaluadas, su costo de
  RAM/CPU aproximado, y la recomendación final con justificación, antes de integrar cualquier
  librería nueva.
- Objetivo funcional mínimo de este bloque:
  1. Moodi puede narrar en voz alta la frase que se va formando o la frase corregida en la
     pantalla Oraciones.
  2. El volumen configurado en Configuraciones (bloque 3.1) aplica también a esta voz.
  3. La reproducción de voz no bloquea la UI ni compite por memoria con el LLM o la visión de
     forma que rompa la estabilidad ya lograda.
- Como exploración adicional (no bloqueante para cerrar este bloque, mencionar en progreso como
  siguiente iteración si no alcanza el tiempo): animaciones reactivas al estado emocional captado
  por el monitor, por ejemplo una expresión de curiosidad con la frase "¿Qué te tiene tan feliz?"
  ante señales de felicidad, o una expresión de preocupación con "Cuéntame, ¿qué te hace sentir
  así?" ante señales de estrés alto — coherente con la idea ya registrada en el progreso previo
  (sección de pendientes de `PROGRESO_BMO_S08_S09.md`).

---

## 6. Persistencia y estructura de configuración

Centralizar todo lo nuevo en un único archivo `config/settings.json` (volumen, idioma, apodo,
tamaño de fuente, y cualquier otra preferencia de esta fase), separado de `config/button_map.json`
(que sigue siendo la fuente de verdad del mapeo de botones). Cargar `settings.json` al inicio de
la app y aplicar sus valores antes de mostrar la primera pantalla. Todo cambio en Configuraciones
debe escribir de vuelta a estos archivos de forma atómica (evitar corrupción si se corta la
energía a mitad de escritura — escribir a un archivo temporal y hacer `rename`).

---

## 7. Pruebas obligatorias antes de dar por cerrado cada bloque

No declares nada como "implementado" sin verificarlo en la Jetson real, siguiendo el mismo
estándar de las sesiones anteriores (pruebas con hardware físico, no solo revisión de código):

- Cambiar idioma en caliente y recorrer las 5+1 pantallas confirmando que no queda ningún texto
  sin traducir.
- Reasignar al menos 3 botones distintos (incluyendo uno de cursor y uno de acción), guardar,
  y confirmar en pantalla táctil que el nuevo mapeo responde sin reiniciar la app.
- Confirmar que el botón central largo sigue fijo a `EMO_TOGGLE` y no aparece como editable.
- Registrar un apodo y confirmar que aparece correctamente en al menos dos saludos distintos de
  Oraciones, en ambos idiomas.
- Recorrer los 4 niveles de tamaño de fuente confirmando que no hay overflow ni solapamiento en
  ninguna pantalla.
- Ejecutar el monitor emocional con los tres módulos activos durante al menos 2 minutos
  verificando fluidez percibida, sin freezes, y `free -h` estable.
- Probar la voz de Moodi narrando al menos una frase de Oraciones en español e inglés, con el
  volumen configurado desde Configuraciones.
- Prueba de estabilidad de memoria de larga duración con el stack completo activo (LLM + monitor
  emocional + voz), activando/desactivando el monitor varias veces, sin degradación.
- Confirmar cero procesos residuales y cámara liberada tras salir de la app (`psutil`).

Al finalizar, actualizar `PROGRESO_BMO_UI_CONFIG_VOZ.md` con la misma estructura que los reportes
anteriores (resumen ejecutivo en tabla, detalle por bloque con rutas de archivo y líneas
modificadas, pruebas realizadas, pendientes).
