# Contexto de trabajo — Moodi (BMO OS): Interactividad completa y corrección de cámara

**Destinatario:** agente de terminal (Claude CLI / Antigravity CLI) trabajando directamente sobre la Jetson Orin Nano.
**Objetivo:** reconstruir/extender `bmo_app.py` para lograr una interfaz interactiva completa (cinta de navegación fantasma + pantallas Home/Caras/Oraciones/Video/Salir) y aplicar la corrección de rotación de la cámara USB, respetando de forma estricta las restricciones de seguridad ya establecidas.

Este documento consolida: el `REQUERIMIENTOS_APP_MOODI.md` previo, el nuevo `Requerimientos.md`, `conexionesfisicas.md`, `MEMORIA_CONFLICTO_DIAGNOSTICO.md` (diagnóstico del conflicto crítico de memoria RAM/GPU, junio 2026), y el diseño validado en el paper IEEE Access y el reporte final. Está escrito para ejecutarse sin ambigüedad.

---

## 0. RESTRICCIONES CRÍTICAS DE SEGURIDAD (LEER PRIMERO — NO NEGOCIABLE)

Un manejo previo de la cámara corrompió el cargador de arranque de la Jetson y obligó a reflashear, perdiendo el proyecto. El cumplimiento de esta sección tiene prioridad sobre cualquier funcionalidad.

1. La app **NUNCA** escribe, modifica, mueve ni borra:
   - `/boot/` en cualquier ruta, `/boot/extlinux/extlinux.conf`.
   - Overlays de Device Tree (`.dtbo`), configuración de kernel/Tegra.
   - Los scripts `enable_csi_camera.sh` / `enable_usb_camera.sh` **no se invocan bajo ninguna circunstancia.**
2. La cámara se accede **exclusivamente** como USB vía OpenCV/V4L2 sobre `/dev/video0`. Nada de CSI, probes I2C ni drivers de sensor.
3. La app **no** se ejecuta con `sudo` ni eleva privilegios. El permiso serie se resuelve fuera de la app (en el lanzador).
4. Prohibido cualquier comando destructivo del SO (formateo, escritura en `/etc`, `/boot`, particiones) dentro de la app o sus scripts.
5. La corrección de rotación de este documento es **software puro sobre el frame ya capturado**. No toca boot, overlays, `runtime.yaml` ni scripts de cámara.
6. Antes de tocar cualquier ruta del sistema, preguntarse: *"¿esto puede afectar el arranque?"* Si la respuesta no es un "no" rotundo, **no se hace.**

---

## 1. Estado actual vs. objetivo

### Ya existe y funciona
- App `bmo_app.py` (PyQt5) a pantalla completa 1024x600, cara animada (`.mp4`) de fondo.
- Lectura RFID vía ESP32 → traducción con `rfid_vocab.json` → apilado → LLM (`ia_bridge.py :5000` → `llama-server :1234`) → Telegram.
- Autodetección/reconexión serie (`/dev/ttyUSB*`, `/dev/ttyACM*`).
- Gestión de procesos (SIGTERM→SIGKILL por grupos), lanzador `start_bmo.sh`.
- Motor de visión `run.py` (Módulos A facial, B gestual, C movimiento + fusión). Módulo A ya migrado MTCNN→MediaPipe (TF no necesita GPU).

### Problema conocido a resolver primero
- El sistema ha mostrado **falta de fluidez y congelamientos de RAM**, con causa raíz diagnosticada: conflicto de memoria RAM/GPU entre `llama-server` y TensorFlow (ver sección 4). Esto se resuelve antes de evaluar cualquier requisito de interfaz.

### Qué cambia con este documento
- **Nueva navegación:** una **cinta fantasma "infinita"** en la parte inferior reemplaza el set ad-hoc de botones fantasma. Contiene: **Home, Caras, Oraciones, Video, Salir.**
- **Pantallas dedicadas** por opción (máquina de estados clara, sección 6).
- **Corrección de rotación** de la cámara USB (montada girada 90°), software puro (sección 5).
- **Botones cursor con comportamiento por pantalla** (cambiar cara en Caras; seleccionar palabra en Oraciones).
- **Audio por cara** en la pantalla Caras.

---

## 2. Contraste con el paper IEEE Access (coherencia de diseño)

La propuesta es consistente con el diseño validado. Mapeo de las funciones del paper a la nueva UI:

| Función del paper (IEEE / reporte) | Pantalla nueva |
|---|---|
| Interfaz visual con expresiones faciales dinámicas + audio pregrabado (interacción "entrada–modelado–respuesta") | **Home** (reposo) + **Caras** (cara + audio por cara) |
| Comunicación aumentativa PECS-RFID → mensaje estructurado → Telegram | **Oraciones** |
| Monitoreo emocional multimodal (facial + gestual + movimiento), fusión + histéresis, nivel de estrés bajo/medio/alto | **Video** (panel embebido del monitor) |
| Interfaz multimodal pantalla + audio | Transversal |

**Divergencia a tener presente (no es conflicto):** el paper documenta la cámara CSI OV5647; la implementación real migró a cámara USB por incompatibilidad del OV5647 con los drivers de Jetpack 6.x (solo IMX219/IMX477 nativos). El requerimiento de rotación nace del montaje físico de esa cámara USB. No se revierte la migración ni se toca CSI.

**Nota de escala emocional:** el paper usa 3 niveles (bajo/medio/alto). El motor `run.py` maneja además un estado "inseguro/gris" (calidad de la predicción). Se conserva el gris como estado de incertidumbre; no contradice al paper.

---

## 3. Mapa físico unificado de botones (GPIO ya conocido)

Fuentes: `conexionesfisicas.md` + `Requerimientos.md`. El mapeo GPIO ↔ etiqueta física **ya está determinado** (antes era desconocido). El modo calibración pasa de "descubrir GPIOs" a **"confirmar rol de cada botón etiquetado"**.

