# core/button_router.py
"""
Traduce eventos de botón crudos (GPIO + presionado/soltado) a roles lógicos,
según bmo_unified/config/button_map.json -- única fuente de verdad del mapeo
(ver REQUERIMIENTOS_APP_MOODI.md 3.3: "toda asignación debe quedar
centralizada en un único mapa, nunca dispersa").

También detecta el long-press de 5s sobre el GPIO mapeado a EMO_TOGGLE para
entrar al modo calibración (3.4).
"""

import json
import logging
import time

from PyQt5.QtCore import QObject, pyqtSignal

log = logging.getLogger("bmo.button_router")

LONG_PRESS_S = 5.0


class ButtonRouter(QObject):
    action = pyqtSignal(str)              # rol lógico disparado
    calibration_requested = pyqtSignal()  # long-press sobre EMO_TOGGLE

    def __init__(self, button_map_path: str, parent=None):
        super().__init__(parent)
        self._map = self._load_map(button_map_path)
        self._press_start = {}  # gpio -> timestamp monotonic

    @staticmethod
    def _load_map(path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            log.exception("No se pudo cargar button_map desde %s", path)
            return {}
        return {str(k): v for k, v in data.items() if not str(k).startswith("_")}

    def on_button_event(self, gpio: int, pressed: bool):
        role = self._map.get(str(gpio))
        if role is None:
            return  # GPIO sin rol asignado (el modo calibración lo ve vía raw_line)

        if pressed:
            self._press_start[gpio] = time.monotonic()
            if role != "EMO_TOGGLE":
                self.action.emit(role)
            return

        # soltado
        t0 = self._press_start.pop(gpio, None)
        if role == "EMO_TOGGLE":
            held = (time.monotonic() - t0) if t0 is not None else 0.0
            if held >= LONG_PRESS_S:
                self.calibration_requested.emit()
            else:
                self.action.emit(role)
