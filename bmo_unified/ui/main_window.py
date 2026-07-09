# ui/main_window.py
"""Ventana principal (4.1): pantalla completa sin bordes 1024x600, cara
animada de fondo permanente, navegación de 5 pantallas (Home/Caras/Oraciones/
Video/Salir) vía cinta fantasma infinita, y orquestación de todos los
subsistemas (serie, botones, PECS, visión)."""

import json
import logging
import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QMainWindow, QWidget

from core.button_router import ButtonRouter
from core.pecs_engine import PecsEngine
from core.serial_manager import SerialManager
from ui.animation_player import AnimationPlayer
from ui.calibration_overlay import CalibrationOverlay
from ui.emo_monitor_panel import EmoMonitorPanel
from ui.ghost_ribbon import GhostRibbon
from ui.pecs_panel import PecsPanel
from vision.engine import VisionEngine

log = logging.getLogger("bmo.main_window")

SCREEN_W, SCREEN_H = 1024, 600

# Leyenda de botones físicos activos por pantalla (5.3): qué hace cada botón
# en la vista actual, ya que los roles cambian según la pantalla activa.
LEGEND_TEXT = {
    "HOME": "Toca la cinta inferior para navegar entre pantallas",
    "CARAS": "Cursores ◀ ▶: cara anterior/siguiente (con su audio)",
    "ORACIONES": "Cursores: mover selección  ·  Isla-Medio: borrar palabra  ·  Isla-Izq: limpiar todo  ·  Panel-Der: enviar",
    "VIDEO": "Panel-Centro: iniciar/detener monitoreo emocional",
    "SALIR": "Mantén presionado el icono Salir ~3s para cerrar la app",
}