### 3.1 RFID (RC522, SPI) sobre ESP32
| Señal | GPIO |
|---|---|
| SDA/SS | GPIO21 |
| SCK | GPIO18 |
| MOSI | GPIO23 |
| MISO | GPIO19 |
| RST | GPIO22 |
| IRQ | GPIO5 (no utilizado; se hace **polling**, no interrupción) |
| GND | GND |
| 3V3 | 3V3 |

### 3.2 Mapa de botones — GPIO REASIGNADO (versión definitiva)

**Este mapa reemplaza los GPIO de botones de `conexionesfisicas.md` y del `Requerimientos.md`.** Solo se movieron **3 pines** problemáticos; los otros 7 se conservan. Todos los pines finales soportan `INPUT_PULLUP` interno, **no** son solo-entrada, **no** son strapping de arranque, **no** son pines de flash (GPIO6–11) ni del UART0 (GPIO1/3) ni del SPI del RC522. **No se necesita ninguna resistencia externa.** Cada pulsador se cablea entre el GPIO y GND; el firmware usa `INPUT_PULLUP`, por lo que presionado = LOW.

| Etiqueta física | GPIO nuevo | GPIO anterior | Rol lógico | Cambio y motivo |
|---|---|---|---|---|
| Cursor arriba | **GPIO4** | GPIO34 | `CURSOR_UP` | **CAMBIA** — GPIO34 es solo-entrada y sin pull-up interno |
| Cursor abajo | **GPIO16** | GPIO35 | `CURSOR_DOWN` | **CAMBIA** — GPIO35 es solo-entrada y sin pull-up interno |
| Cursor izquierda | GPIO32 | GPIO32 | `CURSOR_LEFT` | igual — `INPUT_PULLUP` OK |
| Cursor derecha | GPIO33 | GPIO33 | `CURSOR_RIGHT` | igual — `INPUT_PULLUP` OK |
| Isla derecho | GPIO25 | GPIO25 | `ISLA_R` | igual — `INPUT_PULLUP` OK |
| Isla medio | GPIO26 | GPIO26 | `ISLA_M` | igual — `INPUT_PULLUP` OK |
| Isla izquierdo | GPIO27 | GPIO27 | `ISLA_L` | igual — `INPUT_PULLUP` OK |
| Panel inferior derecha | **GPIO17** | GPIO12 | `PANEL_R` | **CAMBIA** — GPIO12 es strapping de boot (voltaje de flash) |
| Panel inferior izquierda | GPIO14 | GPIO14 | `PANEL_L` | igual — `INPUT_PULLUP` OK |
| Panel inferior central largo | GPIO13 | GPIO13 | `PANEL_C` | igual — `INPUT_PULLUP` OK |

**Resumen del recableado (solo 3 saltos):**
- Cursor arriba: mover de **GPIO34 → GPIO4**.
- Cursor abajo: mover de **GPIO35 → GPIO16**.
- Panel inferior derecha: mover de **GPIO12 → GPIO17**.
- GPIO34, GPIO35 y GPIO12 quedan **sin usar** (dejar sus cables retirados o al aire).

**Pines finales usados por botones:** GPIO4, 13, 14, 16, 17, 25, 26, 27, 32, 33. Todos con `INPUT_PULLUP`, cero resistencias externas, cero riesgo de boot.

> **Nota de módulo (leer si aplica):** GPIO16 y GPIO17 son libres en ESP32-**WROOM** (DevKitC), que es el caso típico. Si tu placa fuera ESP32-**WROVER** (lleva PSRAM), GPIO16/17 están reservados para esa PSRAM; en ese único caso, reemplaza **GPIO16 → GPIO15** y **GPIO17 → GPIO5** (dejando el pin IRQ del RC522 físicamente desconectado para liberar GPIO5). GPIO15 y GPIO5 son strapping, pero con pulsador a GND + `INPUT_PULLUP` quedan en HIGH al encendido, seguros salvo que se mantengan presionados justo durante el arranque. Si no sabes el módulo, usa el mapa principal (WROOM) y verifica que el ESP32 arranque normal tras el recableado.

### 3.3 Asignación de roles funcionales (propuesta — CONFIRMAR posiciones marcadas)
Cursores: comportamiento **dependiente de la pantalla** (ver sección 6). Los tres bloques restantes:

| GPIO | Rol físico | Función | Estado |
|---|---|---|---|
| GPIO13 (`PANEL_C`, largo central) | `EMO_TOGGLE` | Activar/detener monitoreo emocional | **Fijo** (requisito heredado: el largo central debe ser el activador) |
| GPIO17 (`PANEL_R`) | `PECS_SEND` | Enviar frase (LLM + Telegram) — "el botón a mi derecha" | **CONFIRMAR** |
| GPIO14 (`PANEL_L`) | `HOME` | Volver a Home / reposo | **CONFIRMAR** |
| GPIO26 (`ISLA_M`) | `PECS_DELETE` | Borrar palabra seleccionada en Oraciones | **CONFIRMAR** |
| GPIO27 (`ISLA_L`) | `PECS_CLEAR` | Limpiar todo el stack | **CONFIRMAR** |
| GPIO25 (`ISLA_R`) | `DYNAMIC_PLAY` | Reproducir dinámica actual (reservado/opcional) | **CONFIRMAR** |

> Regla dura que se mantiene del requerimiento previo: el **largo central = `EMO_TOGGLE`**. El resto puede reajustarse por ergonomía, pero toda asignación va **centralizada** en `config/button_map.json`, nunca dispersa ni con GPIOs hardcodeados en la lógica.

### 3.4 Modo calibración
Mantener un modo (flag `--calibrate` o mantener `PANEL_C` 5 s) que muestre en pantalla qué evento/GPIO llega al presionar cada botón, para validar la tabla 3.3 sin adivinar. Salir del modo sin reiniciar.

---

## 4. Conflicto crítico de memoria RAM/GPU (RESOLVER ANTES QUE CUALQUIER OTRA COSA)

