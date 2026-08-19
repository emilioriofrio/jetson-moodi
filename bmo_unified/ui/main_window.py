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
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QMainWindow, QPushButton, QWidget

from core import greetings as greetings_mod
from core import i18n, telegram_sender
from core.background_music import BackgroundMusicPlayer
from core.button_router import ButtonRouter, GPIO_PHYS
from core.i18n import t
from core.pecs_engine import PecsEngine
from core.serial_manager import SerialManager
from core.touch_monitor import TouchMonitor
from core.voice import VoiceEngine
from ui import theme
from ui.animation_player import AnimationPlayer
from ui.calibration_overlay import CalibrationOverlay
from ui.emo_monitor_panel import EmoMonitorPanel
from ui.ghost_ribbon import GhostRibbon
from ui.overlays import EMOJI_FONT, ExitConfirmCard, MarqueeLabel, PopBadge
from ui.pecs_panel import PecsPanel
from ui.settings_panel import MUSIC_DIR, SettingsPanel
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

# S13: confirmación de Salir por botón físico (segunda presión de PECS_SEND/
# "panel derecho" dentro de esta ventana confirma; si no llega, se descarta sola).
EXIT_CONFIRM_WINDOW_MS = 4000

# S13 (8ª ronda): botones táctiles de silenciar sonido / video-sin-cortes,
# arriba a la derecha, SOLO visibles en Oraciones (ver _refresh_toggle_buttons
# y _show_view) -- en el resto de pantallas el interruptor es la isla física
# de 3 botones (ver _handle_action). Mismo estilo circular ya probado sin
# problemas en el botón ✕ del monitor emocional (S11/S12).
TOGGLE_BTN_SIZE = 48
TOGGLE_BTN_MARGIN = 12
TOGGLE_BTN_GAP = 8
_TOGGLE_BTN_STYLE = (
    "QPushButton {{ background: rgba(0,0,0,120); border-radius: {r}px; "
    "border: 2px solid rgba(255,255,255,90); }}"
    "QPushButton:pressed {{ background: #FF9500; border: 2px solid #FF9500; }}"
)


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
        # Música de fondo (S13): suena en vez del audio de Moodi mientras ese
        # audio está silenciado -- ver _apply_audio_state().
        self._bg_music = BackgroundMusicPlayer(MUSIC_DIR)
        self._bg_music.set_track(settings.music_track)
        self._bg_music.set_volume(settings.music_volume)
        # Vigilante del táctil (S12): el panel desaparece del bus USB cada
        # tanto; esto lo deja registrado con hora y lo remapea al reaparecer.
        self._touch = TouchMonitor()

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
        # MarqueeLabel (S13, no QLabel): con la navegación global de cursores
        # el texto de algunas pantallas (p. ej. Oraciones) ya no entra en una
        # sola línea a 1024px -- en vez de recortarse, se desplaza como un
        # banner cuando no cabe, y queda quieto y centrado cuando sí cabe.
        self._legend = MarqueeLabel(central)
        self._legend.setVisible(False)
        # La leyenda es puramente informativa y ocupa una franja de ancho
        # completo en la parte superior: sin esto se COME los toques de lo que
        # haya debajo (los eventos de un widget suben a su padre, no pasan al
        # widget de al lado). Es lo que dejaba inaccesible la parte de arriba
        # del botón ✕ del monitor emocional, reportado en S12. MarqueeLabel ya
        # nace con WA_TransparentForMouseEvents (ver ui/overlays.py).

        # Insignias emergentes (S13): se deslizan con rebote y se autoocultan
        # solas. _caption anuncia el título de clip al cambiar de cara en
        # Caras; _audio_toast confirma silenciar/video-sin-cortes cuando se
        # dispara por botón FÍSICO (isla de 3, ver _handle_action) -- en esas
        # pantallas no hay un botón visible en pantalla que ya muestre el
        # estado, así que hace falta el aviso.
        self._caption = PopBadge(central)
        self._audio_toast = PopBadge(central)

        # Botones táctiles de silenciar/activar sonido y video-sin-cortes
        # (S13, 8ª ronda): SOLO existen/se muestran en Oraciones, porque ahí
        # la isla de 3 botones físicos está ocupada con su función propia
        # (borrar palabra/todo) y no queda un botón físico libre para esto.
        # En el resto de pantallas la isla física sigue haciendo de
        # interruptor (con el aviso emergente de arriba) -- ver
        # _handle_action y _show_view.
        self._btn_audio_toggle = QPushButton(central)
        self._btn_seamless_toggle = QPushButton(central)
        for btn in (self._btn_audio_toggle, self._btn_seamless_toggle):
            btn.setFixedSize(TOGGLE_BTN_SIZE, TOGGLE_BTN_SIZE)
            btn.setCursor(Qt.PointingHandCursor)
            # S13 (11ª ronda): bug real confirmado -- a diferencia de TODOS
            # los demás overlays de esta ventana (_monitor_panel, _pecs_panel,
            # _settings_panel, _legend, _caption/_audio_toast -- éste último
            # oculto dentro del propio __init__ de PopBadge), a estos dos
            # botones les faltaba este setVisible(False). Como _show_view()
            # nunca se llama en el arranque (self._view = "HOME" se fija como
            # atributo simple, no navegando), quedaban con la visibilidad por
            # defecto de Qt (visibles) hasta la primera navegación real -- se
            # veían los dos botones en Home apenas abría la app.
            btn.setVisible(False)
        self._btn_audio_toggle.clicked.connect(self._toggle_audio)
        self._btn_seamless_toggle.clicked.connect(self._toggle_seamless_mode)

        # Tarjeta de confirmación de Salir por botón físico (S13): PECS_SEND
        # ("panel derecho") funciona como Enter/Confirmar cuando la pantalla
        # activa es Salir -- ver _handle_action/_handle_exit_confirm_press.
        self._exit_confirm = ExitConfirmCard(central)
        self._exit_confirm.setGeometry(0, 0, SCREEN_W, SCREEN_H)

        self._calibration = CalibrationOverlay(central)
        self._calibration.setGeometry(0, 0, SCREEN_W, SCREEN_H)
        self._calibration.setVisible(False)

        self._view = "HOME"
        # S13: preferencia manual de silencio DENTRO de Caras (AUDIO_TOGGLE /
        # "isla derecha") -- arranca en False (con sonido).
        self._audio_muted = False
        # S13 (6ª ronda): SEAMLESS_TOGGLE ("isla izquierda") -- True (por
        # defecto) fuerza el audio de Moodi a sonar SOLO en Caras, para que
        # el video de fondo pueda usar siempre el loop sin corte en el resto
        # de pantallas (ver ui/animation_player.py). En False, el audio de
        # Moodi vuelve a sonar en cualquier pantalla (comportamiento probado
        # dos rondas atrás), aceptando el corte de loop fuera de Caras --
        # queda como opción manual para quien prefiera sonido por sobre
        # video perfecto. Ver _effective_audio_muted().
        self._seamless_mode = True

        # Alerta automática por Telegram cuando el nivel global se sostiene en ALTO.
        self._alto_since = None
        self._last_alert_ts = 0.0
        # S13: última predicción conocida de cada módulo (A/B/C), para que el
        # reporte al cuidador incluya el detalle por módulo y no solo el
        # nivel global fusionado -- ver _on_module_pred / _on_stress_stats.
        self._last_module_preds = {}

        self._restyle_all()
        self._pecs_panel.set_voice_enabled(settings.voice_enabled)
        self._legend.setText(self._legend_text("HOME"))

        self._wire_signals()
        self._serial.start()

        # S13: el motor de visión (Módulos A/B/C + fusión) arranca solo, en
        # segundo plano, en vez de esperar a que alguien entre a Video o
        # presione EMO_TOGGLE -- el niño debe poder interactuar con
        # cualquier pantalla mientras Moodi sigue vigilando el nivel de
        # estrés y puede avisar por Telegram si detecta un ALTO sostenido
        # (ver _on_stress_stats). Con QTimer.singleShot en vez de arrancarlo
        # aquí mismo: spawnear TF+Detectron2+MediaPipe+cámara tarda varios
        # segundos, y hacerlo síncrono en __init__ retrasaría la primera
        # pintada de la ventana (y con ella el cierre del splash). Se deja
        # que la ventana aparezca primero.
        QTimer.singleShot(1500, self._vision.start)

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
        self._vision.pred_ready.connect(self._on_module_pred)
        # El panel debe olvidar la sesión anterior en CADA arranque/parada del
        # motor: si no, las primeras predicciones de la sesión nueva se votan
        # junto con etiquetas viejas y se muestra una lectura obsoleta.
        self._vision.started.connect(self._monitor_panel.reset_session)
        self._vision.stopped.connect(self._monitor_panel.reset_session)

        self._animation.clip_changed.connect(self._show_caption)

        self._ghost.screen_selected.connect(self._show_view)
        self._ghost.exit_requested.connect(self.close)
        self._ghost.visibility_changed.connect(self._legend.setVisible)

        self._monitor_panel.stop_button.clicked.connect(self._toggle_emotion_recognition)
        self._calibration.close_button.clicked.connect(self._exit_calibration)

        # Interruptor de voz de Oraciones (S11).
        self._pecs_panel.voice_toggled.connect(self._on_voice_toggled)

        # Preferencias en caliente (Configuraciones).
        self._settings.language_changed.connect(self._on_language_changed)
        self._settings.font_scale_changed.connect(self._on_font_scale_changed)
        self._settings.volume_changed.connect(self._animation.set_volume)
        self._settings.voice_enabled_changed.connect(self._pecs_panel.set_voice_enabled)
        self._settings.music_volume_changed.connect(self._bg_music.set_volume)
        self._settings.music_track_changed.connect(self._bg_music.set_track)

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

    def _on_voice_toggled(self, enabled: bool):
        self._settings.set_voice_enabled(enabled)
        if not enabled:
            # Callar YA lo que estuviera narrándose, si no el botón se siente roto.
            self._voice.silence_now()
        log.info("Voz de Moodi %s desde Oraciones", "activada" if enabled else "silenciada")

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
        self._refresh_toggle_buttons()
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
        legend_font = QFont()
        legend_font.setPixelSize(theme.fs(15))
        self._legend.set_style(legend_font, QColor("white"), QColor(0, 0, 0, 120))

        self._caption.set_anchor_top_center(SCREEN_W // 2, 40)
        self._audio_toast.set_anchor_top_right(SCREEN_W - TOGGLE_BTN_MARGIN, 16)

        btn_size = TOGGLE_BTN_SIZE
        y = TOGGLE_BTN_MARGIN
        x_right = SCREEN_W - TOGGLE_BTN_MARGIN - btn_size
        x_left = x_right - TOGGLE_BTN_GAP - btn_size
        self._btn_audio_toggle.setGeometry(x_right, y, btn_size, btn_size)
        self._btn_seamless_toggle.setGeometry(x_left, y, btn_size, btn_size)
        btn_style = _TOGGLE_BTN_STYLE.format(r=btn_size // 2)
        btn_font = QFont(EMOJI_FONT, theme.fs(20))
        for btn in (self._btn_audio_toggle, self._btn_seamless_toggle):
            btn.setStyleSheet(btn_style)
            btn.setFont(btn_font)
        self._refresh_toggle_buttons()

    # ---------- leyenda dinámica de botones por pantalla (5.3) ----------
    def _phys_name(self, role: str) -> str:
        gpio = self._router.gpio_for_role(role)
        if gpio is None:
            return ""
        return t(f"phys.{GPIO_PHYS.get(gpio, gpio)}")

    def _legend_text(self, view: str) -> str:
        # S13: cursores izquierda/derecha cambian de pantalla en TODAS las
        # vistas, así que la leyenda lo recuerda en todas salvo Caras/
        # Oraciones (que ya nombran sus propios cursores arriba/abajo y lo
        # incluyen explícitamente en su propio texto).
        nav_hint = t("legend.nav_hint")
        if view == "ORACIONES":
            parts = [t("legend.oraciones.cursors"), nav_hint]
            for key, role in (("legend.oraciones.delete", "PECS_DELETE"),
                              ("legend.oraciones.clear", "PECS_CLEAR"),
                              ("legend.oraciones.send", "PECS_SEND")):
                phys = self._phys_name(role)
                if phys:
                    parts.append(t(key, btn=phys))
            return "  ·  ".join(parts)
        if view == "CARAS":
            return "  ·  ".join([t("legend.caras"), nav_hint])
        if view == "CONFIG":
            return "  ·  ".join([t("legend.config"), t("legend.config.cursors"), nav_hint])
        if view == "VIDEO":
            phys = self._phys_name("EMO_TOGGLE")
            base = t("legend.video", btn=phys) if phys else ""
            return "  ·  ".join(p for p in (base, nav_hint) if p)
        return "  ·  ".join([t(f"legend.{view.lower()}"), nav_hint])

    # ---------- navegación de vistas (5.3: máquina de estados) ----------
    def _show_view(self, view: str):
        prev = self._view
        if prev == "CONFIG" and view != "CONFIG":
            self._settings_panel.on_left()
        if prev == "SALIR" and view != "SALIR" and self._exit_confirm.is_armed():
            # Salir de la pantalla Salir cancela una confirmación armada pero
            # aún no confirmada (S13): no debe quedar "flotando" a la espera
            # de una segunda presión que ahora significaría otra cosa.
            self._exit_confirm.dismiss()

        self._view = view

        # S13 (revertido en la 5ª ronda): se probó que el sonido de Moodi
        # sonara en cualquier pantalla, pero eso obliga a AnimationPlayer a
        # usar la variante CON audio en todas partes -- y esa variante tiene
        # el loop "sin corte" desactivado a propósito (se congela cada pocos
        # minutos en esta Jetson, ver ui/animation_player.py), así que el
        # video mostraba un corte visible en cada vuelta. El usuario priorizó
        # el video sin cortes, así que el audio de Moodi vuelve a sonar SOLO
        # en Caras (como antes de S13) -- ver _effective_audio_muted().
        # _apply_audio_state() también decide si debe sonar la música de
        # fondo (suena exactamente cuando el audio de Moodi está silenciado,
        # o sea en cualquier pantalla que no sea Caras).
        self._apply_audio_state()

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

        # S13: el motor de visión ya no arranca/para al entrar o salir de esta
        # pantalla -- corre en segundo plano de forma continua desde el boot
        # (ver __init__ y _handle_action/EMO_TOGGLE) para que la detección de
        # estrés y la alerta a Telegram funcionen aunque el niño esté en
        # cualquier otra pantalla, no solo mientras alguien mira Video.
        self._monitor_panel.setVisible(view == "VIDEO" and self._vision.is_running())
        if view == "VIDEO":
            self._monitor_panel.raise_()  # cubre el fondo animado, nunca al revés

        # Botones táctiles de sonido/video-sin-cortes (S13, 8ª ronda): SOLO en
        # Oraciones -- en el resto de pantallas la isla física de 3 botones
        # sigue siendo el interruptor (ver _handle_action), así que un botón
        # en pantalla ahí sería redundante (y en Video además se superpondría
        # con el botón ✕/badge propios del panel de monitoreo).
        show_toggles = view == "ORACIONES"
        self._btn_audio_toggle.setVisible(show_toggles)
        self._btn_seamless_toggle.setVisible(show_toggles)
        if show_toggles:
            self._btn_audio_toggle.raise_()
            self._btn_seamless_toggle.raise_()

        # Elevar la cinta fantasma y la leyenda al frente para que nunca queden tapadas
        self._ghost.raise_()
        self._legend.raise_()

        self._legend.setText(self._legend_text(view))

        self._ghost.set_active(view, emit=False)

    def _pick_greeting(self) -> str:
        return greetings_mod.pick_greeting(
            self._greetings_data, self._settings.language, self._settings.nickname)

    # ---------- insignia temporal de título al cambiar de cara (5.5) ----------
    def _show_caption(self, title: str):
        if self._view != "CARAS":
            return  # esta insignia es solo para Caras; nunca debe aparecer en otras pantallas
        self._caption.show_message("🎵", title, duration_ms=CAPTION_SHOW_MS)

    # ---------- alerta automática de Telegram (nivel ALTO sostenido) ----------
    def _on_module_pred(self, pred: dict):
        module = pred.get("module")
        if module:
            self._last_module_preds[module] = pred

    def _alert_detail_text(self) -> str:
        """Línea por módulo (A/B/C) con su última etiqueta conocida, para que
        el cuidador vea qué disparó el nivel global y no solo el resultado
        fusionado (S13)."""
        lines = []
        for module in ("A", "B", "C"):
            pred = self._last_module_preds.get(module)
            if not pred:
                continue
            mod_name = t(f"monitor.module_{module.lower()}")
            label = i18n.label(pred.get("label", ""))
            lines.append(t("telegram.alert_module_line", module=mod_name, label=label))
        return "\n".join(lines)

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
            detail = self._alert_detail_text()
            text = t("telegram.alert_high")
            if detail:
                text = f"{text}\n{detail}"
            telegram_sender.send_message_async(
                text,
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
            # S13: el mismo botón físico ("panel derecho") funciona como
            # Enter/Confirmar según la pantalla -- en Oraciones envía la
            # frase (comportamiento de siempre); en Salir arma/confirma la
            # tarjeta de salida (ver _handle_exit_confirm_press). En el
            # resto de pantallas, pecs.send() no hace nada si no hay
            # palabras apiladas (igual que antes).
            if self._view == "SALIR":
                self._handle_exit_confirm_press()
            else:
                self._pecs.send()
        elif role == "PECS_DELETE":
            if self._view == "ORACIONES":
                idx = self._pecs_panel.selected_index()
                if idx >= 0:
                    self._pecs.delete_at(idx)
        elif role == "PECS_CLEAR":
            # S13 (8ª ronda): la isla de 3 botones físicos es de Oraciones
            # (borrar palabra/todo) -- pero en cualquier OTRA pantalla no
            # tiene nada que borrar, así que ahí mismo botón pasa a ser el
            # interruptor físico de "video sin cortes" (con su aviso
            # emergente, ver _toggle_seamless_mode). Mismo patrón ya usado
            # para PECS_SEND en Salir.
            if self._view == "ORACIONES":
                self._pecs.clear()
            else:
                self._toggle_seamless_mode()
        elif role == "EMO_TOGGLE":
            self._toggle_emotion_recognition()
        elif role == "AUDIO_TOGGLE":
            self._toggle_audio()
        elif role == "SEAMLESS_TOGGLE":
            self._toggle_seamless_mode()
        elif role == "DYNAMIC_PLAY":
            # S13 (8ª ronda): mismo patrón que PECS_CLEAR arriba -- en
            # Oraciones conserva su función original (reproducir la
            # "dinámica" actual); en cualquier otra pantalla pasa a ser el
            # interruptor físico de silenciar/activar el sonido de Moodi.
            if self._view == "ORACIONES":
                self._animation.play_dynamic()
            else:
                self._toggle_audio()
        elif role == "HOME":
            self._go_home()
        else:
            log.warning("Rol lógico desconocido: %s", role)

    def _handle_cursor(self, role: str):
        # S13: izquierda/derecha desplazan entre pantallas SIEMPRE, en
        # cualquier vista (antes solo cambiaban de cara en Caras, o de
        # selección en Oraciones). Arriba/abajo queda libre para el uso
        # específico de cada pantalla.
        if role in ("CURSOR_LEFT", "CURSOR_RIGHT"):
            self._ghost.step(-1 if role == "CURSOR_LEFT" else 1)
            return

        if self._view == "CARAS":
            if role == "CURSOR_UP":
                self._animation.prev_clip()
            elif role == "CURSOR_DOWN":
                self._animation.next_clip()
        elif self._view == "ORACIONES":
            if role == "CURSOR_UP":
                self._pecs_panel.move_selection(-1)
            elif role == "CURSOR_DOWN":
                self._pecs_panel.move_selection(1)
        elif self._view == "CONFIG":
            if role == "CURSOR_UP":
                self._settings_panel.move_section(-1)
            elif role == "CURSOR_DOWN":
                self._settings_panel.move_section(1)
        # HOME/VIDEO/SALIR: arriba/abajo sin efecto (reposo).

    def _toggle_emotion_recognition(self):
        """EMO_TOGGLE (S13): con el motor de visión corriendo siempre en
        segundo plano, esto ya no decide si detecta o no -- es solo un
        atajo manual de pausa/reanudación (p. ej. privacidad puntual o
        depuración) y un salto directo a la pantalla Video."""
        if self._vision.is_running():
            self._vision.stop()
            self._monitor_panel.setVisible(False)
            self._go_home()
        else:
            self._vision.start()
            self._show_view("VIDEO")

    # ---------- sonido de Moodi (S13, AUDIO_TOGGLE / SEAMLESS_TOGGLE) ----------
    def _effective_audio_muted(self) -> bool:
        """El audio de Moodi (voz/clips) se silencia si:
        - la pantalla activa es Video o Configuraciones -- ahí se fuerza el
          silencio SIEMPRE, sin importar AUDIO_TOGGLE ni SEAMLESS_TOGGLE (no
          tiene sentido "la voz de Moodi" mientras se mira el monitoreo o se
          ajustan preferencias, y es justo donde debe sonar la música de
          fondo en su lugar -- ver _effective_bg_music_active). S13 (13ª
          ronda): esto antes solo pasaba como efecto colateral del modo
          seamless por defecto; al desactivar SEAMLESS_TOGGLE dejaba de
          aplicar y Moodi volvía a sonar en Video/Configuraciones al mismo
          tiempo que la música de fondo. Ahora es incondicional.
        - el usuario lo pidió con AUDIO_TOGGLE ("isla derecha"), o
        - está en modo "video sin cortes" (_seamless_mode, por defecto) Y la
          pantalla activa no es Caras -- fuera de Caras se fuerza el
          silencio siempre en ese modo, para que el video de fondo pueda
          usar el loop sin corte (ver ui/animation_player.py: ese loop se
          desactiva a propósito en cuanto hay pista de audio activa, porque
          se congela cada pocos minutos en esta Jetson).

        Con SEAMLESS_TOGGLE ("isla izquierda") en modo NO seamless, el audio
        de Moodi puede sonar en cualquier OTRA pantalla (si AUDIO_TOGGLE no
        lo está silenciando), aceptando que el video muestre un corte de
        loop fuera de Caras -- decisión manual y explícita del usuario, no
        el comportamiento por defecto."""
        if self._view in ("VIDEO", "CONFIG"):
            return True
        if self._audio_muted:
            return True
        if self._seamless_mode and self._view != "CARAS":
            return True
        return False

    def _apply_audio_state(self):
        self._animation.set_muted(self._effective_audio_muted())
        self._bg_music.set_active(self._effective_bg_music_active())

    def _effective_bg_music_active(self) -> bool:
        """Regla de la música de fondo (S13, 12ª ronda -- corregida, es
        DISTINTA de `_effective_audio_muted`): antes seguía exactamente al
        audio de Moodi (sonaba en cualquier pantalla donde Moodi estuviera
        silenciada, incluida Caras cada vez que el modo seamless la forzaba
        ahí), lo cual no era lo pedido.

        La regla real depende SOLO del interruptor manual `AUDIO_TOGGLE`:
        - Silenciado (`_audio_muted`): música de fondo en TODAS las
          pantallas, sin excepción.
        - No silenciado: música de fondo SOLO en Video y Configuraciones
          (ahí no tiene sentido "la voz de Moodi" -- no se ve la carita).
          En el resto de pantallas no suena nada de música de fondo, para
          dejarle el lugar a la voz de Moodi donde corresponda (Caras)."""
        if self._audio_muted:
            return True
        return self._view in ("VIDEO", "CONFIG")

    def _toggle_audio(self):
        self._audio_muted = not self._audio_muted
        self._apply_audio_state()
        self._refresh_toggle_buttons()
        # El aviso solo hace falta cuando dispara la isla FÍSICA (cualquier
        # pantalla menos Oraciones): en Oraciones el botón táctil, en la
        # misma esquina, ya muestra el estado -- mostrar el aviso ahí
        # también se superpondría con ese mismo botón.
        if self._view == "ORACIONES":
            return
        if self._audio_muted:
            self._audio_toast.show_message("🔇", t("toast.audio_off"))
        else:
            self._audio_toast.show_message("🔊", t("toast.audio_on"))

    def _toggle_seamless_mode(self):
        self._seamless_mode = not self._seamless_mode
        self._apply_audio_state()
        self._refresh_toggle_buttons()
        if self._view == "ORACIONES":
            return
        if self._seamless_mode:
            self._audio_toast.show_message("🎬", t("toast.seamless_on"))
        else:
            self._audio_toast.show_message("🔊", t("toast.seamless_off"))

    def _refresh_toggle_buttons(self):
        """Refleja el estado actual en los dos botones táctiles (S13, 7ª
        ronda) -- son persistentes, así que su propio glifo YA muestra el
        estado; no hace falta un aviso aparte."""
        self._btn_audio_toggle.setText("🔇" if self._audio_muted else "🔊")
        self._btn_audio_toggle.setToolTip(
            t("toast.audio_off" if self._audio_muted else "toast.audio_on"))
        self._btn_seamless_toggle.setText("🎬" if self._seamless_mode else "🔉")
        self._btn_seamless_toggle.setToolTip(
            t("toast.seamless_on" if self._seamless_mode else "toast.seamless_off"))

    # ---------- confirmación de Salir por botón físico (S13) ----------
    def _handle_exit_confirm_press(self):
        if self._exit_confirm.is_armed():
            self._exit_confirm.dismiss()
            self.close()
        else:
            self._exit_confirm.arm(EXIT_CONFIRM_WINDOW_MS)

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
