# core/orchestrator.py
import cv2, time, yaml, traceback, os
from core.messages import FrameMsg
from multiprocessing import Event
from multiprocessing import SimpleQueue as Queue
import queue as pyq  # para Empty si usamos Queue con maxsize

def load_cfg(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def _make_usb_pipeline(dev="/dev/video0", w=960, h=720, fps=30, prefer_mjpg=True):
    """
    Construye una pipeline GStreamer para webcams USB.
    - prefer_mjpg=True: intenta MJPG → jpegdec (más ligero en CPU)
    - si tu cam no ofrece MJPG, pon prefer_mjpg=False (YUY2 → videoconvert)
    """
    if prefer_mjpg:
        return (
            f"v4l2src device={dev} ! "
            f"image/jpeg,framerate={fps}/1 ! "
            f"jpegdec ! videoscale ! video/x-raw,width={w},height={h} ! "
            f"videoconvert ! video/x-raw,format=BGR ! appsink drop=1 sync=0"
        )
    else:
        # YUY2 / YUYV
        return (
            f"v4l2src device={dev} ! "
            f"video/x-raw,format=YUY2,framerate={fps}/1,width={w},height={h} ! "
            f"videoconvert ! video/x-raw,format=BGR ! appsink drop=1 sync=0"
        )

def _make_csi_pipeline(sensor_id=0, w=1280, h=720, fps=30):
    """
    Pipeline nativa para CSI (nvarguscamerasrc) en Jetson.
    """
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM), width=(int){w}, height=(int){h}, "
        f"format=(string)NV12, framerate=(fraction){fps}/1 ! "
        f"nvvidconv flip-method=0 ! video/x-raw, format=(string)BGRx ! "
        f"videoconvert ! video/x-raw, format=(string)BGR ! appsink drop=1 sync=0"
    )

def _open_camera(cfg):
    cam_type   = str(cfg.get("camera_type", "usb")).lower()  # "usb" | "csi" | "index"
    dev        = cfg.get("camera_device", "/dev/video0")
    index      = int(cfg.get("camera_index", 0))
    w          = int(cfg.get("width", 960))
    h          = int(cfg.get("height", 720))
    fps        = int(cfg.get("fps", 30))
    prefer_mjpg = bool(cfg.get("prefer_mjpg", True))
    custom_pipeline = cfg.get("camera_pipeline", None)

    # 1) Pipeline explícita
    if custom_pipeline:
        cap = cv2.VideoCapture(custom_pipeline, cv2.CAP_GSTREAMER)
        if cap.isOpened():
            print("[ORCH] Cámara abierta (pipeline custom GStreamer).", flush=True)
            return cap
        else:
            print("[ORCH] Falló pipeline custom; probando rutas estándar…", flush=True)

    # 2) Según tipo
    if cam_type == "csi":
        pipe = _make_csi_pipeline(sensor_id=int(cfg.get("csi_sensor_id", 0)),
                                  w=w, h=h, fps=fps)
        cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
        if cap.isOpened():
            print("[ORCH] Cámara CSI abierta (GStreamer).", flush=True)
            return cap
        else:
            print("[ORCH] CSI no disponible. ¿Seguro que usas cámara CSI? Probando USB…", flush=True)
            cam_type = "usb"  # cae a USB

    if cam_type == "usb":
        # MJPG primero
        pipe = _make_usb_pipeline(dev=dev, w=w, h=h, fps=fps, prefer_mjpg=True)
        cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
        if cap.isOpened():
            print("[ORCH] Cámara USB abierta (MJPG, GStreamer).", flush=True)
            return cap
        # YUY2 plan B
        pipe = _make_usb_pipeline(dev=dev, w=w, h=h, fps=fps, prefer_mjpg=False)
        cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
        if cap.isOpened():
            print("[ORCH] Cámara USB abierta (YUY2, GStreamer).", flush=True)
            return cap
        # V4L2 directo
        cap = cv2.VideoCapture(dev)  # suele usar V4L2
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            cap.set(cv2.CAP_PROP_FPS,          fps)
            print("[ORCH] Cámara USB abierta (V4L2).", flush=True)
            return cap

    if cam_type == "index":
        cap = cv2.VideoCapture(index)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            cap.set(cv2.CAP_PROP_FPS,          fps)
            print("[ORCH] Cámara por índice abierta.", flush=True)
            return cap

    # Nada funcionó
    return None

# --- Helper: enviar SIEMPRE el frame más reciente a la UI (sin acumular latencia)
def put_latest(q, item):
    """
    Inserta 'item' en 'q' sin bloquear. Si la cola está llena (Queue con maxsize),
    elimina el más antiguo y reintenta. Con SimpleQueue (sin límite) no dispara Full,
    pero igual protegemos la operación y evitamos bloquear.
    """
    # Primer intento: no bloquear
    try:
        # put_nowait existe en Queue con maxsize; en SimpleQueue equivale a put
        q.put_nowait(item)
        return
    except Exception:
        # Si es una Queue llena, intentamos liberar 1 elemento viejo
        pass

    # Intento de liberar uno viejo si la cola lo soporta
    freed = False
    for _ in range(3):  # evita bucles largos
        try:
            q.get_nowait()
            freed = True
            break
        except (pyq.Empty, AttributeError):
            break

    # Reintento final
    try:
        if freed:
            q.put_nowait(item)
        else:
            # Fallback: al menos intenta un put no bloqueante estándar
            q.put(item, block=False)  # puede no estar disponible en SimpleQueue; lo protegemos
    except Exception:
        # Último recurso: intenta un put normal pero sin bloquear el hilo principal
        try:
            q.put_nowait(item)
        except Exception:
            pass

def run_orchestrator(cfg_path: str, qA: Queue, qB: Queue, qC: Queue, stop_event: Event, qUI_frames: Queue=None):
    cfg = load_cfg(cfg_path)
    tick = int(cfg.get("tick_size", 10))

    cap = _open_camera(cfg)
    if not cap or not cap.isOpened():
        print("[ORCH] No se pudo abrir la cámara.", flush=True)
        return

    print("[ORCH] Cámara abierta.", flush=True)

    frame_idx = 0
    try:
        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01)
                continue

            msg = FrameMsg.build(frame_idx, frame)

            # C y B: TODOS los frames (dejamos tu comportamiento tal cual)
            try: qC.put(msg)
            except Exception: pass
            try: qB.put(msg)
            except Exception: pass

            # A (emociones): solo cada tick (tal cual)
            if frame_idx % tick == 0:
                try: qA.put(msg)
                except Exception: pass

            # UI: TODOS los frames, pero siempre el más reciente (sin latencia acumulada)
            if qUI_frames is not None:
                try:
                    put_latest(qUI_frames, msg.__dict__)
                except Exception:
                    pass

            frame_idx += 1
            time.sleep(0.001)

    except KeyboardInterrupt:
        pass
    except Exception:
        print("[ORCH] Error inesperado:\n" + traceback.format_exc(), flush=True)
    finally:
        try: cap.release()
        except Exception: pass
        print("[ORCH] Cámara liberada.", flush=True)
