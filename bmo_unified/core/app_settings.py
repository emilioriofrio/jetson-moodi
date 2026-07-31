# core/app_settings.py
"""
Preferencias persistentes de la app (config/settings.json): volumen, idioma,
apodo del usuario y tamaño de fuente. Única fuente de verdad de estas
preferencias -- separada a propósito de config/button_map.json (que sigue
siendo la fuente de verdad del mapeo físico de botones).

Escritura SIEMPRE atómica (archivo temporal + os.replace en el mismo
directorio): si se corta la energía a mitad de escritura, el archivo previo
queda intacto -- nunca un JSON truncado que impida el siguiente arranque.

Los cambios se aplican en caliente: cada setter emite su señal Qt y MainWindow
propaga (retraducir textos, reescalar fuentes, volumen de audio/voz) sin
reiniciar la app.
"""

import json
import logging
import os
import tempfile

from PyQt5.QtCore import QObject, pyqtSignal

log = logging.getLogger("bmo.app_settings")

VALID_LANGUAGES = ("es", "en")
VALID_FONT_SCALES = ("small", "normal", "large", "xlarge")

DEFAULTS = {
    "volume": 80,          # 0-100, audio de animaciones + voz de Moodi
    "language": "es",      # "es" | "en"
    "nickname": "",        # apodo del usuario ("" = sin registrar)
    "font_scale": "normal",  # small | normal | large | xlarge (ver ui/theme.py)
    # Voz TTS de Moodi encendida/apagada (S11). Separado del volumen a
    # propósito: apagar la voz NO debe silenciar el audio de las animaciones,
    # y volver a encenderla no debe obligar a recordar qué volumen había.
    "voice_enabled": True,
}

MAX_NICKNAME_LEN = 16


def atomic_write_json(path: str, data: dict):
    """Escribe 'data' como JSON en 'path' de forma atómica: tmp + os.replace.
    El tmp vive en el MISMO directorio para garantizar que el rename sea
    atómico (mismo filesystem)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_settings_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class AppSettings(QObject):
    volume_changed = pyqtSignal(int)
    language_changed = pyqtSignal(str)
    nickname_changed = pyqtSignal(str)
    font_scale_changed = pyqtSignal(str)
    voice_enabled_changed = pyqtSignal(bool)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self._data = dict(DEFAULTS)
        self._load()

    # ---------- carga / persistencia ----------
    def _load(self):
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            log.info("Sin settings.json previo, usando defaults (%s)", self._path)
            return
        except Exception:
            log.exception("settings.json ilegible (%s); usando defaults", self._path)
            return
        for key in DEFAULTS:
            if key in raw:
                self._data[key] = raw[key]
        # saneo defensivo: nunca dejar valores fuera de rango en memoria
        self._data["volume"] = self._clamp_volume(self._data.get("volume"))
        if self._data.get("language") not in VALID_LANGUAGES:
            self._data["language"] = DEFAULTS["language"]
        if self._data.get("font_scale") not in VALID_FONT_SCALES:
            self._data["font_scale"] = DEFAULTS["font_scale"]
        self._data["nickname"] = str(self._data.get("nickname", ""))[:MAX_NICKNAME_LEN]
        self._data["voice_enabled"] = bool(self._data.get("voice_enabled", True))

    def _persist(self):
        try:
            atomic_write_json(self._path, self._data)
        except Exception:
            log.exception("No se pudo persistir settings.json en %s", self._path)

    @staticmethod
    def _clamp_volume(value) -> int:
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return DEFAULTS["volume"]

    # ---------- getters ----------
    @property
    def volume(self) -> int:
        return self._data["volume"]

    @property
    def language(self) -> str:
        return self._data["language"]

    @property
    def nickname(self) -> str:
        return self._data["nickname"]

    @property
    def font_scale(self) -> str:
        return self._data["font_scale"]

    @property
    def voice_enabled(self) -> bool:
        return self._data["voice_enabled"]

    # ---------- setters (persisten + emiten en caliente) ----------
    def set_volume(self, value: int):
        value = self._clamp_volume(value)
        if value == self._data["volume"]:
            return
        self._data["volume"] = value
        self._persist()
        self.volume_changed.emit(value)

    def set_language(self, lang: str):
        if lang not in VALID_LANGUAGES or lang == self._data["language"]:
            return
        self._data["language"] = lang
        self._persist()
        self.language_changed.emit(lang)

    def set_nickname(self, nickname: str):
        nickname = str(nickname).strip()[:MAX_NICKNAME_LEN]
        if nickname == self._data["nickname"]:
            return
        self._data["nickname"] = nickname
        self._persist()
        self.nickname_changed.emit(nickname)

    def set_voice_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if enabled == self._data["voice_enabled"]:
            return
        self._data["voice_enabled"] = enabled
        self._persist()
        self.voice_enabled_changed.emit(enabled)

    def set_font_scale(self, scale: str):
        if scale not in VALID_FONT_SCALES or scale == self._data["font_scale"]:
            return
        self._data["font_scale"] = scale
        self._persist()
        self.font_scale_changed.emit(scale)
