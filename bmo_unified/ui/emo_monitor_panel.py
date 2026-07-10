# ui/emo_monitor_panel.py
"""Panel de Monitor Emocional embebido (6.2): feed de video + nivel de estrés
con color, SIN ventanas externas (nada de cv2.imshow). Combina dos vistas de
los módulos A/B/C (ambas alimentadas por VisionEngine.pred_ready): recuadro +
etiqueta dibujados sobre el propio video (para ubicación espacial), y un panel
lateral con la lectura textual de cada módulo (para lectura rápida aunque el
recuadro no caiga bien sobre la persona).

Rediseño: salir de Video por la cinta fantasma YA detiene el motor de visión
(ver MainWindow._show_view), así que el botón "Detener reconocimiento" era
una acción casi redundante presentada como protagonista (pastilla ancha con
texto) -- ahora es un botón circular pequeño (✕) en la esquina, coherente con
las insignias circulares del resto de la app (ui/ghost_ribbon.py). El resto
del panel usa tarjetas por módulo en vez de texto plano suelto."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

PANEL_BG = "#16232C"  # navy oscuro (misma familia que el ojo de Moodi, #2A3C4B)
CARD_BG = "rgba(255,255,255,18)"

STRESS_COLORS = {
    "BAJO": "#3CC83C",
    "MEDIO": "#FFC107",
    "ALTO": "#FF3B30",
    "INSEGURO": "#9E9E9E",
}

MODULE_COLORS = {
    "A": QColor("#00E5FF"),  # facial (cian)
    "B": QColor("#FF4FD8"),  # gestual (magenta)
    "C": QColor("#FFEB3B"),  # movimiento (amarillo)
}

MODULE_NAMES = {
    "A": "A · Facial",
    "B": "B · Gestual",
    "C": "C · Movimiento",
}

_BADGE_STYLE = (
    "background: {color}; color: white; font-size: 20px; font-weight: 700; "
    "border-radius: 22px; padding: 10px 26px;"
)

_STOP_BTN_STYLE = (
    "QPushButton { background: rgba(255,255,255,25); color: white; font-size: 18px; "
    "font-weight: 700; border-radius: 22px; border: 1px solid rgba(255,255,255,60); }"
    "QPushButton:pressed { background: #C1573B; border: 1px solid #C1573B; }"
)


class _ModuleCard(QWidget):
    def __init__(self, module: str, parent=None):
        super().__init__(parent)
        color = MODULE_COLORS[module]
        self.setStyleSheet(f"background: {CARD_BG}; border-radius: 12px;")

        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(f"background: {color.name()}; border-radius: 6px;")

        name_lbl = QLabel(MODULE_NAMES[module])
        name_lbl.setStyleSheet("color: #B8C4CC; font-size: 13px; font-weight: 600; background: transparent;")

        self._value_lbl = QLabel("—")
        self._value_lbl.setWordWrap(True)
        self._value_lbl.setStyleSheet(
            f"color: {color.name()}; font-size: 15px; font-weight: 700; background: transparent;"
        )

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(dot)
        header.addWidget(name_lbl)
        header.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self._value_lbl)

    def set_reading(self, label_txt: str, conf: float, present: bool):
        status = "" if present else " · sin detección"
        self._value_lbl.setText(f"{label_txt}  {conf:.0%}{status}")


class EmoMonitorPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background: {PANEL_BG};")

        self._latest_preds = {}  # module ("A"/"B"/"C") -> último PredMsg (dict)
        self._module_cards = {}  # module -> _ModuleCard

        self._video_label = QLabel()
        self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.setStyleSheet("background: black;")

        self._badge = QLabel("INSEGURO")
        self._badge.setAlignment(Qt.AlignCenter)
        self._badge.setStyleSheet(_BADGE_STYLE.format(color=STRESS_COLORS["INSEGURO"]))

        self._btn_stop = QPushButton("✕")
        self._btn_stop.setFixedSize(44, 44)
        self._btn_stop.setToolTip("Detener reconocimiento")
        self._btn_stop.setStyleSheet(_STOP_BTN_STYLE)

        top = QHBoxLayout()
        top.addWidget(self._badge)
        top.addStretch()
        top.addWidget(self._btn_stop)

        side_panel = QWidget()
        side_panel.setFixedWidth(220)
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(10)

        side_title = QLabel("LECTURA POR MÓDULO")
        side_title.setStyleSheet(
            "color: #8B9AA3; font-size: 11px; font-weight: 700; letter-spacing: 1px; background: transparent;"
        )
        side_layout.addWidget(side_title)

        for module in ("A", "B", "C"):
            card = _ModuleCard(module)
            side_layout.addWidget(card)
            self._module_cards[module] = card
        side_layout.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._video_label, stretch=1)
        body.addWidget(side_panel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)
        layout.addLayout(top)
        layout.addLayout(body, stretch=1)

    @property
    def stop_button(self) -> QPushButton:
        return self._btn_stop

    def on_frame(self, qimage):
        pix = QPixmap.fromImage(qimage)
        if pix.isNull():
            return
        self._draw_module_overlays(pix)
        self._video_label.setPixmap(
            pix.scaled(self._video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def on_pred(self, pred: dict):
        module = pred.get("module")
        if not module:
            return
        self._latest_preds[module] = pred

        card = self._module_cards.get(module)
        if card is None:
            return
        card.set_reading(
            str(pred.get("label", "?")).upper(),
            float(pred.get("conf", 0.0)),
            bool(pred.get("present", False)),
        )

    def on_stats(self, stats: dict):
        label = str(stats.get("label", "INSEGURO")).upper()
        color = STRESS_COLORS.get(label, STRESS_COLORS["INSEGURO"])
        self._badge.setText(label)
        self._badge.setStyleSheet(_BADGE_STYLE.format(color=color))

    def _draw_module_overlays(self, pix: QPixmap):
        """Dibuja sobre 'pix' (en la resolución original del frame, antes de
        escalar al QLabel) el recuadro + etiqueta de la última predicción de
        cada módulo que traiga meta.region -- así el recuadro escala junto
        con la imagen en vez de quedar desalineado."""
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)

        for module, pred in self._latest_preds.items():
            meta = pred.get("meta") or {}
            region = meta.get("region")
            if not region:
                continue
            x, y = int(region.get("x", 0)), int(region.get("y", 0))
            w, h = int(region.get("w", 0)), int(region.get("h", 0))
            if w <= 0 or h <= 0:
                continue

            color = MODULE_COLORS.get(module, QColor("white"))
            pen = QPen(color)
            pen.setWidth(3)
            painter.setPen(pen)
            painter.drawRect(x, y, w, h)

            label = f"{module}: {pred.get('label', '?')} {float(pred.get('conf', 0.0)):.2f}"
            text_y = y - 6 if y - 6 > 12 else y + 16
            painter.drawText(x, text_y, label)

        painter.end()
