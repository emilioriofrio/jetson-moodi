# Requerimientos de la Aplicación — Moodi (BMO OS)

**Proyecto:** Moodi / BMO Unified — Robot asistencial de comunicación aumentativa (SAAC/PECS) y monitoreo emocional para niños con TEA.
**Plataforma objetivo:** NVIDIA Jetson Orin Nano (ARM64, Jetpack 6.x, 8 GB RAM unificada).
**Pantalla:** Eleclab 7" táctil, 1024x600.
**Stack:** Python puro + PyQt5 (GUI) + Flask (puente LLM local). **No es ROS.**
**Destinatario de este documento:** agente de terminal (Antigravity CLI / Claude CLI) que desarrollará `bmo_app.py` y módulos asociados.

> Este documento describe **qué** debe hacer y **cómo** debe lucir y comportarse la aplicación ejecutable que orquesta todo el sistema. Está escrito para que el agente desarrolle con total claridad, sin ambigüedad de interfaz ni de ciclo de vida de procesos.

---

## 0. Restricciones críticas de seguridad (LEER PRIMERO — NO NEGOCIABLE)

Estas reglas existen porque un manejo previo de la cámara **corrompió el cargador de arranque (boot) de la Jetson y obligó a reflashear el equipo, perdiendo todo el proyecto.** El cumplimiento de esta sección tiene prioridad sobre cualquier otra funcionalidad.

1. **La aplicación NUNCA debe escribir, modificar, mover ni borrar archivos del cargador de arranque ni del árbol de dispositivos.** Quedan terminantemente prohibidos para esta app:
   - `/boot/extlinux/extlinux.conf`
   - Cualquier archivo bajo `/boot/`
   - Overlays de Device Tree (`.dtbo`), `runtime.yaml` con cambios de overlay de cámara CSI, o cualquier configuración de kernel/Tegra.
   - Los scripts tipo `enable_csi_camera.sh` / `enable_usb_camera.sh` **no deben ser invocados por la app bajo ninguna circunstancia.**
2. **La cámara se accede EXCLUSIVAMENTE como cámara USB vía espacio de usuario:** OpenCV/V4L2 sobre `/dev/video0`. Nada de habilitar CSI, nada de probes de I2C, nada de drivers de sensor.
3. **La app no debe ejecutarse con `sudo` ni elevar privilegios.** El acceso al puerto serie ya se resuelve con permisos de usuario (`chmod 666 /dev/ttyUSB0` se hace fuera de la app, en el lanzador). La app solo abre el dispositivo, no cambia permisos del sistema.
4. **Prohibido cualquier comando destructivo del sistema operativo** (formateo, escritura en `/etc`, `/boot`, particiones, etc.) dentro del código de la app o de sus scripts auxiliares.
5. Antes de tocar cualquier ruta del sistema, el agente debe preguntarse: *"¿esto puede afectar el arranque?"* Si la respuesta no es un "no" rotundo, **no se hace.**

---

## 1. Propósito y comportamiento general del sistema

Moodi es un robot con cara animada en pantalla que cumple dos funciones principales:

1. **Comunicación aumentativa PECS-RFID:** el niño acerca tarjetas RFID (cada una = una palabra/pictograma). El sistema apila las palabras, forma una frase, la corrige gramaticalmente con un LLM local y la envía por Telegram al cuidador.
2. **Monitoreo emocional avanzado (bajo demanda):** mediante cámara y tres módulos de IA (facial, gestual y de patrones de movimiento) se estima un nivel global de estrés del niño en tiempo real. **Este sistema NO corre por defecto**; solo se activa cuando el cuidador presiona el botón correspondiente.

La aplicación `bmo_app.py` es el "sistema operativo" del robot: arranca en pantalla completa, muestra la cara animada, escucha el hardware (botones y RFID vía ESP32), y orquesta los subsistemas.

---

## 2. Arquitectura de software y componentes

