# -*- coding: utf-8 -*-
#  Modulo A - Jetson (USB UVC) con V4L2 y DeepFace (MTCNN) 

import os
os.environ.setdefault("TF_TRT_DISABLE", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

import cv2
import numpy as np
import time
from PIL import ImageFont, ImageDraw, Image
from deepface import DeepFace

# Parámetros 
ANALYZE_EVERY = 10          # analizar cada N frames
EMA_ALPHA = 0.8             
HYSTERESIS_MARGIN = 8.0     # puntos porcentuales para cambiar dominante

# Robustez de predicción
CONF_MIN = 0.35             # confianza mínima
PERSIST_N = 2               # # seguidas requeridas para cambiar dominante

# Detectores
DETECTORS = ['mtcnn', 'retinaface', 'mediapipe', 'opencv']
MISS_DET_LIMIT = 2          # n fallos seguidos cambiar de backend detector

# Estética UI
COLOR_ELEGANTE = (70, 90, 110)
COLOR_TEXTO = (255, 255, 255)
COLOR_DOMINANTE = (255, 255, 0)
COLOR_ESTRES = {
    "BAJO": (0, 255, 0),
    "MEDIO": (0, 255, 255),
    "ALTO": (0, 0, 255),
    "DESCONOCIDO": (200, 200, 200)
}
WIN = "Modulo_A_Estres"
TARGET_W, TARGET_H = 720, 576
FORCE_DISPLAY_RESIZE = True
FPS_TARGET = 30

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

def open_camera(prefer_dev_indexes=(0,1,2,3), prefer_fourcc=('MJPG','YUYV'),
                w=TARGET_W, h=TARGET_H, fps=FPS_TARGET):
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

# tracker helper (CSRT con fallback a legacy)
def create_tracker():
    tr = None
    if hasattr(cv2, "TrackerCSRT_create"):
        tr = cv2.TrackerCSRT_create()
    elif hasattr(cv2, "legacy") and hasattr(cv2.legacy, "TrackerCSRT_create"):
        tr = cv2.legacy.TrackerCSRT_create()
    else:
        # fallback a KCF
        if hasattr(cv2, "TrackerKCF_create"):
            tr = cv2.TrackerKCF_create()
        elif hasattr(cv2, "legacy") and hasattr(cv2.legacy, "TrackerKCF_create"):
            tr = cv2.legacy.TrackerKCF_create()
    return tr

# Preprocesado del ROI 
def get_face_for_classifier(face_bgr):
    if face_bgr is None or face_bgr.size == 0:
        return None
    # CLAHE en Y luego sharpen leve
    ycrcb = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = cv2.split(ycrcb)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4,4))
    y = clahe.apply(y)
    ycrcb = cv2.merge([y, cr, cb])
    face = cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)

    k = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]], dtype=np.float32)
    face = cv2.filter2D(face, -1, k)

    # tamaño mínimo
    face = cv2.resize(face, (224,224), interpolation=cv2.INTER_LINEAR)
    return face

#  Clasificación de emoción sobre ROI sin redtect.
def classify_emotion_on_roi(face_bgr):
    """ Devuelve (emo_dict or None, max_label, max_score_0_100) """
    if face_bgr is None or face_bgr.size == 0:
        return None, None, 0.0
    face_proc = get_face_for_classifier(face_bgr)
    if face_proc is None:
        return None, None, 0.0

    # si falla -> enforce_detection=False
    try:
        res = DeepFace.analyze(face_proc, actions=['emotion'],
                               detector_backend='skip', enforce_detection=False)
    except Exception:
        res = DeepFace.analyze(face_proc, actions=['emotion'],
                               enforce_detection=False)
    if isinstance(res, list):
        res = res[0]

    emos = res.get('emotion', None)
    if not emos:
        return None, None, 0.0

    # 0..100
    max_label = max(emos, key=lambda k: float(emos[k]))
    max_val = float(emos[max_label])
    return emos, max_label, max_val

