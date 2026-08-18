# DIAGNOSTICO: Conflicto Critico de Memoria RAM/GPU - Jetson Orin Nano
**Archivo de contexto para agente IA CLI**
**Proyecto:** BMO Moodi - Sistema embebido de monitoreo emocional (ESPOL / RAMEL)
**Fecha de generacion:** Junio 2026

---

## CONTEXTO DEL SISTEMA

El sistema BMO Moodi corre completamente en una **NVIDIA Jetson Orin Nano (8 GB de RAM unificada)**.
La RAM es compartida entre CPU y GPU - no hay bancos separados.

El ecosistema completo se levanta con:
```
/home/jetson/start_bmo.sh
```

Ese script lanza en orden:
1. `llama-server` en puerto `1234` (LLM local)
2. `ia_bridge.py` via `gunicorn` en puerto `5000` (puente Flask)
3. Permisos serial `/dev/ttyUSB0`
4. `run.py` desde el entorno virtual en `/home/jetson/integradora/model_ia/pruebas_mod`

---

## EL PROBLEMA: ENOMEM en nvmap (crash/congelamiento)

### Causa raiz

Al arrancar el stack completo, el consumo de RAM supera el limite fisico:

| Componente | RAM consumida |
|---|---|
| OS + Desktop Ubuntu (JetPack 6.x) | ~1.7 GB |
| `llama-server` con DeepSeek-R1-0528-Qwen3-8B Q4_K_M | ~4.8 - 5.0 GB |
| TensorFlow (comportamiento por defecto al detectar GPU) | reclama el 100% del restante |
| **Total intentado sobre 8 GB fisicos** | ~8.5 - 9 GB |

### Error especifico que aparece en logs

```
NvMapMemAllocInternalTagged: 1075072515 error 12 (ENOMEM - Out of Memory)
```

### Mecanismo del fallo

1. `llama-server` carga el modelo de 8B con offload completo a GPU: ocupa ~5 GB de forma permanente.
2. `run.py` inicializa TensorFlow/Keras (Modulo B) y DeepFace (Modulo A).
3. TensorFlow detecta GPU CUDA disponible y por comportamiento por defecto intenta **reservar el 100% de la memoria grafica libre restante**.
4. El driver `nvmap` del kernel Tegra falla con `ENOMEM` porque no hay memoria fisica disponible.
5. El sistema entra en thrashing con ZRAM (swap comprimido en la misma RAM). Load average escala a ~33. La GUI y Python crashean.

---

## ESTADO DEL CODIGO RELEVANTE

### Modulo A - Reconocimiento facial

- **Archivo original de tesis:** usaba MTCNN como backend de DeepFace (pesado, ~3.5s por inferencia).
- **Estado actual (desde S01-S05):** MTCNN fue reemplazado por **MediaPipe** como detector facial. Inferencia actual: milisegundos. MediaPipe corre nativo en CPU, no requiere GPU.
- **Implicacion:** TensorFlow ya NO necesita GPU para el Modulo A. El cuello de botella facial fue eliminado. Forzar CPU en TensorFlow no degrada el rendimiento visible.

### Modulo B - Gestos corporales

- Usa `modelo_modb_v3.keras` (BiLSTM sobre keypoints de MediaPipe Holistic).
- Procesa matrices de forma `(19, 225)` por inferencia.
- TensorFlow/Keras carga este modelo. Es el componente que dispara la reserva greedy de GPU.

### Modulo A - Activacion por ticks (diseno oficial de tesis)

La seccion 3.4.4 del reporte final confirma que el diseno oficial del sistema **no activa el Modulo A en cada frame**, sino en intervalos de ticks:

> "el Módulo A se activó en intervalos de ticks, reduciendo la carga computacional del análisis facial sin pérdida de continuidad, mientras que los Módulos B y C reciben todos los fotogramas para preservar la secuencia requerida por los modelos recurrentes."

Esto es comportamiento esperado y correcto, no un bug.

---

## SOLUCION A APLICAR

### Prioridad 1 - Obligatoria (Via A + Via B combinadas)

Agregar estas dos lineas al **inicio absoluto** de `run.py`, antes de cualquier `import`:

```python
import os
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'        # TensorFlow usa CPU unicamente
os.environ['TF_FORCE_GPU_ALLOW_GROWTH'] = 'true' # Defensa secundaria por si algun import lo ignora
```

**Archivo a modificar:**
```
/home/jetson/integradora/model_ia/sistem_IA/run.py
```

Si el Modulo A tiene su propio archivo separado (por ejemplo `mod_a.py`), aplicar las mismas dos lineas al inicio de ese archivo tambien.

**Archivo potencial a modificar tambien:**
```
/home/jetson/integradora/model_ia/sistem_IA/modules/mod_a.py
```

