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

# Mapeo fijo GPIO -> etiqueta física del botón en el cuerpo de Moodi
# (CONTEXTO_MOODI_UI_INTERACTIVA_08072026.md sección 3.2, mapa definitivo ya
# cableado y flasheado). Esto es conocimiento de HARDWARE, no de configuración:
# qué rol lógico tiene cada botón vive en button_map.json; qué botón físico es
# cada GPIO no cambia sin recablear. Las claves phys.* se traducen vía i18n.
GPIO_PHYS = {
    "4": "cursor_up",
    "16": "cursor_down",
    "32": "cursor_left",
    "33": "cursor_right",
    "27": "isla_l",
    "26": "isla_m",
    "25": "isla_r",
    "14": "panel_l",
    "13": "panel_c",
    "17": "panel_r",
}

# Restricción heredada no editable (CONTEXTO 3.3, regla dura): el botón
# central largo (PANEL_C/GPIO13) siempre es EMO_TOGGLE. La UI de remapeo lo
# muestra bloqueado y ButtonRouter lo reimpone al recargar, por si el JSON se
# editó a mano.
LOCKED_GPIO = "13"
LOCKED_ROLE = "EMO_TOGGLE"


class ButtonRouter(QObject):
    action = pyqtSignal(str)              # rol lógico disparado
    map_reloaded = pyqtSignal(dict)       # mapa recargado en caliente (Configuraciones)
    calibration_requested = pyqtSignal()  # long-press sobre EMO_TOGGLE

    def __init__(self, button_map_path: str, parent=None):
        super().__init__(parent)
        self._path = button_map_path
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
        mapping = {str(k): v for k, v in data.items() if not str(k).startswith("_")}
        if mapping and mapping.get(LOCKED_GPIO) != LOCKED_ROLE:
            log.warning(
                "button_map.json trae GPIO%s=%r; se reimpone %s (restricción fija)",
                LOCKED_GPIO, mapping.get(LOCKED_GPIO), LOCKED_ROLE,
            )
            mapping[LOCKED_GPIO] = LOCKED_ROLE
        return mapping

    # ---------- recarga en caliente (Configuraciones -> remapeo de botones) ----------
    def reload(self):
        """Relee button_map.json y aplica el mapa nuevo sin reiniciar la app.
        Los presses en vuelo (_press_start) se conservan: el rol se resuelve
        al soltar con el mapa vigente en ese momento."""
        self._map = self._load_map(self._path)
        log.info("Mapa de botones recargado en caliente: %s", self._map)
        self.map_reloaded.emit(dict(self._map))

    def current_map(self) -> dict:
        return dict(self._map)

    def gpio_for_role(self, role: str):
        """Primer GPIO asignado a 'role', o None si el rol quedó sin botón
        (usado por la leyenda dinámica de MainWindow)."""
        for gpio, r in self._map.items():
            if r == role:
                return gpio
        return None

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
