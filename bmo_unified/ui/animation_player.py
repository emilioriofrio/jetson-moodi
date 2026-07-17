# ui/animation_player.py
"""
Fondo animado permanente (4.2): un clip en bucle nativo mientras la cara
ocupa la pantalla. Cambiar de clip reutiliza el mismo QVideoWidget (sin abrir
ventanas nuevas ni dejar frames negros entre clips).

Un único QMediaPlayer/QVideoWidget a propósito: se probó una variante con
doble reproductor (para precargar el siguiente clip y evitar el parpadeo al
cambiar) pero en esta Jetson dejaba el video congelado y "pegado" sobre el
resto de la app (Home, Caras, Video) -- la superficie de overlay de video de
GStreamer/X11 no tolera bien dos QVideoWidget nativos compitiendo por el
mismo plano de hardware. Un solo reproductor es más robusto aunque el cambio
de clip tenga un corte breve.

Loop sin corte visible: en vez de esperar a EndOfMedia real y recién ahí
reposicionar+reproducir (lo que primero atraviesa el manejo interno de fin de
stream de GStreamer -- flush, posible frame negro/congelado momentáneo -- y
se nota como un "salto" duro, muy visible incluso en el recuadro chico de
Oraciones), se sondea la posición de reproducción y se reinicia a 0 un poco
ANTES del final real, mientras el pipeline sigue en PlayingState. Saltar al
frame 0 (que siempre es keyframe) desde plena reproducción es una operación
mucho más liviana que la transición de fin de stream, y en la práctica no se
percibe corte. EndOfMedia se conserva solo como red de seguridad (por si la
duración aún no se conoce cuando se carga un clip nuevo).

Silencio (Home/Oraciones/Video) vs. audio (Caras): NUNCA se logra llamando
QMediaPlayer.setMuted(True)/setVolume(0) sobre un clip que sí tiene pista de
audio -- se confirmó con un diagnóstico aislado (9+ pasos de descarte; ver
memoria de proyecto) que silenciar así, en esta Jetson, deja el pipeline de
GStreamer congelado justo en el primer reinicio de loop (EndOfMedia ->
setPosition(0)+play()): el video se queda pegado en el último frame para
siempre, aunque el reproductor siga reportando PlayingState. Un clip SIN
pista de audio (stream de video puro) hace el mismo loop sin problema. Por
eso "silenciar" aquí realmente significa "cargar una variante del mismo clip
sin pista de audio" (generada una vez con ffmpeg -an -c:v copy y cacheada en
disco), no controlar el volumen del reproductor en caliente.
"""

import glob
import logging
import os
import subprocess

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtWidgets import QWidget

log = logging.getLogger("bmo.animation_player")

EXTS = (".mp4", ".mkv", ".mov", ".avi")

# Ver docstring del módulo (loop sin corte visible).
LOOP_LEAD_MS = 120  # margen antes del final real en el que se reinicia a 0
LOOP_POLL_MS = 40   # frecuencia de sondeo de posición (fino, para no pasarse del margen)

# Subcarpeta de caché para las variantes sin pista de audio (ver docstring del
# módulo) -- se generan una sola vez por clip, junto a los originales.
_MUTED_SUBDIR = "_sin_audio"


def _clip_title(path: str) -> str:
    """Deriva un título legible del nombre de archivo, p. ej.
    'Animación Audio 12 - Sonreir.mp4' -> 'Sonreir'."""
    name = os.path.splitext(os.path.basename(path))[0]
    return name.rsplit(" - ", 1)[-1].strip() if " - " in name else name.strip()


