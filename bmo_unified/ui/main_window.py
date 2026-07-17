# ui/main_window.py
"""Ventana principal (4.1): pantalla completa sin bordes 1024x600, cara
animada de fondo permanente, navegación de 6 pantallas (Home/Caras/Oraciones/
Video/Configuraciones/Salir) vía cinta fantasma infinita, y orquestación de
todos los subsistemas (serie, botones, PECS, visión, voz, preferencias).

Preferencias en caliente (fase UI/CONFIG/VOZ): AppSettings emite señales al
cambiar idioma/tamaño de fuente/volumen desde la pantalla Configuraciones y
esta ventana propaga -- retranslate() y restyle() en cada panel, volumen al
reproductor de animaciones (la voz lo lee al hablar). Nada requiere reiniciar
la app."""

import logging
import os
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QLabel, QMainWindow, QWidget

from core import greetings as greetings_mod
from core import i18n, telegram_sender
from core.button_router import ButtonRouter, GPIO_PHYS
from core.i18n import t
from core.pecs_engine import PecsEngine
from core.serial_manager import SerialManager
from core.voice import VoiceEngine
from ui import theme
from ui.animation_player import AnimationPlayer
from ui.calibration_overlay import CalibrationOverlay
from ui.emo_monitor_panel import EmoMonitorPanel
from ui.ghost_ribbon import GhostRibbon
from ui.pecs_panel import PecsPanel
from ui.settings_panel import SettingsPanel
from vision.engine import VisionEngine

log = logging.getLogger("bmo.main_window")

SCREEN_W, SCREEN_H = 1024, 600

# Leyenda temporal con el título del clip al cambiar de cara (5.5).
CAPTION_SHOW_MS = 2500