**Fuente de esta sección:** `MEMORIA_CONFLICTO_DIAGNOSTICO.md` (diagnóstico dedicado, junio 2026). Esta es la causa raíz de la falta de fluidez y de los congelamientos que reportas con la cámara. **Ningún requisito de interfaz de este documento (secciones 5–7) tiene sentido probarlo hasta que esto esté resuelto**, porque un sistema que hace *thrashing* de RAM no va a mostrar fluidez en ninguna pantalla, sea cual sea la UI que se construya encima. Este es el primer punto de la fase de trabajo (ver sección 12, Fase 0).

### 4.1 Diagnóstico (qué está pasando exactamente)

La Jetson Orin Nano tiene **8 GB de RAM unificada** (CPU y GPU comparten el mismo banco físico; no son memorias separadas). Al levantar el ecosistema completo con `start_bmo.sh`:

| Componente | RAM consumida |
|---|---|
| OS + Escritorio Ubuntu (Jetpack 6.x) | ~1.7 GB |
| `llama-server` (DeepSeek-R1-0528-Qwen3-8B Q4_K_M) | ~4.8–5.0 GB **permanentes** |
| TensorFlow (comportamiento por defecto al detectar GPU) | intenta reservar **el 100% de la GPU restante** |
| **Total intentado sobre 8 GB físicos** | **~8.5–9 GB** |

Esto agota la memoria física y el driver `nvmap` del kernel Tegra falla:
```
NvMapMemAllocInternalTagged: 1075072515 error 12 (ENOMEM - Out of Memory)
```
El sistema entra en *thrashing* con ZRAM (swap comprimido, dentro de la misma RAM física, así que no libera nada real), el load average escala a ~33, y la GUI y el proceso Python de visión terminan crasheando o congelándose. **Esto explica directamente la falta de fluidez de cámara que describes: no es un problema de la cámara en sí, es que el sistema entero se queda sin memoria cuando el motor de visión intenta tomar la GPU que ya usa el LLM.**

### 4.2 Por qué la solución es segura y no degrada nada

- El **Módulo A** (facial) ya migró de MTCNN a **MediaPipe** (documentado en `PROGRESO_BMO_S01_S05.md`), y MediaPipe corre nativo en CPU. **TensorFlow ya no necesita GPU para el Módulo A.** El cuello de botella facial que justificaba GPU ya no existe.
- El **Módulo A** se activa por *ticks* (intervalos), no en cada frame — diseño oficial confirmado en la sección 3.4.4 del reporte final de tesis. Esto ya reduce carga; no es un bug, es el comportamiento esperado.
- Forzar CPU en TensorFlow para el **Módulo B** (BiLSTM sobre keypoints de MediaPipe Holistic) tiene un costo de RAM bajo (~0.4–0.6 GB) y no requiere GPU para un modelo de este tamaño.
- El LLM conserva uso exclusivo de la GPU vía `llama-server`, que no pasa por la API CUDA de TensorFlow, así que no hay conflicto entre ambos una vez TensorFlow deja de pedir GPU.

### 4.3 Solución obligatoria — Prioridad 1 (aplicar primero, antes de tocar la UI)

Agregar estas dos líneas al **inicio absoluto** de `run.py`, **antes de cualquier `import`**:

```python
import os
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'        # TensorFlow usa CPU únicamente
os.environ['TF_FORCE_GPU_ALLOW_GROWTH'] = 'true' # Defensa secundaria por si algún import lo ignora

# Recién aquí el resto de imports (cv2, numpy, tensorflow, mediapipe, etc.)
```

**Archivos a modificar (en este orden de verificación):**
1. `/home/jetson/integradora/model_ia/sistem_IA/run.py` — obligatorio.
2. `/home/jetson/integradora/model_ia/sistem_IA/modules/mod_a.py` — **si existe como archivo separado**, aplicar las mismas dos líneas ahí también. Verificar primero con el agente si este archivo existe en el sistema real antes de asumirlo (principio de la sección 14: contextualizar antes de decidir).

**CRÍTICO — orden de las líneas:** si TensorFlow se importa antes de que se ejecute `os.environ['CUDA_VISIBLE_DEVICES'] = '-1'`, el ajuste se ignora silenciosamente. Deben ser las primeras dos líneas ejecutables del archivo, incluso antes de imports de librerías estándar como `cv2` o `numpy`.

Esta corrección es **software puro dentro de `run.py`** (variables de entorno a nivel de proceso Python) y no toca `/boot`, overlays, `runtime.yaml` ni ningún script de cámara — cumple sin excepción la sección 0.

### 4.4 Verificación de resolución en `runtime.yaml` — Prioridad 3

Confirmar que la resolución de cámara configurada sea **960x720** y no una resolución mayor (p. ej. 1920x1080). Una resolución más alta incrementa directamente el costo de procesamiento de MediaPipe en CPU, lo cual empeora la fluidez aunque el problema de RAM ya esté resuelto.

Archivo: `/home/jetson/integradora/model_ia/sistem_IA/config/runtime.yaml` (solo el campo de resolución; **no tocar overlays de cámara ahí**, por la restricción de la sección 0).

### 4.5 Opción de respaldo — Prioridad 4 (solo si el sistema sigue inestable tras 4.3 y 4.4)

Si tras aplicar la corrección de `CUDA_VISIBLE_DEVICES` el sistema sigue mostrando inestabilidad, considerar reemplazar el LLM de 8B por una variante más ligera para la tarea de corrección gramatical de frases PECS (frases cortas, tarea simple):

| Modelo GGUF candidato | Tamaño aprox. | RAM liberada aprox. |
|---|---|---|
| `DeepSeek-R1-Distill-Qwen-3B-Q4_K_M.gguf` | ~2.2 GB | ~2.8 GB |
| `DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf` | ~1.1 GB | ~3.9 GB |

