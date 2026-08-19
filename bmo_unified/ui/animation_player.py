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

S12 -- congelamiento del video, medido: el loop "sin corte" descrito abajo es
estable en los clips SIN pista de audio (28 min, 206 vueltas, cero incidentes)
pero atasca el pipeline en los clips CON audio (2 congelamientos en 5.5 min).
La forma es siempre la misma: al dar la vuelta, la posición se queda clavada en
0 y el reproductor sigue reportando PlayingState. Es la misma familia de
problemas que el silenciado por setMuted() (ver más abajo): este backend no
tolera que se toque el pipeline mientras hay una pista de audio viva. Por eso
los clips con audio dan la vuelta esperando al final real, y además hay un
vigilante (_check_watchdog) que recarga el clip si la posición deja de avanzar
-- exactamente lo que el usuario conseguía a mano cambiando de pantalla.

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

Silencio vs. audio: NUNCA se logra llamando QMediaPlayer.setMuted(True)/
setVolume(0) sobre un clip que sí tiene pista de audio -- se confirmó con un
diagnóstico aislado (9+ pasos de descarte; ver memoria de proyecto) que
silenciar así, en esta Jetson, deja el pipeline de GStreamer congelado justo
en el primer reinicio de loop (EndOfMedia -> setPosition(0)+play()): el video
se queda pegado en el último frame para siempre, aunque el reproductor siga
reportando PlayingState. Un clip SIN pista de audio (stream de video puro)
hace el mismo loop sin problema. Por eso "silenciar" aquí realmente significa
"cargar una variante del mismo clip sin pista de audio" (generada una vez con
ffmpeg -an -c:v copy y cacheada en disco), no controlar el volumen del
reproductor en caliente.

S13 -- se probó (y se revirtió) hacer que el sonido dejara de depender de la
pantalla activa. El problema: el loop "sin corte" de arriba está desactivado
a propósito para clips CON audio (es la causa exacta del congelamiento medido
en S12), así que en cualquier pantalla con audio activo el loop pasa a
depender de EndOfMedia real -- visible, con corte. Mientras el audio solo
sonaba en Caras eso ya pasaba ahí (visible pero acotado a un recuadro chico);
al activar sonido en TODAS las pantallas, el corte se volvió visible en el
fondo animado a pantalla completa de Home/Oraciones/Video también, y el
usuario lo notó y pidió priorizar el video sin cortes. Por eso `_muted` volvió
a arrancar en `True`: MainWindow solo lo pone en `False` cuando la pantalla
activa es Caras (`_show_view`/`_effective_audio_muted`), igual que antes de
S13. El interruptor físico AUDIO_TOGGLE se conserva como silenciar/activar
manual DENTRO de Caras (y como gatillo de la música de fondo en el resto de
pantallas, ver core/background_music.py), no como "sonido en todas partes".

