# core/background_music.py
"""
Música de fondo (S13): un loop instrumental corto que suena EN VEZ del audio
de Moodi (voz/clips) mientras ese audio está silenciado -- ver
MainWindow._apply_audio_state(). Pistas en bmo_unified/assets/musica_fondo/.

Mismo patrón ya validado en ui/animation_player.py para no repetir el
congelamiento de GStreamer descubierto en S12: NUNCA se llama
setVolume()/setMuted() sobre un QMediaPlayer que está reproduciendo -- un
cambio de volumen o de pista siempre se aplica recargando el medio
(setMedia + setVolume + play) desde cero. Como esta es una pista de SOLO
audio (sin QVideoWidget), el riesgo específico documentado en
animation_player.py (perder el overlay de video nativo) no aplica, pero se
mantiene la misma disciplina por las dudas -- es la única combinación
verificada como estable en esta Jetson.
"""

import glob
import logging
import os

from PyQt5.QtCore import QObject, QUrl
from PyQt5.QtMultimedia import QAudio, QMediaContent, QMediaPlayer

log = logging.getLogger("bmo.background_music")

EXTS = (".wav", ".mp3", ".ogg", ".m4a")


def list_tracks(music_dir: str):
    """Nombres de archivo (sin ruta) de las pistas disponibles, ordenados."""
    files = []
    for ext in EXTS:
        files += glob.glob(os.path.join(music_dir, f"*{ext}"))
    return sorted(os.path.basename(f) for f in files)


class BackgroundMusicPlayer(QObject):
    def __init__(self, music_dir: str, parent=None):
        super().__init__(parent)
        self._music_dir = music_dir
        self._track = ""       # nombre de archivo, "" = ninguna
        self._volume = 55
        self._active = False   # ¿debería estar sonando ahora mismo?

        self._player = QMediaPlayer(self)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)

    def available_tracks(self):
        return list_tracks(self._music_dir)

    def set_track(self, filename: str):
        filename = filename or ""
        if filename == self._track:
            return
        self._track = filename
        if self._active:
            self._restart()

    def set_volume(self, volume: int):
        volume = max(0, min(100, int(volume)))
        if volume == self._volume:
            return
        self._volume = volume
        if self._active and self._track:
            self._restart()

    def set_active(self, active: bool):
        """Debe sonar (True) o no (False) -- lo decide MainWindow según el
        estado de silencio del audio de Moodi, no una elección propia."""
        if active == self._active:
            return
        self._active = active
        if active:
            self._restart()
        else:
            self._player.stop()

    # ---------- internos ----------
    def _restart(self):
        if not self._track:
            self._player.stop()
            return
        path = os.path.join(self._music_dir, self._track)
        if not os.path.isfile(path):
            log.warning("Pista de música de fondo no encontrada: %s", path)
            return
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(path)))
        self._player.setVolume(max(1, self._volumen_lineal()))
        self._player.play()

    def _volumen_lineal(self) -> int:
        """Misma conversión perceptual->lineal que AnimationPlayer (S12): el
        volumen de QMediaPlayer es amplitud lineal, no sonoridad percibida."""
        lineal = QAudio.convertVolume(
            self._volume / 100.0, QAudio.LogarithmicVolumeScale, QAudio.LinearVolumeScale)
        return int(round(lineal * 100))

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self._player.setPosition(0)
            self._player.play()
