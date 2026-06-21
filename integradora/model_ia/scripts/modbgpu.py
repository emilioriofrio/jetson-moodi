# -*- coding: utf-8 -*-
"""
Modulo B/C - Tiempo real (Jetson Orin, GPU CUDA para Keras)
- MediaPipe Holistic (TFLite + XNNPACK en CPU)
- Tu modelo Keras (tf.keras) en GPU (CUDA), con memory growth
- Apertura de cámara robusta (USB V4L2 / GStreamer / CSI)
- Flags para forzar CPU y logs de dispositivos
"""

import os
import cv2
import time
import argparse
import numpy as np
import mediapipe as mp
import tensorflow as tf
from collections import deque
from PIL import Image, ImageDraw, ImageFont

# ARGS (selección de cámara y GPU)
ap = argparse.ArgumentParser()
ap.add_argument("--src", choices=["auto","usb","gst-v4l2","csi"], default="auto",
                help="Fuente de cámara: usb (cv2 V4L2), gst-v4l2 (GStreamer v4l2src), csi (nvarguscamerasrc), auto (intenta todo)")
ap.add_argument("--dev", default="/dev/video0", help="Dispositivo V4L2 (USB) p.ej. /dev/video0")
ap.add_argument("--index", type=int, default=0, help="Índice para backend usb (cv2.VideoCapture(index))")
ap.add_argument("--fps", type=int, default=30, help="FPS objetivo de la cámara")
ap.add_argument("--no-gpu", action="store_true", help="Forzar CPU (deshabilita CUDA para tf.keras)")
ap.add_argument("--verbose", action="store_true", help="Logs verbosos de colocación de dispositivos TF")
args = ap.parse_args()

# CONFIG GENERAL
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(BASE_DIR, "resultados_modb_v3", "modelo_modb_v3.keras")
DATASET_DIR = os.path.join(BASE_DIR, "dataset_vectores_v3")
LABELS_MAP_PATH = os.path.join(DATASET_DIR, "labels_map_v3.txt")

# Video
W, H = 960, 720

# Inferencia
N_FRAMES = 10
NORMALIZAR = True
MIN_VIS_POSE = 0.5
SMOOTH_WINDOW = 8
PRED_THRESH = 0.55

# Estilo (BGR)
LINE_COLOR       = (80, 120, 160)
LINE_THICK       = 1
POINT_FILL_COLOR = (100, 175, 200)
POINT_EDGE_COLOR = (50, 80, 120)
POINT_RADIUS     = 3
POINT_EDGE_THICK = 1
VIS_THR_POSE     = 0.5
HEADER_ALPHA     = 0.55
HEADER_H         = 78

# TF: Configurar GPU/CPU y logs
# Forzar CPU si se solicita
if args.no_gpu:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""

# Logs (0=quiet, 1=warnings, 2=info)
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "0" if args.verbose else "1"

# Memory growth para evitar que TF reserve toda la VRAM
gpus = tf.config.list_physical_devices('GPU')
if gpus and not args.no_gpu:
    try:
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)
    except Exception as e:
        print("[WARN] No se pudo habilitar memory growth:", e)

# Logs
if args.verbose:
    tf.debugging.set_log_device_placement(True)

print("GPUs visibles:", tf.config.list_physical_devices("GPU"))

# UTIL: cargar labels
def load_class_names(path):
    if os.path.exists(path):
        idx2name = {}
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                i, nm = line.split(",", 1)
                idx2name[int(i)] = nm.strip()
        return [idx2name[i] for i in sorted(idx2name.keys())]
    return ["clase_0","clase_1","clase_2","clase_3","clase_4","clase_5"]

CLASS_NAMES = load_class_names(LABELS_MAP_PATH)
print(f"[INFO] Clases: {CLASS_NAMES}")

# CARGA MODELO (Keras)
print("[INFO] Cargando modelo Keras...")
model = tf.keras.models.load_model(MODEL_PATH)

# MEDIAPIPE (Holistic - CPU con TFLite)
mp_holistic = mp.solutions.holistic
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

def extract_keypoints(results):
    keypoints = []
    keypoints += _landmark_vec(results.pose_landmarks, 33, MIN_VIS_POSE, use_visibility=True)
    keypoints += _landmark_vec(results.left_hand_landmarks, 21, 0.0, use_visibility=False)
    keypoints += _landmark_vec(results.right_hand_landmarks, 21, 0.0, use_visibility=False)
    return keypoints  # 75*3 = 225

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