# Detección de rostro con backend seleccionable
def detect_with_backend(frame_bgr, backend):
    """
    Devuelve (emociones_dict, (x,y,w,h)) o (None, None) al detectar rostro.
    """
    res = DeepFace.analyze(frame_bgr, actions=['emotion'],
                           detector_backend=backend, enforce_detection=True)
    if isinstance(res, list):
        res = res[0]
    emociones = res.get('emotion', None)
    region = res.get('region', None)
    if emociones and region:
        x, y = int(region.get('x', 0)), int(region.get('y', 0))
        w, h = int(region.get('w', 0)), int(region.get('h', 0))
        return emociones, (x, y, w, h)
    return None, None

# Main
def main():
    # Silenciar warnings NumPy
    try:
        np.seterr(all='ignore')
    except Exception:
        pass
    try:
        from tensorflow.keras.utils import disable_interactive_logging
        disable_interactive_logging()
    except Exception:
        pass
    # OpenCV threads
    try:
        cv2.setNumThreads(1)
    except Exception:
        pass

    cap, dev_idx, fourcc = open_camera()
    if cap is None:
        raise RuntimeError("No se pudo abrir cámara. Revisa `v4l2-ctl --list-devices` y formatos soportados.")

    use_gui = bool(os.environ.get("DISPLAY"))
    if use_gui:
        cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WIN, TARGET_W, TARGET_H)
    else:
        print("Sin DISPLAY: modo headless (sin imshow).")

    font_path = "arial.ttf"
    try:
        font = ImageFont.truetype(font_path, 14)
    except Exception:
        font = ImageFont.load_default()

    print("Presiona 'q' para salir" if use_gui else "Procesando... (Ctrl+C para salir)")

    # Estado
    frame_idx = 0
    emo_keys = ["angry","disgust","fear","happy","sad","surprise","neutral"]
    emo_smooth = {k: 0.0 for k in emo_keys}
    dominant_display = "neutral"
    last_region = None

    # Persistencia
    cand_dom = None
    cand_streak = 0

    # Detección backend estado
    det_idx = 0
    miss_det_count = 0

    # Tracker
    tracker = None
    have_track = False

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

            # 1) DETECCIÓN - TRACKING ENTRE MEDIAS 
            frame_for_cls = None
            if frame_idx % ANALYZE_EVERY == 0:
                backend = DETECTORS[det_idx]
                try:
                    emociones, region = detect_with_backend(frame, backend)
                    if emociones is not None and region is not None:
                        miss_det_count = 0
                        last_region = region
                        # iniciar/rehacer tracker
                        tracker = create_tracker()
                        if tracker is not None:
                            tracker.init(frame, tuple(region))
                            have_track = True
                        else:
                            have_track = False
                        x, y, w, h = region
                        frame_for_cls = frame[max(0,y):y+h, max(0,x):x+w]
                    else:
                        miss_det_count += 1
                except Exception:
                    miss_det_count += 1

                if miss_det_count >= MISS_DET_LIMIT:
                    det_idx = (det_idx + 1) % len(DETECTORS)
                    print(f"[Detect] Cambiando backend -> {DETECTORS[det_idx]}")
                    miss_det_count = 0

                # si no hubo detección intentar tracker
                if frame_for_cls is None and have_track and tracker is not None:
                    ok, box = tracker.update(frame)
                    if ok:
                        x, y, w, h = [int(v) for v in box]
                        last_region = (x,y,w,h)
                        frame_for_cls = frame[max(0,y):y+h, max(0,x):x+w]
                    else:
                        have_track = False
            else:
                # solo tracking
                if have_track and tracker is not None:
                    ok, box = tracker.update(frame)
                    if ok:
                        x, y, w, h = [int(v) for v in box]
                        last_region = (x,y,w,h)
                        frame_for_cls = frame[max(0,y):y+h, max(0,x):x+w]
                    else:
                        have_track = False

            # 2) CLASIFICACIÓN SOBRE ROI
            try:
                if frame_for_cls is not None:
                    emo_roi, label_roi, maxval_roi = classify_emotion_on_roi(frame_for_cls)
                    if emo_roi:
                        # 0..100 -> 0..1
                        max_conf = maxval_roi / 100.0
                        if max_conf >= CONF_MIN:
                            # EMA
                            for k in emo_keys:
                                val = float(emo_roi.get(k, 0.0))
                                emo_smooth[k] = EMA_ALPHA * emo_smooth[k] + (1.0 - EMA_ALPHA) * val

                            # Dominante con histéresis + persistencia
                            dom_new = max(emo_smooth, key=lambda k: emo_smooth[k])
                            if dominant_display not in emo_smooth:
                                dominant_display = dom_new
                                cand_dom, cand_streak = None, 0
                            else:
                                if (emo_smooth[dom_new] - emo_smooth[dominant_display]) >= HYSTERESIS_MARGIN:
                                    if cand_dom == dom_new:
                                        cand_streak += 1
                                    else:
                                        cand_dom = dom_new
                                        cand_streak = 1
                                    if cand_streak >= PERSIST_N:
                                        dominant_display = dom_new
                                        cand_dom, cand_streak = None, 0

                            # Log 
                            print(f"Emoción dominante: {traducir_emocion(dominant_display)} "
                                  f"({emo_smooth[dominant_display]:.1f}%)")
                        # si confianza < umbral se ignora
            except Exception:
                pass

            # 3) DIBUJO 
            if last_region is not None:
                x, y, w, h = last_region
                cv2.rectangle(frame, (x, y), (x + w, y + h), COLOR_ELEGANTE, 1)

            panel_w, panel_h = 220, 255
            if last_region is not None:
                x, y, w, h = last_region
                panel_x = x + w + 10
                panel_y = max(10, y)
            else:
                panel_x, panel_y = 20, 20

            overlay = frame.copy()
            cv2.rectangle(overlay, (panel_x, panel_y), (panel_x + panel_w, panel_y + panel_h), COLOR_ELEGANTE, -1)
            alpha = 0.6
            frame = cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0)

            img_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(img_pil)

            y0 = panel_y + 20
            line_height = 25
            emos_sorted = sorted(emo_smooth.items(), key=lambda kv: kv[1], reverse=True)
            for i, (emo, val) in enumerate(emos_sorted[:7]):
                texto = f"{traducir_emocion(emo)}: {val:.1f}%"
                color = COLOR_DOMINANTE if emo == dominant_display else COLOR_TEXTO
                draw.text((panel_x + 10, y0 + i * line_height), texto, font=font, fill=color)

            txt_dom = f"Dominante: {traducir_emocion(dominant_display)}"
            draw.text((panel_x + 10, y0 + 7 * line_height), txt_dom, font=font, fill=COLOR_DOMINANTE)

            estres = clasificar_estres(dominant_display)
            txt_str = f"Nivel de estrés: {estres}"
            draw.text((panel_x + 10, y0 + 8 * line_height), txt_str, font=font, fill=COLOR_ESTRES[estres])

            frame = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

            # 4) DISPLAY / HEADLESS 
            if FORCE_DISPLAY_RESIZE:
                h, w = frame.shape[:2]
                if w != TARGET_W:
                    scale = TARGET_W / float(w)
                    frame_disp = cv2.resize(frame, (TARGET_W, int(h * scale)))
                else:
                    frame_disp = frame
            else:
                frame_disp = frame

            if use_gui:
                cv2.imshow(WIN, frame_disp)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            else:
                if frame_idx % 120 == 0 and frame_idx > 0:
                    fps = frame_idx / (time.time() - t0 + 1e-6)
                    print(f"[RT] FPS ~{fps:.1f} | detect cada {ANALYZE_EVERY} | α={EMA_ALPHA} | margin={HYSTERESIS_MARGIN} | umbral={CONF_MIN} | persist={PERSIST_N}")
                time.sleep(0.001)

            frame_idx += 1

    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if use_gui:
            cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
