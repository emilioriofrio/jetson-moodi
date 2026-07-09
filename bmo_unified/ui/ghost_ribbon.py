# ui/ghost_ribbon.py
"""Cinta fantasma infinita (5.2): reemplaza ui/ghost_controls.py (fila fija de
botones de acción) por un carrusel de 5 pantallas -- Home, Caras, Oraciones,
Video, Salir. Invisible por defecto, aparece con fundido al tocar la pantalla,
se autooculta a los 5s; la opción activa se resalta en naranja. Flechas
difuminadas en los bordes avanzan/retroceden con wrap-around (índice módulo 5).
Iconos dibujados con QPainter (sin assets externos)."""

from PyQt5.QtCore import QPointF, QPropertyAnimation, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

HIDE_DELAY_MS = 5000
FADE_MS = 250

COLOR_IDLE = QColor(255, 255, 255, 220)
COLOR_SELECTED = QColor(255, 149, 0)  # naranja (5.2: icono seleccionado/hover)

# Orden fijo por la sección 5.2 del documento de especificación.
SCREENS = [
    ("HOME", "Home"),
    ("CARAS", "Caras"),
    ("ORACIONES", "Oraciones"),
    ("VIDEO", "Video"),
    ("SALIR", "Salir"),
]
_KEYS = [k for k, _ in SCREENS]


def _paint_icon(painter: QPainter, key: str, rect: QRectF, color: QColor):
    """Formas planas simples y coherentes, sin depender de assets externos."""
    pen = QPen(color)
    pen.setWidthF(3.0)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    cx, cy = x + w / 2, y + h / 2

    if key == "HOME":
        roof = QPolygonF([
            QPointF(cx, y),
            QPointF(x + w * 0.08, y + h * 0.48),
            QPointF(x + w * 0.92, y + h * 0.48),
        ])
        painter.drawPolyline(roof)
        painter.drawRect(QRectF(x + w * 0.22, y + h * 0.48, w * 0.56, h * 0.44))
    elif key == "CARAS":
        painter.drawEllipse(QRectF(x + w * 0.15, y + h * 0.08, w * 0.7, h * 0.72))
        eye_y = y + h * 0.32
        eye_w = w * 0.08
        painter.drawEllipse(QRectF(cx - w * 0.2, eye_y, eye_w, eye_w))
        painter.drawEllipse(QRectF(cx + w * 0.12, eye_y, eye_w, eye_w))
        painter.drawArc(QRectF(cx - w * 0.2, cy - h * 0.02, w * 0.4, h * 0.26), 200 * 16, 140 * 16)
    elif key == "ORACIONES":
        painter.drawRoundedRect(QRectF(x + w * 0.08, y + h * 0.12, w * 0.84, h * 0.56), 8, 8)
        tail = QPolygonF([
            QPointF(cx - w * 0.14, y + h * 0.68),
            QPointF(cx + w * 0.02, y + h * 0.68),
            QPointF(cx - w * 0.2, y + h * 0.92),
        ])
        painter.drawPolygon(tail)
    elif key == "VIDEO":
        painter.drawRoundedRect(QRectF(x + w * 0.1, y + h * 0.24, w * 0.55, h * 0.52), 4, 4)
        lens = QPolygonF([
            QPointF(x + w * 0.65, y + h * 0.36),
            QPointF(x + w * 0.92, y + h * 0.24),
            QPointF(x + w * 0.92, y + h * 0.76),
            QPointF(x + w * 0.65, y + h * 0.64),
        ])
        painter.drawPolygon(lens)
    elif key == "SALIR":
        painter.drawArc(QRectF(x + w * 0.2, y + h * 0.14, w * 0.6, h * 0.72), -60 * 16, 300 * 16)
        painter.drawLine(QPointF(cx, y + h * 0.08), QPointF(cx, y + h * 0.46))


SALIR_HOLD_MS = 3000  # 5.3/9: "Salir" exige mantener presionado para evitar cierre accidental.


