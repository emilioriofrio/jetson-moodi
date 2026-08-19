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

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                             QWidget)

from core.i18n import t
from ui.theme import ACCENT_ORANGE, BG_TEAL, CREAM, TEXT_NAVY, TEXT_NAVY_SOFT, fs

# Interruptor de voz (S11): target táctil ≥64px como el resto de la fase S10.
VOICE_BTN_SIZE = 68


def _chip_style(selected: bool) -> str:
    base = (
        f"background: {TEXT_NAVY}; color: {CREAM}; padding: 9px 16px; border-radius: 14px; "
        f"font-size: {fs(22)}px; font-weight: 600;"
    )
    return (base + f" border: 3px solid {ACCENT_ORANGE};") if selected else base


class _VoiceButton(QPushButton):
    """Botón de altavoz DIBUJADO con QPainter.

    No usa glifo de fuente a propósito: se comprobó en esta Jetson que ni
    U+1F56A ni U+1F568 (altavoz) existen en ninguna fuente instalada -- salían
    como cuadro vacío (tofu). Es exactamente el mismo problema que en S10 con
    ⚙️ y 🔒, y la solución ya validada entonces es dibujar el icono."""

    def __init__(self, on: bool = True, parent=None):
        super().__init__(parent)
        self._on = on
        self._fg = QColor(CREAM)

    def set_state(self, on: bool, fg: str):
        self._on = bool(on)
        self._fg = QColor(fg)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)   # fondo/borde los sigue poniendo el stylesheet
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        u = min(w, h) / 64.0        # escala relativa al tamaño del botón

        # cuerpo del altavoz (rectángulo + cono), centrado y desplazado a la
        # izquierda para dejar sitio a las ondas
        p.setPen(Qt.NoPen)
        p.setBrush(self._fg)
        bx = cx - 13 * u
        p.drawRect(QRectF(bx, cy - 5 * u, 7 * u, 10 * u))
        cone = QPolygonF([
            QPointF(bx + 7 * u, cy - 5 * u), QPointF(bx + 16 * u, cy - 13 * u),
            QPointF(bx + 16 * u, cy + 13 * u), QPointF(bx + 7 * u, cy + 5 * u),
        ])
        p.drawPolygon(cone)

        p.setBrush(Qt.NoBrush)
        if self._on:
            # dos ondas de sonido
            p.setPen(QPen(self._fg, 2.6 * u, Qt.SolidLine, Qt.RoundCap))
            for r in (8 * u, 14 * u):
                arc = QRectF(cx + 4 * u - r, cy - r, r * 2, r * 2)
                p.drawArc(arc, -55 * 16, 110 * 16)
        else:
            # tachado diagonal = silenciado
            p.setPen(QPen(self._fg, 3.2 * u, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx + 5 * u, cy - 9 * u), QPointF(cx + 17 * u, cy + 9 * u))
            p.drawLine(QPointF(cx + 17 * u, cy - 9 * u), QPointF(cx + 5 * u, cy + 9 * u))
        p.end()


class PecsPanel(QWidget):
    # Interruptor de voz de Moodi (S11). El panel no toca AppSettings
    # directamente: emite y MainWindow persiste, igual que el resto de
    # preferencias en caliente.
    voice_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._selected_index = -1
        self._chip_widgets = []
        self._voice_on = True

        # Interruptor de voz, arriba a la derecha: la voz TTS aún es tosca
        # (espeak-ng) y debe poder callarse sin entrar a Configuraciones ni
        # bajar el volumen general de las animaciones.
        self._btn_voice = _VoiceButton(True, self)
        self._btn_voice.setFixedSize(VOICE_BTN_SIZE, VOICE_BTN_SIZE)
        self._btn_voice.setCursor(Qt.PointingHandCursor)
        self._btn_voice.clicked.connect(self._on_voice_clicked)

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

    # ---------- interruptor de voz (S11) ----------
    def set_voice_enabled(self, enabled: bool):
        """Refleja el estado real de la preferencia (la fuente de verdad es
        AppSettings; MainWindow llama aquí al entrar a la pantalla y cuando la
        preferencia cambia)."""
        self._voice_on = bool(enabled)
        self._refresh_voice_button()

    def _on_voice_clicked(self):
        self._voice_on = not self._voice_on
        self._refresh_voice_button()
        self.voice_toggled.emit(self._voice_on)

    def _refresh_voice_button(self):
        on = self._voice_on
        self._btn_voice.setToolTip(t("pecs.voice_on" if on else "pecs.voice_off"))
        self._btn_voice.setAccessibleName(t("pecs.voice_on" if on else "pecs.voice_off"))
        bg = TEXT_NAVY if on else "rgba(32,50,63,60)"
        fg = CREAM if on else TEXT_NAVY
        border = TEXT_NAVY if on else ACCENT_ORANGE
        self._btn_voice.setStyleSheet(
            f"QPushButton {{ background: {bg}; "
            f"border-radius: {VOICE_BTN_SIZE // 2}px; border: 3px solid {border}; }}"
            f"QPushButton:pressed {{ background: {ACCENT_ORANGE}; }}"
        )
        self._btn_voice.set_state(on, fg)

    def _place_voice_button(self):
        # Arriba a la IZQUIERDA (S13, 8ª ronda; antes a la derecha): la
        # esquina derecha de esta pantalla la ocupan ahora los botones
        # táctiles de silenciar/video-sin-cortes (ui/main_window.py), que
        # solo se muestran en Oraciones -- quedaban "montados" uno sobre
        # otro con este botón si los dos vivían del mismo lado.
        self._btn_voice.move(24, 20)
        self._btn_voice.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_voice_button()

    # ---------- i18n / escala de fuente en caliente ----------
    def retranslate(self):
        self._lbl_instruction.setText(t("pecs.instruction"))
        # los estados transitorios se limpian: su texto pertenece al idioma anterior
        self._lbl_status.setText("")
        self._refresh_voice_button()

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
        self._refresh_voice_button()
        self._place_voice_button()

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
