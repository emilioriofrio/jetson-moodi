# Variables de entorno 
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["TF_FORCE_GPU_ALLOW_GROWTH"] = "true"
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
os.environ.setdefault("KERAS_BACKEND", "tensorflow")

import time, yaml, queue, traceback
import numpy as np
from collections import deque
from multiprocessing import Queue, Event
from core.messages import FrameMsg, PredMsg

# MediaPipe / TensorFlow
import mediapipe as mp
import tensorflow as tf

# utilidades 
def load_cfg(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_class_names(path):
    if os.path.exists(path):
        idx2name = {}
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line: 
                    continue
                i, nm = line.split(",", 1)
                idx2name[int(i)] = nm.strip()
        return [idx2name[i] for i in sorted(idx2name.keys())]
    # fallback
    return ["clase_0","clase_1","clase_2","clase_3","clase_4","clase_5"]

#helpers
PL = mp.solutions.holistic.PoseLandmark

def _landmark_vec(landmarks, count, min_vis=0.5, use_visibility=True):
    vec = []
    if landmarks:
        for lm in landmarks.landmark:
            v = getattr(lm, "visibility", 1.0)
            if use_visibility and (v is not None) and (v < min_vis):
                vec.extend([0.0, 0.0, 0.0])
            else:
                vec.extend([lm.x, lm.y, lm.z])
    else:
        vec.extend([0.0, 0.0, 0.0] * count)
    return vec

def extract_keypoints(results, min_vis_pose=0.5):
    keypoints = []
    keypoints += _landmark_vec(results.pose_landmarks, 33, min_vis_pose, use_visibility=True)
    keypoints += _landmark_vec(results.left_hand_landmarks, 21, 0.0, use_visibility=False)
    keypoints += _landmark_vec(results.right_hand_landmarks, 21, 0.0, use_visibility=False)
    return keypoints  # len 225

def normalize_by_shoulders(vec225):
    arr = np.array(vec225, dtype=np.float32).reshape(-1, 3)  # (75,3)
    pose = arr[:33]
    li = int(PL.LEFT_SHOULDER.value); ri = int(PL.RIGHT_SHOULDER.value)
    L, R = pose[li], pose[ri]
    if np.allclose(L, 0.0, atol=1e-8) or np.allclose(R, 0.0, atol=1e-8):
        return arr.reshape(-1)
    center = (L + R) / 2.0
    dist = np.linalg.norm(L - R)
    if not np.isfinite(dist) or dist < 1e-6:
        return arr.reshape(-1)
    arr = (arr - center) / dist
    return arr.reshape(-1)

def quality_score_pose(results, thr=0.5):
    """Fracción de landmarks de pose con visibilidad >= thr."""
    if not results.pose_landmarks:
        return 0.0
    vis = []
    for lm in results.pose_landmarks.landmark:
        v = getattr(lm, "visibility", 0.0) or 0.0
        vis.append(1.0 if v >= thr else 0.0)
    return float(np.mean(vis)) if vis else 0.0

# Worker B
def run_worker_B(cfg_path: str, in_q: Queue, out_q: Queue, stop_event: Event):
    cfg = load_cfg(cfg_path)
    tick = cfg["tick_size"]

    # __file__ = sistem_IA/modules/mod_b.py  → dirname x3 = modelo_IA
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    bcfg = cfg.get("modulo_b", {})
    MODEL_PATH = os.path.join(BASE_DIR, bcfg.get("model_path", "resultados_modb_v3/modelo_modb_v3.keras"))
    LABELS_MAP_PATH = os.path.join(BASE_DIR, bcfg.get("labels_map_path", "dataset_vectores_v3/labels_map_v3.txt"))

    N_FRAMES       = int(bcfg.get("N_FRAMES", 10))
    SMOOTH_WINDOW  = int(bcfg.get("SMOOTH_WINDOW", 8))
    PRED_THRESH    = float(bcfg.get("PRED_THRESH", 0.55))
    QUALITY_THR    = float(bcfg.get("quality_pose_thr", 0.35))
    STALE_FRAMES   = int(bcfg.get("stale_frames", 20))
    VIS_THR_POSE   = 0.5  

    print(f"[B] Cargando modelo: {MODEL_PATH}", flush=True)
    print(f"[B] Labels map:     {LABELS_MAP_PATH}", flush=True)

    # CARGA MODELO
    model = None
    load_errors = []
    try:
        model = tf.keras.models.load_model(MODEL_PATH, compile=False)
    except Exception as e:
        load_errors.append(("tf.keras.load_model", str(e)))
        model = None
    if model is None:
        try:
            from tensorflow import compat as tfc
            model = tfc.v1.keras.models.load_model(MODEL_PATH, compile=False)
        except Exception as e:
            load_errors.append(("tf.compat.v1.keras.load_model", str(e)))
            model = None
    if model is None:
        try:
            import keras
            model = keras.saving.load_model(MODEL_PATH, compile=False, safe_mode=False)
        except Exception as e:
            load_errors.append(("keras.saving.load_model(safe_mode=False)", str(e)))
            model = None
    if model is None:
        print("[B] Error cargando el modelo por todos los métodos:", flush=True)
        for where, err in load_errors:
            print(f"   - {where}: {err}", flush=True)
        return

    try:
        print("[B] input_shape:", getattr(model, "input_shape", None), flush=True)
    except Exception:
        pass

    CLASS_NAMES = load_class_names(LABELS_MAP_PATH)

    mp_holistic = mp.solutions.holistic

    # Estado
    seq = deque(maxlen=N_FRAMES)
    probs_smooth = deque(maxlen=SMOOTH_WINDOW)
    last_pred_label = "inseguro"
    last_pred_conf = 0.0
    last_pred_frame = -9999

    print("[B] Iniciado (MediaPipe + LSTM).", flush=True)
    holistic = mp_holistic.Holistic(static_image_mode=False)
    try:
        while not stop_event.is_set():
            try:
                if not getattr(in_q, "_reader").poll(0.02):
                    time.sleep(0.01)
                    continue
                msg = in_q.get()
            except Exception:
                time.sleep(0.01)
                continue
            except Exception:
                print("[B] Error inesperado al leer cola:\n" + traceback.format_exc(), flush=True)
                continue

            frame_idx = msg.frame_idx
            frame_bgr = msg.frame
            frame_rgb = frame_bgr[:, :, ::-1]

            # MediaPipe por frame 
            results = holistic.process(frame_rgb)

            # Calidad actual
            qpose = quality_score_pose(results, thr=VIS_THR_POSE)

            # Extraer keypoints y normalizar
            kps = extract_keypoints(results, min_vis_pose=VIS_THR_POSE)
            kps = normalize_by_shoulders(kps)
            seq.append(kps)

            # Defaults de salida
            pred_label = "inseguro"
            pred_conf = 0.0
            present = qpose >= QUALITY_THR
            quality_out = float(np.clip(qpose, 0.0, 1.0))
            meta_out = {}

            # tick
            if (frame_idx % tick == 0) and (len(seq) == N_FRAMES):
                x = np.array(seq, dtype=np.float32).reshape(1, N_FRAMES, 225)
                probs = model.predict(x, verbose=0)[0]
                probs_smooth.append(probs)
                probs_avg = np.mean(probs_smooth, axis=0)

                idx = int(np.argmax(probs_avg))
                cand_conf = float(probs_avg[idx])
                cand_label = CLASS_NAMES[idx] if cand_conf >= PRED_THRESH else "inseguro"

                # actualizar last
                last_pred_label = cand_label
                last_pred_conf = cand_conf
                last_pred_frame = frame_idx

                # Log útil
                print(f"[B][{frame_idx}] {last_pred_label} ({last_pred_conf:.2f})  | quality={quality_out:.2f}", flush=True)

            # salida por tick
            if frame_idx % tick == 0:
                # 1) Quality override
                if qpose < QUALITY_THR:
                    pred_label = "inseguro"
                    pred_conf = 0.0
                    present = False
                else:
                    # 2) Staleness
                    if (frame_idx - last_pred_frame) > STALE_FRAMES:
                        pred_label = "inseguro"
                        pred_conf = 0.0
                        present = False
                    else:
                        # 3) Último resultado
                        pred_label = last_pred_label
                        pred_conf = last_pred_conf

                meta_out = {
                    "class_names": CLASS_NAMES,
                    "last_pred_frame": int(last_pred_frame)
                }

                # Publicar este tick
                pred = PredMsg(
                    module="B",
                    frame_idx=frame_idx,
                    ts=time.time(),
                    label=pred_label,
                    conf=float(np.clip(pred_conf, 0.0, 1.0)),
                    quality=quality_out,
                    present=present,
                    meta=meta_out
                )
                try:
                    out_q.put(pred.to_dict())
                except queue.Full:
                    pass

    except KeyboardInterrupt:
        pass
    except Exception:
        print("[B] Error inesperado fuera del bucle:\n" + traceback.format_exc(), flush=True)
    finally:
        try:
            holistic.close()
        except Exception:
            pass
        print("[B] Saliendo…", flush=True)