class _RibbonIcon(QWidget):
    clicked = pyqtSignal(str)
    long_pressed = pyqtSignal(str)
    press_started = pyqtSignal()  # SALIR: avisa al padre para que no autooculte la cinta a mitad de la espera

    def __init__(self, key: str, label: str, parent=None):
        super().__init__(parent)
        self.key = key
        self._label = label
        self._selected = False
        self.setFixedSize(160, 96)
        self.setCursor(Qt.PointingHandCursor)

        self._name = QLabel(label, self)
        self._name.setAlignment(Qt.AlignHCenter)
        self._name.setGeometry(0, 70, 160, 22)
        self._name.setStyleSheet("color: white; font-size: 13px; background: transparent;")

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._on_hold_complete)

    def set_selected(self, selected: bool):
        if selected == self._selected:
            return
        self._selected = selected
        weight = "bold" if selected else "normal"
        color = "#FF9500" if selected else "white"
        self._name.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: {weight}; background: transparent;"
        )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = COLOR_SELECTED if self._selected else COLOR_IDLE
        _paint_icon(painter, self.key, QRectF(30, 8, 100, 56), color)

    def mousePressEvent(self, event):
        if self.key == "SALIR":
            # No selecciona/emite de inmediato: exige mantener presionado (ver SALIR_HOLD_MS).
            # Retroalimentación visual inmediata para que se perciba como "activo" durante la espera.
            self._name.setText("Manteniendo…")
            self._name.setStyleSheet("color: #FF9500; font-size: 13px; font-weight: bold; background: transparent;")
            self.update()
            self.press_started.emit()  # evita que la cinta se autooculte a mitad de la espera
            self._hold_timer.start(SALIR_HOLD_MS)
        else:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._hold_timer.isActive():
            self._hold_timer.stop()
            self._name.setText(self._label)
            self._name.setStyleSheet(
                f"color: {'#FF9500' if self._selected else 'white'}; font-size: 13px; background: transparent;"
            )
        super().mouseReleaseEvent(event)

    def _on_hold_complete(self):
        self.long_pressed.emit(self.key)


class _RibbonArrow(QLabel):
    clicked = pyqtSignal()

    def __init__(self, glyph: str, parent=None):
        super().__init__(glyph, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(48)
        self.setStyleSheet("color: rgba(255,255,255,150); font-size: 30px; background: transparent;")

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class GhostRibbon(QWidget):
    screen_selected = pyqtSignal(str)
    exit_requested = pyqtSignal()  # emitido tras SALIR_HOLD_MS de presión sostenida sobre "Salir"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: rgba(0, 0, 0, 150);")

        self._icons = {}
        self._current = _KEYS[0]

        self._arrow_prev = _RibbonArrow("‹", self)  # ‹
        self._arrow_next = _RibbonArrow("›", self)  # ›
        self._arrow_prev.clicked.connect(lambda: self._step(-1))
        self._arrow_next.clicked.connect(lambda: self._step(1))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.setAlignment(Qt.AlignCenter)
        for key, label in SCREENS:
            icon = _RibbonIcon(key, label, self)
            icon.clicked.connect(self._on_icon_clicked)
            icon.long_pressed.connect(self._on_icon_long_pressed)
            icon.press_started.connect(self.notify_touch)
            row.addWidget(icon)
            self._icons[key] = icon

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 0, 8, 0)
        outer.addWidget(self._arrow_prev)
        outer.addLayout(row, stretch=1)
        outer.addWidget(self._arrow_next)

        self.set_active(self._current, emit=False)

        # Fundido + autohide (mismo patrón que ui/ghost_controls.py)
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(0.0)
        self.setVisible(False)

        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(FADE_MS)
        self._anim.finished.connect(self._on_anim_finished)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

    def notify_touch(self):
        """Llamar en cada toque de pantalla: muestra (con fundido) y reinicia el contador de 5s."""
        if self._effect.opacity() <= 0.01:
            self.setVisible(True)
            self._fade_to(1.0)
        self._hide_timer.start(HIDE_DELAY_MS)

    def set_active(self, key: str, emit: bool = True):
        """Sincroniza el resaltado con la pantalla activa (p. ej. al volver a Home por botón físico)."""
        if key not in self._icons:
            return
        self._current = key
        for k, icon in self._icons.items():
            icon.set_selected(k == key)
        if emit:
            self.screen_selected.emit(key)

    def _step(self, delta: int):
        idx = (_KEYS.index(self._current) + delta) % len(_KEYS)
        self.set_active(_KEYS[idx])
        self.notify_touch()

    def _on_icon_long_pressed(self, key: str):
        if key == "SALIR":
            self.exit_requested.emit()

    def _on_icon_clicked(self, key: str):
        self.set_active(key)
        self.notify_touch()

    def _fade_out(self):
        self._fade_to(0.0)

    def _fade_to(self, value: float):
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(value)
        self._anim.start()

    def _on_anim_finished(self):
        if self._effect.opacity() <= 0.01:
            self.setVisible(False)
