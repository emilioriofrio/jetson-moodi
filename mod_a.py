# modules/mod_a.py
import os, sys, time, yaml, queue, traceback, importlib, types
import numpy as np
from multiprocessing import Queue, Event
from core.messages import PredMsg
import cv2

# --- Entorno TF/Keras para este worker ---
os.environ.setdefault("KERAS_BACKEND", "tensorflow")
os.environ.setdefault("TF_USE_LEGACY_KERAS", "0")
os.environ.setdefault("TF_TRT_DISABLE", "1")   # silencia TF-TRT si no hay TensorRT
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")  # por si acaso

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
    """
    Construye un árbol 'tf_keras' compatible con TF 2.16 + Keras 3 para DeepFace 0.0.93.
    Provee:
      - tf_keras                  (paquete) con __version__/__file__/__doc__
      - tf_keras.keras            → alias a 'keras' (Keras 3)
      - tf_keras.api              (paquete)
      - tf_keras.api._v2          (paquete)
      - tf_keras.api._v2.keras    (módulo contenedor) con submódulos:
           activations, applications, backend, callbacks, constraints,
           datasets, initializers, layers, losses, metrics, mixed_precision,
           models, optimizers, regularizers, saving, utils,
           preprocessing.image (stub con load_img/img_to_array/array_to_img/save_img),
           dtensor (stub),
           __internal__.backend (alias a keras.backend)
           internal (alias a __internal__, por compat)
      - tf_keras.__internal__.backend (alias a keras.backend)
      - tf_keras.src              (paquete stub)
    """
    import importlib, sys, types

    # Keras 3 real
    try:
        k = importlib.import_module("keras")
    except Exception:
        k = importlib.import_module("tensorflow.keras")

    # Paquete base tf_keras
    pkg = sys.modules.get("tf_keras")
    if not pkg or not hasattr(pkg, "__path__"):
        pkg = types.ModuleType("tf_keras")
        pkg.__path__ = []
        sys.modules["tf_keras"] = pkg

    # AÑADIDO: exponer metadatos en la RAÍZ que DeepFace consulta
    setattr(pkg, "__version__", getattr(k, "__version__", "3-shim"))
    setattr(pkg, "__file__",    getattr(k, "__file__", ""))
    setattr(pkg, "__doc__",     getattr(k, "__doc__", ""))

    # tf_keras.keras → 'keras'
    sys.modules["tf_keras.keras"] = k
    pkg.keras = k

    # tf_keras.api / tf_keras.api._v2
    api_pkg = sys.modules.get("tf_keras.api")
    if not api_pkg:
        api_pkg = types.ModuleType("tf_keras.api"); api_pkg.__path__ = []
        sys.modules["tf_keras.api"] = api_pkg
    v2_pkg = sys.modules.get("tf_keras.api._v2")
    if not v2_pkg:
        v2_pkg = types.ModuleType("tf_keras.api._v2"); v2_pkg.__path__ = []
        sys.modules["tf_keras.api._v2"] = v2_pkg
    pkg.api = api_pkg

    # Contenedor tf_keras.api._v2.keras
    k_api = types.ModuleType("tf_keras.api._v2.keras")
    for name in ("__version__", "__doc__"):
        if hasattr(k, name):
            setattr(k_api, name, getattr(k, name))
    sys.modules["tf_keras.api._v2.keras"] = k_api

    # Helper para mapear submódulos reales de Keras 3
    def _map_attr(name: str, real_mod: str):
        try:
            m = importlib.import_module(real_mod)
            sys.modules[f"tf_keras.api._v2.keras.{name}"] = m
            setattr(k_api, name, m)
        except Exception:
            pass

    mapping = {
        "activations":     "keras.activations",
        "applications":    "keras.applications",
        "backend":         "keras.backend",
        "callbacks":       "keras.callbacks",
        "constraints":     "keras.constraints",
        "datasets":        "keras.datasets",
        "initializers":    "keras.initializers",
        "layers":          "keras.layers",
        "losses":          "keras.losses",
        "metrics":         "keras.metrics",
        "mixed_precision": "keras.mixed_precision",
        "models":          "keras.models",
        "optimizers":      "keras.optimizers",
        "regularizers":    "keras.regularizers",
        "saving":          "keras.saving",
        "utils":           "keras.utils",
    }
    for name, mod in mapping.items():
        _map_attr(name, mod)

    # dtensor (stub)
    dtensor_stub = types.ModuleType("tf_keras.api._v2.keras.dtensor")
    sys.modules["tf_keras.api._v2.keras.dtensor"] = dtensor_stub
    setattr(k_api, "dtensor", dtensor_stub)

    # preprocessing.image (stub que reexpone utilidades de keras.utils)
    pre_pkg = types.ModuleType("tf_keras.api._v2.keras.preprocessing"); pre_pkg.__path__ = []
    sys.modules["tf_keras.api._v2.keras.preprocessing"] = pre_pkg
    img_mod = types.ModuleType("tf_keras.api._v2.keras.preprocessing.image")
    try:
        u = importlib.import_module("keras.utils")
        for fn in ("load_img", "img_to_array", "array_to_img", "save_img"):
            if hasattr(u, fn):
                setattr(img_mod, fn, getattr(u, fn))
    except Exception:
        pass
    sys.modules["tf_keras.api._v2.keras.preprocessing.image"] = img_mod
    setattr(pre_pkg, "image", img_mod)
    setattr(k_api, "preprocessing", pre_pkg)

    # __internal__.backend en ambas rutas: api._v2.keras y raíz
    try:
        kb = importlib.import_module("keras.backend")
    except Exception:
        kb = types.ModuleType("backend")

    internal_api = types.ModuleType("tf_keras.api._v2.keras.__internal__"); internal_api.__path__ = []
    sys.modules["tf_keras.api._v2.keras.__internal__"] = internal_api
    sys.modules["tf_keras.api._v2.keras.__internal__.backend"] = kb
    setattr(internal_api, "backend", kb)
    setattr(k_api, "__internal__", internal_api)

    # AÑADIDO: alias 'internal' → '__internal__' (por si alguna lib lo usa sin guión bajo)
    sys.modules["tf_keras.api._v2.keras.internal"] = internal_api
    setattr(k_api, "internal", internal_api)

    internal_root = types.ModuleType("tf_keras.__internal__"); internal_root.__path__ = []
    sys.modules["tf_keras.__internal__"] = internal_root
    sys.modules["tf_keras.__internal__.backend"] = kb
    setattr(internal_root, "backend", kb)
    setattr(pkg, "__internal__", internal_root)

    # src stub
    src_pkg = types.ModuleType("tf_keras.src"); src_pkg.__path__ = []
    sys.modules["tf_keras.src"] = src_pkg
    pkg.src = src_pkg

