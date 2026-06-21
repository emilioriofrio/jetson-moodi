import time, traceback
from multiprocessing import Event
from core.messages import PredMsg

def run_pred_fanout(in_q, out_q_fusion, out_q_ui, stop_event: Event):
    print("[FANOUT] Iniciado.", flush=True)
    try:
        while not stop_event.is_set():
            try:
                if not getattr(in_q, "_reader").poll(0.02):
                    time.sleep(0.01)
                    continue
                pred_dict = in_q.get()
            except Exception:
                time.sleep(0.01)
                continue

            # Validación
            try:
                _ = PredMsg(**pred_dict)
            except Exception:
                print("[FANOUT] PredMsg inválido:\n" + traceback.format_exc(), flush=True)
                continue

            # Escribe a ambos
            try:
                out_q_fusion.put(pred_dict)
            except Exception:
                pass
            try:
                out_q_ui.put(pred_dict)
            except Exception:
                pass
    except KeyboardInterrupt:
        pass
    except Exception:
        print("[FANOUT] Error inesperado:\n" + traceback.format_exc(), flush=True)
    finally:
        print("[FANOUT] Saliendo…", flush=True)
