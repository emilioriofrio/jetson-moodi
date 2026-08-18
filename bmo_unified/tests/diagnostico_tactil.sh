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
# Un táctil se reconoce por tener los ejes ABS_MT_POSITION_X/Y, que son los
# bits 53 y 54 de la máscara ABS. Cada dígito hexadecimal son 4 bits, así que
# esos dos caen en el dígito nº 13 contando desde la derecha (bits 52-55), y
# dentro de él son los valores 2 y 4.
#
# OJO: la primera versión de esto usaba una expresión regular con intervalos
# ({9,}) y daba NINGUNO con el táctil delante -- mawk no admite intervalos sin
# --re-interval. Un falso negativo aquí es especialmente dañino: este script
# existe justo para decidir si el problema es el hardware o la app.
tactiles=$(awk '
  BEGIN { for (i = 0; i < 10; i++) v[i "" ] = i; v["a"]=10; v["b"]=11; v["c"]=12;
          v["d"]=13; v["e"]=14; v["f"]=15 }
  /^N: Name=/ { nombre = $0; sub(/^N: Name="/, "", nombre); sub(/"$/, "", nombre) }
  /^B: ABS=/ {
    bits = $0; sub(/^B: ABS=/, "", bits)
    n = length(bits)
    if (n >= 14) {
      nibble = v[substr(bits, n - 13, 1)]
      if (int(nibble / 2) % 2 == 1 || int(nibble / 4) % 2 == 1) print "  - " nombre
    }
  }
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

En este robot el táctil es un "HQEmbed Multi-Touch" (ILI Technology, USB
222a:0001), colgado del concentrador interno en el puerto 1-2.3. Que no
aparezca significa que NO se enumeró en el bus, no que la app lo ignore.

Qué hacer, en este orden:
  1. Forzar que el USB lo vuelva a enumerar, sin reiniciar y con la app cerrada:
         sudo bash bmo_unified/reconectar_tactil.sh
     (Confirmado que una suspensión + reanudación de la Jetson también lo
     recupera; este script hace lo mismo por el camino corto.)
  2. Volver a ejecutar este diagnóstico: debe aparecer en (1), (2) y (4).
  3. Si tras eso sigue sin aparecer, el sospechoso es el enlace físico: probar
     otro puerto USB, preferiblemente uno directo de la Jetson y no del
     concentrador.
  4. Si aparece pero los toques caen en el sitio equivocado:
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
