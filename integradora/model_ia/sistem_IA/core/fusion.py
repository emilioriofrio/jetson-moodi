import time, yaml
import traceback
from multiprocessing import Queue, Event
from typing import Optional, Dict
from core.messages import PredMsg, FusionTickMsg

def load_cfg(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def _norm(s: str) -> str:
    return str(s or "").strip().lower()

def run_fusion(cfg_path: str, pred_q: Queue, out_q: Queue, stop_event: Event):
    cfg = load_cfg(cfg_path)

    weights: Dict[str, float] = cfg.get("weights", {})
    fus_cfg = cfg.get("fusion", {}) or {}
    cool_up   = int(fus_cfg.get("cooldown_up",   3))
    cool_down = int(fus_cfg.get("cooldown_down", 1))
    required  = int(fus_cfg.get("required_modules", 3))
    debug     = bool(fus_cfg.get("debug", False))

    maps_cfg = fus_cfg.get("maps", {}) or {}

    map_A_default = {"bajo": 0, "medio": 1, "alto": 2}
    map_B_default = {
        "neutral": 0,
        "otros": 0,
        "dedos_boca": 1,
        "juegos_manos": 2,
        "manos_cabeza": 3,
        "manos_rostro": 3,
        "leve": 0, "repetitivo": 2, "agitacion": 3, "agitación": 3
    }
    map_C_default = {
        "calma": 0, "leve": 0, "repetitivo": 2, "agitación": 3, "agitacion": 3, "intenso": 3
    }

    map_A = { _norm(k): v for k, v in (maps_cfg.get("A", map_A_default)).items() }
    map_B = { _norm(k): v for k, v in (maps_cfg.get("B", map_B_default)).items() }
    map_C = { _norm(k): v for k, v in (maps_cfg.get("C", map_C_default)).items() }

    def map_to_scale(module: str, label: str) -> Optional[int]:
        if _norm(label) == "inseguro":
            return None
        m = (module or "").upper()
        lab = _norm(label)
        if m == "A":
            return map_A.get(lab)
        if m == "B":
            return map_B.get(lab)
        if m == "C":
            return map_C.get(lab)
        return None

    last_level = None
    up_cnt = down_cnt = 0

    current_idx = -1
    bucket = {}
    emitted_for_idx = False

    print("[FUS] Iniciado.")
    try:
        while not stop_event.is_set():
            try:
                if not getattr(pred_q, "_reader").poll(0.02):
                    time.sleep(0.01)
                    continue
                pred_dict = pred_q.get()
            except Exception:
                time.sleep(0.01)
                continue

            try:
                pred = PredMsg(**pred_dict)
            except Exception:
                print("[FUS] Error construyendo PredMsg:\n" + traceback.format_exc())
                continue

            idx = pred.frame_idx

            if idx != current_idx:
                current_idx = idx
                bucket = {}
                emitted_for_idx = False

            bucket[pred.module] = pred

            if not emitted_for_idx and len(bucket) >= required:
                inseg = sum(1 for p in bucket.values() if _norm(p.label) == "inseguro")
                if inseg >= 2:
                    print(f"[FUS][{idx}] => INSEGURO")
                    last_level = "inseguro"
                    emitted_for_idx = True
                    # emitir al reporter
                    try:
                        out_q.put(FusionTickMsg(frame_idx=idx, ts=time.time(),
                                                level=None, state="inseguro", target=None).to_dict())
                    except Exception:
                        pass
                    continue

                score, total_w = 0.0, 0.0
                for m, p in bucket.items():
                    lvl = map_to_scale(m, p.label)
                    if lvl is None:
                        continue
                    q = max(0.0, min(1.0, float(p.quality or 0.0)))
                    w_eff = float(weights.get(m, 0.0)) * q
                    score += float(lvl) * w_eff
                    total_w += w_eff

                if total_w == 0.0:
                    print(f"[FUS][{idx}] => INSEGURO (sin pesos)")
                    last_level = "inseguro"
                    emitted_for_idx = True
                    try:
                        out_q.put(FusionTickMsg(frame_idx=idx, ts=time.time(),
                                                level=None, state="inseguro", target=None).to_dict())
                    except Exception:
                        pass
                    continue

                target = int(round(score / total_w))
                target = max(0, min(3, target))

                state = "init"
                if last_level in (None, "inseguro"):
                    last_level = target
                    print(f"[FUS][{idx}] => {target} (init)")
                    state = "init"
                else:
                    if isinstance(last_level, int):
                        if target > last_level:
                            up_cnt += 1; down_cnt = 0
                            if up_cnt >= cool_up:
                                last_level = target; up_cnt = 0
                                print(f"[FUS][{idx}] => {target} (sube)")
                                state = "sube"
                            else:
                                print(f"[FUS][{idx}] => {last_level} (espera subir {up_cnt}/{cool_up})")
                                state = "espera"
                        elif target < last_level:
                            down_cnt += 1; up_cnt = 0
                            if down_cnt >= cool_down:
                                last_level = target; down_cnt = 0
                                print(f"[FUS][{idx}] => {target} (baja)")
                                state = "baja"
                            else:
                                print(f"[FUS][{idx}] => {last_level} (espera bajar {down_cnt}/{cool_down})")
                                state = "espera"
                        else:
                            up_cnt = down_cnt = 0
                            print(f"[FUS][{idx}] => {last_level} (estable)")
                            state = "estable"
                    else:
                        last_level = target
                        print(f"[FUS][{idx}] => {target} (reset)")
                        state = "init"

                emitted_for_idx = True
                # emitir al reporter el nivel actual (last_level)
                try:
                    out_q.put(FusionTickMsg(frame_idx=idx, ts=time.time(),
                                            level=(last_level if isinstance(last_level, int) else None),
                                            state=("inseguro" if last_level == "inseguro" else state),
                                            target=target).to_dict())
                except Exception:
                    pass

    except KeyboardInterrupt:
        pass
    except Exception:
        print("[FUS] Error inesperado fuera del bucle:\n" + traceback.format_exc())
    finally:
        print("[FUS] Saliendo…")
