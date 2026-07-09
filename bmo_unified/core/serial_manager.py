# core/serial_manager.py
"""
Hilo de lectura del enlace serie con el ESP32 (botones + RFID).
Autodetecta /dev/ttyUSB* y /dev/ttyACM*, reconecta solo si el enlace cae,
y nunca lanza una excepción hacia el hilo de Qt por una trama corrupta.

Protocolo de línea (ver REQUERIMIENTOS_APP_MOODI.md):
  BTN:<gpio>:DOWN|UP   -> evento de botón (gpio = número de pin físico, no rol lógico)
  RFID:<b0>,<b1>,<b2>,<b3> -> UID crudo de una tarjeta leída
  BOOT:OK              -> saludo del ESP32 al iniciar/reconectar
"""

import glob
import logging
import time

import serial
from PyQt5.QtCore import QThread, pyqtSignal

log = logging.getLogger("bmo.serial_manager")

BAUD = 115200
RECONNECT_DELAY_S = 1.0
READ_TIMEOUT_S = 0.2


class SerialManager(QThread):
    button_event = pyqtSignal(int, bool)        # gpio, pressed
    rfid_event = pyqtSignal(tuple)               # (b0, b1, b2, b3)
    raw_line = pyqtSignal(str)                   # línea cruda (para modo calibración)
    connection_changed = pyqtSignal(bool, str)    # conectado, puerto

    def __init__(self, baud: int = BAUD, parent=None):
        super().__init__(parent)
        self._baud = baud
        self._stop = False
        self._ser = None

    def stop(self):
        self._stop = True
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:
            pass

    @staticmethod
    def _candidate_ports():
        return sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))

    def _open_any(self):
        for dev in self._candidate_ports():
            try:
                ser = serial.Serial(dev, self._baud, timeout=READ_TIMEOUT_S)
                log.info("Puerto serie abierto: %s", dev)
                return ser, dev
            except Exception:
                continue
        return None, None

    def _parse_line(self, line: str):
        line = line.strip()
        if not line:
            return
        self.raw_line.emit(line)
        try:
            if line.startswith("BTN:"):
                _, gpio_s, state_s = line.split(":", 2)
                self.button_event.emit(int(gpio_s), state_s.upper() == "DOWN")
            elif line.startswith("RFID:"):
                parts = line[len("RFID:"):].split(",")
                if len(parts) == 4:
                    uid = tuple(int(p) for p in parts)
                    self.rfid_event.emit(uid)
            elif line.startswith("BOOT:"):
                log.info("ESP32: %s", line)
            # cualquier otra línea (ruido/basura) se ignora a propósito
        except Exception:
            log.warning("Trama serie no reconocida, se ignora: %r", line)

    def run(self):
        while not self._stop:
            ser, dev = self._open_any()
            if ser is None:
                time.sleep(RECONNECT_DELAY_S)
                continue

            self._ser = ser
            self.connection_changed.emit(True, dev)
            try:
                while not self._stop:
                    try:
                        raw = ser.readline()
                    except Exception:
                        break  # puerto caído -> salir a reconectar
                    if not raw:
                        continue  # timeout sin datos, seguir esperando
                    try:
                        line = raw.decode("utf-8", errors="ignore")
                    except Exception:
                        continue
                    self._parse_line(line)
            finally:
                try:
                    ser.close()
                except Exception:
                    pass
                self._ser = None
                self.connection_changed.emit(False, dev)

            if not self._stop:
                time.sleep(RECONNECT_DELAY_S)
