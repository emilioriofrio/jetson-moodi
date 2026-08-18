# ui/emo_monitor_panel.py
"""Panel de Monitor Emocional embebido (6.2): feed de video + nivel de estrés
con color, SIN ventanas externas (nada de cv2.imshow). Combina dos vistas de
los módulos A/B/C (ambas alimentadas por VisionEngine.pred_ready): recuadro +
etiqueta dibujados sobre el propio video (para ubicación espacial), y un panel
lateral con la lectura textual de cada módulo (para lectura rápida aunque el
recuadro no caiga bien sobre la persona).

Anti-parpadeo de etiquetas (fase UI/CONFIG/VOZ, bloque cámara): la etiqueta
mostrada por módulo es la MAYORÍA de las últimas SMOOTH_WINDOW predicciones,
no la última cruda -- una predicción aislada distinta ya no alterna el texto
en pantalla frame a frame. Es suavizado SOLO de presentación: no toca la
fusión ni los módulos (que ya tienen su propia histéresis/EMA aguas arriba).

Textos vía core/i18n (t() para UI fija, i18n.label() para las etiquetas que
emiten los módulos, con fallback a la etiqueta cruda) y tamaños vía
ui/theme.fs(); MainWindow llama retranslate()/restyle() en caliente."""

from collections import Counter, deque

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

from core import i18n
from core.i18n import t
from ui.theme import fs

PANEL_BG = "#16232C"  # navy oscuro (misma familia que el ojo de Moodi, #2A3C4B)
CARD_BG = "rgba(255,255,255,18)"

# claves = etiqueta cruda del motor (español); el texto mostrado se traduce
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

# nº de predicciones recientes por módulo sobre las que se vota la etiqueta
SMOOTH_WINDOW = 5

_BADGE_STYLE = (
    "background: {color}; color: white; font-size: {px}px; font-weight: 700; "
    "border-radius: 22px; padding: 10px 26px;"
)

_STOP_BTN_STYLE = (
    "QPushButton {{ background: rgba(255,255,255,25); color: white; font-size: {px}px; "
    "font-weight: 700; border-radius: 32px; border: 1px solid rgba(255,255,255,60); }}"
    "QPushButton:pressed {{ background: #C1573B; border: 1px solid #C1573B; }}"
)


def _majority_label(history: deque) -> str:
    """Etiqueta mayoritaria de la ventana; en empate gana la más reciente."""
    if not history:
        return "?"
    counts = Counter(history)
    best = max(counts.values())
    for lbl in reversed(history):
        if counts[lbl] == best:
            return lbl
    return history[-1]


class _ModuleCard(QWidget):
    def __init__(self, module: str, parent=None):
        super().__init__(parent)
        self.module = module
        color = MODULE_COLORS[module]

        self._dot = QLabel()
        self._dot.setFixedSize(12, 12)
        self._dot.setStyleSheet(f"background: {color.name()}; border-radius: 6px;")

        self._name_lbl = QLabel()
        self._value_lbl = QLabel("—")
        self._value_lbl.setWordWrap(True)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self._dot)
        header.addWidget(self._name_lbl)
        header.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self._value_lbl)

        self.retranslate()
        self.restyle()

    def retranslate(self):
        self._name_lbl.setText(t(f"monitor.module_{self.module.lower()}"))

    def restyle(self):
        color = MODULE_COLORS[self.module]
        self.setStyleSheet(f"background: {CARD_BG}; border-radius: 12px;")
        self._name_lbl.setStyleSheet(
            f"color: #B8C4CC; font-size: {fs(14)}px; font-weight: 600; background: transparent;")
        self._value_lbl.setStyleSheet(
            f"color: {color.name()}; font-size: {fs(17)}px; font-weight: 700; background: transparent;")

    def set_reading(self, label_txt: str, conf: float, present: bool):
        status = "" if present else t("monitor.no_detection")
        self._value_lbl.setText(f"{label_txt}  {conf:.0%}{status}")

    def reset(self):
        """Vuelve a "sin lectura" (arranque/parada del motor de visión)."""
        self._value_lbl.setText("—")


class EmoMonitorPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._latest_preds = {}    # module -> último PredMsg (dict, para overlays)
        self._label_history = {}   # module -> deque de etiquetas (suavizado)
        self._module_cards = {}
        self._last_stats_label = "INSEGURO"

        self._video_label = QLabel()
        self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.setStyleSheet("background: black;")
        # QSizePolicy.Ignored + minimumSize(1,1) (S11, arregla el video
        # "recortado"): un QLabel CON pixmap adopta el tamaño del pixmap como
        # sizeHint Y como minimumSizeHint, así que al hacer setPixmap el layout
        # se veía forzado a reservarle ese alto; sumado a la barra superior
        # excedía los 600px del panel y el borde inferior del video quedaba
        # cortado fuera de pantalla. Con Ignored el layout manda sobre el
        # pixmap y no al revés.
        self._video_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._video_label.setMinimumSize(1, 1)

        self._badge = QLabel()
        self._badge.setAlignment(Qt.AlignCenter)

        # Target táctil ≥64px (antes 44px): botón circular de detener.
        self._btn_stop = QPushButton("✕")
        self._btn_stop.setFixedSize(64, 64)

        top = QHBoxLayout()
        top.addWidget(self._badge)
        top.addStretch()
        top.addWidget(self._btn_stop)

        side_panel = QWidget()
        side_panel.setFixedWidth(220)
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(10)

        self._side_title = QLabel()
        side_layout.addWidget(self._side_title)

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
        # Margen superior 16 -> 48 (S12): la leyenda de botones de MainWindow es
        # una franja de ancho completo de ~39 px (fs(18)+16 con la fuente más
        # grande) que se dibuja ENCIMA de este panel, y solapaba la mitad
        # superior del botón ✕. Ya no intercepta toques (es transparente al
        # ratón desde S12), pero además conviene que el botón no quede debajo
        # de un texto: se ve mejor y el objetivo táctil queda entero.
        layout.setContentsMargins(20, 48, 20, 20)
        layout.setSpacing(14)
        layout.addLayout(top)
        layout.addLayout(body, stretch=1)

        self.retranslate()
        self.restyle()

    # ---------- i18n / escala de fuente en caliente ----------
    def retranslate(self):
        self._side_title.setText(t("monitor.side_title"))
        self._btn_stop.setToolTip(t("monitor.stop_tooltip"))
        for card in self._module_cards.values():
            card.retranslate()
        self._apply_badge(self._last_stats_label)

    def restyle(self):
        self.setStyleSheet(f"background: {PANEL_BG};")
        self._side_title.setStyleSheet(
            f"color: #8B9AA3; font-size: {fs(12)}px; font-weight: 700; "
            "letter-spacing: 1px; background: transparent;")
        self._btn_stop.setStyleSheet(_STOP_BTN_STYLE.format(px=fs(20)))
        for card in self._module_cards.values():
            card.restyle()
        self._apply_badge(self._last_stats_label)

    @property
    def stop_button(self) -> QPushButton:
        return self._btn_stop

    def reset_session(self):
        """Limpia el estado acumulado de una sesión del motor de visión.

        (S11, corrige bug de S10): _label_history / _latest_preds sobrevivían al
        stop() del VisionEngine, así que al reentrar a VIDEO las primeras
        predicciones nuevas se votaban junto con etiquetas de la sesión
        ANTERIOR y el panel mostraba unos segundos una lectura obsoleta.
        MainWindow lo llama al arrancar y al detener el motor."""
        self._latest_preds.clear()
        self._label_history.clear()
        self._last_stats_label = "INSEGURO"
        self._video_label.clear()
        for module, card in self._module_cards.items():
            card.reset()
        self._apply_badge(self._last_stats_label)

    def on_frame(self, qimage):
        pix = QPixmap.fromImage(qimage)
        if pix.isNull():
            return
        self._draw_module_overlays(pix)
        # contentsRect(): área realmente pintable del QLabel (descuenta borde y
        # margen). Con QSizePolicy.Ignored el label ya no crece con el pixmap,
        # así que este destino es estable y el video no se desborda.
        target = self._video_label.contentsRect().size()
        if target.width() < 1 or target.height() < 1:
            return
        self._video_label.setPixmap(
            pix.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def on_pred(self, pred: dict):
        module = pred.get("module")
        if not module:
            return
        self._latest_preds[module] = pred

        history = self._label_history.setdefault(module, deque(maxlen=SMOOTH_WINDOW))
        history.append(str(pred.get("label", "?")))

        card = self._module_cards.get(module)
        if card is None:
            return
        card.set_reading(
            i18n.label(_majority_label(history)),
            float(pred.get("conf", 0.0)),
            bool(pred.get("present", False)),
        )

    def on_stats(self, stats: dict):
        self._apply_badge(str(stats.get("label", "INSEGURO")).upper())

    def _apply_badge(self, raw_label: str):
        self._last_stats_label = raw_label
        color = STRESS_COLORS.get(raw_label, STRESS_COLORS["INSEGURO"])
        self._badge.setText(i18n.label(raw_label).upper())
        self._badge.setStyleSheet(_BADGE_STYLE.format(color=color, px=fs(22)))

    def _draw_module_overlays(self, pix: QPixmap):
        """Dibuja sobre 'pix' (en la resolución original del frame, antes de
        escalar al QLabel) el recuadro + etiqueta de la última predicción de
        cada módulo que traiga meta.region -- así el recuadro escala junto
        con la imagen en vez de quedar desalineado. La etiqueta usa la misma
        mayoría suavizada que la tarjeta lateral (coherencia entre ambas)."""
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

            shown = i18n.label(_majority_label(self._label_history.get(module, deque())))
            label = f"{module}: {shown} {float(pred.get('conf', 0.0)):.2f}"
            text_y = y - 6 if y - 6 > 12 else y + 16
            painter.drawText(x, text_y, label)

        painter.end()