# Oraciones (5.6): recuadro de la carita de Moodi, más chica, "preguntando" por
# la entrada de palabras -- mismo AnimationPlayer, solo reposicionado/reescalado.
# 16:9 (igual que los clips fuente, 1920x1080): un recuadro cuadrado obligaba a
# recortar/escalar la cara de forma pareja en X e Y contra un origen no cuadrado,
# lo que se percibía "aplastada"; manteniendo la proporción original no hace
# falta recortar para llenar el recuadro.
ORACIONES_FACE_RECT = (SCREEN_W // 2 - 160, 28, 320, 180)

# Alerta automática de Telegram (nivel ALTO sostenido + cooldown, para no saturar).
ALERT_SUSTAIN_SECS = 12
ALERT_COOLDOWN_SECS = 300


class MainWindow(QMainWindow):
    VIEWS = ("HOME", "CARAS", "ORACIONES", "VIDEO", "CONFIG", "SALIR")

    def __init__(self, config_dir: str, anim_dir: str, settings, calibrate: bool = False, parent=None):
        super().__init__(parent)

        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setFixedSize(SCREEN_W, SCREEN_H)
        self.setCursor(Qt.BlankCursor)

        central = QWidget(self)
        central.setFixedSize(SCREEN_W, SCREEN_H)
        self.setCentralWidget(central)

        self._settings = settings
        self._greetings_data = greetings_mod.load_greetings(
            os.path.join(config_dir, "greetings.json"))

        # ---- fondo permanente (Home y Caras comparten el mismo reproductor) ----
        self._animation = AnimationPlayer(anim_dir, central)
        self._animation.setGeometry(0, 0, SCREEN_W, SCREEN_H)
        self._animation.set_volume(settings.volume)

        # ---- overlays (se superponen al fondo, nunca lo ocultan) ----
        self._monitor_panel = EmoMonitorPanel(central)
        self._monitor_panel.setGeometry(0, 0, SCREEN_W, SCREEN_H)
        self._monitor_panel.setVisible(False)

        # Oraciones: pantalla completa, fondo plano propio (5.3/5.6) -- reemplaza
        # el video de fondo en vez de superponerse semitransparente a él.
        self._pecs_panel = PecsPanel(central)
        self._pecs_panel.setGeometry(0, 0, SCREEN_W, SCREEN_H)
        self._pecs_panel.setVisible(False)

        # ---- backend (el router se crea antes que SettingsPanel, que lo usa) ----
        self._serial = SerialManager()
        self._router = ButtonRouter(os.path.join(config_dir, "button_map.json"))
        self._telegram_config_path = os.path.join(config_dir, "telegram.json")
        self._pecs = PecsEngine(
            os.path.join(config_dir, "rfid_vocab.json"),
            self._telegram_config_path,
        )
        self._vision = VisionEngine()
        self._voice = VoiceEngine(settings)

        # Configuraciones: pantalla completa de fondo plano, como Oraciones.
        self._settings_panel = SettingsPanel(
            settings, self._router, os.path.join(config_dir, "button_map.json"), central)
        self._settings_panel.setGeometry(0, 0, SCREEN_W, SCREEN_H)
        self._settings_panel.setVisible(False)

        self._ghost = GhostRibbon(central)
        self._ghost.setGeometry(0, SCREEN_H - 110, SCREEN_W, 110)

        # Leyenda de botones activos en la pantalla actual (evita que los roles
        # físicos por pantalla se sientan "sin función definida"). Visible solo
        # en sintonía con la cinta fantasma (aparece/desaparece junto a ella,
        # ver GhostRibbon.visibility_changed) -- permanente era puro ruido
        # visual constante. El texto se compone dinámicamente del mapa REAL de
        # botones (remapeables en Configuraciones), no de textos fijos.
        self._legend = QLabel(central)
        self._legend.setAlignment(Qt.AlignCenter)
        self._legend.setVisible(False)

        # Leyenda temporal con el título del clip al cambiar de cara (5.5).
        self._caption = QLabel(central)
        self._caption.setAlignment(Qt.AlignCenter)
        # Sin QGraphicsOpacityEffect a propósito: cualquier QGraphicsEffect en
        # esta ventana obliga a Qt a componer toda la ventana en un buffer
        # offscreen, lo que rompe el overlay de video nativo de AnimationPlayer
        # (confirmado en diagnóstico: el video se congela tras el primer loop
        # apenas hay un QGraphicsEffect activo en la misma ventana, sin
        # importar en qué widget) -- se muestra/oculta al instante en su lugar.
        self._caption.setVisible(False)
        self._caption_hide_timer = QTimer(self)
        self._caption_hide_timer.setSingleShot(True)
        self._caption_hide_timer.timeout.connect(self._hide_caption)

        self._calibration = CalibrationOverlay(central)
        self._calibration.setGeometry(0, 0, SCREEN_W, SCREEN_H)
        self._calibration.setVisible(False)

        self._view = "HOME"

        # Alerta automática por Telegram cuando el nivel global se sostiene en ALTO.
        self._alto_since = None
        self._last_alert_ts = 0.0

        self._restyle_all()
        self._legend.setText(self._legend_text("HOME"))

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
        self._router.map_reloaded.connect(self._on_button_map_reloaded)

        self._pecs.stack_changed.connect(self._pecs_panel.on_stack_changed)
        self._pecs.stack_changed.connect(self._on_stack_changed)
        self._pecs.card_rejected.connect(self._pecs_panel.on_card_rejected)
        self._pecs.send_started.connect(self._pecs_panel.on_send_started)
        self._pecs.sentence_sent.connect(self._pecs_panel.on_sentence_sent)
        self._pecs.send_failed.connect(self._pecs_panel.on_send_failed)

        # Voz de Moodi: narra cada palabra apilada y la frase corregida final
        # (esta última interrumpe: no debe esperar detrás de palabras sueltas).
        self._pecs.word_added.connect(self._voice.speak)
        self._pecs.sentence_sent.connect(
            lambda _raw, corrected: self._voice.speak(corrected, interrupt=True))

        self._vision.frame_ready.connect(self._monitor_panel.on_frame)
        self._vision.pred_ready.connect(self._monitor_panel.on_pred)
        self._vision.stats_ready.connect(self._monitor_panel.on_stats)
        self._vision.stats_ready.connect(self._on_stress_stats)

        self._animation.clip_changed.connect(self._show_caption)

        self._ghost.screen_selected.connect(self._show_view)
        self._ghost.exit_requested.connect(self.close)
        self._ghost.visibility_changed.connect(self._legend.setVisible)

        self._monitor_panel.stop_button.clicked.connect(self._toggle_emotion_recognition)
        self._calibration.close_button.clicked.connect(self._exit_calibration)

        # Preferencias en caliente (Configuraciones).
        self._settings.language_changed.connect(self._on_language_changed)
        self._settings.font_scale_changed.connect(self._on_font_scale_changed)
        self._settings.volume_changed.connect(self._animation.set_volume)

    def _on_serial_connection_changed(self, connected: bool, port: str):
        log.info("Enlace serie %s (%s)", "conectado" if connected else "desconectado", port)

    def _on_stack_changed(self, words):
        if words and self._view != "ORACIONES":
            self._show_view("ORACIONES")  # 5.2: mostrar automáticamente al detectar la primera tarjeta

    # ---------- preferencias en caliente ----------
    def _on_language_changed(self, lang: str):
        i18n.set_language(lang)
        log.info("Idioma cambiado en caliente a %r", lang)
        self._retranslate_all()

    def _on_font_scale_changed(self, level: str):
        theme.set_font_scale(level)
        log.info("Escala de fuente cambiada en caliente a %r", level)
        self._restyle_all()

    def _on_button_map_reloaded(self, _mapping: dict):
        # la leyenda nombra botones físicos por su rol: refrescarla al remapear
        self._legend.setText(self._legend_text(self._view))

    def _retranslate_all(self):
        self._ghost.retranslate()
        self._pecs_panel.retranslate()
        self._monitor_panel.retranslate()
        self._calibration.retranslate()
        self._settings_panel.retranslate()
        self._legend.setText(self._legend_text(self._view))
        if self._view == "ORACIONES":
            self._pecs_panel.new_greeting(self._pick_greeting())

    def _restyle_all(self):
        self._ghost.restyle()
        self._pecs_panel.restyle()
        self._monitor_panel.restyle()
        self._calibration.restyle()
        self._settings_panel.restyle()

        legend_h = theme.fs(18) + 16
        self._legend.setGeometry(0, 0, SCREEN_W, legend_h)
        self._legend.setStyleSheet(
            f"background: rgba(0,0,0,120); color: white; font-size: {theme.fs(15)}px;")

        self._caption.setGeometry(SCREEN_W // 2 - 220, 40, 440, theme.fs(24) + 28)
        self._caption.setStyleSheet(
            f"background: rgba(0,0,0,150); color: white; font-size: {theme.fs(24)}px; "
            "font-weight: bold; border-radius: 12px;")

    # ---------- leyenda dinámica de botones por pantalla (5.3) ----------
    def _phys_name(self, role: str) -> str:
        gpio = self._router.gpio_for_role(role)
        if gpio is None:
            return ""
        return t(f"phys.{GPIO_PHYS.get(gpio, gpio)}")

    def _legend_text(self, view: str) -> str:
        if view == "ORACIONES":
            parts = [t("legend.oraciones.cursors")]
            for key, role in (("legend.oraciones.delete", "PECS_DELETE"),
                              ("legend.oraciones.clear", "PECS_CLEAR"),
                              ("legend.oraciones.send", "PECS_SEND")):
                phys = self._phys_name(role)
                if phys:
                    parts.append(t(key, btn=phys))
            return "  ·  ".join(parts)
        if view == "VIDEO":
            phys = self._phys_name("EMO_TOGGLE")
            return t("legend.video", btn=phys) if phys else ""
        return t(f"legend.{view.lower()}")

    # ---------- navegación de vistas (5.3: máquina de estados) ----------
    def _show_view(self, view: str):
        prev = self._view
        if prev == "VIDEO" and view != "VIDEO" and self._vision.is_running():
            self._vision.stop()
        if prev == "CONFIG" and view != "CONFIG":
            self._settings_panel.on_left()

        self._view = view

        # Habilitar audio solo en CARAS (5.5)
        self._animation.set_muted(view != "CARAS")

        self._pecs_panel.setVisible(view == "ORACIONES")
        if view == "ORACIONES":
            self._pecs_panel.new_greeting(self._pick_greeting())
            # 5.6: la carita de Moodi se achica y flota sobre el fondo plano,
            # como si preguntara por la entrada de palabras. raise_() aquí deja
            # a `_animation` por encima de TODO lo que se creó antes que ella
            # (incluido `_monitor_panel`) hasta que algo la vuelva a superar --
            # de lo contrario, al visitar después la pantalla Video, el fondo
            # animado (ahora de pantalla completa otra vez) tapa el panel de
            # monitoreo entero, incluido su botón de detener. Por eso cada rama
            # reafirma explícitamente el orden correcto para SU pantalla en
            # vez de asumir el orden de creación.
            self._pecs_panel.raise_()
            self._animation.setGeometry(*ORACIONES_FACE_RECT)
            self._animation.raise_()
        else:
            self._animation.setGeometry(0, 0, SCREEN_W, SCREEN_H)

        self._settings_panel.setVisible(view == "CONFIG")
        if view == "CONFIG":
            self._settings_panel.on_entered()
            self._settings_panel.raise_()  # cubre el fondo animado (mismo patrón que VIDEO)

        if view == "VIDEO" and not self._vision.is_running():
            self._vision.start()
        self._monitor_panel.setVisible(view == "VIDEO" and self._vision.is_running())
        if view == "VIDEO":
            self._monitor_panel.raise_()  # cubre el fondo animado, nunca al revés

        # Elevar la cinta fantasma y la leyenda al frente para que nunca queden tapadas
        self._ghost.raise_()
        self._legend.raise_()

        self._legend.setText(self._legend_text(view))

        self._ghost.set_active(view, emit=False)

    def _pick_greeting(self) -> str:
        return greetings_mod.pick_greeting(
            self._greetings_data, self._settings.language, self._settings.nickname)

    # ---------- leyenda temporal de título al cambiar de cara (5.5) ----------
    def _show_caption(self, title: str):
        if self._view != "CARAS":
            return  # esta leyenda es solo para Caras; nunca debe aparecer en otras pantallas
        self._caption.setText(title)
        self._caption.setVisible(True)
        self._caption.raise_()
        self._caption_hide_timer.start(CAPTION_SHOW_MS)

    def _hide_caption(self):
        self._caption.setVisible(False)

    # ---------- alerta automática de Telegram (nivel ALTO sostenido) ----------
    def _on_stress_stats(self, stats: dict):
        label = str(stats.get("label", "")).upper()
        now = time.monotonic()

        if label != "ALTO":
            self._alto_since = None
            return

        if self._alto_since is None:
            self._alto_since = now
            return

        sustained = (now - self._alto_since) >= ALERT_SUSTAIN_SECS
        cooled_down = (now - self._last_alert_ts) >= ALERT_COOLDOWN_SECS
        if sustained and cooled_down:
            self._last_alert_ts = now
            log.info("Nivel ALTO sostenido %.0fs -- enviando alerta por Telegram", now - self._alto_since)
            telegram_sender.send_message_async(
                t("telegram.alert_high"),
                config_path=self._telegram_config_path,
            )

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
            self._animation.set_muted(False)
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
        # HOME/VIDEO/CONFIG/SALIR: cursores sin efecto (reposo).

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
        self._legend.raise_()
        super().mousePressEvent(event)

    # ---------- cierre limpio (7.2) ----------
    def shutdown(self):
        log.info("Cerrando MainWindow: deteniendo subsistemas…")
        if self._vision.is_running():
            self._vision.stop()
        self._voice.shutdown()
        self._serial.stop()
        self._serial.wait(2000)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