Esto implica cambiar el flag `--model` que usa `llama-server` dentro de `start_bmo.sh` (o el script que lo invoque) a la nueva ruta del `.gguf`. **No es la solución prioritaria**: aplicar primero 4.3 y 4.4, y solo evaluar esto si persisten los cortes de fluidez o los `ENOMEM`.

### 4.6 Verificación posterior al cambio (ejecutar siempre tras aplicar 4.3)

```bash
# Uso de RAM en tiempo real durante el arranque completo
watch -n 2 free -h

# Procesos y su consumo
top -o %MEM

# Confirmar que TensorFlow ya no ve GPU (debe imprimir: [])
# Agregar temporalmente al inicio de run.py tras las dos líneas de entorno:
#   import tensorflow as tf
#   print(tf.config.list_physical_devices('GPU'))
```

RAM esperada tras la corrección (dentro del límite físico de 8 GB):

| Componente | RAM |
|---|---|
| OS + Escritorio | ~1.7 GB |
| `llama-server` (8B Q4) | ~5.0 GB |
| TensorFlow en CPU (Módulo B) | ~0.4–0.6 GB |
| MediaPipe + OpenCV (Módulo A) | ~0.3 GB |
| **Total** | **~7.5 GB** |

### 4.7 Nota de red asociada (misma fuente de diagnóstico)

El firmware ESP32 (`main.cpp`) puede tener hardcodeada una URL de una red antigua:
```
SERVER_URL = "http://192.168.10.140:5000/ask"
```
Verificar la IP actual de la Jetson con `ip addr` en la red donde opere el dispositivo. Si difiere, hay dos caminos válidos (elegir uno, documentar cuál se aplicó):
- Actualizar `SERVER_URL` en el firmware a la IP real actual, o
- Configurar la Jetson con IP estática `192.168.10.140` en su red local.
Esto es independiente del conflicto de memoria, pero se agrupa aquí por venir de la misma fuente de diagnóstico y porque una IP desactualizada puede simular (por Telegram/LLM que nunca responde) un problema que en realidad es de red, no de memoria — conviene descartarlo en la misma pasada de diagnóstico.

### 4.8 Regla dura de esta sección

**No se avanza a probar o pulir la interfaz interactiva (secciones 5–7) mientras el sistema siga entrando en `ENOMEM` o congelándose por RAM.** Verificar 4.3 y 4.6 primero; solo con el sistema estable tiene sentido evaluar la fluidez de la cinta fantasma, las transiciones de Caras, o cualquier otro requisito visual.

---

## 5. Corrección de rotación de cámara (software puro)

### 4.1 Problema
La cámara USB está montada **rotada 90°** por restricción de espacio; el frame se ve girado. Se corrige por software con `cv2.rotate()`, con el sentido configurable (no hardcodeado), aplicado **inmediatamente después de `cap.read()` y antes de cualquier detección o render**.

### 4.2 Dónde aplicarlo
La cámara la abre el proceso que la lee. Los Módulos A/B/C viven en `integradora/model_ia/sistem_IA/run.py`; ese es el bucle de captura real. **La rotación va dentro del bucle de captura de `run.py`**, antes de alimentar a los módulos y antes de dibujar overlays, para que:
- La detección facial/landmarks (Módulo A, MediaPipe) trabaje sobre el frame vertical correcto.
- Las dimensiones se lean **dinámicas** (`h, w = frame.shape[:2]`), ya que la rotación 90° intercambia ancho↔alto. Ningún módulo debe asumir resolución fija.

Si `bmo_app.py` renderiza algún preview propio de cámara, aplica **la misma constante** ahí. Para evitar divergencias, la constante vive en un archivo compartido.

### 4.3 Configuración (sin tocar `runtime.yaml`)
Crear `config/camera.json` (archivo nuevo, plano, de nivel app; **no** es overlay ni afecta boot):
```json
{ "rotate": "CW" }
```
Valores válidos: `"NONE"`, `"CW"` (horario), `"CCW"` (antihorario), `"180"`.

### 4.4 Helper (pegar en un módulo utilitario compartido, p. ej. `core/frame_utils.py`)
```python
import cv2, json, os

_ROTATE_MAP = {
    "NONE": None,
    "CW":   cv2.ROTATE_90_CLOCKWISE,
    "CCW":  cv2.ROTATE_90_COUNTERCLOCKWISE,
    "180":  cv2.ROTATE_180,
}

def load_rotation(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f).get("rotate", "NONE")
    except Exception:
        return "NONE"

def apply_rotation(frame, rotate_key):
    code = _ROTATE_MAP.get(rotate_key)
    return frame if code is None else cv2.rotate(frame, code)
```

Uso en el bucle de captura:
```python
ROTATE = load_rotation("/home/jetson/bmo_unified/config/camera.json")
...
ok, frame = cap.read()
if not ok:
    continue
frame = apply_rotation(frame, ROTATE)   # ANTES de detección y de overlays
h, w = frame.shape[:2]                  # dimensiones dinámicas tras el swap
# ... Módulo A/B/C usan (h, w) dinámicos; overlays sobre 'frame' rotado ...
```

### 4.5 Un solo dueño de la cámara
`/dev/video0` solo lo puede abrir un proceso a la vez. La opción **"Video"** de la cinta **es** el panel embebido del monitor emocional (mismo consumidor de cámara). Reglas:
- La cámara tiene un único dueño activo a la vez.
- Al abrir "Video"/monitor: se lanza/consume `run.py` (que abre la cámara).
- Al salir de "Video"/detener monitor: `VideoCapture.release()` y terminación limpia del subproceso (sección 9).
- `bmo_app.py` no abre `/dev/video0` mientras `run.py` lo tenga tomado.

---

## 6. Especificación de la interfaz

### 5.1 Ventana
- Pantalla completa, sin bordes ni barra de título, fija 1024x600, cursor de ratón oculto.
- Targets táctiles grandes (≥ 64 px).

