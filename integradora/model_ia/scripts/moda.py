# -*- coding: utf-8 -*-
# Modulo A - Jetson (USB UVC) con V4L2 y DeepFace (MTCNN + Tracker + ROI)

import os
# Silenciar logs
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("TF_NUM_INTRAOP_THREADS", "2")
os.environ.setdefault("TF_NUM_INTEROP_THREADS", "2")

# Escalado HiDPI 
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

import cv2
import numpy as np
import time
import io, contextlib

from deepface import DeepFace
from mtcnn import MTCNN

# Parámetros 
ANALYZE_EVERY = 10          # analizar emociones cada N frames 
EMA_ALPHA = 0.8             # 0.8=suave; 0.6=respuesta más rápida
HYSTERESIS_MARGIN = 8.0     # pp para cambiar emoción dominante

# Frecuencia de detección con MTCNN y tamaños
DETECT_EVERY = 15           
DETECT_SIZE = (640, 360)    
ROI_SIZE = (224, 224)       

# Cámara / ventana
WIN = "Modulo_A_Estres"
TARGET_W, TARGET_H = 720, 576
FORCE_DISPLAY_RESIZE = True
FPS_TARGET = 30
PREFER_FOURCC = ('YUYV', 'MJPG') 

# Colores
COLOR_ELEGANTE = (70, 90, 110)
COLOR_TEXTO = (255, 255, 255)
COLOR_DOMINANTE = (255, 255, 0)
COLOR_ESTRES = {
    "BAJO": (0, 255, 0),
    "MEDIO": (0, 255, 255),
    "ALTO": (0, 0, 255),
    "DESCONOCIDO": (200, 200, 200)
}

# Utilidades 
def clasificar_estres(emocion):
    if emocion in ["happy", "neutral"]:
        return "BAJO"
    elif emocion in ["surprise", "fear"]:
        return "MEDIO"
    elif emocion in ["angry", "sad", "disgust"]:
        return "ALTO"
    else:
        return "DESCONOCIDO"

def traducir_emocion(emo):
    return {
        "angry": "Enfado",
        "disgust": "Asco",
        "fear": "Miedo",
        "happy": "Alegría",
        "sad": "Tristeza",
        "surprise": "Sorpresa",
        "neutral": "Neutro"
    }.get(emo, emo)

def open_camera(prefer_dev_indexes=(0,1,2,3), prefer_fourcc=PREFER_FOURCC, w=TARGET_W, h=TARGET_H, fps=FPS_TARGET):
    """
    Intenta abrir /dev/videoX (V4L2) y setear un FOURCC válido.
    """
    for dev in prefer_dev_indexes:
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            continue

        try: cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception: pass

        for fcc in prefer_fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fcc))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            cap.set(cv2.CAP_PROP_FPS,          fps)
            ok, frm = cap.read()
            if ok and frm is not None:
                h0, w0 = frm.shape[:2]
                print(f"[OK] Cámara /dev/video{dev} con FOURCC={fcc} => {w0}x{h0}")
                return cap, dev, fcc
        cap.release()
    return None, None, None

def get_tracker():
    """
    Devuelve un tracker disponible (KCF -> CSRT -> MOSSE).
    """
    creator_names = [
        ("legacy", "TrackerKCF_create"),
        ("legacy", "TrackerCSRT_create"),
        ("legacy", "TrackerMOSSE_create"),
        (None, "TrackerKCF_create"),
        (None, "TrackerCSRT_create"),
        (None, "TrackerMOSSE_create"),
    ]
    for ns, name in creator_names:
        try:
            creator = getattr(cv2.legacy if ns == "legacy" else cv2, name)
            return creator()
        except Exception:
            continue
    return None

