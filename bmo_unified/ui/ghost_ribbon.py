# ui/ghost_ribbon.py
"""Cinta fantasma infinita (5.2): carrusel de 5 pantallas -- Home, Caras,
Oraciones, Video, Salir. Invisible por defecto, aparece al tocar la pantalla,
se autooculta a los 5s. Sin fundido animado a propósito: ver nota en
GhostRibbon.__init__ sobre por qué QGraphicsOpacityEffect rompe el video de
fondo en esta app.

A diferencia de la primera versión (una fila fija de 5 iconos que solo
cambiaba cuál se resaltaba), esta es un "reel" de verdad: solo se ven 3
posiciones (anterior/actual/siguiente) y cambiar de pantalla desliza el reel
lateralmente con una animación, dando la sensación de scrolling infinito en
vez de un simple parpadeo de resaltado. El reel siempre mantiene 5 iconos
montados (offsets -2..+2); los dos extremos quedan fuera del área visible del
"stage" (recortados por Qt automáticamente al ser hijos de un widget más
angosto) y solo se revelan a medida que el reel se desliza -- al terminar la
animación se reconstruyen los 5 iconos centrados en la nueva pantalla activa y
el reel se resetea a su posición de reposo sin animar (idéntico visualmente,
el corte no se percibe), como en cualquier carrusel infinito.

Iconos: insignia circular con gradiente de color propio por pantalla +
glifo emoji a color (fuente "Noto Color Emoji", preinstalada) -- reemplaza el
set anterior de trazos SVG monocromos planos, que además no llenaba bien su
recuadro (no cuadrado, iconos "achatados")."""

import math

from PyQt5.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QRadialGradient
from PyQt5.QtWidgets import QLabel, QWidget

from core.i18n import t
from ui.theme import fs

HIDE_DELAY_MS = 5000

COLOR_SELECTED = QColor(255, 149, 0)  # naranja (acento de selección en toda la app)
EMOJI_FONT = "Noto Color Emoji"

# Orden por la sección 5.2 de la especificación + la 6ª entrada Configuraciones
# (fase UI/CONFIG/VOZ) antes de Salir. Los nombres visibles salen de i18n
# (t("screen.<clave>")) en el momento de construir cada icono.
_KEYS = ["HOME", "CARAS", "ORACIONES", "VIDEO", "CONFIG", "SALIR"]


def _name(key: str) -> str:
    return t(f"screen.{key.lower()}")


# Glifo + color de insignia por pantalla -- colores tomados de la paleta de
# Moodi (turquesa/coral) más acentos propios para Oraciones/Video/Config,
# distintos de los colores A/B/C del panel de monitoreo (cian/magenta/amarillo)
# para no generar confusión visual entre ambos sistemas de color.
# CONFIG no usa glifo emoji: "⚙️" (U+2699 + selector de variante) no existe
# como emoji a color en la Noto Color Emoji de esta Jetson y Qt lo montaba
# roto/desalineado sobre la insignia -- su engrane se dibuja con QPainter puro
# en _RibbonIcon._draw_gear() (además es lo que pide la especificación de la
# fase: ícono propio dibujado, sin depender de assets/fuentes).
EMOJI = {
    "HOME": "🏠",
    "CARAS": "🙂",
    "ORACIONES": "💬",
    "VIDEO": "🎥",
    "SALIR": "🚪",
}
BADGE_COLORS = {
    "HOME": QColor("#4FB3C4"),
    "CARAS": QColor("#EFA082"),
    "ORACIONES": QColor("#3B6FA0"),
    "VIDEO": QColor("#8B6FB3"),
    "CONFIG": QColor("#4E9B6E"),
    "SALIR": QColor("#C1573B"),
}

SALIR_HOLD_MS = 3000

# Geometría del reel (ver docstring del módulo).
ICON_W, ICON_H = 190, 104
SLOT_STEP = 208  # separación entre centros de icono consecutivos
ARROW_W = 64  # target táctil ≥64px (antes 48)

# Micro-feedback al aterrizar en una pantalla: la insignia central "late" una
# vez (crece ~12% y vuelve, 240ms). QVariantAnimation + repintado propio, sin
# QGraphicsEffect (precaución vigente en esta ventana, ver docstring).
POP_MS = 240
POP_AMOUNT = 0.12


