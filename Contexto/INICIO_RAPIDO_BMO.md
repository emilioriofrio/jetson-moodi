# Inicio Rápido — BMO / Moodi

## Arrancar todo (un solo comando)
```bash
cd /home/jetson
bash start_bmo.sh
```

## Qué hace `start_bmo.sh`, paso a paso
1. Si `llama-server` no está corriendo, lo levanta (puerto 1234) — tarda ~12s en cargar el modelo.
2. Si `ia_bridge` no está corriendo, lo levanta (puerto 5000, Flask/gunicorn).
3. Da permiso al puerto serie del ESP32: `sudo -n chmod 666 /dev/ttyUSB0`.
   Si en el log sale `sudo: a password is required`, no pasa nada grave (el resto sigue), pero si
   luego la app no puede abrir el puerto serie, corre esto tú mismo en una terminal real:
   ```bash
   sudo chmod 666 /dev/ttyUSB0
   ```
4. Lanza `bmo_app.py` (la interfaz completa: cinta fantasma + Home/Caras/Oraciones/Video/Salir)
   desde `bmo_unified/`, con el venv `integradora/model_ia/pruebas_mod`.

## Confirmar que todo quedó arriba
```bash
pgrep -af "bmo_app.py|gunicorn|llama-server"
```

## Detener todo
Lo normal es cerrar la app desde la propia interfaz (mantener presionado el icono "Salir" ~3s en la
cinta) — eso ya apaga el motor de visión y el hilo serie de forma ordenada. Si hace falta forzarlo
desde la terminal (p. ej. quedó colgado):
```bash
pkill -f bmo_app.py
pkill -f "gunicorn.*ia_bridge"
pkill -f llama-server
```

## Solo el motor de visión, sin la interfaz completa (para pruebas)
```bash
cd /home/jetson/integradora/model_ia/sistem_IA
<venv>/bin/python run.py
```
Útil para probar los Módulos A/B/C aislados sin levantar el LLM ni la GUI. Configuración en
`config/runtime.yaml` (cámara, módulos habilitados, umbrales).
