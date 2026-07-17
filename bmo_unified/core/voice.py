# core/voice.py
"""
Voz de Moodi: narración TTS on-device de las frases de Oraciones (palabra
apilada y frase corregida por el LLM).

Motor elegido: speech-dispatcher (spd-say) con backend espeak-ng -- YA
instalado en esta Jetson (paquetes libespeak-ng1 + espeak-ng-data +
speech-dispatcher-espeak-ng), voces es/en incluidas. Justificación frente a
las alternativas evaluadas (detalle en PROGRESO_BMO_UI_CONFIG_VOZ.md):

- espeak-ng vía speech-dispatcher: ~0 RAM residente adicional (el daemon de
  speech-dispatcher pesa ~10-15 MB y la síntesis es por proceso efímero),
  latencia <150 ms, es/en nativos, cero dependencias nuevas. Voz robótica,
  pero coherente con el personaje robot. <- ELEGIDO
- pico2wave (libttspico): mejor prosodia, ~30 MB, pero NO está instalado y
  requeriría apt+internet.
- Piper TTS (neural): la mejor calidad, pero ~60-100 MB de RAM por voz
  cargada + descarga de modelos + pip nuevo en una RAM ya comprometida por
  llama-server (~5 GB de 8 GB) -- riesgo directo contra la estabilidad de la
  Fase 0. Queda documentado como upgrade futuro, no para esta fase.

Restricciones de diseño:
- NUNCA bloquear el hilo de UI: un único hilo trabajador consume una cola de
  frases y ejecuta spd-say -w (bloqueante solo dentro del trabajador, lo que
  además serializa las locuciones en orden).
- Sin procesos residuales: el trabajador es daemon y shutdown() cancela lo
  que esté sonando (spd-say -C) y drena la cola.
- Volumen e idioma se leen de AppSettings EN EL MOMENTO de hablar (no al
  encolar), así el slider de Configuraciones aplica también a la voz.
  Volumen 0 = voz silenciada (no se encola nada).
"""

import logging
import queue
import shutil
import subprocess
import threading

log = logging.getLogger("bmo.voice")

SPD_BIN = "spd-say"
# Velocidad levemente más lenta que el default de espeak-ng (rate -100..100):
# más inteligible para el usuario objetivo sin sonar arrastrado.
SPD_RATE = "-15"
MAX_QUEUE = 8  # frases pendientes; más allá se descartan las nuevas (no acumular retraso)

_SENTINEL = object()


class VoiceEngine:
    """speak() encola y retorna al instante; un hilo trabajador narra en orden."""

    def __init__(self, settings):
        self._settings = settings  # AppSettings: .volume (0-100) y .language ("es"/"en")
        self._available = shutil.which(SPD_BIN) is not None
        if not self._available:
            log.warning("%s no está disponible: voz de Moodi deshabilitada", SPD_BIN)
        self._queue = queue.Queue(maxsize=MAX_QUEUE)
        self._worker = threading.Thread(target=self._run, name="VoiceWorker", daemon=True)
        self._worker.start()

    def is_available(self) -> bool:
        return self._available

    # ---------- API pública (hilo de UI) ----------
    def speak(self, text: str, interrupt: bool = False):
        """Narra 'text' en segundo plano. interrupt=True cancela lo que esté
        sonando y lo pendiente (para la frase corregida del LLM, que no debe
        esperar detrás de palabras sueltas ya obsoletas)."""
        text = (text or "").strip()
        if not text or not self._available:
            return
        if self._settings.volume <= 0:
            return  # voz silenciada desde Configuraciones
        if interrupt:
            self._drain()
            self._cancel_current()
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            log.warning("Cola de voz llena; se descarta: %r", text)

    def shutdown(self):
        """Cancela lo que suene y detiene el trabajador (cierre limpio 7.2)."""
        self._drain()
        self._cancel_current()
        try:
            self._queue.put_nowait(_SENTINEL)
        except queue.Full:
            pass

    # ---------- interno ----------
    def _drain(self):
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def _cancel_current(self):
        if not self._available:
            return
        try:
            subprocess.run([SPD_BIN, "-C"], timeout=3, capture_output=True)
        except Exception:
            log.exception("No se pudo cancelar la locución en curso")

    def _run(self):
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                return
            volume = int(self._settings.volume)
            if volume <= 0:
                continue
            lang = self._settings.language
            # spd-say -i acepta -100..100, pero espeak-ng con amplitud alta
            # satura/clipea por sí solo (voz áspera confirmada en el altavoz
            # del panel): se mapea 0..100 -> -100..+50 para dejar margen; el
            # volumen "fuerte" real lo pone el sink maestro de PulseAudio.
            spd_volume = str(int(volume * 1.5) - 100)
            try:
                subprocess.run(
                    [SPD_BIN, "-l", lang, "-i", spd_volume, "-r", SPD_RATE, "-w", "--", item],
                    timeout=30, capture_output=True,
                )
            except FileNotFoundError:
                self._available = False
                log.warning("%s desapareció del sistema: voz deshabilitada", SPD_BIN)
                return
            except subprocess.TimeoutExpired:
                log.warning("Locución excedió el timeout, cancelando: %r", item)
                self._cancel_current()
            except Exception:
                log.exception("Error narrando %r", item)
