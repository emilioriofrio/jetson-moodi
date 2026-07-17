# ui/calibration_overlay.py
"""Modo calibración (3.4): muestra en vivo las últimas tramas serie crudas
(GPIO/evento) para mapear los botones físicos sin adivinar."""

from collections import deque

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from core.i18n import t
from ui.theme import fs

MAX_LINES = 20


class CalibrationOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._lines = deque(maxlen=MAX_LINES)

        self._text = QLabel()
        self._text.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._text.setWordWrap(True)

        self._btn_close = QPushButton()

        layout = QVBoxLayout(self)
        layout.addWidget(self._text, stretch=1)
        layout.addWidget(self._btn_close)

        self.retranslate()
        self.restyle()

    # ---------- i18n / escala de fuente en caliente ----------
    def retranslate(self):
        if not self._lines:
            self._text.setText(t("calib.waiting"))
        self._btn_close.setText(t("calib.exit"))

    def restyle(self):
        self.setStyleSheet(
            f"background: rgba(0,0,0,235); color: #00ff66; font-family: monospace; "
            f"font-size: {fs(17)}px;")
        self._btn_close.setStyleSheet(
            f"min-height: 64px; font-size: {fs(17)}px; color: black; background: white;")

    @property
    def close_button(self) -> QPushButton:
        return self._btn_close

    def on_raw_line(self, line: str):
        self._lines.append(line)
        self._text.setText("\n".join(self._lines))
