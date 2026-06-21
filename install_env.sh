#!/usr/bin/env bash
set -euo pipefail

# Instala un entorno virtual x86_64 (Ubuntu 22.04) para ejecutar Moodi (A/B/C + bridge Flask).
# Incluye compilación de detectron2 desde fuente.

PYTHON_BIN="${PYTHON_BIN:-python3.10}"
VENV_DIR="${VENV_DIR:-.venv_moodi_x86}"

cd "$(dirname "$0")"

echo "[install_env] Apt deps (OpenCV/GStreamer/build tools)..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  "${PYTHON_BIN}-venv" "${PYTHON_BIN}-dev" \
  build-essential cmake pkg-config ninja-build git \
  libglib2.0-0 libsm6 libxext6 libxrender1 \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav

echo "[install_env] Creando venv: ${VENV_DIR}"
"${PYTHON_BIN}" -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip setuptools wheel

echo "[install_env] Instalando Torch CPU..."
pip install --no-cache-dir \
  torch==2.1.2+cpu torchvision==0.16.2+cpu \
  --index-url https://download.pytorch.org/whl/cpu

echo "[install_env] Instalando requisitos (A/B/C + bridge)..."
pip install --no-cache-dir -r requirements_x86.txt

echo "[install_env] Instalando detectron2 (compilación desde fuente)..."
# detectron2 compila extensiones C++: prepara dependencias mínimas
pip install --no-cache-dir cython pycocotools
pip install --no-cache-dir -e ./integradora/model_ia/detectron2 --no-build-isolation

echo "[install_env] OK: entorno listo en ${VENV_DIR}"
echo "[install_env] Nota: detectron2 puede requerir ajustar torch/torchvision si falla la compilación."

