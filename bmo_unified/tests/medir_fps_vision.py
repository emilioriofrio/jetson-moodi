"""Corrida real A+B+C midiendo fps a UI y el GAP maximo entre frames.
IMPRESCINDIBLE el guard __main__: multiprocessing usa "spawn" y cada hijo
re-ejecuta este modulo; sin guard cada hijo relanzaria el motor entero."""
import os, sys, time, statistics

def main():
    sys.path.insert(0, "/home/jetson/bmo_unified")
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer
    DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 110.0
    app = QApplication([])
    from vision.engine import VisionEngine
    ts = []
    eng = VisionEngine()
    eng.frame_ready.connect(lambda _img: ts.append(time.monotonic()))
    t0 = time.monotonic()
    eng.start()
    def fin():
        eng.stop(); app.quit()
    QTimer.singleShot(int(DUR * 1000), fin)
    app.exec_()

    if len(ts) < 3:
        print("SIN FRAMES:", len(ts)); return
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    stable = [g for t, g in zip(ts[1:], gaps) if t - t0 > 60.0]
    print(f"RESULTADO --------------------------------------")
    print(f"frames totales      : {len(ts)} en {ts[-1]-t0:.1f}s")
    print(f"primer frame        : {ts[0]-t0:.1f}s tras start()")
    print(f"fps global          : {len(ts)/(ts[-1]-ts[0]):.2f}")
    if stable:
        print(f"fps estable (>60s)  : {len(stable)/sum(stable):.2f}")
        print(f"gap mediano estable : {statistics.median(stable)*1000:.0f} ms")
        print(f"gap MAXIMO estable  : {max(stable):.2f} s   <-- el sintoma")
        print(f"gaps > 1s (estable) : {sum(1 for g in stable if g > 1.0)}")
        print(f"gaps > 2s (estable) : {sum(1 for g in stable if g > 2.0)}")
    print(f"gap MAXIMO global   : {max(gaps):.2f} s (incluye carga de modelos)")

if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    main()
