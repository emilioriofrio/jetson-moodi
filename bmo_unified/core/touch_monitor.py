# core/touch_monitor.py
"""
Vigilante del panel táctil (S12).

Por qué existe: el usuario reporta que el táctil "a veces no funciona desde el
arranque o se pierde a medio funcionamiento" y hay que recurrir al ratón.
Diagnosticado en el momento del fallo: el kernel no lista NINGÚN dispositivo
táctil (`/proc/bus/input/devices` solo muestra teclado/ratón, webcam, gpio-keys
y audio), o sea que el panel desaparece del bus USB. No es que la app pierda
los eventos: no hay eventos que perder.

Este vigilante no puede resucitar hardware, y no pretende hacerlo. Hace las dos
cosas que sí puede hacer desde el espacio de usuario:

1. **Dejar constancia con hora exacta** de cuándo aparece y cuándo desaparece el
   táctil. Sin esto, "a veces se pierde" es irreproducible; con esto se puede
   correlacionar con lo que estaba haciendo la app (¿al arrancar el motor de
   visión? ¿tras un rato inactivo, o sea autosuspend de USB?).
2. **Volver a mapearlo a la salida de video** en cuanto reaparece. Un táctil que
   se reconecta a mitad de sesión vuelve sin la asociación a la pantalla y sus
   toques pueden caer en coordenadas equivocadas; `xinput map-to-output` lo
   arregla y aquí se aplica solo, sin que el usuario tenga que saberlo.

Complementa (no sustituye) a instalar_regla_tactil.sh, que ataca la causa más
probable de la desaparición: el autosuspend de USB a los 2 s.

Sondeo por temporizador y no inotify sobre /dev/input a propósito: son 5 s de
periodo y leer un archivo de /proc cuesta microsegundos, mientras que un
watcher de /dev requiere permisos y casos especiales que no compensan aquí.
"""

import logging
import re
import subprocess

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger("bmo.touch_monitor")

DEVICES_PATH = "/proc/bus/input/devices"
POLL_MS = 5000

# Nombres típicos de controladores táctiles USB en paneles como el ElecLab.
_PATRON_NOMBRE = re.compile(
    r"touch|ilitek|goodix|egalax|silead|ft5|hid.*multi", re.IGNORECASE)


def _es_tactil(nombre: str, abs_bits: str) -> bool:
    """Un táctil se reconoce por sus ejes ABS multitáctil (ABS_MT_POSITION_X/Y,
    bits 0x35/0x36) o, en su defecto, por el nombre del dispositivo."""
    if abs_bits:
        try:
            bits = int(abs_bits, 16)
            if bits & (1 << 0x35) and bits & (1 << 0x36):
                return True
        except ValueError:
            pass
    return bool(_PATRON_NOMBRE.search(nombre))


def detectar_tactil():
    """Devuelve el nombre del primer dispositivo táctil presente, o None."""
    try:
        with open(DEVICES_PATH, "r", encoding="utf-8", errors="replace") as f:
            contenido = f.read()
    except OSError:
        return None

    for bloque in contenido.split("\n\n"):
        m_nombre = re.search(r'^N: Name="(.*)"', bloque, re.MULTILINE)
        if not m_nombre:
            continue
        m_abs = re.search(r"^B: ABS=([0-9a-f]+)", bloque, re.MULTILINE)
        if _es_tactil(m_nombre.group(1), m_abs.group(1) if m_abs else ""):
            return m_nombre.group(1)
    return None


class TouchMonitor(QObject):
    """Emite presence_changed(bool, nombre) al aparecer/desaparecer el táctil."""

    presence_changed = pyqtSignal(bool, str)

    def __init__(self, salida_video: str = "HDMI-0", parent=None):
        super().__init__(parent)
        self._salida = salida_video
        self._nombre = detectar_tactil()
        self._presente = self._nombre is not None

        if self._presente:
            log.info("Panel táctil detectado al arrancar: %r", self._nombre)
            self._mapear_a_pantalla()
        else:
            log.warning(
                "NO hay panel táctil conectado: la app solo responderá al ratón. "
                "Ver bmo_unified/tests/diagnostico_tactil.sh")

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._comprobar)
        self._timer.start()

    def is_present(self) -> bool:
        return self._presente

    def device_name(self) -> str:
        return self._nombre or ""

    def _comprobar(self):
        nombre = detectar_tactil()
        presente = nombre is not None
        if presente == self._presente:
            return

        self._presente = presente
        self._nombre = nombre
        if presente:
            log.warning("Panel táctil RECONECTADO (%r): remapeando a %s",
                        nombre, self._salida)
            self._mapear_a_pantalla()
        else:
            log.error("Panel táctil DESAPARECIDO del bus: a partir de ahora solo "
                      "responde el ratón (ver instalar_regla_tactil.sh)")
        self.presence_changed.emit(presente, nombre or "")

    def _mapear_a_pantalla(self):
        """Asocia el táctil a la salida de video. Al reconectarse a mitad de
        sesión el dispositivo vuelve sin esa asociación y los toques pueden caer
        desplazados; se hace 'best effort' y nunca se propaga un fallo."""
        if not self._nombre:
            return
        try:
            listado = subprocess.run(["xinput", "list", "--id-only", self._nombre],
                                     capture_output=True, text=True, timeout=5)
            ids = [linea.strip() for linea in listado.stdout.splitlines() if linea.strip()]
            for dev_id in ids:
                subprocess.run(["xinput", "map-to-output", dev_id, self._salida],
                               capture_output=True, timeout=5)
            log.info("Táctil %r mapeado a %s (ids=%s)", self._nombre, self._salida, ids)
        except Exception:
            log.exception("No se pudo mapear el táctil a %s", self._salida)
