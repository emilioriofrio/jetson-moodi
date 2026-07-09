# ui/animation_player.py
"""
Fondo animado permanente (4.2): un clip en bucle nativo mientras la cara
ocupa la pantalla. Cambiar de clip reutiliza el mismo QVideoWidget (sin abrir
ventanas nuevas ni dejar frames negros entre clips).
"""

import glob
import logging
import os

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtWidgets import QVBoxLayout, QWidget

log = logging.getLogger("bmo.animation_player")

EXTS = (".mp4", ".mkv", ".mov", ".avi")


class AnimationPlayer(QWidget):
    def __init__(self, anim_dir: str, parent=None):
        super().__init__(parent)
        self._clips = self._scan_clips(anim_dir)
        self._idx = 0

        self._video_widget = QVideoWidget(self)
        self._video_widget.setAttribute(Qt.WA_TransparentForMouseEvents)
        # "Cubrir" en vez de "ajustar": llena la pantalla recortando el sobrante,
        # nunca deja barras negras (4.1/4.2: pantalla completa, sin negros).
        self._video_widget.setAspectRatioMode(Qt.KeepAspectRatioByExpanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._video_widget)

        self._player = QMediaPlayer(self)
        self._player.setVideoOutput(self._video_widget)
        # Loop manual (en vez de QMediaPlaylist.CurrentItemInLoop): reposicionar
        # a 0 y reanudar sin recargar el pipeline evita el corte/parpadeo visible
        # que el backend GStreamer produce al recargar el elemento de la playlist
        # en cada vuelta.
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)

        if self._clips:
            self._load_clip(0)
        else:
            log.warning("No se encontraron animaciones en %s", anim_dir)

    @staticmethod
    def _scan_clips(anim_dir: str):
        files = []
        for ext in EXTS:
            files += glob.glob(os.path.join(anim_dir, f"*{ext}"))
        return sorted(f for f in files if os.path.isfile(f))

    def _load_clip(self, idx: int):
        if not self._clips:
            return
        self._idx = idx % len(self._clips)
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(self._clips[self._idx])))
        self._player.play()

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self._player.setPosition(0)
            self._player.play()

    def next_clip(self):
        self._load_clip(self._idx + 1)

    def prev_clip(self):
        self._load_clip(self._idx - 1)

    def play_dynamic(self):
        """DYNAMIC_PLAY: reproduce la 'dinámica' actual desde el inicio (animación + audio)."""
        self._player.setPosition(0)
        self._player.play()
