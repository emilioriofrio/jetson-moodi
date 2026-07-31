#!/usr/bin/env bash
set -euo pipefail

# 1. Rutas
LLM_DIR="/home/jetson/apps/llm"
MODELS_DIR="/home/jetson/models"
MODEL_PATH="${MODELS_DIR}/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
BMO_APP_DIR="/home/jetson/bmo_unified"
VENV_IA_BRIDGE="/home/jetson/venvs/ia_bridge"
VENV_PRUEBAS="/home/jetson/integradora/model_ia/pruebas_mod"

echo "========================================================="
echo "        [BMO OS] Iniciando Ecosistema de BMO...          "
echo "========================================================="

# 1.5. Pantalla de carga (S11). Se lanza ANTES que nada para que el usuario vea
# algo desde el segundo cero: el tramo más largo del arranque (los 12s de
# llama-server, más abajo) ocurre antes de que bmo_app.py exista siquiera, así
# que un splash interno a la app llegaría justo cuando ya no hace falta.
# Comunicación por archivo de estado "PORCENTAJE|CLAVE_I18N"; ver ui/splash.py.
BOOT_STATUS="/tmp/moodi_boot.status"
boot_progress() { echo "$1|$2" > "${BOOT_STATUS}" 2>/dev/null || true; }

SPLASH_PID=""
if [ -n "${DISPLAY:-}" ]; then
    boot_progress 0 boot.starting
    "${VENV_PRUEBAS}/bin/python" "${BMO_APP_DIR}/ui/splash.py" >/dev/null 2>&1 &
    SPLASH_PID=$!
    echo "[BMO] Pantalla de carga iniciada (pid=${SPLASH_PID})."
else
    echo "[BMO] Sin DISPLAY: se omite la pantalla de carga."
fi

# Si el script muere a medio arranque, el splash no debe quedarse en pantalla.
cleanup_splash() {
    if [ -n "${SPLASH_PID}" ] && kill -0 "${SPLASH_PID}" 2>/dev/null; then
        boot_progress 100 READY
        sleep 0.6
        kill "${SPLASH_PID}" 2>/dev/null || true
    fi
}
trap cleanup_splash EXIT

# 2. Iniciar llama-server si no está corriendo
boot_progress 8 boot.llm
if ! pgrep -f "llama-server" > /dev/null; then
    echo "[BMO] Iniciando llama-server (Qwen2.5-1.5B-Instruct) en puerto 1234..."
    "${LLM_DIR}/llama.cpp/build/bin/llama-server" \
        -m "${MODEL_PATH}" \
        --host 0.0.0.0 --port 1234 \
        -ngl 999 -c 2048 --flash-attn auto --cont-batching -t 4 \
        > /home/jetson/llama_server.log 2>&1 &
    
    echo "[BMO] Esperando que llama-server inicialice (12s)..."
    sleep 12
else
    echo "[BMO] llama-server ya está en ejecución."
fi

# 3. Iniciar ia_bridge si no está corriendo
boot_progress 45 boot.bridge
if ! pgrep -f "ia_bridge" > /dev/null; then
    echo "[BMO] Iniciando Flask ia_bridge en puerto 5000..."
    "${VENV_IA_BRIDGE}/bin/gunicorn" -w 1 --threads 4 -b 0.0.0.0:5000 \
        --timeout 180 --graceful-timeout 180 --keep-alive 1 \
        --access-logfile - --chdir "${LLM_DIR}" ia_bridge:app \
        > /home/jetson/ia_bridge.log 2>&1 &
    sleep 2
else
    echo "[BMO] ia_bridge ya está en ejecución."
fi

# 4. Cambiar permisos del puerto serial para la ESP32
if [ -e "/dev/ttyUSB0" ]; then
    echo "[BMO] Ajustando permisos de /dev/ttyUSB0..."
    sudo -n chmod 666 /dev/ttyUSB0 || true
fi

# 4.5. Configurar audio de PulseAudio (evitar saturación física en altavoces de ElecLab)
boot_progress 58 boot.audio
echo "[BMO] Configurando perfil de audio HDA, sink por defecto y volumen al 65%..."
pactl set-card-profile alsa_card.platform-3510000.hda output:hdmi-stereo || true
pactl set-default-sink alsa_output.platform-3510000.hda.hdmi-stereo || true
pactl set-sink-volume alsa_output.platform-3510000.hda.hdmi-stereo 65% || true

# 5. Iniciar la aplicación principal (bmo_app.py orquesta todo: cara animada,
#    PECS-RFID, y el motor de visión bajo demanda al presionar EMO_TOGGLE).
boot_progress 68 boot.app
echo "[BMO] Iniciando bmo_app.py..."
cd "${BMO_APP_DIR}"
set +e # Desactivar exit-on-error para asegurar que se ejecute la limpieza al salir
"${VENV_PRUEBAS}/bin/python" bmo_app.py "$@"

# 6. Limpieza al salir
echo "[BMO] Limpiando procesos del ecosistema al salir..."
pkill -f "gunicorn.*ia_bridge" || true
pkill -f "llama-server" || true
echo "[BMO] Ecosistema de BMO apagado con éxito."
