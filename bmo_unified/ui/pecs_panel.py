# ui/pecs_panel.py
"""Pantalla Oraciones (5.6): fondo plano de pantalla completa (sin video),
saludo variable, instrucción fija, palabras apiladas como chips con selección
por cursor, frase corregida y estado de envío. La visibilidad la decide
MainWindow (navegación de vistas); este widget solo refleja el estado de
PecsEngine y el saludo elegido al entrar a la vista."""

import random

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

_CHIP_STYLE = "background: #3a7bd5; color: white; padding: 8px 14px; border-radius: 10px; font-size: 18px;"
_CHIP_SELECTED_STYLE = (
    "background: #3a7bd5; color: white; padding: 8px 14px; border-radius: 10px; "
    "font-size: 18px; border: 3px solid #FF9500;"
)

# 5.6: texto de instrucción literal.
INSTRUCTION_TEXT = (
    "Forma oraciones pasando las tarjetas por mi oreja derecha. "
    "Presiona el botón a mi derecha para Enviar el mensaje."
)


class PecsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Fondo plano opaco (5.3/5.6: "sin video" en la vista Oraciones).
        self.setStyleSheet("background: #1a1a26; color: white;")

        self._selected_index = -1
        self._chip_widgets = []

        self._lbl_greeting = QLabel("")
        self._lbl_greeting.setAlignment(Qt.AlignCenter)
        self._lbl_greeting.setWordWrap(True)
        self._lbl_greeting.setStyleSheet("font-size: 30px; font-weight: bold;")

        self._lbl_instruction = QLabel(INSTRUCTION_TEXT)
        self._lbl_instruction.setAlignment(Qt.AlignCenter)
        self._lbl_instruction.setWordWrap(True)
        self._lbl_instruction.setStyleSheet("font-size: 14px; color: #cfcfcf;")

        self._chips_layout = QHBoxLayout()
        self._chips_layout.setSpacing(10)
        self._chips_layout.setAlignment(Qt.AlignCenter)
        chips_wrap = QWidget()
        chips_wrap.setLayout(self._chips_layout)

        self._lbl_corrected = QLabel("")
        self._lbl_corrected.setAlignment(Qt.AlignCenter)
        self._lbl_corrected.setWordWrap(True)
        self._lbl_corrected.setStyleSheet("font-size: 18px; color: #9be39b;")

        self._lbl_status = QLabel("")
        self._lbl_status.setAlignment(Qt.AlignCenter)
        self._lbl_status.setStyleSheet("font-size: 14px; color: #ffd54f;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.addStretch(1)
        layout.addWidget(self._lbl_greeting)
        layout.addWidget(self._lbl_instruction)
        layout.addStretch(1)
        layout.addWidget(chips_wrap)
        layout.addWidget(self._lbl_corrected)
        layout.addWidget(self._lbl_status)
        layout.addStretch(1)

    # ---------- saludo variable (5.6) ----------
    def new_greeting(self, greetings: list):
        """Llamar cada vez que MainWindow entra a la vista ORACIONES."""
        self._lbl_greeting.setText(random.choice(greetings) if greetings else "")
        self._lbl_corrected.setText("")
        self._lbl_status.setText("")

    # ---------- apilado / selección (5.6: cursores mueven selección) ----------
    def on_stack_changed(self, words):
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._chip_widgets = []

        for word in words:
            chip = QLabel(word)
            chip.setStyleSheet(_CHIP_STYLE)
            self._chips_layout.addWidget(chip)
            self._chip_widgets.append(chip)

        if not self._chip_widgets:
            self._selected_index = -1
        else:
            self._selected_index = min(max(self._selected_index, 0), len(self._chip_widgets) - 1)
        self._refresh_selection()

        if words:
            self._lbl_status.setText("")
            self._lbl_corrected.setText("")

    def move_selection(self, delta: int):
        if not self._chip_widgets:
            return
        self._selected_index = (self._selected_index + delta) % len(self._chip_widgets)
        self._refresh_selection()

    def selected_index(self) -> int:
        return self._selected_index

    def _refresh_selection(self):
        for i, chip in enumerate(self._chip_widgets):
            chip.setStyleSheet(_CHIP_SELECTED_STYLE if i == self._selected_index else _CHIP_STYLE)

    def on_card_rejected(self, uid):
        self._lbl_status.setText("Tarjeta no reconocida")

    def on_send_started(self):
        self._lbl_status.setText("Procesando…")

    def on_sentence_sent(self, raw, corrected):
        self._lbl_status.setText("Enviado ✓")
        self._lbl_corrected.setText(corrected)

    def on_send_failed(self, err):
        self._lbl_status.setText(f"Error: {err}")