| Componente | Rol | Puerto / Ruta |
|---|---|---|
| `bmo_app.py` (PyQt5) | App principal / GUI / orquestador | — |
| `llama-server` | Servidor LLM local (DeepSeek-R1-0528-Qwen3-8B Q4_K_M GGUF) | `:1234` |
| `apps/llm/ia_bridge.py` (Flask + gunicorn) | Puente que recibe frase bruta y devuelve frase corregida | `:5000` |
| `integradora/model_ia/sistem_IA/run.py` | Motor de visión (Módulos A, B, C + fusionador) | subproceso |
| Firmware ESP32 (`main.cpp`) | Lee 10 botones + RFID RC522, envía eventos por serie | `/dev/ttyUSB0` |
| `rfid_vocab.json` | Diccionario UID → palabra en español | archivo |
| `runtime.yaml` | Config del motor de IA (resolución, rutas) — **sin overlays de boot** | archivo |
| `start_bmo.sh` | Lanzador único del ecosistema | script |

**Flujo de comunicación:** ESP32 → (serie `/dev/ttyUSB0`) → `bmo_app.py` → (HTTP/JSON) → `ia_bridge.py` (`:5000`) → `llama-server` (`:1234`) → respuesta → `bmo_app.py` → Telegram Bot API.

---

## 3. Hardware y mapeo físico

### 3.1 Periféricos
- Pantalla táctil Eleclab 7" (1024x600), HDMI→DisplayPort, touch por USB.
- Cámara USB en `/dev/video0`.
- ESP32 en `/dev/ttyUSB0` (con autodetección/reconexión: escanear `/dev/ttyUSB*` y `/dev/ttyACM*`).
- Lector RFID RC522 conectado al ESP32 por SPI.
- 10 pulsadores conectados al ESP32 (GPIO34, 35, 32, 33, 25, 26, 27, 14, 12, 13).

### 3.2 Distribución física de los 10 botones en la carcasa
- **4 botones direccionales (forma de cursor):** ARRIBA, ABAJO, IZQUIERDA, DERECHA (D-pad).
- **3 botones agrupados** (juntos entre sí, sin configuración previa aparente): cluster de acción.
- **3 botones inferiores:** uno **largo central** y dos laterales (izquierdo y derecho).

> La correspondencia exacta GPIO ↔ posición física debe determinarse con un **modo calibración** (ver 3.4). La app trabaja con **roles lógicos**, no con GPIOs hardcodeados.

### 3.3 Asignación de funcionalidades a los botones

| Grupo | Botón físico | Rol lógico | Acción |
|---|---|---|---|
| D-pad | **IZQUIERDA** | `ANIM_PREV` | Animación/emoción anterior de la cara |
| D-pad | **DERECHA** | `ANIM_NEXT` | Animación/emoción siguiente de la cara |
| D-pad | **ARRIBA** | `VIEW_NEXT` | Cambiar a la siguiente vista (Cara → PECS → Monitor) |
| D-pad | **ABAJO** | `VIEW_PREV` | Cambiar a la vista anterior |
| Cluster 3 | **Acción 1** | `PECS_SEND` | Confirmar y enviar la frase (dispara LLM + Telegram) |
| Cluster 3 | **Acción 2** | `PECS_DELETE` | Borrar la última palabra apilada |
| Cluster 3 | **Acción 3** | `PECS_CLEAR` | Limpiar toda la frase (reset del stack) |
| Inferior | **Largo central** | `EMO_TOGGLE` | Activar / Detener el reconocimiento avanzado de emociones |
| Inferior | **Lateral izq.** | `DYNAMIC_PLAY` | Reproducir la "dinámica" actual (animación + audio asociado) |
| Inferior | **Lateral der.** | `HOME` | Volver a la vista Cara (estado de reposo) |

