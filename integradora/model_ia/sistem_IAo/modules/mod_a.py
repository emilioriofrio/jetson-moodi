# modules/mod_a.py
# -*- coding: utf-8 -*-
"""
Worker A (Emociones → Estrés) para Jetson:
- Detección de rostro: MTCNN (principal) y MediaPipe (fallback).
- Ensamble de emoción (DeepFace) sobre ROI y su flip horizontal.
- Lógica temporal: EMA + histéresis + penalización por cambio de valencia.
- Tracking de ROI por N ticks para acelerar.
- Compat DeepFace (build antigua): NO usar 'models', 'prog_bar', 'silent'.
- Usar siempre detector_backend='skip' en analyze() sobre la ROI (evita Haarcascade).
"""

import os, sys, time, yaml, queue, traceback, importlib, types
import numpy as np
from multiprocessing import Queue, Event
from core.messages import PredMsg
import cv2

# --- Entorno TF/Keras para este worker ---
os.environ.setdefault("KERAS_BACKEND", "tensorflow")
os.environ.setdefault("TF_USE_LEGACY_KERAS", "0")
os.environ.setdefault("TF_TRT_DISABLE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")

# ---------------- utilidades ----------------
def load_cfg(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def clasificar_estres(emocion: str) -> str:
    if emocion in ["happy", "neutral"]:
        return "BAJO"
    elif emocion in ["surprise", "fear"]:
        return "MEDIO"
    elif emocion in ["angry", "sad", "disgust"]:
        return "ALTO"
    else:
        return "inseguro"

def traducir_emocion(emo: str) -> str:
    return {
        "angry": "Enfado",
        "disgust": "Asco",
        "fear": "Miedo",
        "happy": "Alegría",
        "sad": "Tristeza",
        "surprise": "Sorpresa",
        "neutral": "Neutro"
    }.get(emo, emo)

# --------- SHIM tf_keras para DeepFace (TF 2.16 + Keras 3) ----------
def _ensure_tf_keras_shim():
    try:
        k = importlib.import_module("keras")
    except Exception:
        k = importlib.import_module("tensorflow.keras")

    pkg = sys.modules.get("tf_keras")
    if not pkg or not hasattr(pkg, "__path__"):
        pkg = types.ModuleType("tf_keras"); pkg.__path__ = []
        sys.modules["tf_keras"] = pkg

    setattr(pkg, "__version__", getattr(k, "__version__", "3-shim"))
    setattr(pkg, "__file__",    getattr(k, "__file__", ""))
    setattr(pkg, "__doc__",     getattr(k, "__doc__", ""))

    sys.modules["tf_keras.keras"] = k
    pkg.keras = k

    api_pkg = types.ModuleType("tf_keras.api"); api_pkg.__path__ = []
    v2_pkg  = types.ModuleType("tf_keras.api._v2"); v2_pkg.__path__ = []
    sys.modules["tf_keras.api"] = api_pkg
    sys.modules["tf_keras.api._v2"] = v2_pkg

    k_api = types.ModuleType("tf_keras.api._v2.keras")
    for name in ("__version__", "__doc__"):
        if hasattr(k, name): setattr(k_api, name, getattr(k, name))
    sys.modules["tf_keras.api._v2.keras"] = k_api

    def _map(name, real):
        try:
            m = importlib.import_module(real)
            sys.modules[f"tf_keras.api._v2.keras.{name}"] = m
            setattr(k_api, name, m)
        except Exception:
            pass

    mapping = {
        "activations":"keras.activations","applications":"keras.applications",
        "backend":"keras.backend","callbacks":"keras.callbacks","constraints":"keras.constraints",
        "datasets":"keras.datasets","initializers":"keras.initializers","layers":"keras.layers",
        "losses":"keras.losses","metrics":"keras.metrics","mixed_precision":"keras.mixed_precision",
        "models":"keras.models","optimizers":"keras.optimizers","regularizers":"keras.regularizers",
        "saving":"keras.saving","utils":"keras.utils",
    }
    for n,m in mapping.items(): _map(n,m)

    # dtensor stub
    dtensor_stub = types.ModuleType("tf_keras.api._v2.keras.dtensor")
    sys.modules["tf_keras.api._v2.keras.dtensor"] = dtensor_stub
    setattr(k_api, "dtensor", dtensor_stub)

    # preprocessing.image → utilidades de keras.utils
    pre_pkg = types.ModuleType("tf_keras.api._v2.keras.preprocessing"); pre_pkg.__path__ = []
    img_mod = types.ModuleType("tf_keras.api._v2.keras.preprocessing.image")
    try:
        u = importlib.import_module("keras.utils")
        for fn in ("load_img","img_to_array","array_to_img","save_img"):
            if hasattr(u, fn): setattr(img_mod, fn, getattr(u, fn))
    except Exception:
        pass
    sys.modules["tf_keras.api._v2.keras.preprocessing"] = pre_pkg
    sys.modules["tf_keras.api._v2.keras.preprocessing.image"] = img_mod
    setattr(pre_pkg, "image", img_mod)
    setattr(k_api, "preprocessing", pre_pkg)

    # __internal__ → backend
    try:
        kb = importlib.import_module("keras.backend")
    except Exception:
        kb = types.ModuleType("backend")
    internal_api = types.ModuleType("tf_keras.api._v2.keras.__internal__"); internal_api.__path__ = []
    sys.modules["tf_keras.api._v2.keras.__internal__"] = internal_api
    sys.modules["tf_keras.api._v2.keras.__internal__.backend"] = kb
    setattr(internal_api, "backend", kb)
    setattr(k_api, "__internal__", internal_api)
    sys.modules["tf_keras.api._v2.keras.internal"] = internal_api
    setattr(k_api, "internal", internal_api)

    # raíz interna
    internal_root = types.ModuleType("tf_keras.__internal__"); internal_root.__path__ = []
    sys.modules["tf_keras.__internal__"] = internal_root
    sys.modules["tf_keras.__internal__.backend"] = kb
    setattr(internal_root, "backend", kb)
    setattr(pkg, "__internal__", internal_root)

    # placeholder src
    src_pkg = types.ModuleType("tf_keras.src"); src_pkg.__path__ = []
    sys.modules["tf_keras.src"] = src_pkg
    pkg.src = src_pkg

def _patch_deepface_validation():
    try:
        pkg = importlib.import_module("deepface.commons.package_utils")
        def _ok(*a, **k): return True
        setattr(pkg, "validate_for_keras3", _ok)
    except Exception:
        pass

def _import_deepface_safely():
    _ensure_tf_keras_shim()
    _patch_deepface_validation()
    from deepface import DeepFace
    return DeepFace

# ------------- helpers de preprocesado y ensamble -------------
def _clahe_bgr(img_bgr):
    ycrcb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = cv2.split(ycrcb)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    y = clahe.apply(y)
    ycrcb = cv2.merge([y, cr, cb])
    return cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)

