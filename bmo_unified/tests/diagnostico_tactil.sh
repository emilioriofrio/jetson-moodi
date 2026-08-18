#!/usr/bin/env bash
# tests/diagnostico_tactil.sh -- ¿por qué no responde el táctil? (S12)
#
# El usuario reportó que la pantalla táctil no responde en absoluto dentro de
# la app y que hay que usar el ratón. Este script contesta la pregunta previa a
# cualquier arreglo de software: ¿existe siquiera un dispositivo táctil?
#
# Uso:  bash tests/diagnostico_tactil.sh
set -uo pipefail

echo "=== 1) Dispositivos de entrada que ve el kernel ==="
grep -E '^N: Name=' /proc/bus/input/devices | sed 's/^N: Name=/  - /'

echo
echo "=== 2) ¿Alguno es táctil? (necesita ejes ABS multitáctil) ==="
tactiles=$(awk '
  /^N: Name=/ { nombre = $0; sub(/^N: Name="/, "", nombre); sub(/"$/, "", nombre) }
  /^B: ABS=/  { if ($0 ~ /ABS=[0-9a-f]*[1-9a-f][0-9a-f]{9,}/) print "  - " nombre }
' /proc/bus/input/devices)
if [ -n "$tactiles" ]; then
  echo "$tactiles"
else
  echo "  NINGUNO"
fi

echo
echo "=== 3) USB conectado ==="
lsusb | sed 's/^/  /'

echo
echo "=== 4) Punteros que ve X11 ==="
DISPLAY=${DISPLAY:-:0} xinput list --short 2>/dev/null | sed 's/^/  /' || echo "  (xinput no disponible)"

echo
if [ -z "$tactiles" ]; then
  cat <<'FIN'
=== DIAGNÓSTICO ===
NO hay ningún dispositivo táctil conectado a la Jetson.

Esto NO es un problema de la app ni de Qt: el kernel no ve el panel táctil, así
que ningún programa puede recibir toques. En el panel ElecLab de 7" el táctil es
un dispositivo USB INDEPENDIENTE del HDMI: el HDMI lleva solo la imagen y el
táctil sale por un conector USB aparte de la placa del panel.

Qué hacer:
  1. Conectar el cable USB del táctil del panel a un puerto USB de la Jetson.
  2. Volver a ejecutar este script: debe aparecer un dispositivo nuevo en (1) y
     (2), normalmente con un nombre tipo "... Touch", "ILITEK", "Goodix" o
     "USB Touchscreen", y también en la lista de X11 (4).
  3. Si aparece pero los toques caen en el sitio equivocado, es cuestión de
     mapear el dispositivo a la salida HDMI:
         xinput map-to-output <id> HDMI-0
FIN
else
  cat <<'FIN'
=== DIAGNÓSTICO ===
Hay un dispositivo táctil presente. Si aun así no responde dentro de la app:
  - comprobar que aparece en la lista de X11 (sección 4);
  - si los toques caen desplazados, mapearlo a la salida de video:
        xinput map-to-output <id> HDMI-0
FIN
fi