> El agente tiene libertad para ajustar esta asignación si encuentra una distribución más ergonómica, pero **debe mantenerse:** (a) el botón **largo central** como el activador/desactivador del reconocimiento emocional (es el botón "el robot está listo para ejecutar una dinámica"), (b) las acciones PECS en el cluster de 3, y (c) la navegación en el D-pad. Toda asignación debe quedar centralizada en un único mapa configurable (`button_map.json` o constante en código), nunca dispersa.

### 3.4 Modo calibración de botones
Incluir un modo accesible (p. ej. mantener presionado el botón largo central 5 s, o un flag de arranque `--calibrate`) que muestre en pantalla qué GPIO/evento llega al presionar cada botón, para mapear sin adivinar. Debe poder salir del modo sin reiniciar.

---

## 4. Requisitos de interfaz (UI/UX)

### 4.1 Ventana y modo de presentación
- **Pantalla completa, sin bordes, sin barra de título** (modo inmersivo), fija a 1024x600.
- Optimizada para toque con dedos (targets grandes, ≥ 64 px).
- Cursor del ratón oculto en operación normal.

### 4.2 Animaciones de fondo SIEMPRE corriendo
- Mientras la **cara del robot ocupe la pantalla**, las animaciones faciales (videos `.mp4` de `~/integradora/animaciones`) deben **reproducirse en bucle continuo de fondo, todo el tiempo**, nunca en pausa ni en negro.
- La animación es el fondo dinámico; todo lo demás (botones, paneles) se superpone encima.
- El cambio de animación (con D-pad o `DYNAMIC_PLAY`) debe ser fluido, sin parpadeos ni ventana negra entre clips.
- Recomendación técnica: usar un reproductor embebido (QMediaPlayer sobre QVideoWidget, o renderizado por frames con QTimer) que haga *loop* nativo; precargar el siguiente clip para evitar cortes.

### 4.3 Botones fantasma (ghost controls)
- Los controles táctiles en pantalla son **invisibles por defecto.**
- **Aparecen solo al tocar la pantalla** y se **ocultan automáticamente tras 5 segundos de inactividad** (sin toques).
- Conjunto mínimo de botones fantasma: **Borrar, Enviar, Cámara/Reconocimiento, Siguiente Cara, Cerrar.** (Reflejan las acciones físicas para operación táctil alternativa.)
- Deben aparecer con un fundido suave (no aparición brusca) y no tapar la cara de forma permanente.

### 4.4 Vistas / paneles de la aplicación
La app tiene tres vistas, todas sobre el fondo animado. Se navega con D-pad (`VIEW_NEXT`/`VIEW_PREV`), con `HOME`, o tocando los botones fantasma.

1. **Vista Cara (Home / reposo, por defecto):** cara animada a pantalla completa + botones fantasma. Es el estado natural del robot.
2. **Sub-interfaz PECS (apilado de palabras):** ver sección 5.
3. **Panel Monitor Emocional (cámara embebida + reconocimiento):** ver sección 6.

---

## 5. Sistema PECS-RFID (apilado de palabras + Telegram)

### 5.1 Lectura y apilado
- El ESP32 lee tarjetas RFID (RC522) y envía el **UID** por serie a la app.
- La app traduce el UID a palabra en español usando **`rfid_vocab.json`** (cargado automáticamente al inicio).
- Cada palabra leída se **apila** (stack) en orden de lectura, formando progresivamente una frase bruta (ej. `PAPÁ ESTOY LISTO COLEGIO`).
- **Tarjeta no registrada:** no se apila; se muestra aviso visual breve ("tarjeta no reconocida") y **se bloquea** su inclusión. No debe romper el flujo.

### 5.2 Sub-interfaz de visualización (requisito explícito)
- Debe existir una **sub-interfaz en pantalla** (panel deslizable inferior o lateral) donde se **muestren en vivo:**
  - **Las palabras que se van apilando**, como "chips"/etiquetas en secuencia, en el orden leído.
  - **La frase que se formó** (texto de la frase bruta acumulada).
  - Tras el envío, **la frase corregida** que devolvió el LLM (para retroalimentación visual).
