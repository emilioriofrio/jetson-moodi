# Reporte de Progreso - Proyecto BMO Unified (Moodi)
**Fecha:** 26 de Mayo, 2026

## 1. Optimizaciones de Rendimiento y Cámara
- **Cambio de Cámara:** Se configuró todo el sistema para utilizar la cámara USB en `/dev/video0` (índice 0) en lugar de la CSI, debido a fallos en el cable flex. Se modificaron los scripts de Yahboom, el motor de IA y el reconocimiento de QR.
- **Eliminación de Lag:** Se reemplazó el detector facial **MTCNN** (pesado) por **MediaPipe** en la configuración de `sistem_IA`. 
    - *Resultado:* La inferencia bajó de ~3.5s a milisegundos, permitiendo video fluido en tiempo real.

## 2. Aplicación de Escritorio Nativa (BMO OS)
Se desarrolló una aplicación en Python usando **PyQt5** que actúa como el "Sistema Operativo" del robot, diseñada para la pantalla **Eleclab 7" (1024x600)**.

### Características Principales:
- **Interfaz Inmersiva:** Modo pantalla completa sin bordes. La cara de BMO (videos MP4) es el fondo dinámico.
- **Controles "Fantasma":** Los botones (Borrar, Enviar, Cámara, Siguiente Cara, Cerrar) son invisibles por defecto. Aparecen solo al tocar la pantalla y se ocultan tras 5 segundos de inactividad.
- **Navegador de Emociones:** Un botón para ciclar entre todas las animaciones disponibles en la carpeta `~/integradora/animaciones`.
- **Cámara Integrada:** Botón para ver el feed de la cámara USB directamente en la interfaz de BMO sin abrir ventanas externas.

## 3. Integración de Hardware y Comunicaciones
- **Firmware ESP32:** Se creó un código (`esp32_firmware_bmo.ino`) para mapear 10 botones físicos y el lector RFID MFRC522.
- **Lógica SAAC:** El sistema recibe UIDs de tarjetas RFID y construye frases. 
- **Inteligencia Artificial (LLM):** La app se comunica con el puente `ia_bridge.py` (DeepSeek) para corregir la gramática de las frases antes de enviarlas.
- **Telegram:** Envío automático de las frases procesadas al chat configurado.

## 4. Gestión de Procesos y Seguridad
- **Limpieza Automática:** Se implementó un sistema de "Matado de Procesos" (`signal.SIGKILL` a grupos de procesos). Al cerrar la App de BMO, se detienen automáticamente:
    - Motor de IA (`run.py`).
    - Puente de LLM (`ia_bridge.py`).
    - Hilos de cámara y serial.
- **Lanzador Único:** Se creó el script `./start_bmo.sh` para arrancar todo el ecosistema con un solo comando.

## 5. Avances Recientes (26 de Mayo, 2026 - Antigravity)
- **Interfaz Funcional y Diccionario RFID:** La aplicación de escritorio `bmo_app.py` ahora carga automáticamente el archivo `rfid_vocab.json` y traduce los UIDs de las tarjetas RFID a palabras en español.
- **Robustez de Conexión Serial:** Se implementó auto-detección y reconexión automática en el puerto serial de la ESP32 (escanea `/dev/ttyUSB*` y `/dev/ttyACM*`).
- **Control Físico y Estabilidad:** Se mapearon los botones adicionales del D-Pad y de acción para controlar la GUI de BMO (por ejemplo, alternar la cámara con el botón A/Modo, y borrar todo con el botón CALM). Se hizo thread-safe el envío de frases a Telegram/LLM para evitar fallos de PyQt.
- **Flasheo de ESP32:** Se compiló y cargó con éxito el firmware del Cerebro Sensorial en la ESP32 mediante PlatformIO.
- **Automatización de Cámara CSI/USB:** Se crearon scripts ejecutables para alternar con total fluidez entre la cámara CSI y la cámara USB, manejando la configuración del cargador de arranque en `extlinux.conf` y el archivo `runtime.yaml` de forma automática.

---
## Archivos Clave Creados/Modificados:
- [bmo_app.py](file:///home/jetson/bmo_unified/bmo_app.py): El cerebro de la interfaz PyQt5 (actualizado con RFID, serial robusto y cámara dinámica).
- [main.cpp](file:///home/jetson/integradora/Oraciones_interpret/src/main.cpp): Código de firmware serial para la ESP32 (compilado y subido con éxito).
- [enable_csi_camera.sh](file:///home/jetson/bmo_unified/enable_csi_camera.sh): Script para habilitar la cámara CSI (Overlay de dispositivo y runtime.yaml).
- [enable_usb_camera.sh](file:///home/jetson/bmo_unified/enable_usb_camera.sh): Script para volver a la cámara USB.
- [start_bmo.sh](file:///home/jetson/start_bmo.sh): Lanzador del sistema.
- [runtime.yaml](file:///home/jetson/integradora/model_ia/sistem_IA/config/runtime.yaml): Configuración de IA optimizada.

**Próximos pasos sugeridos:**
1. **Activar Cámara CSI:** Ejecutar `./enable_csi_camera.sh`.
2. **Probar el Sistema Completo:** Encender BMO y verificar el funcionamiento de las tarjetas RFID y botones físicos.
3. Vincular audios específicos a las animaciones de la cara.
4. Personalizar el panel de "Monitor IA" con gráficas de estrés.
