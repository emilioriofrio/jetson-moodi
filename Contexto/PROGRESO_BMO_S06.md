# Reporte de Progreso y Recuperación - Proyecto BMO Unified (Moodi)
**Semana:** S06 (Mediados de Junio, 2026)
**Archivo:** PROGRESO_BMO_S06.md
**Ubicación:** `/home/jetson/`

---

## 1. Antecedentes: Pérdida de Progreso y Reflasheo
Debido a una corrupción crítica en un archivo del cargador de arranque (`boot`), la NVIDIA Jetson Orin Nano no podía iniciar. Se tuvo que realizar un flasheo completo del sistema operativo desde cero con Jetpack 6.x. Esto eliminó todas las instalaciones previas y dependencias compiladas. 

A partir de esta situación, se iniciaron las labores de puesta en marcha y reconstrucción del entorno para el robot BMO Moodi.

---

## 2. Reconstrucción y Corrección del Entorno de Software

### A. Restauración del Entorno Virtual (Venv)
* Se reactivó y configuró el entorno virtual en `/home/jetson/integradora/model_ia/pruebas_mod` para ejecutar la lógica de inteligencia artificial y control.
* Se instaló `pyserial` en este entorno para permitir la lectura del puerto serie.

### B. Solución a Fallos Críticos de Librerías CUDA/cuDNN
Al intentar usar OpenCV y levantar el servidor local de LLM, el sistema fallaba debido a la ausencia de las librerías dinámicas de NVIDIA en el sistema operativo base de Jetpack (un problema muy común tras un flasheo limpio). Se solucionó instalando manualmente los componentes de tiempo de ejecución sin necesidad de descargar el pesado SDK de desarrollo completo:
* **CUDA Runtime:** Instalación de `cuda-cudart-12-6` (proporciona `libcudart.so.12`) y `libnpp-12-6` (proporciona `libnppc.so.12`).
* **Librerías Matemáticas:** Instalación de `libcublas-12-6`, `libcufft-12-6`, `libcurand-12-6`, `libcusolver-12-6` y `libcusparse-12-6`.
* **Deep Learning (cuDNN):** Instalación de `libcudnn9-cuda-12` (proporciona `libcudnn.so.9`).

Gracias a esto, OpenCV y el servidor de IA ahora cargan y se ejecutan a máxima velocidad utilizando la aceleración de la GPU.

---

## 3. Estado de los Componentes y Puesta en Marcha

### A. Servidor LLM Local (Llama Server)
* Se configuró y validó la inicialización del modelo DeepSeek-R1 (`DeepSeek-R1-0528-Qwen3-8B-Q4_K_M.gguf`) offloadeado al 100% en la GPU del Jetson.
* Escucha en el puerto `1234` mediante `llama-server`.

### B. Puente de Traducción / Lógica SAAC (Flask Bridge)
* El script `ia_bridge.py` (en `/home/jetson/apps/llm/`) se configuró para ejecutarse mediante `gunicorn` en el puerto `5000`.
* **Prueba Funcional:** Se envió la frase `"quiero manzana comer niño"` y el puente retornó la corrección gramatical de forma exitosa en milisegundos: `"Quieres comer una manzana, niño."` a través del modelo.

### C. Conexión Serial con la ESP32
* El microcontrolador (lector RFID y botones) es detectado en `/dev/ttyUSB0`.
* Se configuraron permisos de lectura y escritura (`chmod 666 /dev/ttyUSB0`) para que cualquier script de Python del usuario pueda abrir el puerto serie sin necesitar `sudo`.

---

## 4. Diagnóstico e Instalación de Cámara
* **Cámara CSI (Fallo de Compatibilidad):** Se intentó habilitar la cámara CSI. Sin embargo, el arranque arrojaba `error during i2c read probe (-121)`. Se diagnosticó que la cámara física instalada (Raspberry Pi Zero W 5MP) usa el sensor **OmniVision OV5647**. Jetpack 6.x en Jetson Orin Nano solo soporta de forma nativa los sensores IMX219 e IMX477. El OV5647 no es compatible con el driver IMX219 y por tanto no responde al probe I2C.
* **Cámara USB (Solución Aplicada):** Se recomendó y procedió a conectar una cámara USB (webcam estándar).
  * **Conexión y Detección:** Se conectó en caliente (*hot-plug*) y fue inmediatamente reconocida en `/dev/video0` e `/dev/video1` (ID `1d6c:0103 webcam webcam`).
  * **Configuración:** Se ejecutó con éxito el script para configurar la cámara USB (`enable_usb_camera.sh`), modificando la ruta y la resolución a `960x720` en `runtime.yaml`.
  * **Verificación:** Se ejecutó un script de prueba de captura de frames mediante OpenCV, logrando capturar y guardar una imagen real a resolución completa (`1920x1080`) sin ningún tipo de fallo en la carga de CUDA o dependencias de OpenCV. El canal de visión queda 100% operativo.

---

## 5. Lanzador del Sistema Unificado
Se creó el script unificado `start_bmo.sh` en el home del usuario (`/home/jetson/start_bmo.sh`). 

