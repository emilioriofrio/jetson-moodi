# ui/pecs_panel.py
"""Pantalla Oraciones (5.6): fondo plano de pantalla completa (sin video),
saludo variable, instrucción fija, palabras apiladas como chips con selección
por cursor, frase corregida y estado de envío. La visibilidad la decide
MainWindow (navegación de vistas); este widget solo refleja el estado de
PecsEngine y el saludo elegido al entrar a la vista (el saludo lo elige
MainWindow vía core/greetings.py: idioma + apodo + franja horaria).

Todos los textos pasan por core/i18n.t() y todos los tamaños de fuente por
ui/theme.fs() (idioma y tamaño cambiables en caliente desde Configuraciones:
MainWindow llama retranslate()/restyle())."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from core.i18n import t
from ui.theme import ACCENT_ORANGE, BG_TEAL, CREAM, TEXT_NAVY, TEXT_NAVY_SOFT, fs


def _chip_style(selected: bool) -> str:
    base = (
        f"background: {TEXT_NAVY}; color: {CREAM}; padding: 9px 16px; border-radius: 14px; "
        f"font-size: {fs(22)}px; font-weight: 600;"
    )
    return (base + f" border: 3px solid {ACCENT_ORANGE};") if selected else base


class PecsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._selected_index = -1
        self._chip_widgets = []

        self._lbl_greeting = QLabel("")
        self._lbl_greeting.setAlignment(Qt.AlignCenter)
        self._lbl_greeting.setWordWrap(True)

        self._lbl_instruction = QLabel("")
        self._lbl_instruction.setAlignment(Qt.AlignCenter)
        self._lbl_instruction.setWordWrap(True)

        self._chips_layout = QHBoxLayout()
        self._chips_layout.setSpacing(10)
        self._chips_layout.setAlignment(Qt.AlignCenter)
        chips_wrap = QWidget()
        chips_wrap.setLayout(self._chips_layout)

        self._lbl_corrected = QLabel("")
        self._lbl_corrected.setAlignment(Qt.AlignCenter)
        self._lbl_corrected.setWordWrap(True)

        self._lbl_status = QLabel("")
        self._lbl_status.setAlignment(Qt.AlignCenter)

        # Espacio reservado para la carita de Moodi (ORACIONES_FACE_RECT en
        # main_window.py), que flota encima como widget aparte -- este spacer
        # solo evita que el saludo se dibuje debajo de ella.
        face_spacer = QWidget()
        face_spacer.setFixedHeight(224)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.addWidget(face_spacer)
        layout.addWidget(self._lbl_greeting)
        layout.addWidget(self._lbl_instruction)
        layout.addStretch(1)
        layout.addWidget(chips_wrap)
        layout.addWidget(self._lbl_corrected)
        layout.addWidget(self._lbl_status)
        layout.addStretch(1)

        self.retranslate()
        self.restyle()

    # ---------- i18n / escala de fuente en caliente ----------
    def retranslate(self):
        self._lbl_instruction.setText(t("pecs.instruction"))
        # los estados transitorios se limpian: su texto pertenece al idioma anterior
        self._lbl_status.setText("")

    def restyle(self):
        # Paleta muestreada directamente de los clips de Moodi (ver ui/theme.py)
        # para que Oraciones se sienta parte de la misma cara.
        self.setStyleSheet(f"background: {BG_TEAL}; color: {TEXT_NAVY};")
        self._lbl_greeting.setStyleSheet(
            f"font-size: {fs(34)}px; font-weight: 700; color: {TEXT_NAVY}; background: transparent;")
        self._lbl_instruction.setStyleSheet(
            f"font-size: {fs(17)}px; color: {TEXT_NAVY_SOFT}; background: transparent;")
        self._lbl_corrected.setStyleSheet(
            f"font-size: {fs(21)}px; font-weight: 600; color: #1E6E52; background: transparent;")
        self._lbl_status.setStyleSheet(
            f"font-size: {fs(17)}px; font-weight: 600; color: #A85A22; background: transparent;")
        self._refresh_selection()

    # ---------- saludo variable (5.6) ----------
    def new_greeting(self, greeting: str):
        """Llamar cada vez que MainWindow entra a la vista ORACIONES."""
        self._lbl_greeting.setText(greeting or "")
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
            chip.setStyleSheet(_chip_style(False))
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
            chip.setStyleSheet(_chip_style(i == self._selected_index))

    def on_card_rejected(self, uid):
        self._lbl_status.setText(t("pecs.card_rejected"))

    def on_send_started(self):
        self._lbl_status.setText(t("pecs.processing"))

    def on_sentence_sent(self, raw, corrected):
        self._lbl_status.setText(t("pecs.sent"))
        self._lbl_corrected.setText(corrected)

    def on_send_failed(self, err):
        self._lbl_status.setText(t("pecs.error", err=err))
