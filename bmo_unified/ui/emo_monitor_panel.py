# ui/emo_monitor_panel.py
"""Panel de Monitor Emocional embebido (6.2): feed de video + nivel de estrés
con color, SIN ventanas externas (nada de cv2.imshow). Además de la etiqueta
global de fusión, dibuja el recuadro + etiqueta de la última predicción de
cada módulo (A/B/C) que traiga región (meta.region), para no depender solo
del color agregado."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

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

_BADGE_STYLE = (
    "background: {color}; color: white; font-size: 18px; font-weight: bold; "
    "border-radius: 10px; padding: 8px 16px;"
)


class EmoMonitorPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: rgba(15,15,20,235);")

        self._latest_preds = {}  # module ("A"/"B"/"C") -> último PredMsg (dict)

        self._video_label = QLabel()
        self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.setStyleSheet("background: black;")

        self._badge = QLabel("INSEGURO")
        self._badge.setAlignment(Qt.AlignCenter)
        self._badge.setStyleSheet(_BADGE_STYLE.format(color=STRESS_COLORS["INSEGURO"]))

        self._btn_stop = QPushButton("Detener reconocimiento")
        self._btn_stop.setStyleSheet("min-height: 64px; font-size: 16px;")

        top = QHBoxLayout()
        top.addWidget(self._badge)
        top.addStretch()
        top.addWidget(self._btn_stop)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._video_label, stretch=1)

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
        if module:
            self._latest_preds[module] = pred

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
