# core/frame_utils.py
"""
Corrección de rotación de la cámara USB (montada físicamente a 90°), software
puro sobre el frame ya capturado -- no toca /boot, overlays ni runtime.yaml.

La config vive en bmo_unified/config/camera.json (nivel app, no overlay) en
vez de runtime.yaml, para no mezclar esta corrección de montaje físico con la
configuración de cámara que sí puede tocar overlays. Ruta absoluta, mismo
patrón que SISTEM_IA_DIR en bmo_unified/vision/engine.py: si el archivo no
existe (p. ej. corriendo el pipeline suelto en el venv x86 de desarrollo),
se asume "NONE" y el pipeline sigue funcionando sin rotación.
"""

import json

import cv2

_ROTATE_MAP = {
    "NONE": None,
    "CW": cv2.ROTATE_90_CLOCKWISE,
    "CCW": cv2.ROTATE_90_COUNTERCLOCKWISE,
    "180": cv2.ROTATE_180,
}


def load_rotation(config_path: str) -> str:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f).get("rotate", "NONE")
    except Exception:
        return "NONE"


def apply_rotation(frame, rotate_key: str):
    code = _ROTATE_MAP.get(rotate_key)
    return frame if code is None else cv2.rotate(frame, code)