- Esta sub-interfaz se puede mostrar automáticamente al detectar la primera tarjeta y permanecer visible durante la construcción de la frase. La cara animada sigue de fondo.

### 5.3 Envío (proceso en segundo plano)
Al presionar `PECS_SEND` (botón físico) o el botón fantasma "Enviar":
1. La frase bruta se envía por **HTTP/JSON** al puente `ia_bridge.py` (`http://127.0.0.1:5000/ask`).
2. El puente consulta el `llama-server` (`:1234`), que **reordena, corrige y completa** la oración (ej. `"Papá, estoy listo para ir al colegio."`).
3. **Por detrás**, la app envía la frase corregida por **Telegram** al chat del cuidador (Bot API).
4. El stack se limpia tras un envío exitoso; mostrar confirmación visual.

> **Nota de latencia:** el ciclo completo tarda ~20–29 s (latencia del LLM). La UI **no debe congelarse**: el envío corre en un hilo aparte (thread-safe respecto a PyQt) con un indicador de "procesando…". `PECS_DELETE` borra la última palabra; `PECS_CLEAR` vacía todo el stack.

### 5.4 Configuración Telegram
- Token del bot y `chat_id` del cuidador en un archivo de configuración (`config/telegram.json` o variables de entorno), **nunca hardcodeados** en el código fuente.

---

## 6. Sistema de reconocimiento avanzado de emociones (bajo demanda)

### 6.1 Activación bajo demanda (requisito explícito)
- El reconocimiento **NO corre al arrancar la app.** Permanece apagado.
- Solo se inicia cuando se presiona **`EMO_TOGGLE`** (botón largo central) o el botón fantasma de cámara/reconocimiento. Ese gesto significa: *"el robot está listo para ejecutar una dinámica y empezar a monitorear."*
- Razón técnica adicional (memoria): el `llama-server` ya ocupa ~5 GB de los 8 GB de RAM unificada. Cargar los modelos de visión solo bajo demanda evita el conflicto de memoria (`ENOMEM`/`nvmap`) que congela el sistema. Por ello:
  - **Carga perezosa (lazy load):** importar e inicializar TensorFlow/DeepFace/MediaPipe/Detectron2 **solo al activar** el reconocimiento, no al inicio.
  - Aplicar `TF_FORCE_GPU_ALLOW_GROWTH=true` y/o `CUDA_VISIBLE_DEVICES=-1` para la visión, para no colisionar con la GPU que usa el LLM.

### 6.2 Panel de video EMBEBIDO (requisito explícito — NADA de ventanas emergentes)
- Al activar el reconocimiento, se abre una **pestaña/panel DENTRO de la aplicación** que muestra la salida de la cámara y del reconocimiento. **No** debe abrirse ninguna ventana externa por encima de la interfaz.
- **Prohibido `cv2.imshow()`** o cualquier ventana de OpenCV/Qt independiente. Los frames de la cámara se convierten a `QImage`/`QPixmap` y se pintan dentro de un `QWidget`/`QLabel` del panel embebido.
- El panel embebido muestra:
  - El **feed de video** con los overlays del reconocimiento (recuadro del rostro, probabilidades emocionales, gesto detectado, actividad de flujo óptico).
  - El **nivel global de estrés** fusionado, con su color: **Bajo (verde), Medio (amarillo), Alto (rojo), Inseguro (gris).**
- La captura + inferencia corren en un **hilo dedicado (QThread)** que emite frames por señales hacia el hilo de UI (nunca tocar widgets desde el hilo de trabajo).

