#!/usr/bin/env bash
# instalar_regla_tactil.sh -- evita que el táctil del panel se "pierda" (S12)
#
# Problema reportado: el táctil de la pantalla a veces no funciona desde el
# arranque y a veces se pierde a mitad de funcionamiento (queda solo el ratón).
# Comprobado en el momento del fallo: el kernel NO lista ningún dispositivo
# táctil, o sea que el panel desaparece del bus USB, no es que la app ignore
# los toques.
#
# OJO, ESTO ES UNA RED DE SEGURIDAD, NO EL ARREGLO. Léelo antes de instalarlo:
#
# La hipótesis inicial era el autosuspend de USB, que en esta Jetson está en 2
# segundos (/sys/module/usbcore/parameters/autosuspend = 2). Al recuperarse el
# táctil se pudo comprobar de verdad, y la hipótesis salió DEBILITADA: el
# dispositivo ("HQEmbed Multi-Touch", ILI 222a:0001) aparece con
# power/control=on y runtime_status=active, o sea que usbhid ya lo exime de la
# suspensión por su cuenta. Esta regla, por tanto, probablemente no cambie nada
# para él: solo garantiza que ningún cambio futuro se lo lleve por delante.
#
# Lo que sí se observó: el táctil NO se enumera en el bus algunas veces (no
# aparece en /proc/bus/input/devices ni en lsusb) y una suspensión +
# reanudación de la Jetson lo devuelve. Eso apunta al enlace/enumeración, no a
# la gestión de energía en marcha. Para ese caso está reconectar_tactil.sh, que
# fuerza la re-enumeración sin reiniciar.
#
# La regla desactiva el autosuspend para los dispositivos USB de clase HID
# (03), que es la clase del táctil. NO toca /boot, ni overlays de device tree,
# ni parámetros de arranque del kernel -- restricción no negociable del
# proyecto (REQUERIMIENTOS_APP_MOODI.md §0). Es reversible borrando el archivo
# de regla.
#
# Uso:  sudo bash bmo_unified/instalar_regla_tactil.sh
set -euo pipefail

REGLA=/etc/udev/rules.d/99-moodi-tactil.rules

if [ "$(id -u)" -ne 0 ]; then
  echo "Hay que ejecutarlo con sudo:  sudo bash $0" >&2
  exit 1
fi

cat > "$REGLA" <<'FIN'
# Moodi (S12): nunca suspender dispositivos USB HID (táctil del panel, teclado,
# ratón). Con usbcore.autosuspend=2 el táctil se suspendía a los 2 s de
# inactividad y había controladores que no volvían.
ACTION=="add", SUBSYSTEM=="usb", ATTR{bDeviceClass}=="00", ATTR{power/control}="on"
ACTION=="add", SUBSYSTEM=="usb", ATTRS{bInterfaceClass}=="03", ATTR{power/control}="on"
FIN

udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --action=add

echo "Regla instalada en $REGLA y aplicada."
echo
echo "Estado actual de los USB (control=on significa 'no se suspende'):"
for d in /sys/bus/usb/devices/*/power/control; do
  dev=$(dirname "$(dirname "$d")")
  nombre=$(cat "$dev/product" 2>/dev/null || echo '-')
  printf '  %-8s %-6s %s\n' "$(basename "$dev")" "$(cat "$d")" "$nombre"
done
echo
echo "Si el táctil vuelve a perderse con la regla puesta, el siguiente sospechoso"
echo "es el cable/puerto USB del panel (probar otro puerto, preferiblemente uno"
echo "directo de la Jetson y no del hub)."
