#!/usr/bin/env bash
set -euo pipefail

# Asegurar que se ejecuta como root
if [ "$EUID" -ne 0 ]; then
  echo "[ERROR] Este script debe ejecutarse con sudo."
  echo "Uso: sudo $0"
  exit 1
fi

echo "[USB] 1/2. Actualizando configuración en runtime.yaml..."
python3 -c '
import yaml
path = "/home/jetson/integradora/model_ia/sistem_IA/config/runtime.yaml"
with open(path, "r") as f:
    cfg = yaml.safe_load(f)
cfg["camera_type"] = "usb"
cfg["width"] = 960
cfg["height"] = 720
with open(path, "w") as f:
    yaml.safe_dump(cfg, f)
print("[USB] Configuración guardada en runtime.yaml (camera_type: usb, 960x720)")
'

echo "[USB] 2/2. Restableciendo overlays de Jetson IO..."
JETSON_IO_BIN="/opt/nvidia/jetson-io/config-by-hardware.py"

if [ -f "$JETSON_IO_BIN" ]; then
  # Remueve cualquier overlay añadido de forma segura y restaura el extlinux.conf de respaldo
  "$JETSON_IO_BIN" -r
  echo "[USB] Todos los overlays han sido removidos y restaurados al estado por defecto."
  echo ""
  echo "=========================================================================="
  echo " [¡ÉXITO!] Cámara USB configurada."
  echo " NOTA: Si tenías la cámara CSI activa, es recomendable reiniciar para liberar"
  echo "       los recursos del puerto CSI."
  echo " Ejecuta: sudo reboot"
  echo "=========================================================================="
else
  echo "[WARN] No se encontró $JETSON_IO_BIN. Configuración de runtime.yaml actualizada."
fi
