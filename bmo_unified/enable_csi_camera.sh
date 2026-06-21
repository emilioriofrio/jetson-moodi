#!/usr/bin/env bash
set -euo pipefail

# Asegurar que se ejecuta como root para poder usar jetson-io
if [ "$EUID" -ne 0 ]; then
  echo "[ERROR] Este script debe ejecutarse con sudo."
  echo "Uso: sudo $0"
  exit 1
fi

echo "[CSI] 1/2. Actualizando configuración en runtime.yaml..."
python3 -c '
import yaml
path = "/home/jetson/integradora/model_ia/sistem_IA/config/runtime.yaml"
with open(path, "r") as f:
    cfg = yaml.safe_load(f)
cfg["camera_type"] = "csi"
cfg["width"] = 1280
cfg["height"] = 720
with open(path, "w") as f:
    yaml.safe_dump(cfg, f)
print("[CSI] Configuración guardada en runtime.yaml (camera_type: csi, 1280x720)")
'

echo "[CSI] 2/2. Activando overlay para cámara IMX219 (Puerto A) usando Jetson IO..."
JETSON_IO_BIN="/opt/nvidia/jetson-io/config-by-hardware.py"

if [ -f "$JETSON_IO_BIN" ]; then
  # Intentamos habilitar el perfil estándar de la cámara IMX219 en el puerto A (Raspberry Pi Camera V2)
  # Si el perfil exacto cambia por la versión de Jetpack, el comando lo notificará de forma segura sin romper el boot.
  if "$JETSON_IO_BIN" -n "Jetson Camera IMX219-A" &>/dev/null; then
    echo "[CSI] Overlay 'Jetson Camera IMX219-A' activado con éxito."
  elif "$JETSON_IO_BIN" -n "Camera IMX219-A" &>/dev/null; then
    echo "[CSI] Overlay 'Camera IMX219-A' activado con éxito."
  elif "$JETSON_IO_BIN" -n "Raspberry Pi Camera V2" &>/dev/null; then
     echo "[CSI] Overlay 'Raspberry Pi Camera V2' activado con éxito."
  else
    echo "[WARN] No se pudo activar automáticamente un perfil IMX219 conocido."
    echo "       Por favor, ejecuta manualmente: sudo /opt/nvidia/jetson-io/jetson-io.py"
    echo "       y selecciona tu cámara CSI desde la interfaz gráfica."
    exit 0
  fi

  echo ""
  echo "=========================================================================="
  echo " [¡ÉXITO!] La cámara CSI ha sido habilitada."
  echo " IMPORTANTE: Debes reiniciar la Jetson para que el kernel cargue el overlay."
  echo " Ejecuta: sudo reboot"
  echo "=========================================================================="
else
  echo "[ERROR] No se encontró la herramienta Jetson IO en $JETSON_IO_BIN."
  echo "        Asegúrate de que estás corriendo este script en tu NVIDIA Jetson."
  exit 1
fi
