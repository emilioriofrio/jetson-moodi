import time, csv, os
from multiprocessing import Event
from core.messages import PredMsg

B_ALIAS = {
    "neutral": "BAJO",
    "leve": "MEDIO",
    "repetitivo": "MEDIO",
    "agitacion": "ALTO",
}
C_ALIAS = {
    "CALMA": "BAJO",
    "LEVE": "MEDIO",
    "REPETITIVO": "MEDIO",
    "AGITACIÓN": "ALTO",
    "AGITACION": "ALTO",
}

def summarize_modules(mods: dict):
    def fmt(mkey):
        m = mods.get(mkey, {})
        return f"{m.get('label','?')} (c={m.get('conf',0):.2f},q={m.get('quality',0):.2f})"
    return f"A={fmt('A')}  B={fmt('B')}  C={fmt('C')}"

def compute_custom_level(mods: dict):
    votes = []
    A = (mods.get("A",{}).get("label","inseguro") or "").upper()
    if A in ("BAJO","MEDIO","ALTO"): votes.append(A)
    B = (mods.get("B",{}).get("label","inseguro") or "").lower()
    if B in B_ALIAS: votes.append(B_ALIAS[B])
    C = (mods.get("C",{}).get("label","inseguro") or "").upper()
    if C in C_ALIAS: votes.append(C_ALIAS[C])

    if votes.count("ALTO") >= 2: return "ALTO"
    if ("ALTO" in votes) or ("MEDIO" in votes): return "MEDIO"
    if votes: return "BAJO"
    return "INSEGURO"

def run_supervisor(shared_state, stop_event: Event, *,
                   use_fused: bool = True,
                   log_csv: bool = True,
                   csv_path: str = "resultados/supervisor_log.csv",
                   poll_sec: float = 0.2):
    print("[SUP] Iniciado (Supervisor).", flush=True)
    last_seen_tick = -1
    if log_csv:
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    csv_file = None
    csv_writer = None
    if log_csv:
        csv_file = open(csv_path, "a", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        if csv_file.tell() == 0:
            csv_writer.writerow([
                "ts","tick",
                "GLOBAL_label","GLOBAL_level",
                "A_label","A_conf","A_quality",
                "B_label","B_conf","B_quality",
                "C_label","C_conf","C_quality"
            ])
            csv_file.flush()

    try:
        while not stop_event.is_set():
            try:
                tick = int(shared_state.get("tick", -1))
                mods = dict(shared_state.get("modules", {}))
                fused = dict(shared_state.get("fused", {}))
            except Exception:
                time.sleep(poll_sec)
                continue

            if tick != -1 and tick != last_seen_tick:
                last_seen_tick = tick

                if use_fused:
                    g_label = fused.get("label","INSEGURO")
                    g_level = fused.get("level","inseguro")
                else:
                    g_label = compute_custom_level(mods)
                    g_level = {"BAJO":0,"MEDIO":1,"ALTO":2}.get(g_label,"inseguro")

                details = summarize_modules(mods)
                print(f"[SUP][{tick}] GLOBAL={g_label}  | {details}", flush=True)

                if log_csv and csv_writer:
                    ts = fused.get("ts", time.time())
                    csv_writer.writerow([
                        ts, tick,
                        g_label, g_level,
                        mods.get("A",{}).get("label",""), mods.get("A",{}).get("conf",0.0), mods.get("A",{}).get("quality",0.0),
                        mods.get("B",{}).get("label",""), mods.get("B",{}).get("conf",0.0), mods.get("B",{}).get("quality",0.0),
                        mods.get("C",{}).get("label",""), mods.get("C",{}).get("conf",0.0), mods.get("C",{}).get("quality",0.0),
                    ])
                    csv_file.flush()

            time.sleep(poll_sec)

    except KeyboardInterrupt:
        pass
    finally:
        if csv_file:
            csv_file.close()
        print("[SUP] Saliendo…", flush=True)
