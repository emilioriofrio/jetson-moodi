# vision/engine.py
"""
Orquesta el motor de visión (Módulos A/B/C + fusión) de
integradora/model_ia/sistem_IA SIN MODIFICAR ese código, pero sin pasar por
run.py como caja negra ni por ui/viewer.py (que usa cv2.imshow, prohibido para
el panel embebido — ver REQUERIMIENTOS_APP_MOODI.md 6.2). En su lugar reutiliza
las mismas funciones de entrada de proceso y de apagado que ya define run.py
(entry_orchestrator, entry_worker_a/b/c, entry_fusion, entry_pred_fanout,
entry_reporter, hard_shutdown, kill_residual_children) importándolas como
módulo, y sustituye UIViewer por un QThread propio que re-emite los frames y
predicciones como señales Qt para pintarlos en un QLabel embebido.

Carga perezosa (6.1): nada de TensorFlow/Torch/MediaPipe/DeepFace/Detectron2 se
importa en el proceso principal de bmo_app. Esos imports viven dentro de
modules/mod_{a,b,c}.py y solo se ejecutan dentro de cada proceso hijo, una vez
que VisionEngine.start() ya lo spawneó — exactamente igual que en run.py.
"""

import logging
import multiprocessing as mp
import os
import queue as pyq
import sys
import time

import numpy as np
import yaml
from PyQt5.QtCore import QObject, QThread, pyqtSignal
from PyQt5.QtGui import QImage

log = logging.getLogger("bmo.vision_engine")

SISTEM_IA_DIR = "/home/jetson/integradora/model_ia/sistem_IA"
CFG_REL = "config/runtime.yaml"

# Cooldown mínimo entre un stop() y el siguiente start() aceptado. Se observó
# en logs reales que un evento de botón EMO_TOGGLE espurio (ruido eléctrico o
# conexión intermitente en ese botón físico, no un bug del firmware -- el
# debounce del ESP32 ya es correcto) puede hacer arrancar/detener este motor
# repetidamente en segundos; cada arranque implica spawnear TensorFlow +
# Detectron2 + MediaPipe + la cámara, suficientemente pesado como para
# saturar los 6 núcleos de la Orin Nano y congelar el resto de la app
# (incluida la reproducción de video). Este cooldown evita que un rebote de
# hardware degenere en ese ciclo de arranque/apagado en cascada.
MIN_RESTART_INTERVAL_S = 2.0


def _ensure_sistem_path_first():
    """Garantiza que sistem_IA quede SIEMPRE primero en sys.path.

    bmo_unified y sistem_IA tienen cada uno su propio paquete llamado "core" --
    el orden de sys.path decide cuál de los dos gana al hacer "import core".
    Con multiprocessing en modo "spawn", cada proceso hijo re-ejecuta el script
    principal (bmo_app.py) como "__mp_main__" para reconstruir su estado de
    __main__; eso vuelve a correr `sys.path.insert(0, BASE_DIR)` (bmo_unified)
    DESPUÉS de que este proceso hijo heredó un sys.path que ya tenía sistem_IA
    al frente, dejando sistem_IA en segundo lugar. Por eso no basta con "si no
    está, insertarlo": hay que moverlo al frente siempre, esté donde esté.
    """
    if SISTEM_IA_DIR in sys.path:
        sys.path.remove(SISTEM_IA_DIR)
    sys.path.insert(0, SISTEM_IA_DIR)


def _import_sistem_run_no_chdir():
    """Para el PROCESO PRINCIPAL de bmo_app: solo ajusta sys.path, nunca cambia
    el cwd (rompería las rutas relativas de bmo_unified/config)."""
    _ensure_sistem_path_first()
    import run as sistem_run
    return sistem_run


def _child_entry_setup():
    """Para usar DENTRO de cada proceso hijo ya spawneado (proceso de SO aislado,
    aquí sí es seguro cambiar el cwd): reproduce el entorno que run.py asume
    implícitamente al ejecutarse siempre con cwd=sistem_IA."""
    os.chdir(SISTEM_IA_DIR)
    _ensure_sistem_path_first()
    import run as sistem_run
    return sistem_run


# ---------- funciones de entrada de cada proceso hijo (picklable, nivel de módulo) ----------