### 6.3 Lógica de los módulos (referencia para overlays e integración)
| Módulo | Qué hace | Peso | Mapeo a escala [0–3] |
|---|---|---|---|
| **A — Facial** | DeepFace + (MediaPipe/MTCNN), 7 emociones; corre por *ticks* (intervalos) | 0.25 | Neutro/Alegría=0; Sorpresa/Asco=1; Miedo=2; Tristeza/Enojo=3 |
| **B — Gestos** | MediaPipe Holistic + BiLSTM; ventana (19, 225) | 0.35 | Neutral/Otros=0; Dedos en boca=1; Juego de manos=2; Manos en cabeza/rostro=3 |
| **C — Movimiento** | Flujo óptico + CNN/BiLSTM; secuencias de n frames, 4 clases | 0.40 | Calma/Leve=0; Repetitivo=2; Agitación=3 |
| **Fusión** | Promedio ponderado por calidad q∈[0,1] + histéresis; umbrales 0.75 y 1.75 → etiqueta discreta | — | 0=Bajo, …, 3=Alto |

> El motor de visión vive en `integradora/model_ia/sistem_IA/run.py`. La app puede ejecutarlo como **subproceso** y consumir su salida, o integrarlo en proceso; en ambos casos **la salida de video se renderiza embebida** (ver 6.2) y el subproceso debe ser **rastreable y terminable** (ver sección 7).

### 6.4 Detención limpia (requisito explícito — sin procesos residuales)
- Debe existir un botón **"Detener reconocimiento"** (y el mismo `EMO_TOGGLE` físico) que:
  1. Detiene el hilo de captura/inferencia.
  2. **Libera la cámara** (`VideoCapture.release()`), garantizando que `/dev/video0` queda libre.
  3. **Termina el subproceso `run.py`** y todos sus hijos (ver 7), de modo que **no queden procesos residuales en segundo plano** ni hilos vivos ni memoria de modelos retenida.
  4. Cierra el panel embebido y regresa a la vista Cara.
- Tras detener, el sistema vuelve al estado de reposo: la app sigue corriendo solo con cara animada + PECS, **sin** consumo de los modelos de visión.

---

## 7. Gestión de procesos y ciclo de vida (sin residuales)

### 7.1 Arranque
- `start_bmo.sh` (lanzador, fuera de la app) levanta en segundo plano si no están activos: `llama-server` (`:1234`), `ia_bridge.py` (`:5000`); habilita permisos del serie; lanza `bmo_app.py`. La app **no** modifica configuraciones de sistema.

### 7.2 Rastreo y terminación
- Todo subproceso lanzado por la app (p. ej. `run.py`) debe iniciarse en su **propio grupo de procesos** (`start_new_session=True` / `os.setsid`) para poder terminar al grupo completo.
- Mantener referencias (PIDs / objetos `Popen`) de cada subproceso e hilo.
- **Al detener reconocimiento:** terminar el grupo del motor de visión (SIGTERM → espera corta → SIGKILL si persiste), `join()` de hilos, `release()` de cámara.
- **Al cerrar la app** (botón Cerrar fantasma o cierre de ventana): "matado" ordenado de **todo** lo que la app haya iniciado: `run.py`, hilos de cámara y serie y, si la app los gestionó, `ia_bridge.py` y `llama-server`. Usar grupos de procesos (`os.killpg`) y/o `psutil` para no dejar huérfanos.
- **Verificación:** tras cerrar, no debe quedar ningún proceso Python de visión, ni `llama-server` huérfano lanzado por la app, ni la cámara ocupada. (Útil añadir un chequeo de cortesía con `psutil` que registre advertencia si algo sobrevive.)
- Manejo de señales (`SIGINT`/`SIGTERM`) para limpieza también cuando se cierra desde terminal.

---

## 8. Conexión serie (robustez)
- **Autodetección y reconexión** del ESP32: escanear `/dev/ttyUSB*` y `/dev/ttyACM*`; reconectar automáticamente si se cae el enlace, sin tumbar la UI.
- Parseo robusto de los mensajes del firmware (eventos de botón y UIDs RFID); ignorar tramas corruptas sin crashear.

---

