# core/pecs_engine.py
"""
Motor PECS-RFID: apila palabras leídas por tarjeta, arma la frase bruta,
la envía a ia_bridge para corrección y dispara el aviso por Telegram.

Única fuente de verdad del estado de la frase (ver REQUERIMIENTOS_APP_MOODI.md
sección 5): el ESP32 ya no decide nada de esto, solo manda UIDs crudos.
"""

import logging
import threading

import requests
from PyQt5.QtCore import QObject, pyqtSignal

from core import telegram_sender
from core.i18n import t
from core.rfid_vocab import load_vocab, lookup_word

log = logging.getLogger("bmo.pecs_engine")

IA_BRIDGE_URL = "http://127.0.0.1:5000/ask"
SEND_TIMEOUT_S = (10, 60)  # (connect, read) -- el ciclo completo del LLM puede tardar ~20-30s


class PecsEngine(QObject):
    stack_changed = pyqtSignal(list)        # lista de palabras apiladas, en orden
    word_added = pyqtSignal(str)             # palabra recién apilada (voz de Moodi la narra)
    card_rejected = pyqtSignal(tuple)        # UID no registrado en el vocabulario
    send_started = pyqtSignal()              # arrancó el envío (mostrar "procesando…")
    sentence_sent = pyqtSignal(str, str)     # (frase_bruta, frase_corregida)
    send_failed = pyqtSignal(str)            # mensaje de error

    def __init__(self, vocab_path: str, telegram_config_path: str = "config/telegram.json", parent=None):
        super().__init__(parent)
        self._words = []
        self._vocab = load_vocab(vocab_path)
        self._telegram_config_path = telegram_config_path

    # ---------- apilado ----------
    def add_card(self, uid):
        word = lookup_word(self._vocab, uid)
        if word is None:
            log.info("Tarjeta no reconocida: %s", uid)
            self.card_rejected.emit(tuple(uid))
            return
        self._words.append(word)
        self.stack_changed.emit(list(self._words))
        self.word_added.emit(word)

    def delete_last(self):
        if self._words:
            self._words.pop()
            self.stack_changed.emit(list(self._words))

    def delete_at(self, index: int):
        """Borra la palabra en 'index' (selección por cursor en Oraciones), no necesariamente la última."""
        if 0 <= index < len(self._words):
            self._words.pop(index)
            self.stack_changed.emit(list(self._words))

    def clear(self):
        if self._words:
            self._words = []
            self.stack_changed.emit(list(self._words))

    def get_raw_sentence(self) -> str:
        return " ".join(self._words).strip()

    # ---------- envío ----------
    def send(self):
        sentence = self.get_raw_sentence()
        if not sentence:
            return
        self.send_started.emit()
        threading.Thread(target=self._do_send, args=(sentence,), daemon=True).start()

    def _do_send(self, sentence: str):
        try:
            resp = requests.post(IA_BRIDGE_URL, json={"prompt": sentence}, timeout=SEND_TIMEOUT_S)
            data = resp.json() if resp.content else {}
        except Exception as e:
            log.exception("Error consultando ia_bridge")
            self.send_failed.emit(f"{t('pecs.err_bridge')}: {e}")
            return

        if not resp.ok or "error" in data:
            msg = data.get("error", f"HTTP {resp.status_code}")
            log.error("ia_bridge devolvió error: %s", msg)
            self.send_failed.emit(str(msg))
            return

        corrected = str(data.get("response", "")).strip()
        if not corrected:
            self.send_failed.emit(t("pecs.err_empty"))
            return

        log.info("Frase corregida: %r -> %r", sentence, corrected)
        self.clear()
        self.sentence_sent.emit(sentence, corrected)
        telegram_sender.send_message_async(
            t("telegram.message", sentence=corrected),
            config_path=self._telegram_config_path,
        )