class _RibbonIcon(QWidget):
    clicked = pyqtSignal(str)
    long_pressed = pyqtSignal(str)
    press_started = pyqtSignal()

    def __init__(self, key: str, label: str, parent=None):
        super().__init__(parent)
        self.key = key
        self._label = label
        self._role = "side"  # "center" (pantalla activa) | "side" (vecina, atenuada)
        self.setFixedSize(ICON_W, ICON_H)
        self.setCursor(Qt.PointingHandCursor)

        self._name = QLabel(label, self)
        self._name.setAlignment(Qt.AlignHCenter)
        # 28px de alto: holgura para el nivel "Muy grande" de la escala de fuente
        self._name.setGeometry(0, ICON_H - 30, ICON_W, 28)
        self._apply_label_style()

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._on_hold_complete)

        # animación de "pop" (ver POP_MS/POP_AMOUNT)
        self._pop_scale = 0.0
        self._pop_anim = QVariantAnimation(self)
        self._pop_anim.setDuration(POP_MS)
        self._pop_anim.setStartValue(0.0)
        self._pop_anim.setEndValue(1.0)
        self._pop_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._pop_anim.valueChanged.connect(self._on_pop_tick)

    def pop(self):
        """Micro-bounce al quedar como pantalla activa."""
        self._pop_anim.stop()
        self._pop_anim.start()

    def _on_pop_tick(self, v):
        # sube y baja en una sola pasada (senoidal 0->1->0)
        self._pop_scale = math.sin(float(v) * math.pi) * POP_AMOUNT
        self.update()

    def set_role(self, role: str):
        if role == self._role:
            return
        self._role = role
        self._apply_label_style()
        self.update()

    def _apply_label_style(self):
        if self._role == "center":
            self._name.setStyleSheet(
                f"color: #FF9500; font-size: {fs(16)}px; font-weight: 700; background: transparent;"
            )
        else:
            self._name.setStyleSheet(
                f"color: rgba(255,255,255,150); font-size: {fs(13)}px; font-weight: 500; background: transparent;"
            )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        is_center = self._role == "center"
        d = (76 if is_center else 52) * (1.0 + self._pop_scale)
        cx, cy = self.width() / 2.0, self.height() * 0.40
        rect = QRectF(cx - d / 2, cy - d / 2, d, d)

        if is_center:
            glow = QRadialGradient(QPointF(cx, cy), d * 0.95)
            glow.setColorAt(0.0, QColor(255, 149, 0, 70))
            glow.setColorAt(1.0, QColor(255, 149, 0, 0))
            painter.setBrush(glow)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(cx, cy), d * 0.88, d * 0.88)

        base = QColor(BADGE_COLORS.get(self.key, COLOR_SELECTED))
        if not is_center:
            base.setAlpha(140)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, base.lighter(115))
        grad.setColorAt(1.0, base.darker(110))
        painter.setBrush(grad)
        painter.setPen(QColor(255, 149, 0, 220) if is_center else Qt.NoPen)
        if is_center:
            pen = painter.pen()
            pen.setWidthF(3.0)
            painter.setPen(pen)
        painter.drawEllipse(rect)

        glyph_color = QColor(255, 255, 255, 255 if is_center else 205)
        if self.key == "CONFIG":
            self._draw_gear(painter, QPointF(cx, cy), d * 0.30, glyph_color)
        else:
            painter.setFont(QFont(EMOJI_FONT, int(d * 0.5)))
            painter.setPen(glyph_color)
            painter.drawText(rect, Qt.AlignCenter, EMOJI.get(self.key, "?"))

    @staticmethod
    def _draw_gear(painter: QPainter, center: QPointF, radius: float, color: QColor):
        """Engrane vectorial: anillo + 8 dientes + eje hueco. Sustituye al
        emoji ⚙️ (ver nota junto a EMOJI)."""
        painter.save()
        pen = QPen(color, max(2.0, radius * 0.42), Qt.SolidLine, Qt.FlatCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, radius * 0.78, radius * 0.78)  # anillo
        for i in range(8):  # dientes
            ang = math.radians(i * 45.0)
            x1 = center.x() + math.cos(ang) * radius * 0.92
            y1 = center.y() + math.sin(ang) * radius * 0.92
            x2 = center.x() + math.cos(ang) * radius * 1.28
            y2 = center.y() + math.sin(ang) * radius * 1.28
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        painter.setPen(QPen(color, max(1.5, radius * 0.22)))
        painter.drawEllipse(center, radius * 0.30, radius * 0.30)  # eje
        painter.restore()

    def mousePressEvent(self, event):
        if self.key == "SALIR":
            # No selecciona/emite de inmediato: exige mantener presionado (ver SALIR_HOLD_MS).
            # Retroalimentación visual inmediata para que se perciba como "activo" durante la espera.
            self._name.setText(t("ribbon.holding"))
            self._name.setStyleSheet(
                f"color: #FF9500; font-size: {fs(14)}px; font-weight: bold; background: transparent;")
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
            self._apply_label_style()
        super().mouseReleaseEvent(event)

    def _on_hold_complete(self):
        self.long_pressed.emit(self.key)