### 5.2 Cinta de opciones fantasma "infinita" (nuevo componente central)
Comportamiento:
- **Invisible por defecto.** Aparece en la parte baja de la pantalla **al tocar** cualquier zona.
- Se **oculta** automáticamente a los **5 s** de inactividad, o al **tocar otra zona** fuera de la cinta.
- Aparición/desaparición con **fundido suave**, sin tapar la cara de forma permanente.
- **Semitransparente**; iconos **blancos** por defecto (diseño de icono a tu criterio, coherente y minimalista).
- **Desplazable con el dedo** (scroll horizontal) y con **direccionales difuminadas** en ambos costados como afordancia. Es **"infinita"**: hace *wrap-around* (carrusel cíclico) al llegar al extremo.
- Cada icono lleva **su nombre debajo**.
- Icono seleccionado o en **hover** → se resalta en **naranja** y la pantalla cambia a esa opción.

Contenido (orden): **Home, Caras, Oraciones, Video, Salir.**

Implementación sugerida: contenedor `QWidget` superpuesto (overlay) con `QGraphicsOpacityEffect` para el fade; carrusel con lista horizontal (scroll táctil) y lógica de índice módulo N para el efecto infinito; temporizador `QTimer` de 5 s reiniciable en cada toque de la cinta; captura de toques fuera de la cinta para ocultarla.

### 5.3 Máquina de estados de pantallas
Estado por defecto: **Home**. Navegación primaria: tocar un icono de la cinta. Navegación física opcional: `HOME` (`PANEL_L`) regresa a Home.

| Pantalla | Fondo | Cursores (D-pad) | Acciones asociadas |
|---|---|---|---|
| **Home** | Cara base en loop seamless | — (sin efecto de contenido) | Reposo. La cinta permite ir a otra pantalla |
| **Caras** | Cara animada en loop | `LEFT`/`RIGHT` = cara anterior/siguiente (+ audio de esa cara). `UP`/`DOWN` reservados | Al cambiar cara suena su audio asociado |
| **Oraciones** | **Fondo plano** (color liso, sin video) | `LEFT`/`RIGHT`/`UP`/`DOWN` = mover selección entre palabras apiladas | `PECS_SEND` enviar, `PECS_DELETE` borrar seleccionada, `PECS_CLEAR` limpiar |
| **Video** | Panel embebido del monitor emocional | — | `EMO_TOGGLE` activa/detiene reconocimiento. Sin ventanas emergentes |
| **Salir** | — | — | Apagado ordenado de la app (sección 9). **Requiere confirmación / long-press** para evitar cierre accidental por el niño |

### 5.4 Home
- Reproduce únicamente la **cara base en loop seamless**. Sin overlays salvo la cinta al tocar.

### 5.5 Caras
- Ciclado de caras con **cursores físicos** (`LEFT`/`RIGHT` = anterior/siguiente).
- Al cambiar de cara, **suena el audio determinado para esa cara**.
- Requiere mapa `config/faces_audio.json` (cara → audio). Si el `.mp4` ya trae audio propio embebido, se puede usar ese; se prefiere mapa explícito para control fino. Ver sección 7.
- El cambio entre clips debe ser **fluido, sin parpadeo ni ventana negra** (precarga del siguiente clip / manejo de `EndOfMedia`).

### 5.6 Oraciones (PECS-RFID)
- Al entrar, el **fondo se vuelve plano** (color liso). En grande y centrado, un **mensaje de saludo variable** (cambia cada vez que se abre el apartado):
  - Ejemplos: `"Hola, ¿qué deseas contarme hoy?"`, `"¿Qué me quieres decir?"`, `"Cuéntame algo, te escucho"`, `"¿Qué necesitas?"`, `"Estoy aquí para ti, ¿qué pasa?"`.
  - Guardar la lista en `config/greetings.json`; elegir uno al azar al abrir la pantalla.
- Texto pequeño de instrucción (literal):
  - `"Forma oraciones pasando las tarjetas por mi oreja derecha. Presiona el botón a mi derecha para Enviar el mensaje."`
- **Apilado en vivo:** cada palabra leída por RFID aparece **una por una**, apilándose como chips/etiquetas en orden de lectura.
- **Selección:** con los **cursores** se selecciona entre las palabras apiladas (resaltar la seleccionada).
- **Borrar:** con un botón **Isla** (`PECS_DELETE` = `ISLA_M` propuesto) se borra la palabra seleccionada.
- **Limpiar todo:** `PECS_CLEAR` (`ISLA_L` propuesto).
- **Enviar:** botón "a la derecha" (`PECS_SEND` = `PANEL_R` propuesto) o botón fantasma "Enviar".
- **Tarjeta no registrada:** no se apila; aviso breve "tarjeta no reconocida"; **no rompe el flujo**.
- Tras enviar, mostrar la **frase corregida** que devuelve el LLM (retroalimentación visual) y limpiar el stack en envío exitoso.

Flujo de envío (en segundo plano, UI no bloqueante):
1. Frase bruta → `POST http://127.0.0.1:5000/ask` (`ia_bridge.py`).
2. Puente consulta `llama-server :1234` (DeepSeek-R1-0528-Qwen3-8B), que reordena/corrige/completa.
3. La app envía la frase corregida por **Telegram** (Bot API) al cuidador.
4. Indicador "procesando…" mientras dura (**latencia ~20–29 s**, medida en el reporte). Nunca congelar la UI.

Ejemplo validado: `PAPÁ ESTOY LISTO COLEGIO` → `"Papá, estoy listo para ir al colegio."`