class AnimationPlayer(QWidget):
    clip_changed = pyqtSignal(str)  # título legible del clip recién cargado (leyenda temporal)

    def __init__(self, anim_dir: str, parent=None):
        super().__init__(parent)
        self._clips = self._scan_clips(anim_dir)
        self._idx = 0
        self._muted = True  # Silencioso por defecto (Home)
        # Volumen configurado (Configuraciones 3.1). NUNCA se aplica como
        # setVolume(0)/setMuted(True) sobre un clip con audio (congela el loop
        # en esta Jetson, ver docstring): volumen 0 == cargar la variante sin
        # pista de audio, igual que el silencio por pantalla.
        self._volume = 80

        self._video_widget = QVideoWidget(self)
        self._video_widget.setAttribute(Qt.WA_TransparentForMouseEvents)
        # "Cubrir" en vez de "ajustar": llena la pantalla recortando el sobrante,
        # nunca deja barras negras (4.1/4.2: pantalla completa, sin negros).
        self._video_widget.setAspectRatioMode(Qt.KeepAspectRatioByExpanding)
        self._video_widget.setGeometry(0, 0, self.width(), self.height())

        # Deliberadamente SIN QVBoxLayout aquí: un QVideoWidget administrado
        # por un QLayout que vive en un widget posicionado con setGeometry()
        # manual dentro de otro widget (como este AnimationPlayer dentro de
        # "central" en MainWindow) resultó, en las pruebas, un factor de
        # riesgo adicional; sincronizar la geometría a mano en resizeEvent es
        # la combinación verificada como estable.
        self._player = QMediaPlayer(self)
        self._player.setVideoOutput(self._video_widget)
        # Loop manual (en vez de QMediaPlaylist.CurrentItemInLoop): reposicionar
        # a 0 y reanudar sin recargar el pipeline evita el corte/parpadeo visible
        # que el backend GStreamer produce al recargar el elemento de la playlist
        # en cada vuelta.
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)

        self._loop_timer = QTimer(self)
        self._loop_timer.setInterval(LOOP_POLL_MS)
        self._loop_timer.timeout.connect(self._check_seamless_loop)
        self._loop_timer.start()

        if self._clips:
            self._load_clip(0)
        else:
            log.warning("No se encontraron animaciones en %s", anim_dir)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._video_widget.setGeometry(0, 0, self.width(), self.height())

    @staticmethod
    def _scan_clips(anim_dir: str):
        files = []
        for ext in EXTS:
            files += glob.glob(os.path.join(anim_dir, f"*{ext}"))
        return sorted(f for f in files if os.path.isfile(f))

    def _playback_path(self, path: str) -> str:
        """Ruta a reproducir para 'path': el original si hay audio habilitado
        (Caras), o su variante sin pista de audio si está silenciado (ver
        docstring del módulo -- nunca se logra silencio con setMuted/setVolume
        sobre un clip CON audio, se congela el loop en esta Jetson)."""
        if not self._muted and self._volume > 0:
            return path
        return self._ensure_muted_variant(path)

    @staticmethod
    def _ensure_muted_variant(path: str) -> str:
        directory = os.path.dirname(path)
        cache_dir = os.path.join(directory, _MUTED_SUBDIR)
        out_path = os.path.join(cache_dir, os.path.basename(path))
        if os.path.exists(out_path):
            return out_path
        try:
            os.makedirs(cache_dir, exist_ok=True)
            subprocess.run(
                ["ffmpeg", "-y", "-i", path, "-an", "-c:v", "copy", out_path],
                check=True, capture_output=True,
            )
            log.info("Variante sin audio generada: %s", out_path)
            return out_path
        except Exception:
            log.exception(
                "No se pudo generar variante sin audio de %s; se reproducirá el "
                "original con setMuted() (riesgo de congelamiento de loop en esta Jetson)",
                path,
            )
            return path

    def _load_clip(self, idx: int):
        if not self._clips:
            return
        self._idx = idx % len(self._clips)
        path = self._clips[self._idx]
        self._set_media(path)
        self.clip_changed.emit(_clip_title(path))

    def _set_media(self, path: str):
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(self._playback_path(path))))
        self._player.setMuted(False)
        # Nunca 0: con volumen 0 _playback_path ya eligió la variante sin
        # audio, y setVolume(0) sobre un clip CON audio congela el loop.
        self._player.setVolume(max(1, self._volume))
        self._player.play()

    def _check_seamless_loop(self):
        """Reinicia a 0 justo antes del final real, sin esperar a EndOfMedia
        (ver docstring del módulo) -- el mecanismo normal de loop."""
        dur = self._player.duration()
        if dur <= 0:
            return
        if self._player.position() >= dur - LOOP_LEAD_MS:
            self._player.setPosition(0)

    def _on_media_status_changed(self, status):
        # Red de seguridad: solo debería dispararse si _check_seamless_loop no
        # llegó a tiempo (p. ej. duración aún desconocida al cargar un clip).
        if status == QMediaPlayer.EndOfMedia:
            self._player.setPosition(0)
            self._player.play()

    def set_muted(self, muted: bool):
        """Activa o desactiva el audio: recarga el clip actual con o sin su
        pista de audio (ver docstring del módulo). Deliberadamente NO emite
        clip_changed -- no es un cambio de cara real, solo un cambio de audio
        en el mismo clip, y MainWindow usa esa señal exclusivamente para la
        leyenda temporal de título en la pantalla Caras (5.5); emitirla aquí
        hacía aparecer esa leyenda en cualquier pantalla al entrar/salir de
        Caras (p. ej. Video), que es el bug reportado de "solapamiento"."""
        if muted == self._muted:
            return
        self._muted = muted
        if self._clips:
            self._set_media(self._clips[self._idx])

    def set_volume(self, volume: int):
        """Volumen configurado en Configuraciones (0-100). Con audio activo
        (Caras) recarga el clip para que el cambio aplique de inmediato y para
        cruzar de/hacia 0 cambiando de variante con/sin pista de audio -- no se
        toca el volumen del reproductor en vivo (riesgo de congelar el loop,
        ver docstring del módulo)."""
        volume = max(0, min(100, int(volume)))
        if volume == self._volume:
            return
        self._volume = volume
        if not self._muted and self._clips:
            self._set_media(self._clips[self._idx])

    def next_clip(self):
        self._load_clip(self._idx + 1)

    def prev_clip(self):
        self._load_clip(self._idx - 1)

    def play_dynamic(self):
        """DYNAMIC_PLAY: reproduce la 'dinámica' actual desde el inicio (animación + audio)."""
        self._player.setPosition(0)
        self._player.play()