class _RibbonArrow(QLabel):
    clicked = pyqtSignal()

    def __init__(self, glyph: str, parent=None):
        super().__init__(glyph, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setAlignment(Qt.AlignCenter)
        self.restyle()

    def restyle(self):
        self.setStyleSheet(
            f"color: rgba(255,255,255,150); font-size: {fs(32)}px; background: transparent;")

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class GhostRibbon(QWidget):
    screen_selected = pyqtSignal(str)
    exit_requested = pyqtSignal()  # emitido tras SALIR_HOLD_MS de presión sostenida sobre "Salir"
    visibility_changed = pyqtSignal(bool)  # se muestra/oculta -- MainWindow sincroniza la leyenda de botones a esto

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "background: rgba(15, 15, 25, 180);"
            "border-top: 1px solid rgba(255, 255, 255, 0.15);"
            "border-top-left-radius: 20px;"
            "border-top-right-radius: 20px;"
        )

        self._current = _KEYS[0]
        self._animating = False
        self._icon_widgets = {}  # offset (-2..2) -> _RibbonIcon

        self._arrow_prev = _RibbonArrow("‹", self)
        self._arrow_next = _RibbonArrow("›", self)
        self._arrow_prev.clicked.connect(lambda: self._step(-1))
        self._arrow_next.clicked.connect(lambda: self._step(1))

        # "stage" recorta cualquier icono del reel que caiga fuera de su ancho
        # (comportamiento estándar de Qt: los hijos se recortan al rect del
        # padre) -- así los offsets ±2 quedan ocultos hasta que el deslizamiento
        # los trae a la vista.
        self._stage = QWidget(self)
        self._reel = QWidget(self._stage)
        self._reel.setFixedSize(SLOT_STEP * 5, ICON_H)

        # OutBack: llega, se pasa apenas (~10%) y asienta -- se percibe más
        # "vivo"/amigable que un OutCubic seco. El truco de reconstruir+resetear
        # al final no cambia: OutBack también termina exactamente en EndValue.
        self._reel_anim = QPropertyAnimation(self._reel, b"pos", self)
        self._reel_anim.setDuration(320)
        self._reel_anim.setEasingCurve(QEasingCurve.OutBack)
        self._reel_anim.finished.connect(self._on_reel_anim_finished)

        self._rebuild_icons()

        # Autohide instantáneo (visible/oculto), sin QGraphicsOpacityEffect:
        # un QGraphicsEffect en CUALQUIER widget de esta ventana obliga a Qt a
        # componer toda la ventana a través de un buffer offscreen, lo que
        # rompe el overlay de video nativo de AnimationPlayer -- confirmado en
        # diagnóstico (el video se congela tras el primer loop en cuanto hay
        # un QGraphicsEffect activo en la misma ventana, sin importar en qué
        # widget). Se pierde el fundido suave de la cinta a cambio de que el
        # video de fondo no se rompa.
        self.setVisible(False)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide)

    def _hide(self):
        self.setVisible(False)
        self.visibility_changed.emit(False)

    # ---------- geometría ----------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        h = self.height()
        self._arrow_prev.setGeometry(0, 0, ARROW_W, h)
        self._arrow_next.setGeometry(max(self.width() - ARROW_W, ARROW_W), 0, ARROW_W, h)
        self._stage.setGeometry(ARROW_W, 0, max(self.width() - 2 * ARROW_W, 0), h)
        self._reel.move(self._reel_base_pos())

    def _reel_base_pos(self) -> QPoint:
        base_x = self._stage.width() // 2 - ICON_W // 2 - 2 * SLOT_STEP
        base_y = (self._stage.height() - ICON_H) // 2
        return QPoint(base_x, base_y)

    def _rebuild_icons(self):
        for icon in self._icon_widgets.values():
            icon.deleteLater()
        self._icon_widgets = {}

        cur_idx = _KEYS.index(self._current)
        for offset in (-2, -1, 0, 1, 2):
            key = _KEYS[(cur_idx + offset) % len(_KEYS)]
            icon = _RibbonIcon(key, _name(key), self._reel)
            icon.set_role("center" if offset == 0 else "side")
            icon.setGeometry((offset + 2) * SLOT_STEP, 0, ICON_W, ICON_H)
            icon.clicked.connect(lambda k, off=offset: self._on_icon_clicked(off))
            icon.long_pressed.connect(self._on_icon_long_pressed)
            icon.press_started.connect(self.notify_touch)
            icon.show()
            self._icon_widgets[offset] = icon

        self._reel.move(self._reel_base_pos())

    # ---------- i18n / escala de fuente en caliente ----------
    def retranslate(self):
        """Reconstruye los iconos con los nombres del idioma activo."""
        self._rebuild_icons()

    def restyle(self):
        """Reaplica estilos tras un cambio de escala de fuente."""
        self._rebuild_icons()
        self._arrow_prev.restyle()
        self._arrow_next.restyle()

    # ---------- API pública ----------
    def notify_touch(self):
        """Llamar en cada toque de pantalla: muestra y reinicia el contador de 5s de autohide."""
        if not self.isVisible():
            self.setVisible(True)
            self.visibility_changed.emit(True)
        self._hide_timer.start(HIDE_DELAY_MS)

    def set_active(self, key: str, emit: bool = True):
        """Sincroniza el resaltado con la pantalla activa (p. ej. al volver a Home por botón
        físico): salto directo, sin deslizar -- la navegación ya ocurrió, esto solo refleja
        el estado."""
        if key not in _KEYS or self._animating:
            return
        self._current = key
        self._rebuild_icons()
        if emit:
            self.screen_selected.emit(key)

    # ---------- API pública ----------
    def step(self, delta: int):
        """Avanza/retrocede una pantalla (S13: cursores físicos izquierda/derecha
        en cualquier pantalla). Misma animación que las flechas ‹ › o el swipe."""
        self._step(delta)

    # ---------- deslizamiento infinito ----------
    def _step(self, delta: int):
        if self._animating:
            return
        self._animating = True
        idx = (_KEYS.index(self._current) + delta) % len(_KEYS)
        self._current = _KEYS[idx]

        start = self._reel.pos()
        end = QPoint(start.x() - delta * SLOT_STEP, start.y())
        self._reel_anim.stop()
        self._reel_anim.setStartValue(start)
        self._reel_anim.setEndValue(end)
        self._reel_anim.start()
        self.notify_touch()

    def _on_reel_anim_finished(self):
        self._animating = False
        # Reconstruye centrado en la nueva pantalla y resetea el reel a su
        # posición de reposo sin animar -- visualmente idéntico a donde quedó
        # el deslizamiento, así el "corte" nunca se percibe (truco estándar de
        # carrusel infinito).
        self._rebuild_icons()
        center = self._icon_widgets.get(0)
        if center is not None:
            center.pop()  # micro-feedback al aterrizar en la pantalla nueva
        self.screen_selected.emit(self._current)

    def _on_icon_clicked(self, offset: int):
        if offset == 0:
            return  # ya es la pantalla activa
        self._step(1 if offset > 0 else -1)

    def _on_icon_long_pressed(self, key: str):
        if key == "SALIR":
            self.exit_requested.emit()