def load_font(size=36):
    candidates = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                pass
    return ImageFont.load_default()

FONT_MAIN = load_font(40)

def alpha_blend(overlay_rgba, base_rgba):
    a = overlay_rgba[:, :, 3:4].astype(np.float32) / 255.0
    out_rgb = (overlay_rgba[:, :, :3].astype(np.float32) * a +
               base_rgba[:, :, :3].astype(np.float32) * (1.0 - a))
    out = base_rgba.copy()
    out[:, :, :3] = out_rgb.astype(np.uint8)
    out[:, :, 3] = 255
    return out

def draw_transparent_header(img_bgr, text):
    h, w = img_bgr.shape[:2]
    header = np.zeros((HEADER_H, w, 4), dtype=np.uint8)
    header[:, :, :3] = (40, 40, 40)
    header[:, :, 3] = int(255 * HEADER_ALPHA)
    img_rgba = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2BGRA)
    img_rgba[0:HEADER_H, :, :] = alpha_blend(header, img_rgba[0:HEADER_H, :, :])
    pil_img = Image.fromarray(img_rgba)
    draw = ImageDraw.Draw(pil_img)
    draw.text((18, 18), text, font=FONT_MAIN, fill=(255, 255, 255, 255))
    img_rgba = np.array(pil_img)
    return cv2.cvtColor(img_rgba, cv2.COLOR_BGRA2BGR)

def draw_landmarks_elegant(img, results):
    h, w = img.shape[:2]
    def to_px(lm): return int(lm.x * w), int(lm.y * h)
    def visible_pose(lm):
        v = getattr(lm, "visibility", 1.0)
        return (v is None) or (v >= VIS_THR_POSE)
    def valid_norm(lm): return 0.0 <= lm.x <= 1.0 and 0.0 <= lm.y <= 1.0
    def draw_set(landmarks, connections, check_visibility=False):
        if not landmarks: return
        # líneas
        for i, j in connections:
            lmi = landmarks.landmark[i]; lmj = landmarks.landmark[j]
            if check_visibility and not (visible_pose(lmi) and visible_pose(lmj)): continue
            if not (valid_norm(lmi) and valid_norm(lmj)): continue
            x1, y1 = to_px(lmi); x2, y2 = to_px(lmj)
            cv2.line(img, (x1, y1), (x2, y2), LINE_COLOR, LINE_THICK, lineType=cv2.LINE_AA)
        # puntos
        for lm in landmarks.landmark:
            if check_visibility and not visible_pose(lm): continue
            if not valid_norm(lm): continue
            cx, cy = to_px(lm)
            cv2.circle(img, (cx, cy), POINT_RADIUS + POINT_EDGE_THICK, POINT_EDGE_COLOR, -1, lineType=cv2.LINE_AA)
            cv2.circle(img, (cx, cy), POINT_RADIUS, POINT_FILL_COLOR, -1, lineType=cv2.LINE_AA)
    if results.pose_landmarks:
        draw_set(results.pose_landmarks, mp_holistic.POSE_CONNECTIONS, check_visibility=True)
    if results.left_hand_landmarks:
        draw_set(results.left_hand_landmarks, mp_holistic.HAND_CONNECTIONS, check_visibility=False)
    if results.right_hand_landmarks:
        draw_set(results.right_hand_landmarks, mp_holistic.HAND_CONNECTIONS, check_visibility=False)

# Cámara
def build_gst_v4l2_pipeline(device, width, height, fps):
    return (
        f"v4l2src device={device} ! "
        f"video/x-raw, width={width}, height={height}, framerate={fps}/1 ! "
        f"videoconvert ! "
        f"video/x-raw, format=BGR ! appsink drop=true sync=false"
    )

def build_gst_csi_pipeline(width, height, fps):
    return (
        f"nvarguscamerasrc ! "
        f"video/x-raw(memory:NVMM), width={width}, height={height}, framerate={fps}/1 ! "
        f"nvvidconv ! video/x-raw, format=BGRx ! "
        f"videoconvert ! video/x-raw, format=BGR ! appsink drop=true sync=false"
    )

def try_open_usb_index(index, width, height, fps):
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")) 
    return cap

def try_open_gst(pipeline):
    return cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)