Este script automatiza el arranque completo del ecosistema al inicializar la aplicación:
1. Levanta `llama-server` (puerto 1234) en segundo plano si no está activo.
2. Levanta el puente `ia_bridge` (puerto 5000) en segundo plano si no está activo.
3. Habilita los permisos del puerto serie `/dev/ttyUSB0`.
4. Ejecuta la aplicación de visión por computadora (`run.py`) de la carpeta `sistem_IA` usando el entorno virtual configurado.

---

## 6. Diagnóstico del Conflicto de Memoria Crítica (RAM/GPU) y Opciones de Solución

Para analizar este estado junto con el documento de la tesis, se detalla a continuación el cuello de botella técnico actual del sistema:

### A. Anatomía del Hardware y Consumo de Recursos
* **Dispositivo:** NVIDIA Jetson Orin Nano (8 GB de RAM unificada compartida entre CPU y GPU).
* **Consumo Base (Sistema Operativo + Desktop GUI de Ubuntu):** ~1.5 GB - 2.0 GB de RAM.
* **Modelo LLM (`llama-server` con DeepSeek-R1-8B Q4 GGUF):** Requiere **~4.8 GB - 5.0 GB de RAM** permanentes al estar cargado.
* **Sistema de Visión (`run.py` con Módulos A y B):** TensorFlow/Keras + MediaPipe + DeepFace. Requiere RAM para inicializar los modelos de detección de rostros y emociones.

### B. El Conflicto Técnico (Causa del Crash/Congelamiento)
1. Al arrancar el ecosistema completo con el script unificado, el uso de RAM total del sistema se sitúa inmediatamente al límite físico (~6.8 GB a 7.0 GB consumidos).
2. **Comportamiento por defecto de TensorFlow:** Al detectar una GPU compatible (gracias a la restauración de CUDA y cuDNN base), TensorFlow intenta reservar el **100% de la memoria gráfica libre** de forma predeterminada para optimizar sus buffers de inferencia.
3. **Fallo del Driver de NVIDIA (`nvmap`):** Dado que la GPU y el CPU comparten la misma RAM física y `llama-server` ya tiene ocupados ~5 GB, el driver gráfico del kernel de Tegra arroja un error crítico de asignación:
   ```text
   NvMapMemAllocInternalTagged: 1075072515 error 12 (ENOMEM - Out of Memory)
   ```
4. **Thrashing y Congelamiento:** El sistema operativo, al quedarse sin RAM física disponible, entra en estado de intercambio continuo con la memoria Swap virtual (ZRAM comprimida en RAM, la cual no puede crear espacio físico real). El promedio de carga del CPU se dispara a niveles críticos (**33.68**), paralizando por completo la interfaz gráfica y crasheando el proceso Python.

