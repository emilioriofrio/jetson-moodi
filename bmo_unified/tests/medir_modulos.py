#!/usr/bin/env python3
"""Corrida real A+B+C midiendo, ademas del fps a la UI, la CADENCIA POR MODULO.

Motivo (S12): S11 dejo abierto que el Modulo C emite del orden de 2
predicciones cada 130 s (pendiente 4 de su seccion 9). Para atacarlo hay que
saber donde se va el tiempo, y el arnes de S11 (medir_fps_vision.py) solo mide
frames a la UI. Este mide, por modulo: cuantas predicciones llegan, cada
cuanto, y en el caso de C cuantas son "calentando" (ventana incompleta) frente
a predicciones reales.

IMPRESCINDIBLE el guard __main__: multiprocessing usa "spawn" y cada hijo
re-ejecuta este modulo.

Uso:
    cd /home/jetson/bmo_unified
    <venv>/bin/python tests/medir_modulos.py [segundos]
"""
import os
import statistics
import sys
import time


def resumen(nombre, ts, t0):
    if not ts:
        print(f"  {nombre}: SIN PREDICCIONES")
        return
    print(f"  {nombre}: {len(ts)} predicciones | primera a los {ts[0]-t0:.1f}s", end="")
    if len(ts) > 1:
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        print(f" | intervalo mediano {statistics.median(gaps):.2f}s"
              f" | max {max(gaps):.2f}s | min {min(gaps):.2f}s")
    else:
        print()


def main():
    sys.path.insert(0, "/home/jetson/bmo_unified")
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 130.0
    app = QApplication([])
    from vision.engine import VisionEngine

    t0 = time.monotonic()
    frames = []
    preds = {"A": [], "B": [], "C": []}
    c_warmup = []
    c_labels = []

    eng = VisionEngine()
    eng.frame_ready.connect(lambda _img: frames.append(time.monotonic()))

    def on_pred(p):
        mod = str(p.get("module", "?"))
        ahora = time.monotonic()
        meta = p.get("meta") or {}
        if mod == "C" and meta.get("ready") is False:
            c_warmup.append(ahora)   # aviso de ventana incompleta, no es prediccion real
            return
        if mod in preds:
            preds[mod].append(ahora)
        if mod == "C":
            c_labels.append((round(ahora - t0, 1), p.get("label"),
                             round(float(p.get("conf") or 0), 2),
                             meta.get("person_ratio")))

    eng.pred_ready.connect(on_pred)
    eng.start()

    QTimer.singleShot(int(dur * 1000), lambda: (eng.stop(), app.quit()))
    app.exec_()

    print("\nRESULTADO --------------------------------------")
    if len(frames) >= 3:
        gaps = [b - a for a, b in zip(frames, frames[1:])]
        estables = [g for t, g in zip(frames[1:], gaps) if t - t0 > 60.0]
        print(f"frames a la UI      : {len(frames)} | fps global "
              f"{len(frames)/(frames[-1]-frames[0]):.2f}")
        if estables:
            print(f"fps estable (>60s)  : {len(estables)/sum(estables):.2f} | "
                  f"gap max {max(estables):.2f}s")
    else:
        print(f"frames a la UI      : {len(frames)} (insuficientes)")

    print("predicciones por modulo:")
    for mod in ("A", "B", "C"):
        resumen(f"modulo {mod}", preds[mod], t0)
    print(f"  modulo C 'calentando' (ventana incompleta): {len(c_warmup)}")
    if c_labels:
        print("  detalle C (t, etiqueta, conf, person_ratio):")
        for fila in c_labels:
            print(f"    {fila}")


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    main()