def detect_face_mtcnn_full(frame_bgr, mtcnn, detect_wh=DETECT_SIZE):
    """
    Detecta la cara con MTCNN en resolución reducida y reescala bbox al frame original.
    Retorna (x, y, w, h) o None.
    """
    H, W = frame_bgr.shape[:2]
    dw, dh = detect_wh
    small = cv2.resize(frame_bgr, (dw, dh))
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    dets = mtcnn.detect_faces(rgb)
    if not dets:
        return None
    # Cara más grande
    i = max(range(len(dets)), key=lambda k: dets[k]['box'][2] * dets[k]['box'][3])
    x, y, w, h = dets[i]['box']
    # Reescalar a coords del frame original
    fx, fy = W / float(dw), H / float(dh)
    x, y, w, h = int(x * fx), int(y * fy), int(w * fx), int(h * fy)
    # Margen
    m = int(0.12 * max(w, h))
    x0 = max(0, x - m); y0 = max(0, y - m)
    x1 = min(W, x + w + m); y1 = min(H, y + h + m)
    return (x0, y0, x1 - x0, y1 - y0)

def analyze_emotion_roi(frame_bgr, bbox):
    """
    Pasa solo el ROI al modelo de emociones de DeepFace, sin redetección.
    Silencia barras de progreso de Keras para no frenar la consola.
    """
    x, y, w, h = bbox
    crop = frame_bgr[y:y+h, x:x+w]
    if crop.size == 0:
        return None
    crop_small = cv2.resize(crop, ROI_SIZE)
    # Silenciar stdout/stderr 
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        out = DeepFace.analyze(
            crop_small,
            actions=['emotion'],
            detector_backend='skip',
            enforce_detection=False
        )
    if isinstance(out, list):
        out = out[0]
    return out.get('emotion', None)

def draw_panel(frame, panel_x, panel_y, panel_w, panel_h, alpha=0.6):
    overlay = frame.copy()
    cv2.rectangle(overlay, (panel_x, panel_y), (panel_x + panel_w, panel_y + panel_h), COLOR_ELEGANTE, -1)
    return cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0)

def put_text(img, text, org, color, scale=0.6, thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)

def warmup_deepface_emotion():
    """
    Carga el modelo de emociones sin usar APIs internas ni barras de progreso.
    """
    dummy = np.zeros((ROI_SIZE[1], ROI_SIZE[0], 3), dtype=np.uint8)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            DeepFace.analyze(
                dummy,
                actions=['emotion'],
                detector_backend='skip',
                enforce_detection=False
            )
        except Exception:
            pass