def _entry_orchestrator(qA, qB, qC, stop_event, qUI_frm):
    sistem_run = _child_entry_setup()
    sistem_run.entry_orchestrator(CFG_REL, qA, qB, qC, stop_event, qUI_frm)


def _entry_worker_a(in_q, out_q, stop_event):
    sistem_run = _child_entry_setup()
    sistem_run.entry_worker_a(CFG_REL, in_q, out_q, stop_event)


def _entry_worker_b(in_q, out_q, stop_event):
    sistem_run = _child_entry_setup()
    sistem_run.entry_worker_b(CFG_REL, in_q, out_q, stop_event)


def _entry_worker_c(in_q, out_q, stop_event):
    sistem_run = _child_entry_setup()
    sistem_run.entry_worker_c(CFG_REL, in_q, out_q, stop_event)


def _entry_pred_fanout(pred_in, out_to_fusion, out_to_ui, stop_event):
    sistem_run = _child_entry_setup()
    sistem_run.entry_pred_fanout(pred_in, out_to_fusion, out_to_ui, stop_event)


def _entry_fusion(pred_q, out_q, stop_event):
    sistem_run = _child_entry_setup()
    sistem_run.entry_fusion(CFG_REL, pred_q, out_q, stop_event)


def _entry_reporter(in_q, stop_event, qUI_stats):
    sistem_run = _child_entry_setup()
    sistem_run.entry_reporter(CFG_REL, in_q, stop_event, qUI_stats)


class _QueuePump(QThread):
    """Drena las colas de salida del motor (frames/predicciones/stats) y las
    re-emite como señales Qt -- nunca se tocan widgets desde este hilo."""

    frame_ready = pyqtSignal(QImage)
    pred_ready = pyqtSignal(dict)
    stats_ready = pyqtSignal(dict)

    def __init__(self, qUI_frm, qUI_preds, qUI_stats, parent=None):
        super().__init__(parent)
        self._qUI_frm = qUI_frm
        self._qUI_preds = qUI_preds
        self._qUI_stats = qUI_stats
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        while not self._stop:
            did_work = False

            try:
                msg = self._qUI_frm.get_nowait()
            except (pyq.Empty, Exception):
                msg = None
            if msg is not None:
                did_work = True
                self._emit_frame(msg)

            try:
                if self._qUI_preds._reader.poll(0):
                    pred = self._qUI_preds.get()
                    did_work = True
                    self.pred_ready.emit(pred)
            except Exception:
                pass

            try:
                if self._qUI_stats._reader.poll(0):
                    stats = self._qUI_stats.get()
                    did_work = True
                    self.stats_ready.emit(stats)
            except Exception:
                pass

            if not did_work:
                self.msleep(15)

    def _emit_frame(self, msg):
        try:
            frame = msg.get("frame") if isinstance(msg, dict) else None
            if frame is None:
                return
            h, w = frame.shape[:2]
            rgb = np.ascontiguousarray(frame[:, :, ::-1])  # BGR -> RGB
            qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
            self.frame_ready.emit(qimg)
        except Exception:
            log.exception("Error convirtiendo frame de visión a QImage")


