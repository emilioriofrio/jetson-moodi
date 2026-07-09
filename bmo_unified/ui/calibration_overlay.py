# ui/calibration_overlay.py
"""Modo calibración (3.4): muestra en vivo las últimas tramas serie crudas
(GPIO/evento) para mapear los botones físicos sin adivinar."""

from collections import deque

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

MAX_LINES = 20


class CalibrationOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "background: rgba(0,0,0,235); color: #00ff66; font-family: monospace; font-size: 16px;"
        )

        self._lines = deque(maxlen=MAX_LINES)

        self._text = QLabel("Modo calibración: toca un botón en el robot…")
        self._text.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._text.setWordWrap(True)

        self._btn_close = QPushButton("Salir de calibración")
        self._btn_close.setStyleSheet("min-height: 56px; font-size: 16px; color: black; background: white;")

        layout = QVBoxLayout(self)
        layout.addWidget(self._text, stretch=1)
        layout.addWidget(self._btn_close)

    @property
    def close_button(self) -> QPushButton:
        return self._btn_close

    def on_raw_line(self, line: str):
        self._lines.append(line)
        self._text.setText("\n".join(self._lines))
