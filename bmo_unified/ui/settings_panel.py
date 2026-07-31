# ui/settings_panel.py
"""
Pantalla Configuraciones (6ª entrada de la cinta fantasma): volumen, idioma,
apodo, tamaño de texto y remapeo de los 10 botones físicos.

Diseño: pantalla completa de fondo plano con la misma paleta Moodi que
Oraciones (ui/theme.py), navegación por secciones en una columna izquierda de
targets grandes (≥64px) y el contenido de la sección activa a la derecha.

Persistencia:
- Volumen/idioma/apodo/tamaño de texto -> config/settings.json vía AppSettings
  (escritura atómica; ver core/app_settings.py).
- Remapeo de botones -> config/button_map.json (la fuente de verdad que ya usa
  ButtonRouter), SOLO al presionar "Guardar" (guardado explícito, nunca
  autoguardado silencioso: un toque accidental del niño no debe reasignar
  nada), también atómico, y con recarga en caliente (ButtonRouter.reload()).

Remapeo (3.3 del prompt de fase):
- Vista frontal de Moodi (MoodiDiagram) sobre el render real del robot,
  assets/MOODI_VIEW.png (S11; en S10 era un dibujo QPainter de figuras,
  documentado entonces como placeholder a la espera de este asset). Los 10
  botones responden al hover y el seleccionado PULSA con un glow naranja
  animado, como la reasignación de botones de un control de videojuego.
- PANEL_C/GPIO13 fijo como EMO_TOGGLE: se dibuja con candado, no editable, con
  explicación al tocarlo (restricción heredada, ver core/button_router.py).
- Aviso (no bloqueo) si un rol crítico queda sin ningún botón asignado.

Sin QGraphicsEffect en ningún widget (precaución vigente de esta ventana, ver
ui/ghost_ribbon.py): todas las animaciones son QPainter + QTimer.
"""

import json
import logging
import math
import os

from PyQt5.QtCore import QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QScroller,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.app_settings import MAX_NICKNAME_LEN, VALID_FONT_SCALES, atomic_write_json
from core.button_router import GPIO_PHYS, LOCKED_GPIO, LOCKED_ROLE
from core.i18n import t
from ui.theme import ACCENT_ORANGE, BG_TEAL, CORAL, CREAM, TEXT_NAVY, TEXT_NAVY_SOFT, fs

log = logging.getLogger("bmo.settings_panel")

SECTIONS = ("volume", "language", "nickname", "fontsize", "buttons")

# Acciones asignables a un botón físico (roles que MainWindow._handle_action ya
# entiende). "NONE" = botón sin función (se omite del JSON).
ACTIONS = (
    "CURSOR_UP", "CURSOR_DOWN", "CURSOR_LEFT", "CURSOR_RIGHT",
    "PECS_SEND", "PECS_DELETE", "PECS_CLEAR",
    "HOME", "DYNAMIC_PLAY", "EMO_TOGGLE", "NONE",
)

# Roles que no deberían quedar sin ningún botón: sin ellos el niño pierde el
# envío de frases o la vuelta a Home desde botón físico. Aviso, no bloqueo.
CRITICAL_ROLES = ("PECS_SEND", "HOME")

KEYBOARD_ROWS = (
    tuple("QWERTYUIOP"),
    tuple("ASDFGHJKLÑ"),
    tuple("ZXCVBNM") + ("⌫",),
)

PULSE_INTERVAL_MS = 40


def _big_button_style(selected: bool, font_px: int) -> str:
    base = (
        f"QPushButton {{ font-size: {font_px}px; font-weight: 600; border-radius: 14px; "
        f"padding: 8px 14px; text-align: left; "
    )
    if selected:
        return base + (
            f"background: {TEXT_NAVY}; color: {CREAM}; "
            f"border: 3px solid {ACCENT_ORANGE}; }}"
        )
    return base + (
        f"background: rgba(255,255,255,90); color: {TEXT_NAVY}; "
        "border: 2px solid rgba(32,50,63,60); }"
        "QPushButton:pressed { background: rgba(255,255,255,160); }"
    )