def open_camera(src_mode, device, index, width, height, fps):
    tried = []

    def ok(cap):
        if not cap or not cap.isOpened(): return False
        ret, _ = cap.read()
        return bool(ret)

    if src_mode in ("usb", "auto"):
        cap = try_open_usb_index(index, width, height, fps)
        tried.append(f"usb(index={index})")
        if ok(cap):
            print(f"[INFO] Cámara abierta por USB (cv2 V4L2) index={index}")
            return cap
        if cap: cap.release()

    if src_mode in ("gst-v4l2", "auto"):
        pipe = build_gst_v4l2_pipeline(device, width, height, fps)
        cap = try_open_gst(pipe)
        tried.append(f"gst-v4l2(device={device})")
        if ok(cap):
            print(f"[INFO] Cámara abierta por GStreamer v4l2src device={device}")
            return cap
        if cap: cap.release()

    if src_mode in ("csi", "auto"):
        pipe = build_gst_csi_pipeline(width, height, fps)
        cap = try_open_gst(pipe)
        tried.append("csi(nvarguscamerasrc)")
        if ok(cap):
            print("[INFO] Cámara CSI abierta con nvarguscamerasrc")
            return cap
        if cap: cap.release()

    raise RuntimeError(f"No se pudo abrir la cámara. Intentos: {', '.join(tried)}")

# LOOP EN TIEMPO REAL
cv2.namedWindow("Modulo B/C - Tiempo real")

try:
    cap = open_camera(args.src, args.dev, args.index, W, H, args.fps)
except Exception as e:
    print("[ERROR] No se pudo abrir la cámara.")
    print("Detalle:", e)
    print("\nChecklist rápido:")
    print("  1) ¿La webcam es USB?  Prueba:  ls -l /dev/video*")
    print("  2) ¿Tu usuario está en el grupo 'video'?  id | grep video")
    print("  3) ¿Otra app está usando la cámara?  (cierra browsers/Zoom/Teams)")
    print("  4) Probar manualmente:")
    print("     - USB V4L2:   gst-launch-1.0 v4l2src device=/dev/video0 ! videoconvert ! autovideosink")
    print("     - CSI NVArgus: gst-launch-1.0 nvarguscamerasrc ! nvvidconv ! nvoverlaysink")
    print("  5) Forzar backend, p.ej.:")
    print("     python scripts/modb_rt_interface.py --src usb --index 0")
    print("     python scripts/modb_rt_interface.py --src gst-v4l2 --dev /dev/video0")
    print("     python scripts/modb_rt_interface.py --src csi")
    raise SystemExit(1)

seq = deque(maxlen=N_FRAMES)
probs_smooth = deque(maxlen=SMOOTH_WINDOW)
draw_lmks = True

# Warmup + timing
dummy = np.zeros((1, N_FRAMES, 225), dtype=np.float32)
_ = model.predict(dummy, verbose=0)  # warmup
t0 = time.time()
_ = model.predict(dummy, verbose=0)
print(f"[TIMING] predict(1) tomó {time.time()-t0:.4f} s")

print("[INFO] Iniciando webcam. Teclas: q = salir, v = landmarks ON/OFF")
with mp_holistic.Holistic(static_image_mode=False, model_complexity=0) as holistic:
    while True:
        ret, frame_bgr = cap.read()
        if not ret:
            print("[WARN] No hay frame de la cámara (¿desconectada/ocupada?).")
            time.sleep(0.1)
            continue

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        results = holistic.process(frame_rgb)

        annotated = frame_bgr.copy()
        if draw_lmks:
            draw_landmarks_elegant(annotated, results)

        keypoints = extract_keypoints(results)
        if NORMALIZAR:
            keypoints = normalize_by_shoulders(keypoints)
        seq.append(keypoints)

        pred_label = "-"
        pred_conf = 0.0
        if len(seq) == N_FRAMES:
            x = np.array(seq, dtype=np.float32).reshape(1, N_FRAMES, 225)
            probs = model.predict(x, verbose=0)[0]
            probs_smooth.append(probs)
            probs_avg = np.mean(probs_smooth, axis=0)
            idx = int(np.argmax(probs_avg))
            pred_conf = float(probs_avg[idx])
            pred_label = CLASS_NAMES[idx] if pred_conf >= PRED_THRESH else "inseguro"
            print(f"[PRED] {pred_label} ({pred_conf:.2f})")

        header_txt = f"Predicción:  {pred_label} ({pred_conf:.2f})"
        annotated = draw_transparent_header(annotated, header_txt)

        cv2.imshow("Modulo B/C - Tiempo real", annotated)
        key = (cv2.waitKey(1) & 0xFF)
        if key == ord('q'):
            break
        if key == ord('v'):
            draw_lmks = not draw_lmks

cap.release()
cv2.destroyAllWindows()
