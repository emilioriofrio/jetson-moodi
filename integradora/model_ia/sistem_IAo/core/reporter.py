# sistem_IA/core/reporter.py
import time, yaml, os, json, traceback
from collections import deque
from multiprocessing import Event
from typing import Optional, Deque, Tuple
# NUEVO: solo para type-hint y compat con colas hacia la UI (opcional)
from multiprocessing import Queue

from core.messages import FusionTickMsg

def load_cfg(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def level_to_label_avg(avg: float, low_thr: float, med_thr: float) -> str:
    if avg < low_thr:
        return "BAJO"
    if avg < med_thr:
        return "MEDIO"
    return "ALTO"

def level_to_label_majority(levels_03) -> str:
    # 0->BAJO, 1->MEDIO, 2/3->ALTO
    buckets = {"BAJO":0, "MEDIO":0, "ALTO":0}
    for lv in levels_03:
        if lv == 0: buckets["BAJO"] += 1
        elif lv == 1: buckets["MEDIO"] += 1
        else: buckets["ALTO"] += 1
    # desempate simple por orden ALTO>MEDIO>BAJO si hay igualdad
    return max(buckets.items(), key=lambda kv: (kv[1], ["BAJO","MEDIO","ALTO"].index(kv[0])))[0]

# NUEVO: qUI_stats es opcional; si viene, enviamos cada payload a la UI.
def run_reporter(cfg_path: str, in_q, stop_event: Event, qUI_stats: Optional[Queue] = None):
    cfg = load_cfg(cfg_path)
    rcfg = cfg.get("reporter", {}) or {}
    window_secs = float(rcfg.get("window_secs", 20))
    step_secs   = float(rcfg.get("step_secs", 5))
    method      = str(rcfg.get("method", "average")).lower()
    th_cfg      = rcfg.get("thresholds", {}) or {}
    low_thr     = float(th_cfg.get("low", 0.75))
    med_thr     = float(th_cfg.get("med", 1.75))
    out_path    = rcfg.get("output_jsonl", "resultados/estres_resumen.jsonl")
    print_console = bool(rcfg.get("print_console", True))

    # preparar archivo
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # buffer (ts, level)
    buf: Deque[Tuple[float, Optional[int]]] = deque()
    last_emit = 0.0

    print("[REP] Iniciado (ventana=%.1fs, paso=%.1fs, método=%s)" % (window_secs, step_secs, method), flush=True)

    # NUEVO: función local para emitir si toca (o forzar). Centraliza la lógica.
    def _emit(now: float, force: bool = False):
        nonlocal last_emit
        if not force and (now - last_emit) < step_secs:
            return  # aún no toca

        last_emit = now
        # niveles válidos (ignora None/inseguros)
        vals = [lv for (_ts, lv) in buf if lv is not None]
        n_vals = len(vals)
        ts_start = buf[0][0] if buf else now
        ts_end   = buf[-1][0] if buf else now

        if n_vals == 0:
            label = "INSEGURO"
            payload = {
                "ts_start": ts_start, "ts_end": ts_end,
                "n_ticks": len(buf), "n_valid": 0,
                "method": method, "label": label
            }
        else:
            if method == "majority":
                label = level_to_label_majority(vals)
                payload = {
                    "ts_start": ts_start, "ts_end": ts_end,
                    "n_ticks": len(buf), "n_valid": n_vals,
                    "method": method, "label": label
                }
            else:
                avg = float(sum(vals) / n_vals)
                label = level_to_label_avg(avg, low_thr, med_thr)
                payload = {
                    "ts_start": ts_start, "ts_end": ts_end,
                    "n_ticks": len(buf), "n_valid": n_vals,
                    "method": "average", "avg_level": avg, "label": label
                }

        # consola
        if print_console:
            ts_local = time.strftime("%H:%M:%S", time.localtime(ts_end))
            if "avg_level" in payload:
                print(f"[REP][{ts_local}] {payload['label']}  avg={payload['avg_level']:.2f}  n={payload['n_valid']}/{payload['n_ticks']}", flush=True)
            else:
                print(f"[REP][{ts_local}] {payload['label']}  n={payload['n_valid']}/{payload['n_ticks']}", flush=True)

        # archivo
        if out_path:
            try:
                with open(out_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            except Exception:
                print("[REP] Error escribiendo JSONL:\n" + traceback.format_exc(), flush=True)

        # NUEVO: enviar a la UI si hay cola
        if qUI_stats is not None:
            try:
                qUI_stats.put(payload)
            except Exception:
                pass

    try:
        while not stop_event.is_set():
            try:
                # poll no bloqueante
                if not getattr(in_q, "_reader").poll(0.05):
                    time.sleep(0.01)
                    # NUEVO: aunque no lleguen eventos, emitimos si ya pasó step_secs
                    _emit(time.time(), force=False)
                    continue
                msgd = in_q.get()
            except Exception:
                time.sleep(0.01)
                continue

            try:
                tick = FusionTickMsg(**msgd)
            except Exception:
                # mensaje no reconocible
                continue

            now = time.time()
            # agregar al buffer
            buf.append((tick.ts, tick.level))

            # podar ventana por tiempo
            tmin = now - window_secs
            while buf and (buf[0][0] < tmin):
                buf.popleft()

            # emitir si toca (por step)
            _emit(now, force=False)

    except KeyboardInterrupt:
        pass
    except Exception:
        print("[REP] Error inesperado:\n" + traceback.format_exc(), flush=True)
    finally:
        print("[REP] Saliendo…", flush=True)
