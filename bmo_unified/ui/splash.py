# ui/splash.py
"""
Pantalla de carga de Moodi (S11).

Por qué es un PROCESO APARTE y no un widget dentro de bmo_app.py: buena parte
de la espera de arranque ocurre ANTES de que exista un QApplication -- los ~12s
que start_bmo.sh aguarda a llama-server, más gunicorn/ia_bridge y el arranque
del propio intérprete con PyQt. Un splash interno solo podría aparecer al final
de esa espera, que es justo el tramo que el usuario percibe como "la app tarda
en arrancar". Este proceso se lanza primero, ocupa la pantalla desde el segundo
cero y se cierra solo cuando bmo_app ya pintó su ventana.

Protocolo (deliberadamente mínimo, un archivo de texto):
    /tmp/moodi_boot.status  contiene UNA línea "PORCENTAJE|CLAVE_I18N"
    p.ej.  "45|boot.bridge"
    La clave especial "READY" (en cualquier posición) cierra el splash.
Se eligió un archivo y no un socket/FIFO porque lo escriben tanto bash
(start_bmo.sh) como Python (bmo_app.py), y un `echo >` es atómico de sobra para
un único escritor secuencial.

Sin video ni QGraphicsEffect: el fondo es QPainter puro, así que no compite con
el overlay nativo de GStreamer que usará después AnimationPlayer (ver la
precaución documentada en ui/ghost_ribbon.py y ui/animation_player.py).
"""

import json
import math
import os
import random
import sys

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPixmap,
                         QTransform)
from PyQt5.QtWidgets import QApplication, QWidget

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import i18n  # noqa: E402
from ui.theme import ACCENT_ORANGE, BG_TEAL, CORAL, CREAM, TEXT_NAVY  # noqa: E402

STATUS_PATH = "/tmp/moodi_boot.status"
SCREEN_W, SCREEN_H = 1024, 600

POLL_MS = 100     # lectura del archivo de estado
ANIM_MS = 33      # ~30 fps de animación

# Piezas de rompecabezas cayendo. 22 -> 34 y repartidas por CARRILES (S12): con
# la posición horizontal totalmente al azar se formaban grupos y quedaban franjas
# de pantalla vacías -- el usuario lo describió como que la lluvia "se ve un poco
# recortada". Cada pieza vive en su propio carril vertical y conserva el carril al
# reaparecer arriba, así la caída cubre TODO el ancho sin dejar de verse orgánica
# (dentro del carril la posición, el tamaño, el giro y la velocidad siguen siendo
# aleatorios).
N_PIECES = 34
# Ancho con el que se dibuja el LOGO, en píxeles de pantalla. Antes era un
# factor de escala fijo (2.4x) atado al tamaño del PNG que había entonces
# (149x51), así que sustituirlo por una imagen de más resolución lo habría
# dibujado gigante en vez de nítido. Con un ancho objetivo, el mismo código
# aprovecha automáticamente cualquier LOGO.png de mayor definición (S12: el
# usuario va a proveer uno) sin tocar nada más.
LOGO_TARGET_W = 360.0

# Recortes de las piezas COMPLETAS dentro de assets/lluvia_rompecabezas.png
# (x, y, ancho, alto). Se calcularon una sola vez por componentes conexas sobre
# el canal alfa, descartando las piezas que la lámina corta por el borde y las
# de proporción rara. Van fijos en vez de recalcularse al arrancar porque el
# splash debe aparecer cuanto antes: analizar la imagen en Python costaría más
# que todo el resto de su arranque, y la lámina no cambia.
PIECE_RECTS = [
    (16, 74, 82, 73), (49, 184, 78, 78), (74, 357, 75, 86),
    (162, 160, 78, 78), (168, 299, 88, 77), (225, 56, 69, 86),
    (269, 387, 87, 76), (286, 204, 76, 87), (300, 88, 81, 86),
]