### 5.7 Video (monitor emocional embebido)
- **No corre al arrancar.** Se inicia solo con `EMO_TOGGLE` (`PANEL_C`) o al abrir "Video".
- **Carga perezosa:** importar/inicializar TensorFlow/MediaPipe/DeepFace/Detectron2 **solo al activar**.
- **`CUDA_VISIBLE_DEVICES='-1'`** para TF (Módulo A ya no necesita GPU tras migrar a MediaPipe) y/o `TF_FORCE_GPU_ALLOW_GROWTH=true`, para no colisionar con el LLM (~5 GB de los 8 GB unificados → evita `ENOMEM`/`nvmap`).
- **Panel embebido dentro de la app**; los frames van a `QImage`/`QPixmap` en un `QLabel`/`QWidget`. **Prohibido `cv2.imshow()`** o cualquier ventana externa.
- Captura + inferencia en **`QThread` dedicado**; los frames se emiten por señales al hilo de UI (nunca tocar widgets desde el hilo de trabajo).
- Overlays: recuadro de rostro, probabilidades, gesto, actividad de flujo óptico; y **nivel global de estrés** con color: **Bajo=verde, Medio=amarillo, Alto=rojo, Inseguro=gris.**
- **Detener** (`EMO_TOGGLE` de nuevo o salir de "Video"): parar hilo, `release()` cámara, terminar `run.py` y sus hijos, cerrar panel, volver a Home. Sin procesos residuales.

---

## 7. Archivos de configuración (nuevos y existentes)

| Archivo | Contenido | Estado |
|---|---|---|
| `config/button_map.json` | GPIO/evento → rol lógico (tabla 3.3) | crear/actualizar |
| `config/camera.json` | `{ "rotate": "CW" }` | **crear** |
| `config/faces_audio.json` | cara `.mp4` → audio a reproducir | **crear** |
| `config/greetings.json` | lista de saludos variables de Oraciones | **crear** |
| `config/telegram.json` | token + chat_id (NO versionar, NO hardcodear) | existente |
| `rfid_vocab.json` | UID → palabra en español | existente |

Ejemplo `config/faces_audio.json`:
```json
{
  "cara_alegre.mp4": "audios/alegria.wav",
  "cara_perro.mp4":  "audios/perro_guau.wav",
  "cara_gato.mp4":   "audios/gato_miau.wav",
  "cara_manzana.mp4":"audios/color_manzana.wav"
}
```
Reproducir el audio por un canal separado (p. ej. `QMediaPlayer` dedicado a audio, o `aplay`/`pygame.mixer`) para no interferir con el audio del video de fondo. Ajustar a los nombres reales de `~/integradora/animaciones` y a las 6 categorías de audio del reporte.

---

## 8. Firmware ESP32 (`main.cpp`)

- Leer los **10 botones** con debounce (~20–30 ms) y **polling del RC522** (IRQ en GPIO5 no se usa).
- **Pull-ups:** usar `INPUT_PULLUP` en **todos** los pines de botón del mapa definitivo (GPIO 4, 13, 14, 16, 17, 25, 26, 27, 32, 33). Con la reasignación de la sección 3.2, **ya no hace falta ninguna resistencia externa** ni hay pines de arranque comprometidos. Cada pulsador va entre el GPIO y GND; presionado = LOW.
- Los antiguos GPIO34/35 (solo-entrada) y GPIO12 (strapping) **quedan fuera de uso** y no deben leerse.
- Solo si la placa fuese WROVER: aplicar la sustitución 16→15 y 17→5 de la nota de módulo en 3.2 (IRQ del RC522 desconectado). En ese caso, GPIO15 y GPIO5 también se leen con `INPUT_PULLUP`.
- Emitir **eventos con tokens estables** por serie, uno por línea, fáciles de parsear. Sugerido:
  - Botones: `BTN:CURSOR_UP`, `BTN:CURSOR_DOWN`, `BTN:CURSOR_LEFT`, `BTN:CURSOR_RIGHT`, `BTN:ISLA_L`, `BTN:ISLA_M`, `BTN:ISLA_R`, `BTN:PANEL_L`, `BTN:PANEL_C`, `BTN:PANEL_R`.
  - RFID: `RFID:AABBCCDD` (UID en hex).
- La app parsea eventos → rol lógico vía `button_map.json`; tramas corruptas se ignoran sin crashear.
- Corregir cualquier `SERVER_URL` residual: la comunicación LLM es **local** (`127.0.0.1:5000` desde la app; el firmware **no** debe apuntar a IPs de red antigua como `192.168.10.140`).

---

## 9. Ciclo de vida de procesos (sin residuales)

- **Arranque:** `start_bmo.sh` levanta (si no están activos) `llama-server :1234` e `ia_bridge.py :5000`, habilita permisos serie (`chmod 666 /dev/ttyUSB0` fuera de la app) y lanza `bmo_app.py`. La app no modifica configuraciones del sistema.
- Todo subproceso lanzado por la app (p. ej. `run.py`) inicia en **su propio grupo** (`start_new_session=True` / `os.setsid`).
- **Al detener monitor:** parar hilo captura/inferencia → `release()` cámara → terminar grupo de `run.py` (SIGTERM → espera corta → SIGKILL) → `join()` de hilos.
- **Al salir (Salir / cierre de ventana):** matado ordenado de **todo** lo que inició la app (`run.py`, hilos de cámara y serie y, si la app los gestiona, `ia_bridge.py`/`llama-server`), con `os.killpg` y/o `psutil`. Verificar que no queden huérfanos ni la cámara ocupada.
- Manejo de `SIGINT`/`SIGTERM` para limpieza también desde terminal.

---

## 10. Requisitos no funcionales

- **Estabilidad de memoria:** nunca cargar visión + LLM de forma que exceda la RAM. Visión **solo bajo demanda** + `CUDA_VISIBLE_DEVICES='-1'`.
- **UI no bloqueante:** toda E/S lenta (HTTP LLM, Telegram, serie, inferencia) fuera del hilo de UI; PyQt solo desde el hilo principal.
- **Robustez:** tarjeta no registrada, desconexión de cámara/ESP32 o timeout del LLM se muestran como avisos y **no crashean** la app.
- **Sin secretos en código:** token/chat_id/rutas en config o variables de entorno.
- **Logs:** a archivo (RFID, envíos, activación/detención de visión, errores).

