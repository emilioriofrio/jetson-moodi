#!/usr/bin/env bash
# Instala el acceso directo de Moodi (S11): icono en el Escritorio + entrada en
# el menú de aplicaciones, para no tener que abrir una terminal y escribir el
# comando cada vez.
#
# Es idempotente: se puede volver a ejecutar tras cambiar moodi.desktop.
# No toca nada del sistema ni requiere sudo (todo vive bajo $HOME).
set -euo pipefail

SRC="/home/jetson/bmo_unified/moodi.desktop"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
APPS_DIR="$HOME/.local/share/applications"

mkdir -p "${DESKTOP_DIR}" "${APPS_DIR}" /home/jetson/bmo_unified/logs

install -m 755 "${SRC}" "${DESKTOP_DIR}/moodi.desktop"
install -m 644 "${SRC}" "${APPS_DIR}/moodi.desktop"

# GNOME (esta Jetson corre ubuntu:GNOME) ignora los .desktop del Escritorio que
# no estén marcados como "de confianza": sin esto el icono aparece como archivo
# de texto y pide confirmación en vez de lanzar la app.
if command -v gio >/dev/null 2>&1; then
    gio set "${DESKTOP_DIR}/moodi.desktop" metadata::trusted true 2>/dev/null \
        && echo "[OK] Marcado como de confianza para GNOME." \
        || echo "[AVISO] No se pudo marcar como de confianza (¿sin sesión gráfica?)."
    gio set "${DESKTOP_DIR}/moodi.desktop" metadata::xffm-exec-checksum \
        "$(sha256sum "${SRC}" | cut -d' ' -f1)" 2>/dev/null || true
fi

update-desktop-database "${APPS_DIR}" 2>/dev/null || true

echo "[OK] Acceso directo instalado en:"
echo "     ${DESKTOP_DIR}/moodi.desktop"
echo "     ${APPS_DIR}/moodi.desktop"
echo "Si el icono del Escritorio aún pide confirmación, haz clic derecho ->"
echo "\"Permitir ejecutar\" una sola vez (limitación de GNOME, no del script)."
