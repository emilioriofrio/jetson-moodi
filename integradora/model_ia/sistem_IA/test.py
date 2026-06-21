# test_backends.py
import sys, types, importlib, time
import cv2

def shim_tf_keras():
    try:
        k = importlib.import_module("keras")
    except Exception:
        k = importlib.import_module("tensorflow.keras")

    pkg = types.ModuleType("tf_keras"); pkg.__path__ = []
    pkg.__version__ = getattr(k, "__version__", "3-shim")
    pkg.__file__ = getattr(k, "__file__", "")
    pkg.__doc__ = getattr(k, "__doc__", "")
    sys.modules["tf_keras"] = pkg
    sys.modules["tf_keras.keras"] = k
    pkg.keras = k

    api_pkg = types.ModuleType("tf_keras.api"); api_pkg.__path__ = []
    v2_pkg  = types.ModuleType("tf_keras.api._v2"); v2_pkg.__path__ = []
    sys.modules["tf_keras.api"] = api_pkg
    sys.modules["tf_keras.api._v2"] = v2_pkg
    # DeepFace solo necesita que exista este módulo
    sys.modules["tf_keras.api._v2.keras"] = k

shim_tf_keras()
from deepface import DeepFace

cap = cv2.VideoCapture(0)
assert cap.isOpened(), "No pude abrir cámara"
ok, frame = cap.read()
cap.release()
assert ok, "No pude leer frame"

for be in ["mtcnn","retinaface","mediapipe"]:
    try:
        t0 = time.time()
        res = DeepFace.analyze(img_path=frame, actions=['emotion'], detector_backend=be, enforce_detection=True)
        dt = (time.time()-t0)*1000
        r0 = res[0] if isinstance(res, list) else res
        region = r0['region']; emo = r0['emotion']
        top = max(emo, key=emo.get)
        print(f"[{be}] OK  t={dt:.1f}ms  region={region}  top={top}")
    except Exception as e:
        print(f"[{be}] FAIL -> {e!r}")