---

## 11. Estructura de archivos sugerida

```
/home/jetson/
├── bmo_unified/
│   ├── bmo_app.py                 # App principal (PyQt5, orquestador)
│   ├── ui/
│   │   ├── ghost_ribbon.py        # Cinta fantasma infinita (overlay + carrusel + fade)
│   │   ├── screen_home.py
│   │   ├── screen_caras.py
│   │   ├── screen_oraciones.py    # apilado PECS + saludo variable + envío
│   │   └── screen_video.py        # panel embebido del monitor emocional
│   ├── core/
│   │   ├── serial_manager.py      # autodetección/reconexión ESP32
│   │   ├── button_router.py       # evento → rol lógico (button_map.json)
│   │   ├── frame_utils.py         # apply_rotation / load_rotation
│   │   ├── rfid_vocab.py
│   │   ├── telegram_sender.py
│   │   ├── audio_player.py        # audio por cara (canal separado)
│   │   └── process_manager.py     # grupos, SIGTERM→SIGKILL, sin huérfanos
│   ├── config/
│   │   ├── button_map.json
│   │   ├── camera.json            # {"rotate":"CW"}
│   │   ├── faces_audio.json
│   │   ├── greetings.json
│   │   ├── telegram.json          # NO versionar
│   │   └── rfid_vocab.json
│   └── start_bmo.sh
├── integradora/model_ia/
│   ├── sistem_IA/
│   │   ├── run.py                        # motor visión (A,B,C,fusión) — aplicar rotación (5) Y fix de memoria (4) AQUÍ, en este orden: primero las 2 líneas de os.environ, luego el resto de imports
│   │   ├── modules/mod_a.py              # Módulo A, SI EXISTE como archivo separado — aplicar también el fix de memoria (sección 4.3)
│   │   └── config/runtime.yaml           # resolución de cámara — verificar 960x720 (sección 4.4). NO tocar overlays aquí
│   ├── pruebas_mod/                       # entorno virtual (venv) desde el que se ejecuta run.py, según start_bmo.sh
│   └── resultados_modb_v3/modelo_modb_v3.keras   # modelo Módulo B (BiLSTM)
└── apps/llm/
    ├── ia_bridge.py                       # puente Flask :5000
    └── llama.cpp/build/                   # binario llama-server y modelo .gguf (:1234) — ruta del modelo candidata a cambiar si se aplica sección 4.5
```

---

## 12. Plan de trabajo por fases (orden recomendado para el agente)

0. **Conflicto de memoria RAM/GPU (bloqueante, resolver primero).** Aplicar sección 4: las dos líneas `os.environ` al inicio absoluto de `run.py` (y de `modules/mod_a.py` si existe), verificar resolución 960x720 en `runtime.yaml`, verificar con `free -h` / `top` / `tf.config.list_physical_devices('GPU')` que el consumo se mantiene ~7.5 GB y que TensorFlow no ve GPU. Solo avanzar a la fase 1 con el sistema estable.
1. **Rotación de cámara (aislada y verificable primero).** Crear `config/camera.json` y `core/frame_utils.py`; integrar en el bucle de captura de `run.py` con dimensiones dinámicas. Verificar con el monitor embebido que la imagen sale vertical y que A/B/C operan sobre el frame corregido. **No tocar boot/overlays.**
2. **Mapa de botones + firmware.** Recablear los 3 pines movidos (34→4, 35→16, 12→17) según sección 3.2. Actualizar `main.cpp` con tokens estables, debounce e `INPUT_PULLUP` en los 10 pines definitivos (sin resistencias externas). Verificar que el ESP32 arranca normal. Crear `config/button_map.json` y `core/button_router.py`. Añadir modo calibración. Aprovechar para verificar/corregir la IP de `SERVER_URL` (sección 4.7).
3. **Cinta fantasma infinita** (`ui/ghost_ribbon.py`): overlay semitransparente, iconos blancos, hover/selección naranja, scroll táctil + flechas difuminadas, wrap-around, autohide 5 s / toque fuera, fade.
4. **Máquina de estados de pantallas** y router de navegación (cinta táctil + `HOME` físico).
5. **Home** (loop seamless base) y **Caras** (cursores + audio por cara, `faces_audio.json`).
6. **Oraciones**: fondo plano, saludo variable (`greetings.json`), instrucción literal, apilado en vivo, selección con cursores, borrar/limpiar/enviar, frase corregida. Reusar el flujo LLM+Telegram existente.
7. **Video**: reutilizar el panel embebido del monitor emocional (carga perezosa, `CUDA_VISIBLE_DEVICES='-1'`, sin `cv2.imshow`, detención limpia).
8. **Salir** con confirmación/long-press → apagado ordenado (sección 9).
9. **Cierre:** verificación de no-residuales (`psutil`), logs, pruebas end-to-end, incluyendo una prueba de estabilidad de memoria de larga duración con el stack completo activo (LLM + monitor emocional activado y desactivado varias veces).

---

## 13. Criterios de aceptación (checklist)

**Memoria RAM/GPU (bloqueante — verificar primero)**
- [ ] `run.py` tiene `os.environ['CUDA_VISIBLE_DEVICES'] = '-1'` y `TF_FORCE_GPU_ALLOW_GROWTH'] = 'true'` como las dos primeras líneas ejecutables, antes de cualquier import.
- [ ] Si existe `modules/mod_a.py` como archivo separado, tiene las mismas dos líneas al inicio.
- [ ] `tf.config.list_physical_devices('GPU')` imprime `[]` con el stack completo corriendo.
- [ ] `runtime.yaml` tiene la cámara en 960x720 (no una resolución mayor).
- [ ] Con `llama-server` + monitor emocional activo simultáneamente, `free -h` se mantiene por debajo de ~7.5–8 GB, sin `ENOMEM`/`nvmap` en los logs, sin thrashing de swap ni load average disparado.
- [ ] El sistema no se congela tras activar/desactivar el monitor emocional repetidas veces.
- [ ] (Si se aplicó la sección 4.5) `llama-server` apunta al `.gguf` correcto en `start_bmo.sh` y la corrección gramatical de frases PECS sigue siendo satisfactoria con el modelo más ligero.

