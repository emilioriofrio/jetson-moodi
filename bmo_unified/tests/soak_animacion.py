#!/usr/bin/env python3
# tests/soak_animacion.py
"""
Arnés de soak del fondo animado (S12).

Motivo: el usuario reporta que, tras un rato largo en la misma pantalla, el
video de la cara de Moodi se congela por completo y solo se recupera al
cambiar de pantalla y volver (lo que en MainWindow._show_view() equivale a
recargar el medio con set_muted()). El bucle debería ser eterno.

Este arnés reproduce el MISMO AnimationPlayer de la app (no una copia) sobre
el display real, y registra una vez por segundo el estado interno del
reproductor para responder tres preguntas que no se pueden contestar leyendo
el código:

  1. ¿La posición deja de avanzar aunque el reproductor siga en PlayingState?
     (congelamiento silencioso -- el síntoma reportado)
  2. Tras setPosition(0), ¿cuánto tarda position() en reflejar el salto? Si
     tarda más que LOOP_POLL_MS, _check_seamless_loop() dispara una RÁFAGA de
     seeks por cada vuelta en vez de uno solo, y esas ráfagas se acumulan a lo
     largo de cientos de vueltas.
  3. ¿Crece la memoria residente del proceso con las vueltas?

No toca cámara, ni serie, ni /boot. Un solo QVideoWidget: NO ejecutar con
bmo_app.py abierto (dos superficies de video nativas compitiendo por el mismo
plano de hardware es un problema ya conocido en esta Jetson).

Uso:
    cd /home/jetson/bmo_unified
    <venv>/bin/python tests/soak_animacion.py --minutos 25 --salida /tmp/soak.csv
"""

import argparse
import csv
import os
import resource
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from PyQt5.QtCore import QTimer  # noqa: E402
from PyQt5.QtMultimedia import QMediaPlayer  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from ui.animation_player import AnimationPlayer  # noqa: E402

ANIM_DIR = "/home/jetson/integradora/animaciones"

ESTADOS = {
    QMediaPlayer.StoppedState: "Stopped",
    QMediaPlayer.PlayingState: "Playing",
    QMediaPlayer.PausedState: "Paused",
}
MEDIA_STATUS = {
    QMediaPlayer.UnknownMediaStatus: "Unknown",
    QMediaPlayer.NoMedia: "NoMedia",
    QMediaPlayer.LoadingMedia: "Loading",
    QMediaPlayer.LoadedMedia: "Loaded",
    QMediaPlayer.StalledMedia: "Stalled",
    QMediaPlayer.BufferingMedia: "Buffering",
    QMediaPlayer.BufferedMedia: "Buffered",
    QMediaPlayer.EndOfMedia: "EndOfMedia",
    QMediaPlayer.InvalidMedia: "Invalid",
}

# Sin avance de posición durante este tiempo, estando en PlayingState = congelado.
UMBRAL_CONGELADO_S = 3.0