class VisionEngine(QObject):
    """API pública para bmo_app: start()/stop() bajo demanda (EMO_TOGGLE) +
    señales Qt con frames/predicciones/estadísticas para el panel embebido."""

    frame_ready = pyqtSignal(QImage)
    pred_ready = pyqtSignal(dict)
    stats_ready = pyqtSignal(dict)
    started = pyqtSignal()
    stopped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._procs = []
        self._queues = []
        self._stop_event = None
        self._pump = None
        self._running = False
        self._last_stop_ts = 0.0

    def is_running(self) -> bool:
        return self._running

    def start(self):
        if self._running:
            return

        elapsed = time.monotonic() - self._last_stop_ts
        if elapsed < MIN_RESTART_INTERVAL_S:
            log.warning(
                "Arranque de motor de visión ignorado: %.1fs desde el último apagado "
                "(cooldown anti-rebote, ver MIN_RESTART_INTERVAL_S)", elapsed,
            )
            return

        sistem_run = _import_sistem_run_no_chdir()
        ctx = mp.get_context("spawn")

        cfg_full_path = os.path.join(SISTEM_IA_DIR, CFG_REL)
        with open(cfg_full_path, "r", encoding="utf-8") as f:
            cfg_data = yaml.safe_load(f)
        qmax = int(cfg_data.get("queues_maxsize", 5))

        # Colas de frames ACOTADAS (S11): ver core/orchestrator.put_drop_if_full.
        # Con SimpleQueue el orquestador se bloqueaba escribiendo cada frame
        # (~900 KB) en un pipe de 64 KB hasta que el worker lo leyera, así que
        # la captura corría al ritmo del módulo MÁS LENTO y el video se
        # congelaba durante las ráfagas de VGG16 del Módulo C.
        qA_in = ctx.Queue(maxsize=qmax)
        qB_in = ctx.Queue(maxsize=qmax)
        qC_in = ctx.Queue(maxsize=qmax)
        qPred = ctx.SimpleQueue()
        qFusIn = ctx.SimpleQueue()
        qFused = ctx.SimpleQueue()
        qUI_frm = ctx.Queue(maxsize=2)
        qUI_preds = ctx.SimpleQueue()
        qUI_stats = ctx.SimpleQueue()
        stop_event = ctx.Event()

        enabled_modules = cfg_data.get("enabled_modules", ["A", "B", "C"])
        log.info("Iniciando motor de visión, módulos habilitados: %s", enabled_modules)

        procs = [
            ctx.Process(name="Orchestrator", target=_entry_orchestrator,
                        args=(qA_in, qB_in, qC_in, stop_event, qUI_frm)),
        ]
        if "A" in enabled_modules:
            procs.append(ctx.Process(name="WorkerA", target=_entry_worker_a, args=(qA_in, qPred, stop_event)))
        if "B" in enabled_modules:
            procs.append(ctx.Process(name="WorkerB", target=_entry_worker_b, args=(qB_in, qPred, stop_event)))
        if "C" in enabled_modules:
            procs.append(ctx.Process(name="WorkerC", target=_entry_worker_c, args=(qC_in, qPred, stop_event)))

        procs.extend([
            ctx.Process(name="PredFanout", target=_entry_pred_fanout,
                        args=(qPred, qFusIn, qUI_preds, stop_event)),
            ctx.Process(name="Fusion", target=_entry_fusion,
                        args=(qFusIn, qFused, stop_event)),
            ctx.Process(name="Reporter", target=_entry_reporter,
                        args=(qFused, stop_event, qUI_stats)),
        ])

        for p in procs:
            p.daemon = False
            p.start()
            log.info("[VISION] Lanzado %s (pid=%s)", p.name, p.pid)

        self._procs = procs
        self._queues = [qA_in, qB_in, qC_in, qPred, qFusIn, qFused, qUI_frm, qUI_preds, qUI_stats]
        self._stop_event = stop_event
        self._sistem_run = sistem_run

        self._pump = _QueuePump(qUI_frm, qUI_preds, qUI_stats)
        self._pump.frame_ready.connect(self.frame_ready)
        self._pump.pred_ready.connect(self.pred_ready)
        self._pump.stats_ready.connect(self.stats_ready)
        self._pump.start()

        self._running = True
        self.started.emit()

    def stop(self):
        if not self._running:
            return
        log.info("[VISION] Deteniendo motor de visión…")
        self._stop_event.set()

        if self._pump is not None:
            self._pump.stop()
            self._pump.wait(2000)
            self._pump = None

        self._sistem_run.hard_shutdown(self._procs, grace=2.0)

        for q in self._queues:
            # cancel_join_thread() antes de close(): las Queue acotadas tienen
            # un hilo alimentador que puede colgar el cierre si quedaron frames
            # sin drenar (el motor se apaga y enciende varias veces por sesión).
            try:
                q.cancel_join_thread()
            except Exception:
                pass
            try:
                q.close()
            except Exception:
                pass

        self._sistem_run.kill_residual_children()

        self._procs = []
        self._queues = []
        self._stop_event = None
        self._running = False
        self._last_stop_ts = time.monotonic()
        self.stopped.emit()
        log.info("[VISION] Motor de visión detenido.")