def _avg_dicts(d1, d2):
    ks = set(d1.keys()) | set(d2.keys())
    return {k: (float(d1.get(k,0.0)) + float(d2.get(k,0.0))) / 2.0 for k in ks}

def _analyze_emotion_ensemble(DeepFace, roi_bgr):
    """
    Analiza emoción sobre ROI y su flip.
    MUY IMPORTANTE: detector_backend='skip' + enforce_detection=False
    para evitar cualquier intento de usar Haarcascade en tu Jetson.
    """
    t0 = time.time()

    # PASE 1: ROI tal cual
    res1 = DeepFace.analyze(
        img_path=roi_bgr,
        actions=['emotion'],
        detector_backend='skip',
        enforce_detection=False
    )
    emo1 = (res1[0] if isinstance(res1, list) else res1)['emotion']

    # PASE 2: ROI espejada
    roi_flip = cv2.flip(roi_bgr, 1)
    res2 = DeepFace.analyze(
        img_path=roi_flip,
        actions=['emotion'],
        detector_backend='skip',
        enforce_detection=False
    )
    emo2 = (res2[0] if isinstance(res2, list) else res2)['emotion']

    t_ms = (time.time() - t0) * 1000.0
    return _avg_dicts(emo1, emo2), t_ms

# ------------- MTCNN directo (más control que vía DeepFace) -------------
def _mtcnn_detect(detector, frame_bgr, max_side, roi=None):
    """
    detector: mtcnn.MTCNN instanciado
    frame_bgr: frame original en BGR
    max_side: Redimensiona el lado mayor antes de detectar
    roi: (x0,y0,x1,y1) si quieres buscar sólo en una región
    Devuelve: (x,y,w,h, t_ms) en coords del frame original o None
    """
    H, W = frame_bgr.shape[:2]
    if roi is not None:
        x0,y0,x1,y1 = roi
        x0 = max(0, x0); y0 = max(0, y0); x1 = min(W, x1); y1 = min(H, y1)
        patch = frame_bgr[y0:y1, x0:x1]
        if patch.size == 0:
            return None
        base_x, base_y = x0, y0
    else:
        patch = frame_bgr
        base_x, base_y = 0, 0

    h0, w0 = patch.shape[:2]
    scale = 1.0
    if max(h0, w0) > max_side:
        scale = max_side / float(max(h0, w0))
        patch = cv2.resize(patch, (int(w0*scale), int(h0*scale)), interpolation=cv2.INTER_AREA)

    rgb = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)
    t0 = time.time()
    dets = detector.detect_faces(rgb)
    t_ms = (time.time() - t0) * 1000.0
    if not dets:
        return None

    # elegir la caja más grande
    best = None; bestA = 0
    for d in dets:
        x, y, w, h = d.get("box", [0,0,0,0])
        x = max(0, int(x)); y = max(0, int(y))
        w = max(0, min(int(w), rgb.shape[1]-x))
        h = max(0, min(int(h), rgb.shape[0]-y))
        A = w*h
        if A > bestA:
            bestA = A; best = (x,y,w,h)
    if not best:
        return None

    x,y,w,h = best
    # remap: escala + offset ROI
    x = int(x/scale) + base_x
    y = int(y/scale) + base_y
    w = int(w/scale)
    h = int(h/scale)

    # clamp a límites del frame
    x = max(0, min(x, W-1)); y = max(0, min(y, H-1))
    w = max(0, min(w, W - x)); h = max(0, min(h, H - y))
    if w == 0 or h == 0:
        return None
    return (x, y, w, h, t_ms)