## 9. Requisitos no funcionales
- **Estabilidad de memoria:** nunca cargar visión + LLM de forma que se exceda la RAM (ver 6.1). Visión solo bajo demanda.
- **UI no bloqueante:** toda E/S lenta (HTTP al LLM, Telegram, serie, inferencia) corre fuera del hilo de UI; PyQt solo se toca desde el hilo principal.
- **Robustez:** una tarjeta no registrada, una desconexión de cámara o de ESP32, o un timeout del LLM **no deben crashear** la app; se muestran como avisos y el sistema sigue.
- **Sin secretos en el código:** token de Telegram, chat_id y rutas configurables en archivos de config o variables de entorno.
- **Logs:** registro a archivo de eventos clave (lecturas RFID, envíos, activación/detención de visión, errores) para depuración.

---

## 10. Estructura de archivos sugerida
```
/home/jetson/
├── bmo_unified/
│   ├── bmo_app.py            # App principal (PyQt5, orquestador)
│   ├── ui/                   # Vistas: cara, pecs_panel, emo_monitor, ghost_controls
│   ├── core/                 # serial_manager, rfid_vocab, telegram_sender, process_manager
│   ├── vision/               # wrapper de lanzamiento/consumo de run.py + render embebido
│   ├── config/
│   │   ├── button_map.json
│   │   ├── telegram.json     # token + chat_id (NO versionar)
│   │   └── rfid_vocab.json
│   └── start_bmo.sh
├── integradora/model_ia/sistem_IA/run.py   # motor de visión (A, B, C, fusión)
└── apps/llm/ia_bridge.py                    # puente Flask :5000
```

---

## 11. Criterios de aceptación (checklist para el agente)

**Interfaz**
- [ ] Arranca en pantalla completa sin bordes a 1024x600.
- [ ] La cara animada corre en bucle de fondo de forma continua, sin negros ni cortes.
- [ ] Los botones en pantalla son fantasma: aparecen al tocar, se ocultan tras 5 s de inactividad.
- [ ] Tres vistas navegables: Cara, PECS, Monitor emocional.

**Botones físicos**
- [ ] Los 10 botones del ESP32 están mapeados a roles lógicos según la tabla 3.3 (o equivalente ergonómico justificado), centralizados en un solo mapa.
- [ ] Existe modo calibración para verificar la correspondencia GPIO ↔ posición.

**PECS-RFID**
- [ ] Las tarjetas RFID se traducen con `rfid_vocab.json` y se apilan en orden.
- [ ] Una sub-interfaz muestra en vivo las palabras apiladas y la frase formada (y la frase corregida tras enviar).
- [ ] El envío dispara LLM (`:5000`→`:1234`) y luego Telegram, en segundo plano, sin congelar la UI.
- [ ] Tarjeta no registrada se bloquea sin romper el flujo.

**Reconocimiento emocional**
- [ ] El reconocimiento NO corre hasta presionar el botón activador (`EMO_TOGGLE`).
- [ ] El video y el reconocimiento se muestran EMBEBIDOS en un panel de la app; no se abre ninguna ventana emergente externa (sin `cv2.imshow`).
- [ ] Se muestra el nivel global de estrés con su color (verde/amarillo/rojo/gris).
- [ ] "Detener reconocimiento" libera la cámara, termina el subproceso y sus hilos, y **no deja procesos residuales**.
- [ ] Modelos de visión con carga perezosa y `TF_FORCE_GPU_ALLOW_GROWTH`/`CUDA_VISIBLE_DEVICES=-1` para no colisionar con el LLM.

**Ciclo de vida**
- [ ] Subprocesos lanzados en su propio grupo; terminación con SIGTERM→SIGKILL.
- [ ] Al cerrar la app se terminan todos los procesos/hilos que inició; cámara liberada; sin huérfanos.

**Seguridad (sección 0)**
- [ ] La app NO escribe ni toca `/boot`, `extlinux.conf`, overlays ni scripts de cámara CSI.
- [ ] La cámara se usa solo como USB vía OpenCV/V4L2 sobre `/dev/video0`.
- [ ] La app no se ejecuta con sudo ni eleva privilegios.
```