def _load_greetings(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("greetings", [])
    except Exception:
        return []


class MainWindow(QMainWindow):
    VIEWS = ("HOME", "CARAS", "ORACIONES", "VIDEO", "SALIR")

    def __init__(self, config_dir: str, anim_dir: str, calibrate: bool = False, parent=None):
        super().__init__(parent)

        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setFixedSize(SCREEN_W, SCREEN_H)
        self.setCursor(Qt.BlankCursor)

        central = QWidget(self)
        central.setFixedSize(SCREEN_W, SCREEN_H)
        self.setCentralWidget(central)

        self._greetings = _load_greetings(os.path.join(config_dir, "greetings.json"))

        # ---- fondo permanente (Home y Caras comparten el mismo reproductor) ----
        self._animation = AnimationPlayer(anim_dir, central)
        self._animation.setGeometry(0, 0, SCREEN_W, SCREEN_H)

        # ---- overlays (se superponen al fondo, nunca lo ocultan) ----
        self._monitor_panel = EmoMonitorPanel(central)
        self._monitor_panel.setGeometry(0, 0, SCREEN_W, SCREEN_H)
        self._monitor_panel.setVisible(False)

        # Oraciones: pantalla completa, fondo plano propio (5.3/5.6) -- reemplaza
        # el video de fondo en vez de superponerse semitransparente a él.
        self._pecs_panel = PecsPanel(central)
        self._pecs_panel.setGeometry(0, 0, SCREEN_W, SCREEN_H)
        self._pecs_panel.setVisible(False)

        self._ghost = GhostRibbon(central)
        self._ghost.setGeometry(0, SCREEN_H - 110, SCREEN_W, 110)

        # Leyenda de botones activos en la pantalla actual (evita que los roles
        # físicos por pantalla se sientan "sin función definida").
        self._legend = QLabel(central)
        self._legend.setGeometry(0, 0, SCREEN_W, 28)
        self._legend.setAlignment(Qt.AlignCenter)
        self._legend.setStyleSheet(
            "background: rgba(0,0,0,120); color: white; font-size: 12px;"
        )
        self._legend.setText(LEGEND_TEXT.get("HOME", ""))

        self._calibration = CalibrationOverlay(central)
        self._calibration.setGeometry(0, 0, SCREEN_W, SCREEN_H)
        self._calibration.setVisible(False)

        self._view = "HOME"

        # ---- backend ----
        self._serial = SerialManager()
        self._router = ButtonRouter(os.path.join(config_dir, "button_map.json"))
        self._pecs = PecsEngine(
            os.path.join(config_dir, "rfid_vocab.json"),
            os.path.join(config_dir, "telegram.json"),
        )
        self._vision = VisionEngine()

        self._wire_signals()
        self._serial.start()

        if calibrate:
            self._enter_calibration()

    # ---------- conexiones ----------
    def _wire_signals(self):
        self._serial.rfid_event.connect(self._pecs.add_card)
        self._serial.button_event.connect(self._router.on_button_event)
        self._serial.raw_line.connect(self._calibration.on_raw_line)
        self._serial.connection_changed.connect(self._on_serial_connection_changed)

        self._router.action.connect(self._handle_action)
        self._router.calibration_requested.connect(self._enter_calibration)

        self._pecs.stack_changed.connect(self._pecs_panel.on_stack_changed)
        self._pecs.stack_changed.connect(self._on_stack_changed)
        self._pecs.card_rejected.connect(self._pecs_panel.on_card_rejected)
        self._pecs.send_started.connect(self._pecs_panel.on_send_started)
        self._pecs.sentence_sent.connect(self._pecs_panel.on_sentence_sent)
        self._pecs.send_failed.connect(self._pecs_panel.on_send_failed)

        self._vision.frame_ready.connect(self._monitor_panel.on_frame)
        self._vision.pred_ready.connect(self._monitor_panel.on_pred)
        self._vision.stats_ready.connect(self._monitor_panel.on_stats)

        self._ghost.screen_selected.connect(self._show_view)
        self._ghost.exit_requested.connect(self.close)

        self._monitor_panel.stop_button.clicked.connect(self._toggle_emotion_recognition)
        self._calibration.close_button.clicked.connect(self._exit_calibration)

    def _on_serial_connection_changed(self, connected: bool, port: str):
        log.info("Enlace serie %s (%s)", "conectado" if connected else "desconectado", port)

    def _on_stack_changed(self, words):
        if words and self._view != "ORACIONES":
            self._show_view("ORACIONES")  # 5.2: mostrar automáticamente al detectar la primera tarjeta

    # ---------- navegación de vistas (5.3: máquina de estados) ----------
    def _show_view(self, view: str):
        prev = self._view
        if prev == "VIDEO" and view != "VIDEO" and self._vision.is_running():
            self._vision.stop()

        self._view = view
        self._pecs_panel.setVisible(view == "ORACIONES")
        if view == "ORACIONES":
            self._pecs_panel.new_greeting(self._greetings)

        if view == "VIDEO" and not self._vision.is_running():
            self._vision.start()
        self._monitor_panel.setVisible(view == "VIDEO" and self._vision.is_running())

        self._legend.setText(LEGEND_TEXT.get(view, ""))

        self._ghost.set_active(view, emit=False)

    def _go_home(self):
        self._show_view("HOME")

    # ---------- acciones de botones físicos (5.3: cursores dependen de pantalla) ----------
    def _handle_action(self, role: str):
        log.info("Acción: %s", role)
        if role.startswith("CURSOR_"):
            self._handle_cursor(role)
        elif role == "PECS_SEND":
            self._pecs.send()
        elif role == "PECS_DELETE":
            if self._view == "ORACIONES":
                idx = self._pecs_panel.selected_index()
                if idx >= 0:
                    self._pecs.delete_at(idx)
        elif role == "PECS_CLEAR":
            self._pecs.clear()
        elif role == "EMO_TOGGLE":
            self._toggle_emotion_recognition()
        elif role == "DYNAMIC_PLAY":
            self._animation.play_dynamic()
        elif role == "HOME":
            self._go_home()
        else:
            log.warning("Rol lógico desconocido: %s", role)

    def _handle_cursor(self, role: str):
        if self._view == "CARAS":
            if role == "CURSOR_LEFT":
                self._animation.prev_clip()
            elif role == "CURSOR_RIGHT":
                self._animation.next_clip()
            # CURSOR_UP/CURSOR_DOWN reservados (5.3), sin efecto por ahora.
        elif self._view == "ORACIONES":
            if role in ("CURSOR_LEFT", "CURSOR_UP"):
                self._pecs_panel.move_selection(-1)
            elif role in ("CURSOR_RIGHT", "CURSOR_DOWN"):
                self._pecs_panel.move_selection(1)
        # HOME/VIDEO/SALIR: cursores sin efecto (reposo).

    def _toggle_emotion_recognition(self):
        if self._vision.is_running():
            self._vision.stop()
            self._monitor_panel.setVisible(False)
            self._go_home()
        else:
            self._vision.start()
            self._show_view("VIDEO")

    # ---------- calibración (3.4) ----------
    def _enter_calibration(self):
        self._calibration.setVisible(True)
        self._calibration.raise_()

    def _exit_calibration(self):
        self._calibration.setVisible(False)

    # ---------- toque de pantalla -> cinta fantasma ----------
    def mousePressEvent(self, event):
        self._ghost.notify_touch()
        self._ghost.raise_()
        super().mousePressEvent(event)

    # ---------- cierre limpio (7.2) ----------
    def shutdown(self):
        log.info("Cerrando MainWindow: deteniendo subsistemas…")
        if self._vision.is_running():
            self._vision.stop()
        self._serial.stop()
        self._serial.wait(2000)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