**Cámara / rotación**
- [ ] La imagen se muestra vertical y correcta en el panel embebido.
- [ ] La rotación se aplica tras `cap.read()` y antes de detección/overlays, con sentido configurable en `config/camera.json` (no hardcodeado).
- [ ] Módulos A/B/C usan dimensiones dinámicas (`frame.shape`); nada asume resolución fija.
- [ ] Cero cambios en `/boot`, `extlinux.conf`, overlays, `runtime.yaml` (parte de cámara) o scripts CSI/USB.
- [ ] Un solo proceso abre `/dev/video0` a la vez; se libera al detener.

**Interfaz**
- [ ] Pantalla completa sin bordes 1024x600, cursor oculto.
- [ ] Cara base en loop seamless, sin negros ni cortes.
- [ ] Cinta fantasma: aparece al tocar, autohide 5 s o al tocar fuera, fade, semitransparente, iconos blancos, hover/selección naranja, scroll táctil + flechas difuminadas, wrap-around infinito, nombre bajo cada icono.
- [ ] Cinta con: Home, Caras, Oraciones, Video, Salir; seleccionar cambia de pantalla.

**Caras**
- [ ] Cursores cambian de cara; cada cambio reproduce el audio asociado; sin parpadeo entre clips.

**Oraciones**
- [ ] Fondo plano; saludo grande variable; instrucción literal en pequeño.
- [ ] Palabras aparecen una por una apiladas en orden.
- [ ] Cursores seleccionan palabra; Isla borra; existe limpiar todo y enviar.
- [ ] Envío dispara LLM→Telegram en segundo plano sin congelar UI; muestra frase corregida; tarjeta no registrada no rompe el flujo.

**Video / monitor**
- [ ] No corre al arrancar; se activa con `EMO_TOGGLE` o al abrir "Video".
- [ ] Panel embebido; sin `cv2.imshow` ni ventanas externas.
- [ ] Nivel de estrés con color (verde/amarillo/rojo/gris).
- [ ] Detener libera cámara, termina subproceso e hilos, sin residuales.
- [ ] Carga perezosa + `CUDA_VISIBLE_DEVICES='-1'` para no colisionar con el LLM.

**Botones / firmware**
- [ ] 10 botones mapeados a roles vía `button_map.json` (centralizado, sin GPIOs dispersos).
- [ ] Largo central = `EMO_TOGGLE` (fijo). Send/Delete/Clear/Home confirmados.
- [ ] Botones cableados a los GPIO definitivos (4, 13, 14, 16, 17, 25, 26, 27, 32, 33), todos con `INPUT_PULLUP`, sin resistencias externas; GPIO34/35/12 fuera de uso; el ESP32 arranca normal tras el recableado.
- [ ] Modo calibración operativo.
- [ ] Firmware sin IPs de red antigua; comunicación LLM local.

**Ciclo de vida / seguridad**
- [ ] Subprocesos en su propio grupo; terminación SIGTERM→SIGKILL.
- [ ] Al salir se terminan todos los procesos/hilos iniciados; cámara liberada; sin huérfanos.
- [ ] La app no usa `sudo` ni eleva privilegios; cámara solo USB/V4L2 sobre `/dev/video0`.

---

## 14. Cómo resolver decisiones durante el desarrollo

**Principio para el agente de terminal:** ante cualquier punto de decisión o ambigüedad, **primero contextualizar con los archivos del proyecto a disposición** (este documento, `REQUERIMIENTOS_APP_MOODI.md`, `conexionesfisicas.md`, `PROGRESO_BMO_S01_S05.md`, `PROGRESO_BMO_S06.md`, `MEMORIA_CONFLICTO_DIAGNOSTICO.md`, el paper IEEE Access y el reporte final, además del código real en la Jetson: `bmo_app.py`, `run.py`, `modules/mod_a.py` si existe, `ia_bridge.py`, `main.cpp`, `rfid_vocab.json`, `runtime.yaml`). No adivinar ni inventar rutas, nombres de archivo, resoluciones ni pines: leerlos del sistema. Si tras revisar los archivos la decisión sigue abierta, elegir la opción más segura (que nunca afecte el arranque) y dejarla registrada en un `.md` de progreso para validación posterior. La regla de oro de la sección 0 prevalece sobre cualquier decisión.

### Puntos aún por confirmar (no bloquean el arranque del desarrollo)
1. **Enviar / Borrar / Limpiar / Home:** confirmar la asignación física propuesta (`PANEL_R`=Enviar, `ISLA_M`=Borrar, `ISLA_L`=Limpiar, `PANEL_L`=Home). Validar con el modo calibración. Los **GPIO ya son definitivos** (sección 3.2); lo único abierto es qué rol va en cada botón etiquetado.
2. **Sentido de rotación** real: `"CW"` o `"CCW"` (según cómo quedó montada la cámara). Ajustar `config/camera.json` verificando la salida en el panel embebido.
3. **Mapa cara→audio:** nombres reales de los `.mp4` en `~/integradora/animaciones` y de los audios de las 6 categorías (leer el directorio antes de escribir `faces_audio.json`).
4. **"Video" = monitor emocional**: confirmar que esta opción reutiliza el panel embebido del monitor (recomendado) y no un preview de cámara aparte, para no duplicar el acceso a `/dev/video0`.
5. **Salir:** confirmar el gesto de confirmación (diálogo o long-press) para evitar cierre accidental por el niño.
6. **Tipo de módulo ESP32** (WROOM vs WROVER): solo relevante para GPIO16/17 (ver nota de módulo en 3.2). Verificar antes de recablear si hay duda.
