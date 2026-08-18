#!/usr/bin/env bash
# reconectar_tactil.sh -- fuerza que el USB vuelva a enumerar el táctil (S12)
#
# Cuando el panel táctil desaparece del bus a mitad de sesión, la alternativa
# habitual es reiniciar la Jetson. Esto intenta lo mismo sin reiniciar:
# desengancha y vuelve a enganchar el concentrador USB, lo que obliga a
# re-enumerar todo lo que cuelga de él.
#
# OJO: del mismo concentrador cuelgan también la cámara y el ESP32
# (/dev/ttyUSB0). Durante unos segundos desaparecen los tres, así que hay que
# ejecutarlo con la app CERRADA. No toca /boot ni overlays: solo el bus USB.
#
# Uso:  sudo bash bmo_unified/reconectar_tactil.sh
set -uo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Hay que ejecutarlo con sudo:  sudo bash $0" >&2
  exit 1
fi

if pgrep -f "bmo_app.py" >/dev/null; then
  echo "La app Moodi está corriendo. Ciérrala primero (esto desconecta también" >&2
  echo "la cámara y el ESP32 durante unos segundos)." >&2
  exit 1
fi

echo "Antes:"
grep -c . /proc/bus/input/devices >/dev/null
grep -E '^N: Name=' /proc/bus/input/devices | sed 's/^N: Name=/  - /'

for hub in $(ls /sys/bus/usb/drivers/usb/ | grep -E '^[0-9]+-[0-9]+$'); do
  echo "Reiniciando $hub…"
  echo "$hub" > /sys/bus/usb/drivers/usb/unbind 2>/dev/null || true
  sleep 2
  echo "$hub" > /sys/bus/usb/drivers/usb/bind 2>/dev/null || true
  sleep 3
done

echo
echo "Después:"
grep -E '^N: Name=' /proc/bus/input/devices | sed 's/^N: Name=/  - /'
echo
echo "Si el táctil sigue sin aparecer, es el cable o el puerto del panel."