### C. Análisis de la Configuración de Red e IP (Discrepancia)
* En el firmware de la ESP32 ([main.cpp](file:///home/jetson/integradora/Oraciones_interpret/src/main.cpp#L14)), la URL de comunicación apunta a:
  `SERVER_URL = "http://192.168.10.140:5000/ask"`
* La IP actual del Jetson en la red es `192.168.100.116`. 
* Dado que el usuario confirma que **nunca configuró un dispositivo externo** para correr el LLM y que el sistema funcionaba lento pero ejecutaba en su red anterior, esto confirma que **la Jetson tenía la IP `192.168.10.140` en la red antigua** y que el modelo sí corría de manera 100% local, operando en el límite absoluto de la capacidad de hardware del Orin Nano de 8GB.

### D. Soluciones Técnicas Propuestas para Comparar con la Tesis

Para mitigar el crash y estabilizar el sistema local, se han identificado las siguientes vías de acción:

* **Vía A: Crecimiento Dinámico de TensorFlow en GPU**
  * **Acción:** Declarar la variable de entorno `os.environ['TF_FORCE_GPU_ALLOW_GROWTH'] = 'true'` en [run.py](file:///home/jetson/integradora/model_ia/sistem_IA/run.py) antes de inicializar TensorFlow.
  * **Efecto:** Evita que TensorFlow intente reservar el 100% de la GPU restante, limitando su consumo al mínimo necesario (~800 MB). El LLM local (8B) seguirá consumiendo ~5 GB.
* **Vía B: Forzar Inferencia del Modelo de Visión en CPU**
  * **Acción:** Configurar `CUDA_VISIBLE_DEVICES = "-1"` en [mod_a.py](file:///home/jetson/integradora/model_ia/sistem_IA/modules/mod_a.py).
  * **Efecto:** Desvincula por completo a TensorFlow de la GPU, eliminando la colisión del asignador `nvmap`. MediaPipe (el extractor facial) corre sumamente rápido en CPU, por lo que el impacto en FPS es mínimo y el sistema no crasheará por ENOMEM. El LLM local (8B) conserva el uso exclusivo de la GPU.
* **Vía C: Redimensionamiento del Modelo LLM (Reducción de RAM)**
  * **Acción:** Reemplazar el modelo DeepSeek-R1 8B por una versión optimizada y más ligera como **DeepSeek-R1 1.5B** (~1.1 GB de RAM) o **3B** (~2.2 GB de RAM).
  * **Efecto:** Libera entre 2.8 GB y 3.9 GB de RAM de forma permanente en la Jetson, permitiendo que la visión (en GPU o CPU) y el LLM local coexistan con amplio margen de seguridad y rapidez.
  * **Estado Final (Solución Implementada):** Se descargó e instaló `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` para `llama-server`, liberando suficiente memoria RAM para que todos los módulos y el LLM se ejecuten concurrentemente.

---

## 7. Avances de Cierre (22 de Junio): Optimización de Emoción y Activación del Módulo C

### A. Reactivación y Construcción del Módulo C (Detectron2 + VGG16 + BiLSTM)
El Módulo C estaba inactivo debido a incompatibilidades de versiones de PyTorch y Torchvision instaladas sobre Jetpack 6.x / CUDA 12.6, las cuales causaban errores de enlazado de símbolos. Se procedió con la siguiente solución definitiva:
1. **Enlazado de Bibliotecas CUDA Heredadas:** Se instalaron paquetes de desarrollo adicionales de CUDA 12.6 (`libcusparse-dev-12-6`, `libcublas-dev-12-6`, `libcusolver-dev-12-6`, `libcurand-dev-12-6`, `libcufft-dev-12-6` y el compilador `cuda-compiler-12-6`).
2. **Bibliotecas Dinámicas Privadas:** Para satisfacer las llamadas de PyTorch de NVIDIA sin contaminar las librerías globales de Jetpack, se descargó cuDNN 8.9.5 desde el repositorio redistribuible público de NVIDIA, y se ubicaron sus librerías de forma privada dentro de `/home/jetson/integradora/model_ia/pruebas_mod/lib/python3.10/site-packages/torch/lib/` (resolviendo la falta de `libcudnn.so.8`). Asimismo, se instaló `nvidia-cusparselt-cu12` y se ubicó `libcusparseLt.so.0` en la misma ruta.
3. **Compilación de Torchvision 0.19.0 desde Fuente:**
   * Se degradó `setuptools` a `v64.0.3` para dar soporte a compiladores basados en `numpy.distutils`.
   * Se clonó y compiló desde cero la versión `v0.19.0` de `torchvision` (compatible con `torch 2.4.0`), enlazándose de forma limpia con los encabezados del sistema.
   * Se eliminó el folder anterior de `torchvision-0.26.0` que provocaba el conflicto `RuntimeError: operator torchvision::nms does not exist`.
4. **Distribución Híbrida de Inferencia CPU/GPU:**
   * Se de-activó la variable `CUDA_VISIBLE_DEVICES = '-1'` al inicio de `run.py`.
   * Se mantuvo esta instrucción únicamente al inicio de `mod_a.py` y `mod_b.py`.
   * Esto permite que **el Módulo C (PyTorch) tenga acceso total a la GPU CUDA**, mientras que TensorFlow (Módulos A y B) corre en CPU de fondo sin colisionar memoria.
5. **Activación de Configuración:** Se modificó `runtime.yaml` para añadir `"C"` a los módulos activos y definir pesos balanceados de fusión: `A: 0.35`, `B: 0.35`, `C: 0.30`.

### B. Afinamiento de Precisión de "Alegría" (Módulo A)
Para resolver la lentitud del robot en detectar expresiones de felicidad/alegría y corregir la imprecisión del clasificador bajo la iluminación física:
1. **Control de Filtro CLAHE:** Se modificó `mod_a.py` para permitir variar la potencia del ecualizador CLAHE desde la configuración. Se redujo `modA_clahe_clip` a `1.2` (de `2.0`) en `runtime.yaml`, evitando que el realce de contraste deforme la sonrisa y cause falsos negativos de enfado o tristeza.
2. **Flexibilidad Temporal:** Se flexibilizaron las variables de histéresis y valencia en `runtime.yaml` para dotar al robot de mayor dinamismo expresivo:
   * `modA_ema_alpha: 0.30` (antes `0.45`), incrementando la velocidad de actualización.
   * `modA_min_gap_for_switch: 4.0` (antes `7.0`), facilitando el cambio de emoción dominante.
   * `modA_hysteresis_margin: 1.5` (antes `3.0`), reduciendo la inercia necesaria para cambiar el estado.
   * `modA_valence_extra_gap: 1.0` (antes `5.0`), minimizando la penalización impuesta al pasar entre valencias de emociones positivas y negativas.

### C. Prueba de Ejecución y Monitoreo de Recursos
Se validó la ejecución unificada de todo el sistema por medio de `./start_bmo.sh`, con los siguientes resultados satisfactorios:
* El Módulo C inicializa y arroja clasificaciones en GPU: `[C][20] CALMA (1.00) | person_ratio=0.00`.
* El Módulo A procesa emociones sobre CPU en un tiempo de inferencia promedio de **~160 ms**.
* La fusión combina adecuadamente las salidas y el reporter consolida el estado del robot.
* La memoria RAM total consumida se estabiliza en **6.8 GB**, sin bloqueos del sistema ni llamadas a OOM.