class Soak:
    def __init__(self, minutos: float, salida: str, con_audio: bool = False):
        self.duracion_s = minutos * 60.0
        self.t0 = time.monotonic()

        self.ventana = QWidget()
        self.ventana.setWindowTitle("Soak animación Moodi (S12)")
        self.ventana.resize(640, 360)
        self.anim = AnimationPlayer(ANIM_DIR, self.ventana)
        self.anim.setGeometry(0, 0, 640, 360)
        # Con audio se reproduce la variante con pista de audio (lo que hace la
        # pantalla Caras). Importa distinguirlo: en esta Jetson ya se demostró
        # (S10/S11) que el audio es lo que interactúa mal con el loop, así que
        # un congelamiento podría aparecer SOLO en este modo.
        if con_audio:
            self.anim.set_muted(False)
        self.ventana.show()

        self.player = self.anim._player  # acceso deliberado al interno: es un arnés

        self.pos_previa = -1
        self.t_ultimo_avance = self.t0
        self.vueltas = 0
        self.seeks = 0
        self.congelado_desde = None
        self.max_estancamiento = 0.0

        # Contar cada seek emitido por el bucle real (pregunta 2): se envuelve
        # setPosition del propio QMediaPlayer para no alterar la lógica.
        self._set_position_original = self.player.setPosition
        self.player.setPosition = self._set_position_contado

        self.archivo = open(salida, "w", newline="")
        self.csv = csv.writer(self.archivo)
        self.csv.writerow([
            "t_s", "pos_ms", "dur_ms", "estado", "media_status",
            "vueltas", "seeks", "estancado_s", "rss_mb", "error",
        ])

        self.timer = QTimer()
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.muestrear)
        self.timer.start()

        self._log(f"inicio: clip={os.path.basename(self.anim._clips[self.anim._idx])} "
                  f"dur_declarada={self.player.duration()}ms")

    def _set_position_contado(self, ms):
        self.seeks += 1
        return self._set_position_original(ms)

    def _log(self, msg: str):
        t = time.monotonic() - self.t0
        linea = f"[{t:8.1f}s] {msg}"
        print(linea, flush=True)

    def muestrear(self):
        ahora = time.monotonic()
        t = ahora - self.t0
        pos = self.player.position()
        dur = self.player.duration()
        estado = ESTADOS.get(self.player.state(), "?")
        status = MEDIA_STATUS.get(self.player.mediaStatus(), "?")
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

        if pos < self.pos_previa:
            self.vueltas += 1
        if pos != self.pos_previa:
            self.t_ultimo_avance = ahora
            if self.congelado_desde is not None:
                self._log(f"RECUPERADO solo tras {ahora - self.congelado_desde:.1f}s congelado "
                          f"(pos={pos} estado={estado} status={status})")
                self.congelado_desde = None
        self.pos_previa = pos

        estancado = ahora - self.t_ultimo_avance
        self.max_estancamiento = max(self.max_estancamiento, estancado)

        if (estancado >= UMBRAL_CONGELADO_S and estado == "Playing"
                and self.congelado_desde is None):
            self.congelado_desde = self.t_ultimo_avance
            self._log(f"*** CONGELADO: {estancado:.1f}s sin avanzar en PlayingState "
                      f"(pos={pos}/{dur} status={status} vueltas={self.vueltas} "
                      f"seeks={self.seeks} rss={rss_mb:.0f}MB)")

        self.csv.writerow([f"{t:.1f}", pos, dur, estado, status, self.vueltas,
                           self.seeks, f"{estancado:.1f}", f"{rss_mb:.1f}",
                           self.player.error()])
        self.archivo.flush()

        if int(t) % 60 < 1:
            self._log(f"vueltas={self.vueltas} seeks={self.seeks} "
                      f"seeks/vuelta={self.seeks / max(1, self.vueltas):.1f} "
                      f"estado={estado}/{status} rss={rss_mb:.0f}MB "
                      f"max_estancamiento={self.max_estancamiento:.1f}s")

        if t >= self.duracion_s:
            self.terminar()

    def terminar(self):
        self.timer.stop()
        self._log("=" * 60)
        self._log(f"RESUMEN: vueltas={self.vueltas} seeks={self.seeks} "
                  f"seeks/vuelta={self.seeks / max(1, self.vueltas):.2f} "
                  f"max_estancamiento={self.max_estancamiento:.1f}s "
                  f"congelado_al_final={'sí' if self.congelado_desde else 'no'}")
        self.archivo.close()
        QApplication.instance().quit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=25.0)
    ap.add_argument("--salida", default="/tmp/soak_animacion.csv")
    ap.add_argument("--con-audio", action="store_true",
                    help="reproduce la variante CON pista de audio (pantalla Caras)")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    soak = Soak(args.minutos, args.salida, con_audio=args.con_audio)
    app._soak = soak  # evitar recolección
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