**Por que esto funciona:**
- `CUDA_VISIBLE_DEVICES = '-1'` hace que TensorFlow no vea ninguna GPU. No intenta reservar memoria grafica. El LLM conserva uso exclusivo de la GPU via llama-server (que no usa la API CUDA de TensorFlow).
- MediaPipe no es afectado porque no usa el backend CUDA de TensorFlow.
- El impacto en FPS es minimo porque MediaPipe ya corria en CPU.

### Prioridad 2 - Verificar posicion de las lineas

**CRITICO:** Si TensorFlow es importado antes de que se ejecute `os.environ['CUDA_VISIBLE_DEVICES'] = '-1'`, el setting es ignorado. Las lineas deben ser las primeras del archivo, incluso antes de imports de librerias del sistema.

Estructura correcta de run.py:
```python
# ESTAS LINEAS PRIMERO, SIN EXCEPCION
import os
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
os.environ['TF_FORCE_GPU_ALLOW_GROWTH'] = 'true'

# Recien aqui los demas imports
import cv2
import numpy as np
# ... resto del codigo
```

### Prioridad 3 - Verificar runtime.yaml

Confirmar que la resolucion de camara este en `960x720` y no en `1920x1080`. Resolucion mas alta aumenta el costo de procesamiento de MediaPipe en CPU.

**Archivo:**
```
/home/jetson/integradora/model_ia/sistem_IA/config/runtime.yaml
```

### Prioridad 4 - Opcional si el sistema sigue inestable (Via C)

Reemplazar el modelo LLM de 8B por una version mas ligera. Para la funcion de correccion gramatical de frases RFID cortas, un modelo de 1.5B es suficiente.

Modelos candidatos (en formato GGUF para llama-server):
- `DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf` (~1.1 GB - libera ~3.9 GB)
- `DeepSeek-R1-Distill-Qwen-3B-Q4_K_M.gguf` (~2.2 GB - libera ~2.8 GB)

El modelo actual esta en:
```
/home/jetson/apps/llm/llama.cpp/build/  (o ruta donde este el .gguf)
```

El script de llama-server en `start_bmo.sh` apunta al modelo con un flag `--model`. Cambiar esa ruta al nuevo .gguf.

---

## VERIFICACION POST-CAMBIO

Despues de aplicar las modificaciones, verificar con:

```bash
# Ver uso de RAM en tiempo real
watch -n 2 free -h

# Ver procesos y su consumo
top -o %MEM

# Ver si TensorFlow intenta usar GPU (no debe aparecer ningun CUDA device)
# Agregar temporalmente al inicio de run.py:
# import tensorflow as tf
# print(tf.config.list_physical_devices('GPU'))
# Debe imprimir: []
```

Uso de RAM esperado despues del fix:
| Componente | RAM |
|---|---|
| OS + Desktop | ~1.7 GB |
| llama-server (8B Q4) | ~5.0 GB |
| TensorFlow en CPU (Modulo B) | ~0.4 - 0.6 GB |
| MediaPipe + OpenCV (Modulo A) | ~0.3 GB |
| **Total** | ~7.5 GB - dentro del limite |

---

## ARCHIVOS CLAVE DEL SISTEMA

```
/home/jetson/start_bmo.sh                          - Lanzador del ecosistema completo
/home/jetson/bmo_unified/bmo_app.py                - Interfaz PyQt5 principal
/home/jetson/integradora/model_ia/sistem_IA/run.py - Motor de IA (MODIFICAR AQUI)
/home/jetson/integradora/model_ia/sistem_IA/modules/mod_a.py  - Modulo A (MODIFICAR SI EXISTE)
/home/jetson/integradora/model_ia/sistem_IA/config/runtime.yaml - Config de camara y IA
/home/jetson/apps/llm/ia_bridge.py                 - Puente Flask del LLM (puerto 5000)
/home/jetson/integradora/model_ia/resultados_modb_v3/modelo_modb_v3.keras - Modelo Modulo B
```

---

## CONTEXTO DE RED (IP)

El firmware de la ESP32 (`main.cpp`) tiene hardcodeada la URL:
```
SERVER_URL = "http://192.168.10.140:5000/ask"
```

La IP actual de la Jetson en la red activa puede ser diferente. Verificar con `ip addr` y actualizar el firmware de la ESP32 si la red cambio, o configurar la Jetson con IP estatica `192.168.10.140` en la red local.

---

## RESUMEN DE ACCIONES PARA EL AGENTE

1. Abrir `/home/jetson/integradora/model_ia/sistem_IA/run.py`
2. Insertar al inicio absoluto del archivo (linea 1 y 2):
   ```python
   import os
   os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
   os.environ['TF_FORCE_GPU_ALLOW_GROWTH'] = 'true'
   ```
3. Si existe `/home/jetson/integradora/model_ia/sistem_IA/modules/mod_a.py`, insertar las mismas lineas al inicio de ese archivo.
4. Verificar resolucion en `runtime.yaml` es `960x720`.
5. Reiniciar el sistema con `./start_bmo.sh`.
6. Monitorear RAM con `watch -n 2 free -h` durante el arranque.