class MoodiDiagram(QWidget):
    """Vista frontal de Moodi para el remapeo de botones, sobre el render real
    del robot (assets/MOODI_VIEW.png) -- S11, sustituye al dibujo QPainter de
    figuras de S10, que era un placeholder explícito a la espera de un asset.

    Los 10 botones se ubican con coordenadas NORMALIZADAS (0-1) medidas sobre
    el PNG original de 451x553 por detección de color de cada control (d-pad,
    isla de 3 puntos, barra inferior), no estimadas a ojo: así siguen cuadrando
    aunque la imagen se escale a cualquier tamaño de widget.

    Interacción tipo hover: el botón bajo el puntero se ilumina y muestra su
    acción; el seleccionado además pulsa con un glow naranja. En la pantalla
    táctil no hay hover real, así que el toque también selecciona (y deja el
    resalte), manteniendo el mismo recorrido visual.

    PANEL_C/GPIO13 se dibuja con candado y no es seleccionable (restricción
    heredada, ver core/button_router.py).

    Sin QGraphicsEffect (precaución vigente de esta ventana): todo es QPainter.
    """

    button_selected = pyqtSignal(str)  # gpio (str)

    # Centro y tamaño de cada control en coordenadas normalizadas del PNG.
    # Medidos sobre assets/MOODI_VIEW.png (451x553) con detección de color.
    _POS = {
        # d-pad (cruz coral, abajo-izquierda del cuerpo)
        "4":  (0.360, 0.522, 0.055, 0.060),   # arriba
        "16": (0.359, 0.606, 0.055, 0.060),   # abajo
        "32": (0.308, 0.562, 0.070, 0.045),   # izquierda
        "33": (0.411, 0.562, 0.070, 0.045),   # derecha
        # isla de 3 puntos (derecha), ordenados de izquierda a derecha
        "27": (0.568, 0.495, 0.050, 0.042),
        "26": (0.649, 0.598, 0.050, 0.042),
        "25": (0.691, 0.562, 0.050, 0.042),
        # barra inferior: redondo coral / óvalo largo central / redondo turquesa
        "14": (0.344, 0.694, 0.075, 0.062),
        "13": (0.492, 0.694, 0.095, 0.058),   # central largo -- BLOQUEADO
        "17": (0.647, 0.699, 0.058, 0.050),
    }

    _DPAD = {"4": "up", "16": "down", "32": "left", "33": "right"}
    _DPAD_DEFAULT = {"4": "CURSOR_UP", "16": "CURSOR_DOWN",
                     "32": "CURSOR_LEFT", "33": "CURSOR_RIGHT"}

    _ASSET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "assets", "MOODI_VIEW.png")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hits = {}          # gpio -> QRect táctil (≥56px de lado)
        self._shapes = {}        # gpio -> QRectF del control sobre la imagen
        self._assignments = {}   # gpio -> rol pendiente (lo actualiza SettingsPanel)
        self._selected = None
        self._hover = None
        self._phase = 0.0
        self._img_rect = QRectF()

        self._pixmap = QPixmap(self._ASSET)
        if self._pixmap.isNull():
            log.warning("No se pudo cargar %s; el diagrama saldrá vacío", self._ASSET)
        self._scaled = QPixmap()
        self._scaled_for = None

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(PULSE_INTERVAL_MS)
        self._pulse_timer.timeout.connect(self._on_pulse_tick)

        self.setMouseTracking(True)   # necesario para el hover
        self.setMinimumSize(300, 380)

    def selected_gpio(self):
        return self._selected

    def select(self, gpio):
        self._selected = gpio
        if gpio is None:
            self._pulse_timer.stop()
        else:
            self._phase = 0.0
            self._pulse_timer.start()
        self.update()

    def set_assignments(self, mapping: dict):
        """Mapa pendiente gpio->rol para rotular cada botón con su acción."""
        self._assignments = dict(mapping)
        self.update()

    def _on_pulse_tick(self):
        self._phase += PULSE_INTERVAL_MS / 1000.0
        self.update()

    # ---------- geometría ----------
    def _layout(self):
        """Encaja el PNG dentro del widget conservando su proporción y deriva
        de ahí la caja de cada botón."""
        w, h = self.width(), self.height()
        if self._pixmap.isNull() or w <= 0 or h <= 0:
            self._img_rect = QRectF(0, 0, w, h)
        else:
            iw, ih = self._pixmap.width(), self._pixmap.height()
            scale = min(w / iw, h / ih)
            dw, dh = iw * scale, ih * scale
            self._img_rect = QRectF((w - dw) / 2.0, (h - dh) / 2.0, dw, dh)

        ox, oy = self._img_rect.x(), self._img_rect.y()
        iw, ih = self._img_rect.width(), self._img_rect.height()

        self._shapes = {}
        for gpio, (cx, cy, sw, sh) in self._POS.items():
            bw, bh = sw * iw, sh * ih
            self._shapes[gpio] = QRectF(ox + cx * iw - bw / 2.0,
                                        oy + cy * ih - bh / 2.0, bw, bh)

        # Zona táctil ampliada: los controles del render son pequeños, pero el
        # dedo necesita ≥56px. El resalte visual sigue el tamaño real del botón.
        self._hits = {}
        for gpio, rect in self._shapes.items():
            hit = rect.toRect()
            grow_w = max(0, 56 - hit.width())
            grow_h = max(0, 56 - hit.height())
            self._hits[gpio] = hit.adjusted(-grow_w // 2, -grow_h // 2,
                                            grow_w // 2, grow_h // 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout()

    # ---------- interacción ----------
    def _gpio_at(self, pos):
        """GPIO bajo 'pos'. Se prueba primero la caja real y luego la ampliada,
        para que dos zonas táctiles solapadas (d-pad) no se roben el toque."""
        for gpio, rect in self._shapes.items():
            if rect.contains(pos):
                return gpio
        best, best_d = None, None
        for gpio, rect in self._hits.items():
            if rect.contains(pos):
                c = rect.center()
                d = (c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2
                if best_d is None or d < best_d:
                    best, best_d = gpio, d
        return best

    def mouseMoveEvent(self, event):
        gpio = self._gpio_at(event.pos())
        if gpio != self._hover:
            self._hover = gpio
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        gpio = self._gpio_at(event.pos())
        if gpio is not None:
            self._hover = gpio       # en táctil no hay hover: el toque lo fija
            self.select(gpio)
            self.button_selected.emit(gpio)
            return
        super().mousePressEvent(event)

    # ---------- dibujo ----------
    def _short_action(self, gpio: str) -> str:
        role = self._assignments.get(gpio)
        return t(f"actionshort.{role}") if role else t("actionshort.NONE")

    def paintEvent(self, event):
        if not self._shapes:
            self._layout()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # ---- render de Moodi ----
        if not self._pixmap.isNull():
            size = self._img_rect.size().toSize()
            if self._scaled_for != size:
                self._scaled = self._pixmap.scaled(
                    size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self._scaled_for = size
            painter.drawPixmap(self._img_rect.topLeft(), self._scaled)

        navy = QColor(TEXT_NAVY)

        # ---- controles ----
        for gpio, rect in self._shapes.items():
            locked = gpio == LOCKED_GPIO
            selected = gpio == self._selected
            hovered = gpio == self._hover and not selected

            if selected:
                # pulso naranja: 2 ciclos por segundo
                k = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi * 2.0)
                grow = 4 + 5 * k
                glow = QColor(ACCENT_ORANGE)
                glow.setAlpha(int(90 + 140 * k))
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(glow, 4))
                painter.drawRoundedRect(rect.adjusted(-grow, -grow, grow, grow), 14, 14)
                fill = QColor(ACCENT_ORANGE)
                fill.setAlpha(70)
                painter.setBrush(fill)
                painter.setPen(QPen(QColor(ACCENT_ORANGE), 3))
                painter.drawRoundedRect(rect, 10, 10)
            elif hovered:
                # resalte de hover: más sutil que la selección, sin animación
                fill = QColor(CREAM)
                fill.setAlpha(90)
                painter.setBrush(fill)
                painter.setPen(QPen(QColor(CREAM), 3))
                painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 12, 12)

            if locked:
                self._draw_padlock(painter, rect, navy)

        # ---- rótulo de la acción: solo del botón activo (hover o selección) ----
        # Rotular los 10 a la vez saturaba el dibujo y los textos se pisaban
        # entre sí (problema visible en la versión de S10).
        # El hover MANDA sobre la selección: la etiqueta es el afordance de
        # "¿qué hace este botón?", así que debe seguir al dedo/puntero. Al
        # revés, al explorar otros botones seguía nombrando al ya seleccionado.
        focus = self._hover or self._selected
        if focus is not None:
            self._draw_action_tag(painter, focus)

        painter.end()

    def _draw_action_tag(self, painter: QPainter, gpio: str):
        """Etiqueta flotante con el nombre físico del botón y su acción."""
        rect = self._shapes.get(gpio)
        if rect is None:
            return
        phys = t(f"phys.{GPIO_PHYS.get(gpio, '')}") if gpio in GPIO_PHYS else ""
        action = self._short_action(gpio)
        text = f"{phys} · {action}" if phys else action

        painter.setFont(QFont("Ubuntu", fs(11), QFont.Bold))
        metrics = painter.fontMetrics()
        tw = metrics.horizontalAdvance(text) + 22
        th = metrics.height() + 12

        # encima del botón; si no cabe arriba, debajo
        x = rect.center().x() - tw / 2.0
        y = rect.top() - th - 8
        if y < 2:
            y = rect.bottom() + 8
        x = max(2.0, min(x, self.width() - tw - 2.0))

        tag = QRectF(x, y, tw, th)
        painter.setBrush(QColor(TEXT_NAVY))
        painter.setPen(QPen(QColor(ACCENT_ORANGE), 2))
        painter.drawRoundedRect(tag, 10, 10)
        painter.setPen(QColor(CREAM))
        painter.drawText(tag, Qt.AlignCenter, text)

    @staticmethod
    def _draw_padlock(painter: QPainter, rect: QRectF, color: QColor):
        """Candado vectorial (el emoji 🔒 renderizaba mal sobre QPainter)."""
        c = rect.center()
        bw, bh = 16.0, 11.0
        painter.setBrush(QColor(255, 255, 255, 210))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(rect.center(), 15, 15)
        body = QRectF(c.x() - bw / 2, c.y() - 1, bw, bh)
        painter.setBrush(color)
        painter.drawRoundedRect(body, 3, 3)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(color, 2.5))
        shackle = QRectF(c.x() - 5, c.y() - 9, 10, 12)
        painter.drawArc(shackle, 0, 180 * 16)


