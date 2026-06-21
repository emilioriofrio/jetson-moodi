# run.py
import os, sys, time, signal
import multiprocessing as mp

# ========= KERAS/TF para TODOS los hijos =========
# - En Jetson con TF 2.16+NV: usa el backend tf.keras "legacy shim" y
#   evita mezclar con paquetes externos tipo tf_keras.
os.environ.setdefault("TF_USE_LEGACY_KERAS", "0")
os.environ.setdefault("KERAS_BACKEND", "tensorflow")
# Opcional: silencia TF-TRT si no tienes TensorRT 10 instalado
os.environ.setdefault("TF_TRT_DISABLE", "1")

CFG = "config/runtime.yaml"

# ---------- Wrappers ----------
def entry_orchestrator(cfg_path, qA, qB, qC, stop_event, qUI_frames=None):
    from core.orchestrator import run_orchestrator
    run_orchestrator(cfg_path, qA, qB, qC, stop_event, qUI_frames)

def entry_fusion(cfg_path, pred_q, out_q, stop_event):
    from core.fusion import run_fusion
    run_fusion(cfg_path, pred_q, out_q, stop_event)

def entry_worker_a(cfg_path, in_q, out_q, stop_event):
    from modules.mod_a import run_worker_A
    run_worker_A(cfg_path, in_q, out_q, stop_event)

def entry_worker_b(cfg_path, in_q, out_q, stop_event):
    from modules.mod_b import run_worker_B
    run_worker_B(cfg_path, in_q, out_q, stop_event)

def entry_worker_c(cfg_path, in_q, out_q, stop_event):
    from modules.mod_c import run_worker_C
    run_worker_C(cfg_path, in_q, out_q, stop_event)

def entry_reporter(cfg_path, in_q, stop_event, qUI_stats=None):
    from core.reporter import run_reporter
    run_reporter(cfg_path, in_q, stop_event, qUI_stats)

def entry_pred_fanout(pred_in, out_to_fusion, out_to_ui, stop_event):
    from core.pred_fanout import run_pred_fanout
    run_pred_fanout(pred_in, out_to_fusion, out_to_ui, stop_event)

def entry_ui_viewer(cfg_path, frames_q, preds_q, reporter_q, stop_event):
    from ui.viewer import run_viewer
    run_viewer(cfg_path, frames_q, preds_q, reporter_q, stop_event)

# ---------- Utilidades de cierre ----------
def hard_shutdown(procs, grace=2.0):
    deadline = time.time() + grace
    for p in procs:
        try:
            p.join(max(0.0, deadline - time.time()))
        except:
            pass
    still = [p for p in procs if p.is_alive()]
    if still:
        print("[MAIN] Forzando terminate():", [p.name for p in still], flush=True)
        for p in still:
            try: p.terminate()
            except: pass
        for p in still:
            try: p.join(1.0)
            except: pass

def kill_residual_children():
    for ch in mp.active_children():
        try:
            print(f"[MAIN] Matando residual {ch.name} (pid={ch.pid})", flush=True)
            ch.terminate()
        except:
            pass
        try:
            ch.join(0.5)
        except:
            pass

def main():
    if sys.platform.startswith("win"):
        mp.freeze_support()
    mp.set_start_method("spawn", force=True)
    print("[MAIN] start method:", mp.get_start_method(), flush=True)

    ctx = mp.get_context("spawn")

    # Colas
    qA_in    = ctx.SimpleQueue()
    qB_in    = ctx.SimpleQueue()
    qC_in    = ctx.SimpleQueue()
    qPred    = ctx.SimpleQueue()

    qFusIn   = ctx.SimpleQueue()
    qFused   = ctx.SimpleQueue()

    qUI_frm  = ctx.Queue(maxsize=2)
    qUI_preds= ctx.SimpleQueue()
    qUI_stats= ctx.SimpleQueue()

    stop_event = ctx.Event()

    procs = [
        ctx.Process(name="Orchestrator", target=entry_orchestrator,
                    args=(CFG, qA_in, qB_in, qC_in, stop_event, qUI_frm)),

        ctx.Process(name="WorkerA", target=entry_worker_a, args=(CFG, qA_in, qPred, stop_event)),
        ctx.Process(name="WorkerB", target=entry_worker_b, args=(CFG, qB_in, qPred, stop_event)),
        ctx.Process(name="WorkerC", target=entry_worker_c, args=(CFG, qC_in, qPred, stop_event)),

        ctx.Process(name="PredFanout", target=entry_pred_fanout,
                    args=(qPred, qFusIn, qUI_preds, stop_event)),

        ctx.Process(name="Fusion", target=entry_fusion,
                    args=(CFG, qFusIn, qFused, stop_event)),

        ctx.Process(name="Reporter", target=entry_reporter,
                    args=(CFG, qFused, stop_event, qUI_stats)),

        ctx.Process(name="UIViewer", target=entry_ui_viewer,
                    args=(CFG, qUI_frm, qUI_preds, qUI_stats, stop_event)),
    ]

    for p in procs:
        p.daemon = False
        p.start()
        print(f"[MAIN] Lanzado {p.name} (pid={p.pid})", flush=True)

    def on_sigint(sig, frame):
        print("\n[MAIN] Ctrl+C recibido → stop_event", flush=True)
        stop_event.set()
    signal.signal(signal.SIGINT, on_sigint)

    try:
        while any(p.is_alive() for p in procs):
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[MAIN] KeyboardInterrupt → stop_event", flush=True)
        stop_event.set()
    finally:
        stop_event.set()
        time.sleep(0.2)

        for q in (qA_in, qB_in, qC_in, qPred, qFusIn, qFused, qUI_frm, qUI_preds, qUI_stats):
            try: q.close()
            except: pass

        hard_shutdown(procs, grace=2.0)

        for p in procs:
            try: p.close()
            except: pass

        kill_residual_children()
        print("[MAIN] Salida completa.", flush=True)
        os._exit(0)

if __name__ == "__main__":
    main()