# ---------------- worker A ----------------
def run_worker_A(cfg_path: str, in_q: Queue, out_q: Queue, stop_event: Event):
    cfg = load_cfg(cfg_path)
    tick = int(cfg.get("tick_size", 10))
    face_timeout_ticks = int(cfg.get("thresholds", {}).get("face_timeout_ticks", 3))

    # Hiperparámetros
    EMA_ALPHA = float(cfg.get("modA_ema_alpha", 0.45))
    HYSTERESIS_MARGIN = float(cfg.get("modA_hysteresis_margin", 3.0))
    MIN_GAP_SWITCH = float(cfg.get("modA_min_gap_for_switch", 7.0))
    VALENCE_EXTRA_GAP = float(cfg.get("modA_valence_extra_gap", 5.0))

    # MTCNN tuning
    MTCNN_MAX_SIDE = int(cfg.get("modA_mtcnn_max_side", 640))
    MTCNN_STEPS = cfg.get("modA_mtcnn_steps", [0.8, 0.9, 0.95])
    MTCNN_MIN_FACE = int(cfg.get("modA_mtcnn_min_face", 40))

    # ROI tracking
    ROI_EXPAND = float(cfg.get("modA_roi_expand", 1.6))
    ROI_ONLY_TICKS = int(cfg.get("modA_roi_track_ticks", 5))  # tras detectar rostro, n ticks buscando en ROI

    # Orden de backends
    cfg_order = cfg.get("modA_backends_order", ["mtcnn", "mediapipe"])
    tmp = [str(b).lower().strip() for b in cfg_order]
    order = []
    for b in tmp:
        if b in ("mtcnn", "mediapipe") and b not in order:
            order.append(b)
    if not order:
        order = ["mtcnn", "mediapipe"]

    emo_keys = ["angry","disgust","fear","happy","sad","surprise","neutral"]
    emo_smooth = {k: 0.0 for k in emo_keys}
    dominant_display = "neutral"
    missing_ticks = 0

    # Import DeepFace seguro
    try:
        DeepFace = _import_deepface_safely()
    except Exception as e:
        print("[A] ERROR importando DeepFace:", repr(e), flush=True)
        return

    # TF info
    try:
        import tensorflow as tf
        print(f"[A] TF {tf.__version__} | GPUs: {tf.config.list_physical_devices('GPU')}", flush=True)
    except Exception:
        pass

    # Warm-up (ROI dummy). Usar skip para no tocar Haarcascade.
    try:
        dummy = np.full((224,224,3), 128, dtype=np.uint8)
        _ = DeepFace.analyze(
            img_path=dummy,
            actions=['emotion'],
            detector_backend='skip',
            enforce_detection=False
        )
    except Exception:
        pass

    # Instancia de MTCNN (directo)
    mtcnn = None
    if "mtcnn" in order:
        try:
            from mtcnn import MTCNN
            mtcnn = MTCNN(steps_threshold=MTCNN_STEPS, min_face_size=MTCNN_MIN_FACE, scale_factor=0.709)
        except Exception as e:
            print("[A] WARN: no se pudo instanciar MTCNN:", repr(e), flush=True)
            order = [b for b in order if b != "mtcnn"]

    print(f"[A] Iniciado (DeepFace) | backends={order}", flush=True)

    # lógica temporal (EMA + histéresis + valencia)
    valence = {"happy":+1.0,"surprise":+0.5,"neutral":0.0,"disgust":-0.7,"fear":-0.6,"angry":-1.0,"sad":-1.0}
    def _apply_temporal_logic(emociones: dict):
        nonlocal dominant_display
        for k in emo_keys:
            emo_smooth[k] = EMA_ALPHA * emo_smooth[k] + (1.0 - EMA_ALPHA) * float(emociones.get(k, 0.0))

        sorted_k = sorted(emo_keys, key=lambda k: emo_smooth[k], reverse=True)
        top1, top2 = sorted_k[0], sorted_k[1]
        v1, v2 = emo_smooth[top1], emo_smooth[top2]
        gap = v1 - v2

        extra_gap = 0.0
        if np.sign(valence.get(top1,0.0)) != np.sign(valence.get(dominant_display,0.0)):
            extra_gap = VALENCE_EXTRA_GAP

        candidate = dominant_display
        if gap >= (MIN_GAP_SWITCH + extra_gap):
            candidate = top1

        if dominant_display not in emo_smooth or (emo_smooth[candidate] - emo_smooth[dominant_display]) >= HYSTERESIS_MARGIN:
            dominant_display = candidate

        conf = float(np.clip(emo_smooth[dominant_display] / 100.0, 0.0, 1.0))
        return dominant_display, conf

    last_box = None       # (x,y,w,h) en coords del frame completo
    last_roi_ticks = 0    # cuantos ticks nos quedan de "ROI-only"

    # --------------- bucle principal ---------------
    try:
        while not stop_event.is_set():
            # lectura no bloqueante de la cola
            try:
                if not getattr(in_q, "_reader").poll(0.02):
                    time.sleep(0.01)
                    continue
                msg = in_q.get()
            except Exception:
                time.sleep(0.01)
                continue

            idx = msg["frame_idx"] if isinstance(msg, dict) else msg.frame_idx
            frame = msg["frame"] if isinstance(msg, dict) else msg.frame  # BGR
            if idx % tick != 0:
                continue

            H, W = frame.shape[:2]
            present = False
            label_out, conf_out, quality_out = "inseguro", 0.0, 0.0
            meta_out = {}
            used_backend = None

            for backend in order:
                try:
                    # ---------------------------------------------------------
                    # BACKEND: MTCNN (directo)
                    # ---------------------------------------------------------
                    if backend == "mtcnn" and mtcnn is not None:
                        # Inicializa SIEMPRE (evita UnboundLocalError)
                        tried_roi = False
                        x0 = y0 = w0 = h0 = None
                        t_det = 0.0

                        # 1) ROI si hay last_box reciente
                        if last_box is not None and last_roi_ticks > 0:
                            tried_roi = True
                            x, y, w, h = last_box
                            cx, cy = x + w // 2, y + h // 2
                            half_w = int(w * ROI_EXPAND * 0.5)
                            half_h = int(h * ROI_EXPAND * 0.5)
                            rx0 = max(0, cx - half_w); ry0 = max(0, cy - half_h)
                            rx1 = min(W, cx + half_w); ry1 = min(H, cy + half_h)

                            hit = _mtcnn_detect(mtcnn, frame, MTCNN_MAX_SIDE, roi=(rx0,ry0,rx1,ry1))
                            if hit:
                                x0,y0,w0,h0,t_det = hit
                                used_backend = "mtcnn-roi"
                            else:
                                # Forzamos intento full-frame
                                x0 = y0 = w0 = h0 = None
                                t_det = 0.0
                                used_backend = None

                        # 2) Full-frame si no hay ROI o ROI falló
                        if (not tried_roi) or (w0 is None or h0 is None or w0 <= 0 or h0 <= 0):
                            hit = _mtcnn_detect(mtcnn, frame, MTCNN_MAX_SIDE, roi=None)
                            if hit:
                                x0,y0,w0,h0,t_det = hit
                                used_backend = "mtcnn"
                            else:
                                used_backend = None  # pasar a siguiente backend

                        # 3) Si hay bbox válida → emoción
                        if used_backend is not None and w0 and h0:
                            x1, y1 = x0 + w0, y0 + h0
                            roi = frame[y0:y1, x0:x1]
                            if roi.size > 0:
                                roi_proc = _clahe_bgr(roi)
                                emociones, t_emo = _analyze_emotion_ensemble(DeepFace, roi_proc)

                                face_area = w0 * h0
                                quality_out = float(np.clip((face_area / float(W*H)) * 4.0, 0.0, 1.0))

                                dom, conf_out = _apply_temporal_logic(emociones)
                                label_out = clasificar_estres(dom)
                                present = True; missing_ticks = 0
                                last_box = (x0,y0,w0,h0)
                                last_roi_ticks = ROI_ONLY_TICKS  # renuevo ventana ROI

                                print(f"[A][{idx}] Cara OK | be={used_backend} | bbox=({x0},{y0},{w0},{h0}) "
                                      f"ratio={face_area/(W*H):.3f} | Dominante: {traducir_emocion(dom)} "
                                      f"({emo_smooth[dom]:.1f}%) | Estrés: {label_out} "
                                      f"| t_det={t_det:.1f}ms t_emo={t_emo:.1f}ms", flush=True)

                                meta_out = {
                                    "dominant_emotion": dom,
                                    "emotions": {k: round(v, 2) for k, v in emo_smooth.items()},
                                    "region": {"x": int(x0), "y": int(y0), "w": int(w0), "h": int(h0)},
                                    "backend": used_backend
                                }
                                break  # ya detectamos con mtcnn

                    # ---------------------------------------------------------
                    # BACKEND: MediaPipe vía DeepFace.extract_faces (fallback)
                    # ---------------------------------------------------------
                    elif backend == "mediapipe":
                        t0 = time.time()
                        faces = DeepFace.extract_faces(
                            img_path=frame, detector_backend='mediapipe',
                            enforce_detection=False, align=True
                        )
                        t_det = (time.time() - t0) * 1000.0

                        best=None; bestA=0
                        for f in faces or []:
                            fa = f.get("facial_area") or {}
                            x, y, w, h = int(fa.get("x",0)), int(fa.get("y",0)), int(fa.get("w",0)), int(fa.get("h",0))
                            A = max(0,w) * max(0,h)
                            if A > bestA: bestA=A; best=(x,y,w,h)
                        if not best:
                            continue
                        x0,y0,w0,h0 = best
                        x1,y1 = x0+w0, y0+h0
                        roi = frame[y0:y1, x0:x1]
                        if roi.size == 0:
                            continue

                        roi_proc = _clahe_bgr(roi)
                        emociones, t_emo = _analyze_emotion_ensemble(DeepFace, roi_proc)

                        face_area = w0*h0
                        quality_out = float(np.clip((face_area / float(W*H)) * 4.0, 0.0, 1.0))

                        dom, conf_out = _apply_temporal_logic(emociones)
                        label_out = clasificar_estres(dom)
                        present = True; missing_ticks = 0
                        last_box = (x0,y0,w0,h0)
                        last_roi_ticks = ROI_ONLY_TICKS
                        used_backend = "mediapipe"

                        print(f"[A][{idx}] Cara OK | be=mediapipe | bbox=({x0},{y0},{w0},{h0}) "
                              f"ratio={face_area/(W*H):.3f} | Dominante: {traducir_emocion(dom)} "
                              f"({emo_smooth[dom]:.1f}%) | Estrés: {label_out} "
                              f"| t_det={t_det:.1f}ms t_emo={t_emo:.1f}ms", flush=True)

                        meta_out = {
                            "dominant_emotion": dom,
                            "emotions": {k: round(v, 2) for k, v in emo_smooth.items()},
                            "region": {"x": int(x0), "y": int(y0), "w": int(w0), "h": int(h0)},
                            "backend": "mediapipe"
                        }
                        break

                except Exception as e:
                    print(f"[A][{idx}] EXC({backend}): {repr(e)}", flush=True)
                    continue

            # Sin rostro
            if not present:
                missing_ticks += 1
                last_roi_ticks = max(0, last_roi_ticks - 1)
                label_out = "inseguro"; conf_out = 0.0; quality_out = 0.0
                print(f"[A][{idx}] Sin rostro ({missing_ticks}/{face_timeout_ticks})", flush=True)
                if missing_ticks >= face_timeout_ticks:
                    last_box = None  # olvidar ROI si perdimos demasiado

            # Publicar predicción
            pred = PredMsg(
                module="A",
                frame_idx=idx,
                ts=time.time(),
                label=label_out,
                conf=conf_out,
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
        print("[A] Error inesperado fuera del bucle:\n" + traceback.format_exc(), flush=True)
    finally:
        print("[A] Saliendo…", flush=True)