# Main
def main():
    # Warm-up del modelo de emociones
    warmup_deepface_emotion()
    mtcnn = MTCNN(min_face_size=60, scale_factor=0.709, steps_threshold=[0.6, 0.7, 0.7])

    # Optimización OpenCV
    cv2.setUseOptimized(True)
    cv2.setNumThreads(2)

    # Cámara
    cap, dev_idx, fourcc = open_camera()
    if cap is None:
        raise RuntimeError("No se pudo abrir cámara. Revisa `v4l2-ctl --list-devices` y formatos soportados.")

    # Ventana / modo headless
    use_gui = bool(os.environ.get("DISPLAY"))
    if use_gui:
        cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WIN, TARGET_W, TARGET_H)
    print("Presiona 'q' para salir" if use_gui else "Procesando... (Ctrl+C para salir)")

    # Estado
    frame_idx = 0
    emo_keys = ["angry","disgust","fear","happy","sad","surprise","neutral"]
    emo_smooth = {k: 0.0 for k in emo_keys}
    dominant_display = "neutral"
    last_bbox = None

    tracker = None
    fail_reads = 0
    t0 = time.time()

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                fail_reads += 1
                if fail_reads > 15:
                    cap.release()
                    time.sleep(0.2)
                    cap, dev_idx, fourcc = open_camera(prefer_dev_indexes=(dev_idx,0,1,2,3))
                    if cap is None:
                        print("No se pudo reabrir la cámara. Saliendo.")
                        break
                    fail_reads = 0
                continue
            fail_reads = 0

            # Tracking + redetección MTCNN
            if frame_idx % DETECT_EVERY == 0 or tracker is None:
                bbox = detect_face_mtcnn_full(frame, mtcnn)
                if bbox:
                    last_bbox = bbox
                    tracker = get_tracker()
                    if tracker is not None:
                        try:
                            tracker.init(frame, tuple(last_bbox))
                        except Exception:
                            tracker = None
                else:
                    tracker = None
            else:
                if tracker is not None:
                    ok, tb = tracker.update(frame)
                    if ok:
                        last_bbox = tuple(map(int, tb))
                    else:
                        tracker = None  # fuerza redetección en próximo ciclo

            # Análisis de emociones sobre ROI
            try:
                if frame_idx % ANALYZE_EVERY == 0 and last_bbox is not None:
                    emociones = analyze_emotion_roi(frame, last_bbox)
                    if emociones:
                        # EMA
                        for k in emo_keys:
                            val = float(emociones.get(k, 0.0))
                            emo_smooth[k] = EMA_ALPHA * emo_smooth[k] + (1.0 - EMA_ALPHA) * val
                        # Dominante con histéresis
                        dom_new = max(emo_smooth, key=lambda k: emo_smooth[k])
                        if (dominant_display not in emo_smooth) or (emo_smooth[dom_new] - emo_smooth[dominant_display] >= HYSTERESIS_MARGIN):
                            dominant_display = dom_new
                        # Consola
                        print(f"Emoción dominante: {traducir_emocion(dominant_display)} "
                              f"({emo_smooth[dominant_display]:.1f}%)")
            except Exception:
                pass  # no bloquear la UI si falla un análisis puntual

            # dibujo
            if last_bbox is not None:
                x, y, w, h = last_bbox
                cv2.rectangle(frame, (x, y), (x + w, y + h), COLOR_ELEGANTE, 1)
                panel_x = x + w + 10
                panel_y = max(10, y)
            else:
                panel_x, panel_y = 20, 20

            panel_w, panel_h = 220, 255
            frame = draw_panel(frame, panel_x, panel_y, panel_w, panel_h, alpha=0.6)
            y0 = panel_y + 20
            line_h = 22

            # Emociones suavizadas
            emos_sorted = sorted(emo_smooth.items(), key=lambda kv: kv[1], reverse=True)
            for i, (emo, val) in enumerate(emos_sorted[:7]):
                texto = f"{traducir_emocion(emo)}: {val:.1f}%"
                color = COLOR_DOMINANTE if emo == dominant_display else COLOR_TEXTO
                put_text(frame, texto, (panel_x + 10, y0 + i * line_h), color, scale=0.55, thick=1)

            txt_dom = f"Dominante: {traducir_emocion(dominant_display)}"
            put_text(frame, txt_dom, (panel_x + 10, y0 + 7 * line_h), COLOR_DOMINANTE, scale=0.6, thick=1)

            estres = clasificar_estres(dominant_display)
            txt_str = f"Nivel de estrés: {estres}"
            put_text(frame, txt_str, (panel_x + 10, y0 + 8 * line_h), COLOR_ESTRES[estres], scale=0.6, thick=1)

            # Escalado solo para visualización
            if FORCE_DISPLAY_RESIZE:
                h, w = frame.shape[:2]
                if w != TARGET_W:
                    scale = TARGET_W / float(w)
                    frame_disp = cv2.resize(frame, (TARGET_W, int(h * scale)))
                else:
                    frame_disp = frame
            else:
                frame_disp = frame

            # GUI vs headless
            use_gui = bool(os.environ.get("DISPLAY"))
            if use_gui:
                cv2.imshow(WIN, frame_disp)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            else:
                if frame_idx % 60 == 0 and frame_idx > 0:
                    fps = frame_idx / (time.time() - t0 + 1e-6)
                    print(f"Frames procesados: {frame_idx} | ~{fps:.1f} FPS")
                time.sleep(0.001)

            frame_idx += 1

    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if bool(os.environ.get("DISPLAY")):
            cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
