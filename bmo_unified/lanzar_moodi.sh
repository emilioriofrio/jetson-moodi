#!/usr/bin/env bash
# Lanzador de Moodi para el acceso directo del Escritorio (S11).
#
# Existe como script propio y no como redirección dentro de la línea Exec del
# .desktop porque la especificación de Desktop Entry NO admite redirecciones ni
# comillas de shell en Exec (desktop-file-validate lo rechaza), aunque algunos
# entornos lo toleren. Aquí además se deja rastro en un log: al lanzar desde el
# icono no hay terminal donde ver un fallo temprano.
set -uo pipefail

LOG="/home/jetson/bmo_unified/logs/lanzador.log"
mkdir -p "$(dirname "${LOG}")"

{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') lanzando Moodi desde el acceso directo ====="
} >> "${LOG}"

# Evita dos instancias: relanzar sobre una app ya abierta pelearía por la cámara
# y por /dev/ttyUSB0.
if pgrep -f "python.*bmo_app\.py" > /dev/null; then
    echo "[LANZADOR] Moodi ya está en ejecución; no se abre otra instancia." >> "${LOG}"
    exit 0
fi

exec /home/jetson/start_bmo.sh >> "${LOG}" 2>&1