class _Piece:
    """Una pieza de rompecabezas cayendo, con su propia velocidad y giro."""

    __slots__ = ("x", "y", "vy", "rot", "vrot", "scale", "alpha", "tile", "_n",
                 "_lane", "_lanes")

    def __init__(self, rnd, n_tiles, lane=0, lanes=1, start_above=False):
        self.tile = 0
        self._n = n_tiles
        self._lane = lane      # carril vertical propio (ver N_PIECES)
        self._lanes = max(1, lanes)
        self.reset(rnd, start_above)

    def reset(self, rnd, start_above=True):
        self.tile = rnd.randrange(self._n) if self._n else 0
        # Dentro del carril, con desborde a los lados para que las piezas
        # entren y salgan por los bordes en vez de "empezar" dentro de la
        # pantalla.
        ancho_carril = (SCREEN_W + 80) / self._lanes
        self.x = -40 + (self._lane + rnd.uniform(0.1, 0.9)) * ancho_carril
        self.y = rnd.uniform(-260, -20) if start_above else rnd.uniform(-260, SCREEN_H)
        # Profundidad: las piezas grandes caen más rápido y más opacas
        # (paralaje simple, da sensación de volumen sin costo real).
        self.scale = rnd.uniform(0.30, 0.85)
        self.vy = 22 + self.scale * 58
        self.rot = rnd.uniform(0, 360)
        self.vrot = rnd.uniform(-26, 26)
        self.alpha = 0.30 + self.scale * 0.55


class SplashWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFixedSize(SCREEN_W, SCREEN_H)
        self.setCursor(Qt.BlankCursor)

        self._rnd = random.Random(20260720)  # reproducible: misma caída cada arranque
        self._pct = 0.0
        self._shown_pct = 0.0   # valor interpolado (la barra no salta de golpe)
        self._msg_key = "boot.starting"
        self._t = 0.0
        self._done = False

        assets = os.path.join(BASE_DIR, "assets")
        self._logo = QPixmap(os.path.join(assets, "LOGO.png"))
        sheet = QPixmap(os.path.join(assets, "lluvia_rompecabezas.png"))
        self._piece_tiles = self._cut_pieces(sheet)
        n_tiles = max(1, len(self._piece_tiles))
        self._pieces = [_Piece(self._rnd, n_tiles, lane=i, lanes=N_PIECES,
                               start_above=False)
                        for i in range(N_PIECES)]

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)
        self._anim.start(ANIM_MS)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._read_status)
        self._poll.start(POLL_MS)

    # ---------- assets ----------
    @staticmethod
    def _cut_pieces(sheet: QPixmap):
        """Extrae las piezas sueltas de la lámina usando PIECE_RECTS.

        La lámina es una composición fija: desplazarla entera se vería como un
        patrón deslizándose, no como piezas cayendo. Un corte en rejilla ciega
        (primer intento) partía piezas por la mitad y las mitades se leían como
        escombros, así que se recortan las piezas completas ya localizadas."""
        if sheet.isNull():
            return []
        tiles = []
        for x, y, w, h in PIECE_RECTS:
            tile = sheet.copy(x, y, w, h)
            if not tile.isNull():
                tiles.append(tile)
        return tiles

    # ---------- estado de arranque ----------
    def _read_status(self):
        try:
            with open(STATUS_PATH, "r", encoding="utf-8") as f:
                raw = f.read().strip()
        except OSError:
            return
        if not raw:
            return
        if "READY" in raw:
            self._finish()
            return
        pct, _, key = raw.partition("|")
        try:
            self._pct = max(0.0, min(100.0, float(pct)))
        except ValueError:
            pass
        if key:
            self._msg_key = key.strip()

    def _finish(self):
        if self._done:
            return
        self._done = True
        self._pct = 100.0
        self._msg_key = "boot.ready"
        self._poll.stop()
        # Un instante en 100% antes de cerrar: pasar de 90% a ventana negra se
        # percibe como un corte, no como "terminó de cargar".
        QTimer.singleShot(450, self._close_now)

    def _close_now(self):
        self._anim.stop()
        self.close()
        QApplication.quit()

    # ---------- animación ----------
    def _tick(self):
        dt = ANIM_MS / 1000.0
        self._t += dt
        for p in self._pieces:
            p.y += p.vy * dt
            p.rot += p.vrot * dt
            if p.y > SCREEN_H + 120:
                p.reset(self._rnd, start_above=True)
        # La barra persigue el objetivo en vez de saltar: los pasos de arranque
        # son escalones grandes y muy espaciados.
        self._shown_pct += (self._pct - self._shown_pct) * min(1.0, dt * 3.0)
        self.update()

    # ---------- pintado ----------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        self._paint_background(painter)
        self._paint_pieces(painter)
        self._paint_logo(painter)
        self._paint_progress(painter)

        painter.end()

    def _paint_background(self, painter):
        grad = QLinearGradient(0, 0, 0, SCREEN_H)
        grad.setColorAt(0.0, QColor(BG_TEAL).lighter(112))
        grad.setColorAt(1.0, QColor(BG_TEAL).darker(108))
        painter.fillRect(self.rect(), grad)

    def _paint_pieces(self, painter):
        if not self._piece_tiles:
            return
        for p in self._pieces:
            tile = self._piece_tiles[p.tile % len(self._piece_tiles)]
            painter.save()
            painter.setOpacity(p.alpha)
            painter.translate(p.x, p.y)
            painter.rotate(p.rot)
            w = tile.width() * p.scale
            h = tile.height() * p.scale
            painter.drawPixmap(QRectF(-w / 2, -h / 2, w, h), tile,
                               QRectF(tile.rect()))
            painter.restore()
        painter.setOpacity(1.0)

    def _paint_logo(self, painter):
        if self._logo.isNull():
            return
        escala = LOGO_TARGET_W / max(1, self._logo.width())
        lw = self._logo.width() * escala
        lh = self._logo.height() * escala
        # respiración suave (±2%), da vida sin distraer
        breath = 1.0 + 0.02 * (0.5 - 0.5 * math.cos(self._t * 1.6))
        lw *= breath
        lh *= breath
        x = (SCREEN_W - lw) / 2.0
        y = SCREEN_H * 0.30 - lh / 2.0
        painter.drawPixmap(QRectF(x, y, lw, lh), self._logo, QRectF(self._logo.rect()))

    def _paint_progress(self, painter):
        bar_w = SCREEN_W * 0.52
        bar_h = 20.0
        x = (SCREEN_W - bar_w) / 2.0
        y = SCREEN_H * 0.62

        # pista
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 110))
        painter.drawRoundedRect(QRectF(x, y, bar_w, bar_h), bar_h / 2, bar_h / 2)

        # relleno (coral -> naranja de acento, la paleta de Moodi)
        frac = max(0.0, min(1.0, self._shown_pct / 100.0))
        if frac > 0.001:
            fill_w = max(bar_h, bar_w * frac)
            grad = QLinearGradient(x, 0, x + bar_w, 0)
            grad.setColorAt(0.0, QColor(CORAL))
            grad.setColorAt(1.0, QColor(ACCENT_ORANGE))
            painter.setBrush(grad)
            painter.drawRoundedRect(QRectF(x, y, fill_w, bar_h), bar_h / 2, bar_h / 2)

        # borde
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QColor(TEXT_NAVY))
        painter.drawRoundedRect(QRectF(x, y, bar_w, bar_h), bar_h / 2, bar_h / 2)

        # mensaje del paso actual
        painter.setPen(QColor(TEXT_NAVY))
        painter.setFont(QFont("Ubuntu", 15, QFont.Bold))
        painter.drawText(QRectF(0, y + bar_h + 14, SCREEN_W, 34),
                         Qt.AlignHCenter | Qt.AlignTop, i18n.t(self._msg_key))

        # porcentaje
        painter.setFont(QFont("Ubuntu", 11, QFont.Bold))
        painter.setPen(QColor(TEXT_NAVY).lighter(125))
        painter.drawText(QRectF(0, y - 30, SCREEN_W, 24),
                         Qt.AlignHCenter | Qt.AlignTop, f"{int(self._shown_pct)}%")


def _language_from_settings() -> str:
    """Idioma guardado, leído directamente del JSON: el splash arranca antes que
    AppSettings y no debe instanciar nada de la app."""
    try:
        with open(os.path.join(BASE_DIR, "config", "settings.json"), encoding="utf-8") as f:
            lang = json.load(f).get("language", "es")
        return lang if lang in ("es", "en") else "es"
    except Exception:
        return "es"


def main():
    # El estado se reinicia aquí: si un arranque anterior murió dejando READY,
    # el splash nuevo se cerraría al instante.
    try:
        with open(STATUS_PATH, "w", encoding="utf-8") as f:
            f.write("0|boot.starting")
    except OSError:
        pass

    app = QApplication(sys.argv)
    i18n.load(os.path.join(BASE_DIR, "config"))
    i18n.set_language(_language_from_settings())

    win = SplashWindow()
    win.showFullScreen()

    # Red de seguridad: si bmo_app muere antes de escribir READY, el splash no
    # puede quedarse encima de la pantalla para siempre.
    QTimer.singleShot(120_000, win._close_now)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
