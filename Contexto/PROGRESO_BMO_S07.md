# Reporte de Progreso - Proyecto BMO Unified (Moodi)
**Semana:** S07 (22 de Junio, 2026)
**Archivo:** PROGRESO_BMO_S07.md
**Ubicación:** `/home/jetson/`

---

## 1. Contexto y objetivo de la sesión

`bmo_app.py` (la aplicación PyQt5 que actúa como "sistema operativo" del robot, descrita en
`PROGRESO_BMO.md` y perdida en el reflasheo documentado en `PROGRESO_BMO_S06.md`) se reconstruyó
desde cero siguiendo el documento de especificación `REQUERIMIENTOS_APP_MOODI.md` añadido al
proyecto. Esta sesión cubrió: diseño de arquitectura, implementación completa de la app, reescritura
del firmware ESP32, y verificación funcional en la Jetson real (pantalla, cámara, LLM y enlace serie
reales — no simulados).

---

## 2. Decisiones de arquitectura tomadas (y por qué)

### A. El ESP32 deja de ser autónomo
Antes, el firmware ([`integradora/Oraciones_interpret/src/main.cpp`](file:///home/jetson/integradora/Oraciones_interpret/src/main.cpp))
hacía WiFi + HTTP a `ia_bridge` + envío de Telegram **desde el propio microcontrolador**, con el
SSID, password de WiFi y token de Telegram en texto plano dentro del código versionado, y con un
`while` bloqueante de reconexión WiFi que congelaba también la lectura de botones/RFID si la red
caía.

**Decisión:** el ESP32 pasa a ser un periférico puro — lee 10 botones + UID RFID crudo y los manda
por serie; toda la orquestación (traducción UID→palabra, armado de frase, llamada al LLM, Telegram)
vive ahora en Python, en `bmo_app.py`. Motivos: (1) elimina los secretos del firmware porque ya no
necesita WiFi en absoluto, (2) arregla el bloqueo de lectura de botones por reconexión WiFi, (3)
evita mantener dos máquinas de estado de la misma frase (una en C++, otra en Python).

### B. El panel de Monitor Emocional no usa `cv2.imshow`
El visor existente, [`integradora/model_ia/sistem_IA/ui/viewer.py`](file:///home/jetson/integradora/model_ia/sistem_IA/ui/viewer.py)
(líneas 292 y 415), abre una ventana de OpenCV — prohibido explícitamente por la sección 6.2 de los
requerimientos para la nueva app. **Decisión:** en vez de lanzar `run.py` como caja negra, se
reescribió la capa de orquestación de procesos como [`bmo_unified/vision/engine.py`](file:///home/jetson/bmo_unified/vision/engine.py),
reutilizando las funciones de `run.py` (no se tocó ese archivo) pero sin instanciar `UIViewer`, y
sustituyéndolo por un `QThread` propio (`_QueuePump`) que repinta los frames dentro de un `QLabel`
embebido vía señales Qt.

### C. Telegram con placeholders
[`bmo_unified/config/telegram.json`](file:///home/jetson/bmo_unified/config/telegram.json) se creó
con `bot_token`/`chat_id` vacíos (y se agregó a `.gitignore`); el usuario los completa localmente.

---

## 3. Mapa de archivos nuevos/modificados

```
bmo_unified/
├── bmo_app.py                     # Punto de entrada
├── config/
│   ├── button_map.json            # GPIO -> rol lógico (única fuente de verdad del mapeo)
│   ├── rfid_vocab.json            # Copiado de integradora/Oraciones_interpret/data/
│   └── telegram.json              # Placeholders, ignorado por git
├── core/
│   ├── serial_manager.py          # Hilo de lectura serie (autodetección + reconexión)
│   ├── button_router.py           # GPIO -> rol lógico -> acción + detección de long-press
│   ├── rfid_vocab.py               # Carga del diccionario UID -> palabra
│   ├── pecs_engine.py              # Pila de palabras + envío a ia_bridge + Telegram
│   └── telegram_sender.py         # Envío async a la API de Telegram
├── vision/
│   └── engine.py                   # Orquestación del motor de visión (A/B/C) sin ui/viewer.py
└── ui/
    ├── main_window.py              # Ventana principal: integra todo
    ├── animation_player.py         # Fondo animado en bucle (QMediaPlayer + QVideoWidget)
    ├── ghost_controls.py            # Controles táctiles fantasma
    ├── pecs_panel.py                 # Sub-interfaz PECS (chips + frase + estado)
    ├── emo_monitor_panel.py          # Panel embebido de video + nivel de estrés
    └── calibration_overlay.py        # Modo calibración de botones

integradora/Oraciones_interpret/
├── src/main.cpp                    # REESCRITO: ESP32 como periférico puro
└── platformio.ini                  # Se quitó ArduinoJson y el filesystem SPIFFS

start_bmo.sh                        # Paso 5 ahora lanza bmo_app.py en vez de run.py
integradora/model_ia/pruebas_mod/pyvenv.cfg   # include-system-site-packages = true (para ver PyQt5 de apt)
```

---

## 4. Referencias de código clave

### 4.1 Firmware ESP32 — periférico puro

[`integradora/Oraciones_interpret/src/main.cpp:40`](file:///home/jetson/integradora/Oraciones_interpret/src/main.cpp#L40)
```cpp
constexpr uint8_t BUTTON_PINS[NUM_BUTTONS] = {4, 5, 32, 33, 25, 26, 27, 14, 12, 13};
```
**Cambio post-flasheo inicial:** la tabla 3.2 de los requerimientos proponía GPIO 34 y 35 para los
dos primeros botones. Esos dos pines son de solo entrada en el ESP32 clásico y no tienen resistencia
de pull interna; al flashear el primer firmware y probarlo en la placa real, quedaron flotando
(sin resistencia pull-up externa cableada) y dispararon falsos `BTN:34:DOWN`/`BTN:35:DOWN` sin que
nadie tocara nada. Como no se va a cablear una resistencia externa, se reasignaron esos dos roles a
**GPIO 4 y GPIO 5**, que sí soportan `INPUT_PULLUP` interno igual que los otros 8 pines — mismo
cableado simple (botón a GND) para los 10 botones, sin componentes externos adicionales. Verificado
en la placa real tras el cambio: solo `BOOT:OK` al reiniciar, cero disparos falsos.

[`integradora/Oraciones_interpret/src/main.cpp:56-73`](file:///home/jetson/integradora/Oraciones_interpret/src/main.cpp#L56) —
`pollButtons()`: debounce de 50ms por pin, emite `Serial.println("BTN:<gpio>:DOWN")` o `:UP`. El
GPIO físico se manda crudo — el mapeo a rol lógico (D-pad, PECS_SEND, etc.) **no vive en el
firmware**, vive en `bmo_unified/config/button_map.json`, para poder remapear sin reflashear.

[`integradora/Oraciones_interpret/src/main.cpp:77-89`](file:///home/jetson/integradora/Oraciones_interpret/src/main.cpp#L77) —
`readRFID()`: lee UID de 4 bytes vía MFRC522 y lo manda como `RFID:<b0>,<b1>,<b2>,<b3>` — crudo, sin
traducir a palabra (eso ahora lo hace Python).

Se quitaron por completo: `WiFi.h`, `HTTPClient.h`, `WiFiClientSecure`, `SPIFFS`, `ArduinoJson`, y
las constantes `SSID`/`PASS`/`TELEGRAM_TOKEN`/`CHAT_ID`/`SERVER_URL` que antes estaban hardcodeadas
en texto plano. Compilado con PlatformIO (RAM 6.6%, Flash 21.2%) y **flasheado a la ESP32 física**
en esta sesión (dos veces: la versión inicial con GPIO 34/35, y la versión corregida con GPIO 4/5).

### 4.2 `core/serial_manager.py` — enlace serie robusto

[`bmo_unified/core/serial_manager.py:27`](file:///home/jetson/bmo_unified/core/serial_manager.py#L27) —
`class SerialManager(QThread)`: autodetecta `/dev/ttyUSB*` y `/dev/ttyACM*`, reconecta solo si el
cable se desconecta, nunca lanza excepción hacia la UI por una trama corrupta.

[`bmo_unified/core/serial_manager.py:61-76`](file:///home/jetson/bmo_unified/core/serial_manager.py#L61) —
`_parse_line()`: traduce `BTN:`/`RFID:`/`BOOT:` a señales Qt (`button_event`, `rfid_event`); cualquier
otra línea se ignora a propósito (robustez ante ruido).

### 4.3 `core/button_router.py` — mapeo centralizado + calibración

[`bmo_unified/core/button_router.py:23`](file:///home/jetson/bmo_unified/core/button_router.py#L23) —
`class ButtonRouter`: carga `button_map.json` (GPIO string -> rol), traduce eventos crudos del
ESP32 a roles lógicos (`action` signal).

[`bmo_unified/core/button_router.py:42-58`](file:///home/jetson/bmo_unified/core/button_router.py#L42) —
`on_button_event()`: si el rol es `EMO_TOGGLE`, espera a que se suelte el botón para decidir si fue
un tap normal o un long-press de 5s (`LONG_PRESS_S`, línea 20) — en cuyo caso emite
`calibration_requested` en vez de la acción normal.

### 4.4 `core/pecs_engine.py` — única fuente de verdad de la frase PECS

[`bmo_unified/core/pecs_engine.py:25`](file:///home/jetson/bmo_unified/core/pecs_engine.py#L25) —
`class PecsEngine(QObject)`: apila palabras, valida contra el vocabulario, arma la frase, dispara el
envío.

[`bmo_unified/core/pecs_engine.py:39-46`](file:///home/jetson/bmo_unified/core/pecs_engine.py#L39) —
`add_card(uid)`: si el UID no está en `rfid_vocab.json` emite `card_rejected` y no apila nada (la
tarjeta no rompe el flujo).

[`bmo_unified/core/pecs_engine.py:62-95`](file:///home/jetson/bmo_unified/core/pecs_engine.py#L62) —
`send()`/`_do_send()`: lanza un hilo aparte (`threading.Thread`, no bloquea la UI) que hace
`POST http://127.0.0.1:5000/ask` (línea 21, `IA_BRIDGE_URL`) con `{"prompt": frase}`; si
`ia_bridge` responde con éxito, limpia el stack, emite `sentence_sent(bruta, corregida)`, y dispara
`telegram_sender.send_message_async(...)`. Si falla, el stack **no se borra** (se puede reintentar).
Probado end-to-end en esta sesión contra `ia_bridge`/`llama-server` reales: `"YO QUIERO AGUA"` →
`"Quiero agua."`.

### 4.5 `vision/engine.py` — el motor de visión sin `ui/viewer.py`

[`bmo_unified/vision/engine.py:37-53`](file:///home/jetson/bmo_unified/vision/engine.py#L37) —
`_import_sistem_run_no_chdir()` / `_child_entry_setup()`: la pieza técnica clave de esta integración.
Cada función de entrada de un proceso hijo (`_entry_orchestrator`, `_entry_worker_a`, etc., líneas
56-92) hace `os.chdir()` + `sys.path.insert()` hacia `integradora/model_ia/sistem_IA` **dentro del
proceso hijo ya aislado** antes de importar `run.py` — así se reutilizan literalmente las funciones
`entry_orchestrator`, `entry_worker_a/b/c`, `entry_fusion`, `entry_reporter`, `entry_pred_fanout`,
`hard_shutdown` y `kill_residual_children` que ya existían en
[`integradora/model_ia/sistem_IA/run.py`](file:///home/jetson/integradora/model_ia/sistem_IA/run.py)
sin modificar ese archivo ni duplicar su lógica, y sin romper las rutas relativas de configuración
que esos módulos asumen.

[`bmo_unified/vision/engine.py:94-154`](file:///home/jetson/bmo_unified/vision/engine.py#L94) —
`class _QueuePump(QThread)`: drena las colas `qUI_frm`/`qUI_preds`/`qUI_stats` (las mismas que ya
construye `run.py`) y las re-emite como señales Qt (`frame_ready`, `pred_ready`, `stats_ready`) —
aquí es donde el frame BGR de OpenCV se convierte a `QImage` para pintarlo en un `QLabel`.

[`bmo_unified/vision/engine.py:171-255`](file:///home/jetson/bmo_unified/vision/engine.py#L171) —
`class VisionEngine(QObject)`: `start()` (línea 192) construye las colas y lanza los procesos
(idéntico a `run.py::main()` pero sin `UIViewer`); `stop()` (línea 255) reutiliza
`hard_shutdown()`/`kill_residual_children()` de `run.py` para garantizar cero procesos residuales.
**Carga perezosa confirmada en esta sesión:** TensorFlow/Torch no se importan en el proceso principal
de `bmo_app` hasta que se llama a `start()` (verificado con `sys.modules`).

#### Bug real encontrado y corregido: colisión del paquete `core` bajo `multiprocessing` con `spawn`

Al lanzar `bmo_app.py` por primera vez como script real (`python bmo_app.py`, vía `start_bmo.sh`) y
activar `EMO_TOGGLE`, los 7 procesos del motor de visión fallaron con
`ModuleNotFoundError: No module named 'core.messages'` (y `core.fusion`, `core.orchestrator`,
`core.reporter`, `core.pred_fanout`). Este error **no apareció en las pruebas anteriores de esta
misma sesión** porque esas pruebas se lanzaban con `python -c "..."` (sin archivo real), y el bug
solo se manifiesta cuando el script principal tiene un `__file__` real.

**Causa:** tanto `bmo_unified/` como `integradora/model_ia/sistem_IA/` tienen, cada uno, su propio
paquete llamado `core` (uno con `serial_manager.py`/`pecs_engine.py`/etc., el otro con
`messages.py`/`fusion.py`/etc.). `multiprocessing` con `start_method="spawn"` reconstruye cada
proceso hijo re-ejecutando el script principal (`bmo_app.py`) como `__mp_main__` para reconstituir
el estado de `__main__` antes de invocar la función objetivo real — esto vuelve a correr
[`bmo_unified/bmo_app.py:29`](file:///home/jetson/bmo_unified/bmo_app.py#L29)
(`sys.path.insert(0, BASE_DIR)`) **después** de que el proceso hijo ya había heredado un `sys.path`
con `sistem_IA` al frente, dejando `bmo_unified` de nuevo primero. La función
`_child_entry_setup()` solo insertaba `sistem_IA` "si no estaba ya" en `sys.path` — y como ya
estaba (solo que en segundo lugar), nunca lo volvía a poner primero, así que `from core.messages
import PredMsg` (dentro de `mod_a.py`) resolvía el paquete `core` equivocado.

**Corrección:** [`bmo_unified/vision/engine.py:37-50`](file:///home/jetson/bmo_unified/vision/engine.py#L37) —
nueva función `_ensure_sistem_path_first()`, usada tanto por `_import_sistem_run_no_chdir()` (línea
54) como por `_child_entry_setup()` (línea 62): en vez de "insertar solo si no está", siempre lo
**remueve y lo vuelve a insertar al frente** (`sys.path.remove(...)` + `sys.path.insert(0, ...)`),
sin importar dónde haya quedado tras el rearranque interno de `multiprocessing`. Adicionalmente, se
movió el import de `from ui.main_window import MainWindow` en
[`bmo_unified/bmo_app.py:82`](file:///home/jetson/bmo_unified/bmo_app.py#L82) de nivel de módulo a
dentro de `main()`, para que ese import (y con él, el paquete `core` de `bmo_unified`) nunca se
ejecute durante el rearranque `__mp_main__` de los hijos.

Verificado el fix reproduciendo el escenario exacto (script real, no `-c`) con un caso de prueba
aislado: motor de visión arrancando y entregando frames/predicciones sin errores.

### 4.6 `ui/main_window.py` — integración de todo

[`bmo_unified/ui/main_window.py:79-104`](file:///home/jetson/bmo_unified/ui/main_window.py#L79) —
`_wire_signals()`: conecta serie → router/pecs, pecs → panel PECS, visión → panel monitor, ghost
controls → acciones. Es el "cableado" central de toda la app.

[`bmo_unified/ui/main_window.py:115-130`](file:///home/jetson/bmo_unified/ui/main_window.py#L115) —
`_show_view()`: el panel de animación **nunca se oculta**; solo se muestran/ocultan los paneles que
se superponen encima (PECS, Monitor, calibración) — así se garantiza que la cara nunca quede en
negro al cambiar de vista.

[`bmo_unified/ui/main_window.py:157-165`](file:///home/jetson/bmo_unified/ui/main_window.py#L157) —
`_toggle_emotion_recognition()`: el único lugar donde se llama a `VisionEngine.start()`/`stop()` —
confirma que el reconocimiento solo corre bajo demanda (botón físico `EMO_TOGGLE`, botón fantasma
"Cámara", o botón "Detener reconocimiento" del panel).

[`bmo_unified/ui/main_window.py:175-179`](file:///home/jetson/bmo_unified/ui/main_window.py#L175) —
`mousePressEvent()`: cualquier toque en la ventana llama a `self._ghost.notify_touch()` — es el
gancho de "botones fantasma aparecen al tocar".

[`bmo_unified/ui/main_window.py:181-190`](file:///home/jetson/bmo_unified/ui/main_window.py#L181) —
`shutdown()`/`closeEvent()`: detiene `VisionEngine` (si estaba corriendo) y el hilo serie al cerrar
— sin importar si el cierre vino del botón fantasma "Cerrar" o de una señal del sistema operativo.

### 4.7 `ui/animation_player.py` — fondo siempre en bucle

[`bmo_unified/ui/animation_player.py:32`](file:///home/jetson/bmo_unified/ui/animation_player.py#L32) —
`setAspectRatioMode(Qt.KeepAspectRatioByExpanding)`: corrige un problema real encontrado en pruebas
— por defecto `QVideoWidget` deja barras negras laterales (pillarbox) si el video no calza
exactamente con 1024×600; este modo lo hace "cubrir" la pantalla completa (recorta el sobrante en
vez de dejar negro), tal como pide la sección 4.2.

[`bmo_unified/ui/animation_player.py:42`](file:///home/jetson/bmo_unified/ui/animation_player.py#L42) —
`QMediaPlaylist.CurrentItemInLoop`: bucle nativo del clip activo sin intervención de Python en cada
vuelta.

### 4.8 `bmo_app.py` — entrada y limpieza

[`bmo_unified/bmo_app.py:47-50`](file:///home/jetson/bmo_unified/bmo_app.py#L47) y
[`bmo_unified/bmo_app.py:90-91`](file:///home/jetson/bmo_unified/bmo_app.py#L90) — manejo de
`SIGINT`/`SIGTERM`: como Qt no puede ejecutar código Qt directamente dentro de un signal handler de
POSIX, se usa el patrón estándar de marcar una bandera y revisarla con un `QTimer` cada 200ms, que
entonces llama a `window.close()` (cierre ordenado vía `closeEvent`).

[`bmo_unified/bmo_app.py:52-75`](file:///home/jetson/bmo_unified/bmo_app.py#L52) —
`_courtesy_residual_check()`: usa `psutil` para listar los procesos hijos sobrevivientes tras
cerrar y advertir en el log si alguno de los procesos de visión (Orchestrator/WorkerA/B/C/Fusion/
Reporter/PredFanout) quedó vivo — el chequeo de cortesía pedido en la sección 7.2.

### 4.9 Entorno

[`integradora/model_ia/pruebas_mod/pyvenv.cfg`](file:///home/jetson/integradora/model_ia/pruebas_mod/pyvenv.cfg) —
se cambió `include-system-site-packages` de `false` a `true` para que el venv (que tiene
torch/torchvision/detectron2 compilados a mano, ver `PROGRESO_BMO_S06.md` sección 7.A) pueda ver el
`python3-pyqt5`/`python3-pyqt5.qtmultimedia` instalados vía `apt`, sin recompilar nada. Se instaló
`psutil` con `pip` dentro del venv. También se instaló `gstreamer1.0-libav` (faltaba el decodificador
H.264 que usa `QMediaPlayer` internamente — sin él, las animaciones no reproducían: se descubrió en
las pruebas de esta sesión).

Adicionalmente se descubrió y corrigió que **todo el árbol de `~/.platformio`** (binarios del venv de
PlatformIO y el toolchain `xtensa-esp32-elf`) había perdido el bit ejecutable — probablemente al
restaurar desde un backup/zip que no preservó permisos tras el reflasheo de `PROGRESO_BMO_S06.md`. Se
restauró con `chmod +x` para poder compilar el firmware.

---

## 5. Conexiones físicas (cableado de la ESP32)

Pinout final tal como quedó flasheado y verificado en la placa real (`featheresp32` / Adafruit
ESP32 Feather, ver [`integradora/Oraciones_interpret/platformio.ini`](file:///home/jetson/integradora/Oraciones_interpret/platformio.ini)).
Todos los botones usan el mismo cableado simple: **botón entre el GPIO y GND, sin resistencia
externa** (pull-up interno activado en firmware vía `INPUT_PULLUP`,
[`main.cpp:49`](file:///home/jetson/integradora/Oraciones_interpret/src/main.cpp#L49)).

| GPIO | Rol lógico (`button_map.json`) | Función física | Notas de cableado |
|---|---|---|---|
| 4  | `ANIM_PREV`    | D-pad IZQUIERDA (animación anterior) | Reasignado en esta sesión — ver abajo |
| 5  | `ANIM_NEXT`    | D-pad DERECHA (animación siguiente)  | Reasignado en esta sesión — ver abajo |
| 32 | `VIEW_NEXT`    | D-pad ARRIBA (vista siguiente)        | Pull-up interno, sin componentes extra |
| 33 | `VIEW_PREV`    | D-pad ABAJO (vista anterior)          | Pull-up interno, sin componentes extra |
| 25 | `PECS_SEND`    | Cluster de acción 1 (enviar frase)    | Pull-up interno, sin componentes extra |
| 26 | `PECS_DELETE`  | Cluster de acción 2 (borrar última palabra) | Pull-up interno, sin componentes extra |
| 27 | `PECS_CLEAR`   | Cluster de acción 3 (limpiar frase)   | Pull-up interno, sin componentes extra |
| 14 | `EMO_TOGGLE`   | Botón inferior largo central (activar/detener visión; mantener 5s = modo calibración) | Pull-up interno, sin componentes extra |
| 12 | `DYNAMIC_PLAY` | Botón inferior lateral izquierdo (reproducir dinámica) | Pull-up interno, sin componentes extra |
| 13 | `HOME`         | Botón inferior lateral derecho (volver a Cara) | Pull-up interno, sin componentes extra |
| 21 | — (`SS_PIN`)   | RFID RC522, pin SDA/SS  | SPI, no es un botón |
| 22 | — (`RST_PIN`)  | RFID RC522, pin RST    | SPI, no es un botón |
| 18 / 19 / 23 | — | RFID RC522, SCK / MISO / MOSI | Bus SPI por defecto del ESP32 (`SPI.begin()`), no configurable por software sin recablear |

### Por qué se reasignaron `ANIM_PREV`/`ANIM_NEXT` de GPIO 34/35 a GPIO 4/5

La tabla 3.2 original de `REQUERIMIENTOS_APP_MOODI.md` proponía GPIO 34 y 35 para estos dos roles.
Al flashear el primer firmware y probarlo en la placa real (sin nada todavía conectado a esos dos
pines), se detectaron eventos `BTN:34:DOWN`/`BTN:35:DOWN` espurios. Causa: en el ESP32 clásico, los
GPIO 34-39 son de **solo entrada** y **no tienen resistencia de pull interna** — a diferencia de
todos los demás GPIO del chip. Sin una resistencia pull-up externa a 3.3V cableada a mano en esos
dos pines, quedan flotando y el ruido eléctrico ambiente los hace leer aleatoriamente como
presionados.

Como se confirmó con el usuario que no se va a cablear esa resistencia externa, se reasignaron esos
dos roles a **GPIO 4 y GPIO 5** — ambos soportan `INPUT_PULLUP` interno igual que los otros 8 pines
de botón, así los 10 botones quedan con exactamente el mismo cableado (botón a GND, nada más). Se
verificó en la placa real tras el cambio: solo aparece `BOOT:OK` al reiniciar, sin disparos falsos.

**Importante para quien cablee la carcasa:** los botones físicos D-pad IZQUIERDA/DERECHA deben ir a
GPIO 4 y 5 (no 34/35 como decía el documento de requerimientos original). El resto de la tabla 3.2
no cambió.

---

## 6. Pruebas realizadas en esta sesión (Jetson real)

- **Serie:** `SerialManager` conectado de verdad a `/dev/ttyUSB0` con el ESP32 físico.
- **PECS end-to-end:** inyección de UIDs reales del vocabulario → apilado → `POST /ask` real a
  `ia_bridge` (puerto 5000) → `llama-server` real (Qwen2.5-1.5B, puerto 1234) → frase corregida
  recibida (`"YO QUIERO AGUA"` → `"Quiero agua."`) → stack limpiado. Tarjeta no registrada
  correctamente rechazada. Telegram sin configurar correctamente no rompe el flujo (solo advierte).
  `llama-server`/`ia_bridge` se apagaron al terminar la prueba para no dejar RAM ocupada.
- **Visión end-to-end:** `VisionEngine.start()` con cámara USB real, Módulos A, B y C inicializando
  y produciendo predicciones reales, Fusión combinándolas, Reporter generando reportes; `stop()`
  verificado para no dejar procesos residuales (`ps aux`) ni la cámara ocupada (`fuser /dev/video0`).
- **GUI completa:** capturas de pantalla reales (vía `ffmpeg -f x11grab` sobre la sesión X física)
  confirmando: cara animada a pantalla completa sin negros, panel PECS apareciendo automáticamente
  con chips + frase, controles fantasma visibles, panel de Monitor embebido (sin ninguna ventana
  externa) con badge de nivel de estrés.
- **Firmware:** compilado y **flasheado dos veces** a la ESP32 física con PlatformIO — primero con
  el mapeo GPIO 34/35 (descubrió los falsos disparos descritos en la sección 5), luego con la
  corrección a GPIO 4/5 (verificado limpio: solo `BOOT:OK` al reiniciar).
- **Lanzamiento real vía `start_bmo.sh`:** primer intento end-to-end con `python bmo_app.py` como
  script real (no `-c`) — reveló el bug de colisión del paquete `core` descrito en la sección 4.5
  (`EMO_TOGGLE` se activó solo, aparentemente por un botón físico presionado durante la prueba, y el
  motor de visión falló al arrancar). Corregido y **verificado en aislado** reproduciendo el mismo
  escenario de rearranque de `multiprocessing` con un script de prueba real — frames y predicciones
  llegando sin error. **Pendiente para el inicio de la próxima sesión:** relanzar `start_bmo.sh`
  completo (la app + `llama-server` + `ia_bridge`) para confirmar el fix en el flujo real de extremo
  a extremo; se detuvo la sesión antes de hacerlo a pedido del usuario.

---

## 7. Pendiente (fuera del alcance posible sin hardware/decisión del usuario, o dejado para el inicio de la próxima sesión)

- Relanzar `start_bmo.sh` completo y confirmar el fix de la sección 4.5 en el flujo real (primera
  tarea de la próxima sesión).
- Verificar los 10 botones reales contra el modo calibración (`--calibrate`) con la carcasa armada.
- Pruebas con tarjetas RFID físicas y toques reales en la pantalla Eleclab.
- Completar `bmo_unified/config/telegram.json` con el token/chat_id reales del bot.

---

## 8. Recomendaciones para la siguiente iteración de la app

Estas son sugerencias de mejora identificadas con el usuario al cierre de esta sesión, **aún no
implementadas** — quedan para una próxima sesión de trabajo:

1. **Controles fantasma más inmersivos.** Actualmente `ghost_controls.py` usa `QPushButton`
   estándar con fondo semitransparente plano. Se propone rediseñarlos con un estilo más "animado":
   iconografía propia, transparencia variable según interacción (hover/press), y desplegarlos desde
   una **barra deslizante** (slide-up/slide-in) que en sí misma sea fantasma (también invisible
   hasta el toque, en vez de un `QWidget` que solo cambia de opacidad en el lugar) — para que la
   aparición se sienta como parte de la animación y no como una superposición de UI clásica.
2. **Mostrar en pantalla lo que detectan los Módulos A, B y C en vivo**, no solo el nivel de estrés
   fusionado. Hoy `vision/engine.py` ya emite `pred_ready(dict)` con cada `PredMsg` individual de
   A/B/C (módulo, etiqueta, confianza, calidad) — ver
   [`bmo_unified/vision/engine.py` líneas 156-160](file:///home/jetson/bmo_unified/vision/engine.py#L156)
   y [`emo_monitor_panel.py`](file:///home/jetson/bmo_unified/ui/emo_monitor_panel.py) — pero
   `MainWindow` todavía no conecta esa señal a ningún elemento visual; solo se usa `stats_ready`
   (el resumen fusionado). Falta agregar, dentro del panel de Monitor, tres indicadores (uno por
   módulo) que se actualicen con cada `pred_ready` para mostrar la etiqueta/confianza cruda de cada
   uno en tiempo real.
3. **Persistencia del reporte de cada sesión de detección en una carpeta designada.** El motor ya
   escribe automáticamente a
   [`integradora/model_ia/sistem_IA/resultados/estres_resumen.jsonl`](file:///home/jetson/integradora/model_ia/sistem_IA/resultados/estres_resumen.jsonl)
   vía `core/reporter.py` (configurado en `runtime.yaml`, clave `reporter.output_jsonl`) — eso ya
   cumple "se guarda un reporte cada vez que se activa el sistema", pero hoy es un único archivo que
   se va acumulando indefinidamente entre todas las sesiones. Se recomienda que `VisionEngine.start()`
   genere un nombre de archivo nuevo por sesión (por ejemplo con timestamp,
   `resultados/sesion_2026-06-22_07-15.jsonl`) para que cada activación quede como un reporte
   independiente y fácil de ubicar/exportar para el informe.

Estos tres puntos se abordarán en una próxima sesión, según lo indicado por el usuario.