class SettingsPanel(QWidget):
    """La visibilidad la decide MainWindow (vista CONFIG); este widget solo
    edita AppSettings + button_map.json."""

    def __init__(self, settings, router, button_map_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._settings = settings
        self._router = router
        self._button_map_path = button_map_path

        self._section = "volume"
        self._pending_map = {}   # gpio -> rol (estado editado, aún sin guardar)
        self._map_dirty = False

        self._build()
        self.refresh_from_settings()
        self.retranslate()
        self.restyle()

    # =====================================================================
    # construcción
    # =====================================================================
    def _build(self):
        self._title = QLabel()

        # -------- navegación izquierda --------
        self._nav_buttons = {}
        nav = QVBoxLayout()
        nav.setSpacing(10)
        for section in SECTIONS:
            btn = QPushButton()
            btn.setMinimumHeight(66)
            btn.clicked.connect(lambda _, s=section: self._show_section(s))
            self._nav_buttons[section] = btn
            nav.addWidget(btn)
        nav.addStretch(1)
        nav_wrap = QWidget()
        nav_wrap.setFixedWidth(250)
        nav_wrap.setLayout(nav)

        # -------- páginas --------
        self._stack = QStackedWidget()
        self._pages = {
            "volume": self._build_volume_page(),
            "language": self._build_language_page(),
            "nickname": self._build_nickname_page(),
            "fontsize": self._build_fontsize_page(),
            "buttons": self._build_buttons_page(),
        }
        for section in SECTIONS:
            self._stack.addWidget(self._pages[section])

        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(nav_wrap)
        body.addWidget(self._stack, stretch=1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 18, 28, 20)
        layout.setSpacing(10)
        layout.addWidget(self._title)
        layout.addLayout(body, stretch=1)

    # -------- Volumen --------
    def _build_volume_page(self) -> QWidget:
        page = QWidget()
        self._volume_hint = QLabel()
        self._volume_hint.setWordWrap(True)

        self._volume_value = QLabel("80")
        self._volume_value.setAlignment(Qt.AlignCenter)

        self._btn_vol_down = QPushButton("−")
        self._btn_vol_up = QPushButton("+")
        for btn in (self._btn_vol_down, self._btn_vol_up):
            btn.setFixedSize(76, 76)
        self._btn_vol_down.clicked.connect(lambda: self._bump_volume(-10))
        self._btn_vol_up.clicked.connect(lambda: self._bump_volume(+10))

        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setMinimumHeight(64)
        # Persistencia con debounce (400ms tras el último cambio): cubre tanto
        # el arrastre (sin escribir en cada pixel) como el toque directo en la
        # barra, que NO dispara sliderReleased.
        self._volume_persist_timer = QTimer(self)
        self._volume_persist_timer.setSingleShot(True)
        self._volume_persist_timer.setInterval(400)
        self._volume_persist_timer.timeout.connect(
            lambda: self._settings.set_volume(self._volume_slider.value()))
        self._volume_slider.valueChanged.connect(self._on_volume_slider_changed)

        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(self._btn_vol_down)
        row.addWidget(self._volume_slider, stretch=1)
        row.addWidget(self._btn_vol_up)

        layout = QVBoxLayout(page)
        layout.setSpacing(18)
        layout.addWidget(self._volume_hint)
        layout.addStretch(1)
        layout.addWidget(self._volume_value)
        layout.addLayout(row)
        layout.addStretch(2)
        return page

    def _on_volume_slider_changed(self, value: int):
        self._volume_value.setText(str(value))
        self._volume_persist_timer.start()

    def _bump_volume(self, delta: int):
        value = max(0, min(100, self._volume_slider.value() + delta))
        self._volume_slider.setValue(value)
        self._settings.set_volume(value)

    # -------- Idioma --------
    def _build_language_page(self) -> QWidget:
        page = QWidget()
        self._language_hint = QLabel()
        self._language_hint.setWordWrap(True)

        self._lang_buttons = {}
        row = QHBoxLayout()
        row.setSpacing(20)
        for lang in ("es", "en"):
            btn = QPushButton()
            btn.setMinimumSize(240, 90)
            btn.clicked.connect(lambda _, l=lang: self._settings.set_language(l))
            self._lang_buttons[lang] = btn
            row.addWidget(btn)
        row.addStretch(1)

        layout = QVBoxLayout(page)
        layout.setSpacing(18)
        layout.addWidget(self._language_hint)
        layout.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(2)
        return page

    # -------- Apodo --------
    def _build_nickname_page(self) -> QWidget:
        page = QWidget()
        self._nickname_hint = QLabel()
        self._nickname_hint.setWordWrap(True)

        self._nickname_value = QLabel()
        self._nickname_value.setAlignment(Qt.AlignCenter)
        self._nickname_value.setMinimumHeight(64)

        self._nickname_buffer = ""

        keyboard = QVBoxLayout()
        keyboard.setSpacing(8)
        self._key_buttons = []
        for row_keys in KEYBOARD_ROWS:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addStretch(1)
            for key in row_keys:
                btn = QPushButton(key)
                btn.setFixedSize(88 if key == "⌫" else 64, 64)
                btn.clicked.connect(lambda _, k=key: self._on_key(k))
                self._key_buttons.append(btn)
                row.addWidget(btn)
            row.addStretch(1)
            keyboard.addLayout(row)

        self._btn_nickname_save = QPushButton()
        self._btn_nickname_save.setMinimumHeight(66)
        self._btn_nickname_save.clicked.connect(self._save_nickname)
        self._nickname_status = QLabel("")
        self._nickname_status.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.addWidget(self._nickname_hint)
        layout.addWidget(self._nickname_value)
        layout.addLayout(keyboard)
        layout.addWidget(self._btn_nickname_save)
        layout.addWidget(self._nickname_status)
        layout.addStretch(1)
        return page

    def _on_key(self, key: str):
        if key == "⌫":
            self._nickname_buffer = self._nickname_buffer[:-1]
        elif len(self._nickname_buffer) < MAX_NICKNAME_LEN:
            self._nickname_buffer += key
        self._nickname_status.setText("")
        self._refresh_nickname_value()

    def _save_nickname(self):
        # capitalizado amable para el saludo ("PEPE" -> "Pepe")
        nickname = self._nickname_buffer.strip().capitalize()
        self._settings.set_nickname(nickname)
        self._nickname_buffer = nickname
        self._refresh_nickname_value()
        self._nickname_status.setText(t("settings.nickname.saved"))

    def _refresh_nickname_value(self):
        shown = self._nickname_buffer or t("settings.nickname.empty")
        self._nickname_value.setText(shown)

    # -------- Tamaño de texto --------
    def _build_fontsize_page(self) -> QWidget:
        page = QWidget()
        self._fontsize_hint = QLabel()
        self._fontsize_hint.setWordWrap(True)

        self._fontsize_buttons = {}
        grid = QVBoxLayout()
        grid.setSpacing(12)
        for level in VALID_FONT_SCALES:
            btn = QPushButton()
            btn.setMinimumHeight(72)
            btn.clicked.connect(lambda _, lv=level: self._settings.set_font_scale(lv))
            self._fontsize_buttons[level] = btn
            grid.addWidget(btn)

        layout = QVBoxLayout(page)
        layout.setSpacing(14)
        layout.addWidget(self._fontsize_hint)
        layout.addLayout(grid)
        layout.addStretch(1)
        return page

    # -------- Botones (remapeo) --------
    def _build_buttons_page(self) -> QWidget:
        page = QWidget()
        self._buttons_hint = QLabel()
        self._buttons_hint.setWordWrap(True)

        self._diagram = MoodiDiagram()
        self._diagram.button_selected.connect(self._on_diagram_selected)

        self._assign_header = QLabel()
        self._assign_header.setWordWrap(True)

        actions_col = QVBoxLayout()
        actions_col.setSpacing(8)
        self._action_buttons = {}
        for action in ACTIONS:
            btn = QPushButton()
            btn.setMinimumHeight(64)
            btn.clicked.connect(lambda _, a=action: self._assign_action(a))
            self._action_buttons[action] = btn
            actions_col.addWidget(btn)
        actions_col.addStretch(1)
        actions_wrap = QWidget()
        actions_wrap.setLayout(actions_col)

        self._actions_scroll = QScrollArea()
        self._actions_scroll.setWidget(actions_wrap)
        self._actions_scroll.setWidgetResizable(True)
        self._actions_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._actions_scroll.setFrameShape(QScrollArea.NoFrame)
        # scroll táctil con arrastre (kinetic), no solo la barra
        QScroller.grabGesture(self._actions_scroll.viewport(), QScroller.LeftMouseButtonGesture)

        self._map_warning = QLabel("")
        self._map_warning.setWordWrap(True)
        self._map_status = QLabel("")
        self._map_status.setWordWrap(True)

        self._btn_map_save = QPushButton()
        self._btn_map_save.setMinimumHeight(70)
        self._btn_map_save.clicked.connect(self._save_button_map)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self._assign_header)
        right.addWidget(self._actions_scroll, stretch=1)
        right.addWidget(self._map_warning)
        right.addWidget(self._map_status)
        right.addWidget(self._btn_map_save)
        right_wrap = QWidget()
        right_wrap.setFixedWidth(320)
        right_wrap.setLayout(right)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._diagram, stretch=1)
        body.addWidget(right_wrap)

        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        # Margen inferior: la cinta fantasma (110px, siempre por encima de este
        # panel) no debe tapar el botón Guardar cuando aparece.
        layout.setContentsMargins(0, 0, 0, 96)
        layout.addWidget(self._buttons_hint)
        layout.addLayout(body, stretch=1)
        return page

    # =====================================================================
    # lógica de remapeo
    # =====================================================================
    def _load_pending_map(self):
        self._pending_map = self._router.current_map()
        self._pending_map[LOCKED_GPIO] = LOCKED_ROLE
        self._map_dirty = False
        self._diagram.set_assignments(self._pending_map)

    def _on_diagram_selected(self, gpio: str):
        locked = gpio == LOCKED_GPIO
        phys = t(f"phys.{GPIO_PHYS.get(gpio, gpio)}")
        if locked:
            self._assign_header.setText(t("settings.buttons.locked"))
        else:
            self._assign_header.setText(t("settings.buttons.assign_to", btn=phys))
        for btn in self._action_buttons.values():
            btn.setEnabled(not locked)
        self._refresh_action_highlight()

    def _assign_action(self, action: str):
        gpio = self._diagram.selected_gpio()
        if gpio is None or gpio == LOCKED_GPIO:
            return
        if action == "NONE":
            self._pending_map.pop(gpio, None)
        else:
            self._pending_map[gpio] = action
        self._map_dirty = True
        self._diagram.set_assignments(self._pending_map)
        self._map_status.setText(t("settings.buttons.unsaved"))
        self._refresh_action_highlight()
        self._refresh_map_warning()

    def _refresh_action_highlight(self):
        gpio = self._diagram.selected_gpio()
        current = self._pending_map.get(gpio) if gpio else None
        if gpio and current is None:
            current = "NONE"
        for action, btn in self._action_buttons.items():
            btn.setStyleSheet(_big_button_style(action == current, fs(17)))

    def _missing_critical_roles(self):
        assigned = set(self._pending_map.values())
        return [r for r in CRITICAL_ROLES if r not in assigned]

    def _refresh_map_warning(self):
        missing = self._missing_critical_roles()
        if missing:
            names = ", ".join(t(f"action.{r}") for r in missing)
            self._map_warning.setText(t("settings.buttons.warning", roles=names))
        else:
            self._map_warning.setText("")

    def _save_button_map(self):
        data = {}
        # conservar el comentario original del archivo (documentación in situ)
        try:
            with open(self._button_map_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            if "_comentario" in old:
                data["_comentario"] = old["_comentario"]
        except Exception:
            pass
        data.update({gpio: role for gpio, role in self._pending_map.items()})
        data[LOCKED_GPIO] = LOCKED_ROLE
        try:
            atomic_write_json(self._button_map_path, data)
        except Exception:
            log.exception("No se pudo guardar button_map.json")
            self._map_status.setText(t("pecs.error", err="button_map.json"))
            return
        self._router.reload()
        self._map_dirty = False
        self._map_status.setText(t("settings.buttons.saved"))
        self._refresh_map_warning()
        log.info("button_map.json guardado y recargado en caliente: %s", self._pending_map)

    # =====================================================================
    # API para MainWindow
    # =====================================================================
    def on_entered(self):
        """Llamar cada vez que se entra a la vista CONFIG: refleja el estado
        persistido real y descarta ediciones de botones a medias."""
        self.refresh_from_settings()
        self._load_pending_map()
        self._diagram.select(None)
        self._assign_header.setText(t("settings.buttons.select_prompt"))
        for btn in self._action_buttons.values():
            btn.setEnabled(False)
        self._map_status.setText("")
        self._refresh_action_highlight()
        self._refresh_map_warning()

    def on_left(self):
        self._diagram.select(None)  # detiene el QTimer del pulso

    def refresh_from_settings(self):
        self._volume_slider.blockSignals(True)
        self._volume_slider.setValue(self._settings.volume)
        self._volume_slider.blockSignals(False)
        self._volume_value.setText(str(self._settings.volume))
        self._nickname_buffer = self._settings.nickname
        self._refresh_nickname_value()
        self._refresh_language_highlight()
        self._refresh_fontsize_highlight()

    def _show_section(self, section: str):
        self._section = section
        self._stack.setCurrentWidget(self._pages[section])
        if section == "buttons":
            self._load_pending_map()
            self._diagram.select(None)
            self._assign_header.setText(t("settings.buttons.select_prompt"))
            for btn in self._action_buttons.values():
                btn.setEnabled(False)
            self._map_status.setText("")
            self._refresh_action_highlight()
            self._refresh_map_warning()
        else:
            self._diagram.select(None)
        self._refresh_nav_highlight()

    # =====================================================================
    # i18n + escala de fuente (en caliente)
    # =====================================================================
    def retranslate(self):
        self._title.setText(t("settings.title"))
        for section, btn in self._nav_buttons.items():
            btn.setText(t(f"settings.section.{section}"))
        self._volume_hint.setText(t("settings.volume.hint"))
        self._language_hint.setText(t("settings.language.hint"))
        for lang, btn in self._lang_buttons.items():
            btn.setText(t(f"settings.language.{lang}"))
        self._nickname_hint.setText(t("settings.nickname.hint"))
        self._btn_nickname_save.setText(t("settings.nickname.save"))
        self._nickname_status.setText("")
        self._refresh_nickname_value()
        self._fontsize_hint.setText(t("settings.fontsize.hint"))
        for level, btn in self._fontsize_buttons.items():
            btn.setText(t(f"settings.fontsize.{level}"))
        self._buttons_hint.setText(t("settings.buttons.hint"))
        self._diagram.update()  # títulos de grupo y rótulos de acción del dibujo
        for action, btn in self._action_buttons.items():
            btn.setText(t(f"action.{action}"))
        self._btn_map_save.setText(t("settings.buttons.save"))
        self._assign_header.setText(t("settings.buttons.select_prompt"))
        self._map_status.setText("")
        self._refresh_map_warning()

    def restyle(self):
        self.setStyleSheet(f"background: {BG_TEAL}; color: {TEXT_NAVY};")
        self._title.setStyleSheet(
            f"font-size: {fs(30)}px; font-weight: 800; color: {TEXT_NAVY}; background: transparent;")

        hint_style = f"font-size: {fs(17)}px; color: {TEXT_NAVY_SOFT}; background: transparent;"
        for lbl in (self._volume_hint, self._language_hint, self._nickname_hint,
                    self._fontsize_hint, self._buttons_hint):
            lbl.setStyleSheet(hint_style)

        self._volume_value.setStyleSheet(
            f"font-size: {fs(44)}px; font-weight: 800; color: {TEXT_NAVY}; background: transparent;")
        for btn in (self._btn_vol_down, self._btn_vol_up):
            btn.setStyleSheet(
                f"QPushButton {{ font-size: {fs(34)}px; font-weight: 700; border-radius: 38px; "
                f"background: {TEXT_NAVY}; color: {CREAM}; }}"
                f"QPushButton:pressed {{ background: {ACCENT_ORANGE}; }}")
        self._volume_slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 18px; border-radius: 9px; "
            "background: rgba(32,50,63,70); }"
            f"QSlider::sub-page:horizontal {{ background: {TEXT_NAVY}; border-radius: 9px; }}"
            "QSlider::handle:horizontal { width: 48px; height: 48px; margin: -16px 0; "
            f"border-radius: 24px; background: {ACCENT_ORANGE}; }}")

        self._nickname_value.setStyleSheet(
            f"font-size: {fs(30)}px; font-weight: 700; color: {TEXT_NAVY}; "
            f"background: rgba(255,255,255,120); border-radius: 14px; padding: 6px;")
        for btn in self._key_buttons:
            btn.setStyleSheet(
                f"QPushButton {{ font-size: {fs(20)}px; font-weight: 700; border-radius: 12px; "
                f"background: rgba(255,255,255,140); color: {TEXT_NAVY}; "
                "border: 1px solid rgba(32,50,63,60); }"
                f"QPushButton:pressed {{ background: {ACCENT_ORANGE}; color: white; }}")
        self._btn_nickname_save.setStyleSheet(_big_button_style(True, fs(19)))
        self._nickname_status.setStyleSheet(
            f"font-size: {fs(16)}px; font-weight: 600; color: #1E6E52; background: transparent;")

        self._assign_header.setStyleSheet(
            f"font-size: {fs(17)}px; font-weight: 700; color: {TEXT_NAVY}; background: transparent;")
        self._actions_scroll.setStyleSheet("background: transparent;")
        self._map_warning.setStyleSheet(
            f"font-size: {fs(15)}px; font-weight: 700; color: #A83A22; background: transparent;")
        self._map_status.setStyleSheet(
            f"font-size: {fs(15)}px; font-weight: 600; color: #1E6E52; background: transparent;")
        self._btn_map_save.setStyleSheet(_big_button_style(True, fs(20)))

        self._refresh_nav_highlight()
        self._refresh_language_highlight()
        self._refresh_fontsize_highlight()
        self._refresh_action_highlight()

    def _refresh_nav_highlight(self):
        for section, btn in self._nav_buttons.items():
            btn.setStyleSheet(_big_button_style(section == self._section, fs(20)))

    def _refresh_language_highlight(self):
        for lang, btn in self._lang_buttons.items():
            btn.setStyleSheet(_big_button_style(lang == self._settings.language, fs(22)))

    def _refresh_fontsize_highlight(self):
        # cada nivel se previsualiza con SU propio tamaño (Aa comparativo real)
        from ui.theme import FONT_SCALE_FACTORS
        for level, btn in self._fontsize_buttons.items():
            preview_px = max(12, round(19 * FONT_SCALE_FACTORS[level]))
            selected = level == self._settings.font_scale
            btn.setStyleSheet(_big_button_style(selected, preview_px))