def _patch_deepface_validation():
    """
    Parcha deepface.commons.package_utils.validate_for_keras3 para que no intente
    importar/verificar tf_keras real.
    """
    try:
        pkg = importlib.import_module("deepface.commons.package_utils")
        def _ok(*a, **k): return True
        setattr(pkg, "validate_for_keras3", _ok)
    except Exception:
        pass

def _import_deepface_safely():
    # 1) asegurar shim antes de que DeepFace toque tf_keras
    _ensure_tf_keras_shim()
    # 2) parchear validación
    _patch_deepface_validation()
    # 3) importar
    from deepface import DeepFace
    return DeepFace

# ---------------- worker A ----------------
def run_worker_A(cfg_path: str, in_q: Queue, out_q: Queue, stop_event: Event):
    cfg = load_cfg(cfg_path)
    tick = int(cfg.get("tick_size", 10))
    face_timeout_ticks = int(cfg.get("thresholds", {}).get("face_timeout_ticks", 3))

    # Hiperparámetros (ajustables en YAML si quieres)
    EMA_ALPHA = float(cfg.get("modA_ema_alpha", 0.8))
    HYSTERESIS_MARGIN = float(cfg.get("modA_hysteresis_margin", 8.0))
    DETECTOR_BACKEND = str(cfg.get("modA_backend", "retinaface"))  # 'mtcnn' o 'retinaface'

    emo_keys = ["angry","disgust","fear","happy","sad","surprise","neutral"]
    emo_smooth = {k: 0.0 for k in emo_keys}
    dominant_display = "neutral"
    missing_ticks = 0
    
    _ensure_tf_keras_shim()

    # Import seguro de DeepFace
    try:
        DeepFace = _import_deepface_safely()
    except Exception as e:
        print("[A] ERROR importando DeepFace:", repr(e), flush=True)
        return

    # Info TF para diagnóstico
    try:
        import tensorflow as tf
        print(f"[A] TF {tf.__version__} | GPUs: {tf.config.list_physical_devices('GPU')}", flush=True)
    except Exception:
        pass

    print("[A] Iniciado (DeepFace).", flush=True)
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

            idx = msg["frame_idx"] if isinstance(msg, dict) else msg.frame_idx
            frame = msg["frame"] if isinstance(msg, dict) else msg.frame  # BGR

            # Solo analizamos en TICK
            if idx % tick != 0:
                continue

            H, W = frame.shape[:2]
            present = False
            label_out = "inseguro"
            conf_out = 0.0
            quality_out = 0.0
            meta_out = {}

            try:
                result = DeepFace.analyze(
                    frame,
                    actions=['emotion'],
                    detector_backend=DETECTOR_BACKEND,
                    enforce_detection=True
                )
                emociones = result[0]['emotion']
                region = result[0]['region']
                x, y, w, h = region['x'], region['y'], region['w'], region['h']

                # calidad ~ área relativa del rostro (0..1)
                face_area = max(0, min(W, w)) * max(0, min(H, h))
                quality_out = float(np.clip((face_area / float(W*H)) * 4.0, 0.0, 1.0))

                # EMA sobre porcentajes (0..100)
                for k in emo_keys:
                    val = float(emociones.get(k, 0.0))
                    emo_smooth[k] = EMA_ALPHA * emo_smooth[k] + (1.0 - EMA_ALPHA) * val

                # dominante con histéresis
                dom_new = max(emo_smooth, key=lambda k: emo_smooth[k])
                if dominant_display not in emo_smooth:
                    dominant_display = dom_new
                else:
                    if (emo_smooth[dom_new] - emo_smooth[dominant_display]) >= HYSTERESIS_MARGIN:
                        dominant_display = dom_new

                conf_out = float(np.clip(emo_smooth[dominant_display] / 100.0, 0.0, 1.0))
                label_out = clasificar_estres(dominant_display)
                present = True
                missing_ticks = 0

                print(f"[A][{idx}] Dominante: {traducir_emocion(dominant_display)} "
                      f"({emo_smooth[dominant_display]:.1f}%) | Estrés: {label_out}",
                      flush=True)

                meta_out = {
                    "dominant_emotion": dominant_display,
                    "emotions": {k: round(v, 2) for k, v in emo_smooth.items()},
                    "region": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
                }

            except Exception:
                missing_ticks += 1
                present = False
                label_out = "inseguro"
                conf_out = 0.0
                quality_out = 0.0
                print(f"[A][{idx}] Sin rostro ({missing_ticks}/{face_timeout_ticks})", flush=True)

            if missing_ticks >= face_timeout_ticks:
                present = False
                label_out = "inseguro"
                conf_out = 0.0
                quality_out = 0.0

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
        print("[A] Error inesperado fuera del bucle:\n" + traceback.format_exc())
    finally:
        print("[A] Saliendo…", flush=True)