S13 (ronda siguiente) -- con el audio ya de vuelta acotado a Caras, el usuario
notó que ESA pantalla en concreto tampoco quedaba seamless (el corte de
EndOfMedia de arriba) y pidió recuperarlo ahí, aceptando el riesgo de
congelamiento ya medido. Con los clips de Caras durando ~8.2-8.8s cada uno,
"un corte por vuelta" es un corte cada ~8s -- mucho más frecuente que la tasa
de congelamiento medida en S12 (~1 cada 165s). La cuenta sale a favor de
reactivar el salto anticipado también para audio (`SEAMLESS_LOOP_WITH_AUDIO`
más abajo, ahora `True`), apoyado en un vigilante más rápido
(`STALL_TIMEOUT_MS` 4000->1800ms) para que el ~5% de vueltas que sí se
congelan se noten lo menos posible. Sigue siendo el mismo riesgo de fondo
que en S12, solo que ahora el usuario lo aceptó explícitamente después de
que se le explicara -- si en el robot real la tasa de congelamiento es peor
de lo medido, `SEAMLESS_LOOP_WITH_AUDIO = False` es la única línea a tocar
para volver a la vuelta "segura" (con corte pero sin riesgo de congelar).
"""

import glob
import logging
import os
import subprocess
import time

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtMultimedia import QAudio, QMediaContent, QMediaPlayer
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtWidgets import QWidget

log = logging.getLogger("bmo.animation_player")

EXTS = (".mp4", ".mkv", ".mov", ".avi")

# Ver docstring del módulo (loop sin corte visible).
LOOP_LEAD_MS = 120  # margen antes del final real en el que se reinicia a 0
LOOP_POLL_MS = 40   # frecuencia de sondeo de posición (fino, para no pasarse del margen)

# Vigilante de congelamiento (S12) -- ver _check_watchdog().
WATCHDOG_POLL_MS = 1000      # cada cuánto se comprueba que el video siga vivo
# 4000 -> 1800 (S13): al reactivar el salto anticipado para clips CON audio
# (ver SEAMLESS_LOOP_WITH_AUDIO más abajo), el vigilante es la red de
# seguridad que hace tolerable ese riesgo -- cuanto antes detecte un
# congelamiento, menos se nota. 1800ms sigue siendo generoso frente al
# avance normal de posición (cada frame, ~33ms a 30fps), así que no debería
# generar falsos positivos.
STALL_TIMEOUT_MS = 1800
RELOAD_GRACE_MS = 6000       # margen tras una recarga antes de volver a intervenir

# Recarga preventiva (S12): además de reaccionar al congelamiento, el pipeline
# se renueva cada tanto por su cuenta. Ver _check_watchdog(). Poner en 0 para
# desactivarla.
PREVENTIVE_RELOAD_MS = 20 * 60 * 1000  # 20 minutos
PREVENTIVE_MAX_POS_MS = 1500           # solo justo después de dar la vuelta

# Loop de los clips CON pista de audio (S13, reactivado -- ver más abajo por
# qué). Ver _check_seamless_loop().
#
# Con el salto anticipado a 0 (el loop "sin corte" de S11) los clips con
# audio se congelan cada pocos minutos: medido en banco (S12), 2
# congelamientos en 5.5 min contra 0 en 28 min del mismo clip sin pista de
# audio. Por eso en S12 los clips con audio pasaron a dar la vuelta esperando
# el final real (EndOfMedia) -- estable, pero con un corte duro visible en
# CADA vuelta (ver docstring del módulo).
#
# S13: el usuario pidió recuperar el loop sin corte en Caras a pesar del
# riesgo medido. Los clips de Caras duran ~8.2-8.8s cada uno (medido con
# ffprobe), así que "un corte en cada vuelta" es un corte cada ~8s -- MUY
# seguido. Con la tasa de congelamiento medida en S12 (2 en 5.5 min = 330s,
# ~1 cada 165s, o sea ~1 de cada 19-20 vueltas), la cuenta sale a favor de
# reactivar el salto anticipado: ~95% de las vueltas quedan perfectamente
# seamless y solo ~5% se congela brevemente -- contra el 100% de vueltas con
# corte duro de la alternativa "segura". Esto SOLO es tolerable porque
# STALL_TIMEOUT_MS bajó de 4000 a 1800ms a la vez (ver arriba): cuando sí se
# congela, se nota mucho menos tiempo. Si en el robot real la tasa de
# congelamiento resulta mayor a la medida en banco, o el recorte a 1800ms
# genera falsos positivos (recargas de más), volver False acá -- es la
# única línea que hay que tocar para revertir a la vuelta "segura".
SEAMLESS_LOOP_WITH_AUDIO = True

# Subcarpeta de caché para las variantes sin pista de audio (ver docstring del
# módulo) -- se generan una sola vez por clip, junto a los originales.
_MUTED_SUBDIR = "_sin_audio"

# Variantes con el audio normalizado y comprimido (S12) -- ver
# _ensure_normalized_variant().
_NORM_SUBDIR = "_audio_normalizado"
LOUDNESS_TARGET_LUFS = -18.0   # sonoridad integrada objetivo (EBU R128): la MISMA que ya tenían
TRUE_PEAK_MAX_DBTP = -6.0      # techo de pico real: 4.5 dB por debajo del pico original
LOUDNESS_RANGE_LU = 5.0        # rango dinámico objetivo
# Compresor previo: baja los picos hacia la media para que loudnorm pueda
# mantener la sonoridad con un techo mucho más bajo (ver _ensure_normalized_variant).
_COMPRESOR = "acompressor=threshold=-24dB:ratio=6:attack=5:release=150"


def _now_ms() -> int:
    """Reloj monótono en ms (no se ve afectado por cambios de hora del sistema)."""
    return int(time.monotonic() * 1000)


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
        # S13 (revertido en la 5ª ronda): se probó sonido por defecto en
        # CUALQUIER pantalla, pero eso hace que casi todas las pantallas usen
        # la variante CON pista de audio -- y el loop "sin corte" (ver
        # _check_seamless_loop) está DESACTIVADO a propósito para esa
        # variante, porque se congela cada pocos minutos en esta Jetson (ver
        # docstring del módulo). El usuario priorizó el video sin cortes por
        # sobre el sonido fuera de Caras, así que vuelve el diseño original:
        # silencioso por defecto, y MainWindow solo habilita audio en Caras
        # (ver _show_view/_effective_audio_muted en ui/main_window.py).
        self._muted = True
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

        # Vigilante de congelamiento (S12): estado para _check_watchdog().
        self._last_pos = -1
        self._last_advance_ms = _now_ms()
        self._last_reload_ms = 0
        self._recuperaciones = 0
        self._player.error.connect(self._on_player_error)

        self._watchdog = QTimer(self)
        self._watchdog.setInterval(WATCHDOG_POLL_MS)
        self._watchdog.timeout.connect(self._check_watchdog)
        self._watchdog.start()

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
        """Ruta a reproducir para 'path': la variante de audio normalizado si
        hay audio habilitado (Caras), o su variante sin pista de audio si está
        silenciado (ver docstring del módulo -- nunca se logra silencio con
        setMuted/setVolume sobre un clip CON audio, se congela el loop en esta
        Jetson)."""
        if not self._muted and self._volume > 0:
            return self._ensure_normalized_variant(path)
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

    @staticmethod
    def _ensure_normalized_variant(path: str) -> str:
        """Variante del clip con el audio normalizado y COMPRIMIDO (S12).

        Por qué: el audio se oye saturado en el altavoz del panel ElecLab y ya
        no queda margen por volumen -- el sink de PulseAudio está al 65% desde
        S10 y el usuario confirmó (S12) que subir el volumen de la pantalla al
        máximo, la vía que S11 dejó por comprobar, NO lo arregla. Medido con
        loudnorm/volumedetect, la saturación no viene del nivel medio sino de la
        EXCURSIÓN: los clips promedian -17.9 a -22.8 LUFS pero pican hasta
        -1.5 dBTP, ~22 dB de factor de cresta que ese altavoz pequeño no
        reproduce sin distorsionar.

        Corrección (la estándar para altavoces chicos): comprimir los picos
        contra la media y renormalizar a la MISMA sonoridad integrada con un
        techo de pico mucho más bajo. Medido sobre 'Sonreir': -17.9 LUFS /
        -1.5 dBTP -> -17.3 LUFS / -5.9 dBTP; se oye igual de fuerte (0.6 LU más,
        de hecho) golpeando el altavoz 4.4 dB menos. De paso empareja los cuatro
        clips, que antes se llevaban 4.9 LU entre el más fuerte y el más flojo.

        No sube el volumen: reduce el pico a igualdad de sonoridad. Es
        mitigación, no cura -- la solución real sigue siendo hardware (altavoz
        externo o amplificador), descartada por ahora por espacio.

        Solo se re-codifica el AUDIO (-c:v copy): el video queda bit a bit
        idéntico, así que esto no puede afectar a la reproducción ni al loop.
        Se cachea igual que la variante sin audio: se genera una vez por clip.
        """
        directory = os.path.dirname(path)
        cache_dir = os.path.join(directory, _NORM_SUBDIR)
        out_path = os.path.join(cache_dir, os.path.basename(path))
        if os.path.exists(out_path):
            return out_path
        try:
            os.makedirs(cache_dir, exist_ok=True)
            # OJO: nada de alimiter aquí. Su opción 'level' viene activada por
            # defecto y RE-NIVELA la salida hasta el techo, deshaciendo lo que
            # acaba de hacer loudnorm: con él la variante quedaba en -13.9 LUFS
            # y -0.4 dBTP, es decir MÁS saturada que el original (medido).
            filtro = (
                f"{_COMPRESOR},"
                f"loudnorm=I={LOUDNESS_TARGET_LUFS}:TP={TRUE_PEAK_MAX_DBTP}"
                f":LRA={LOUDNESS_RANGE_LU}"
            )
            subprocess.run(
                ["ffmpeg", "-y", "-i", path, "-af", filtro,
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", out_path],
                check=True, capture_output=True,
            )
            log.info("Variante de audio normalizado generada: %s", out_path)
            return out_path
        except Exception:
            log.exception(
                "No se pudo normalizar el audio de %s; se reproduce el original "
                "(puede sonar saturado en el altavoz del panel)", path)
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
        self._player.setVolume(max(1, self._volumen_lineal()))
        self._player.play()
        # Cargar un medio nuevo reinicia la cuenta del vigilante (S12): la
        # posición vuelve a 0 y tarda un momento en avanzar.
        self._last_reload_ms = _now_ms()
        self._last_advance_ms = self._last_reload_ms
        self._last_pos = -1

    def _volumen_lineal(self) -> int:
        """Convierte el volumen de Configuraciones (0-100, escala PERCEPTUAL)
        al valor lineal que espera QMediaPlayer.setVolume (S12).

        QMediaPlayer.setVolume es amplitud lineal, no sonoridad percibida: la
        mitad del recorrido del slider (50) son solo -6 dB, así que casi toda
        la diferencia audible se concentraba en el extremo bajo y el tramo
        70-100 sonaba prácticamente igual de fuerte. Con el panel saturando,
        eso deja al usuario sin control fino justo donde lo necesita: bajar de
        100 a 80 apenas quitaba 1.9 dB. Con la conversión de Qt (medido: 80 ->
        -9.1 dB, 92 -> -5.2 dB, 50 -> -16.4 dB) el slider se vuelve utilizable
        de punta a punta."""
        lineal = QAudio.convertVolume(
            self._volume / 100.0,
            QAudio.LogarithmicVolumeScale,
            QAudio.LinearVolumeScale,
        )
        return int(round(lineal * 100))

    def _check_seamless_loop(self):
        """Reinicia a 0 justo antes del final real, sin esperar a EndOfMedia
        (ver docstring del módulo) -- el mecanismo normal de loop.

        Excepción medida en S12: en los clips CON pista de audio este salto
        anticipado atasca el pipeline cada pocos minutos (la posición se queda
        clavada en 0 aunque el reproductor siga en PlayingState). Ahí se deja
        que el clip llegue al final de verdad y se reinicia desde
        _on_media_status_changed. Es exactamente la misma familia de problemas
        que ya obligó a no usar setMuted()/setVolume() sobre clips con audio en
        esta Jetson: lo que no tolera el backend es tocar el pipeline mientras
        hay una pista de audio viva."""
        if self._tiene_audio() and not SEAMLESS_LOOP_WITH_AUDIO:
            return
        dur = self._player.duration()
        if dur <= 0:
            return
        if self._player.position() >= dur - LOOP_LEAD_MS:
            self._player.setPosition(0)

    def _tiene_audio(self) -> bool:
        """Si lo que se está reproduciendo es la variante CON pista de audio."""
        return not self._muted and self._volume > 0

    def _on_media_status_changed(self, status):
        # En los clips CON audio éste es el mecanismo de loop PRINCIPAL desde
        # S12 (ver _check_seamless_loop). En los silenciosos sigue siendo solo
        # una red de seguridad, por si el salto anticipado no llegó a tiempo
        # (p. ej. duración aún desconocida al cargar un clip).
        if status == QMediaPlayer.EndOfMedia:
            self._player.setPosition(0)
            self._player.play()
        elif status == QMediaPlayer.StalledMedia:
            # No se recupera aquí: solo se deja rastro. La recuperación la
            # decide _check_watchdog(), que es quien sabe cuánto lleva parado.
            log.warning("El reproductor reporta StalledMedia (clip %s)",
                        os.path.basename(self._clips[self._idx]) if self._clips else "?")

    def _on_player_error(self, *_args):
        log.error("Error del reproductor (%s): %s",
                  self._player.error(), self._player.errorString())

    # ---------- vigilante de congelamiento (S12) ----------
    def _check_watchdog(self):
        """Detecta que el video se quedó congelado y lo revive solo.

        Síntoma reportado por el usuario (S12): tras un rato largo en la misma
        pantalla, la cara de Moodi se queda pegada en un frame y solo vuelve a
        moverse al cambiar de pantalla y regresar. Ese "cambiar y volver" es,
        en el código, MainWindow._show_view() -> set_muted() -> _set_media(),
        o sea una RECARGA del medio: el pipeline de GStreamer se reconstruye y
        el video arranca de nuevo. El reproductor, mientras tanto, sigue
        reportando PlayingState -- por eso el congelamiento es silencioso y no
        hay ningún error que atrapar.

        Así que el criterio no puede ser el estado del reproductor sino el
        AVANCE REAL de la posición: si lleva STALL_TIMEOUT_MS sin moverse
        cuando debería estar reproduciendo, está congelado y se aplica la misma
        recarga que hace el cambio de pantalla, automáticamente y sin que el
        usuario tenga que hacer nada.

        Se registra cada intervención (nivel WARNING) a propósito: es la única
        forma de saber, en el robot real, cada cuánto ocurre de verdad.
        """
        if not self._clips:
            return

        ahora = _now_ms()

        # Tras una recarga hay que darle tiempo a abrir el archivo y arrancar;
        # si no, el propio arranque (posición aún en 0) se leería como congelado.
        if ahora - self._last_reload_ms < RELOAD_GRACE_MS:
            self._last_advance_ms = ahora
            return

        status = self._player.mediaStatus()
        if status in (QMediaPlayer.LoadingMedia, QMediaPlayer.NoMedia,
                      QMediaPlayer.UnknownMediaStatus):
            self._last_advance_ms = ahora
            return

        pos = self._player.position()
        if pos != self._last_pos:
            self._last_pos = pos
            self._last_advance_ms = ahora
            self._check_recarga_preventiva(ahora, pos)
            return

        parado_ms = ahora - self._last_advance_ms
        if parado_ms < STALL_TIMEOUT_MS:
            return

        self._recuperaciones += 1
        log.warning(
            "Video congelado: %.1fs sin avanzar (pos=%d estado=%s status=%s clip=%s). "
            "Recargando el clip -- recuperación nº%d de esta sesión.",
            parado_ms / 1000.0, pos, self._player.state(), status,
            os.path.basename(self._clips[self._idx]), self._recuperaciones,
        )
        self._recargar_clip_actual()

    def _check_recarga_preventiva(self, ahora: int, pos: int):
        """Renueva el pipeline cada PREVENTIVE_RELOAD_MS aunque no se haya
        detectado nada raro.

        Por qué existe además del vigilante reactivo: hay un segundo modo de
        fallo posible que el vigilante NO puede ver. Si lo que se queda pegado
        es la superficie de video (el plano de overlay de X11/GStreamer) y no
        el pipeline, la posición sigue avanzando con normalidad y para el
        reproductor todo está bien, pero la pantalla no se actualiza. Encaja
        con el detalle de que al usuario le baste "cambiar de pantalla y
        volver": entrar a Oraciones no solo puede recargar el medio, también
        cambia la geometría del widget de video (setGeometry al recuadro
        chico), que es justo lo que fuerza a redibujar ese plano.

        Como no se puede detectar desde dentro del proceso (grabar la ventana
        de un overlay nativo devuelve el color de clave, no el video), la
        defensa es renovar el pipeline periódicamente. Se hace SOLO justo
        después de que el clip dio la vuelta (posición < PREVENTIVE_MAX_POS_MS):
        el clip ya reinicia solo cada ~8 s, así que reiniciarlo ahí se ve igual
        que una vuelta normal.
        """
        if PREVENTIVE_RELOAD_MS <= 0 or self._last_reload_ms <= 0:
            return
        if ahora - self._last_reload_ms < PREVENTIVE_RELOAD_MS:
            return
        if pos > PREVENTIVE_MAX_POS_MS:
            return
        log.info("Recarga preventiva del clip tras %.0f min de reproducción continua",
                 (ahora - self._last_reload_ms) / 60000.0)
        self._recargar_clip_actual()

    def _recargar_clip_actual(self):
        """Recarga el clip en curso: lo mismo que consigue el usuario al cambiar
        de pantalla y volver, pero sin que tenga que hacerlo él."""
        self._last_reload_ms = _now_ms()
        self._last_advance_ms = self._last_reload_ms
        self._last_pos = -1
        # setMedia(QMediaContent()) primero: fuerza a soltar el pipeline
        # anterior en vez de reutilizar el que se quedó colgado.
        self._player.setMedia(QMediaContent())
        self._set_media(self._clips[self._idx])

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
