# ui/viewer.py
# ============================================================
# Viewer sobrio y profesional:
# - HiDPI como en mod_A original
# - Header/boxes con transparencia (PIL) y tipografía limpia
# - Panel por módulos A/B/C alineable a la cara (si hay region)
# - Landmarks elegantes (opcional, tecla 'v') vía MediaPipe Holistic
# - Alternar detalles técnicos (conf/q/pr) con tecla 'i'
# - Nivel global destacado (BAJO/MEDIO/ALTO) con color semáforo
# ============================================================

# --- HiDPI antes de importar cv2/Qt ---
import os
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

import time, yaml, cv2, numpy as np
from multiprocessing import Event
from core.messages import FrameMsg, PredMsg

# PIL para texto nítido
from PIL import Image, ImageDraw, ImageFont

# MediaPipe (solo para overlay visual, NO inferencia)
try:
    import mediapipe as mp
    MP_AVAILABLE = True
except Exception:
    MP_AVAILABLE = False

# ------------------ Config visual ------------------
COLORS = {
    "BAJO":     (60, 200, 60),    # verde
    "MEDIO":    (0, 200, 255),    # cian
    "ALTO":     (0, 80, 255),     # rojo/azulado sobrio
    "INSEGURO": (160, 160, 160),  # gris
}
PANEL_BG = (45, 55, 65)      # fondo panel sobrio
HEADER_BG = (40, 40, 40)     # header/barra
ALPHA_HEADER = 0.75          # transparencia header
ALPHA_PANEL  = 0.65          # transparencia panel
HEADER_H = 68                # alto de barra superior
FONT_MAIN_SIZE = 20          # tamaño base tipografía PIL

# Box lateral
PANEL_W = 340
PANEL_H = 210
PANEL_MARGIN = 12
LINE_H = 26

# Landmarks estilo (como en mod_B)
LINE_COLOR       = (80, 120, 160)
LINE_THICK       = 1
POINT_FILL_COLOR = (100, 175, 200)
POINT_EDGE_COLOR = (50, 80, 120)
POINT_RADIUS     = 3
POINT_EDGE_THICK = 1
VIS_THR_POSE     = 0.5  # visibilidad pose

TARGET_W, TARGET_H = 720, 576 #1280, 720      # tamaño objetivo de ventana

# ================== Inicialización ==================
# Cámara: fija backend y resolución (prueba CAP_DSHOW o CAP_MSMF en Windows)
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, TARGET_W)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, TARGET_H)

# ------------------ Utilidades ------------------
def load_cfg(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def clamp01(x):
    try:
        return max(0.0, min(1.0, float(x)))
    except Exception:
        return 0.0

def load_font(size=FONT_MAIN_SIZE):
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

FONT = load_font(FONT_MAIN_SIZE)
FONT_BOLD = load_font(FONT_MAIN_SIZE + 2)

def draw_alpha_rect(img_bgr, x, y, w, h, bg, alpha=0.6):
    overlay = img_bgr.copy()
    cv2.rectangle(overlay, (x, y), (x+w, y+h), bg, -1)
    return cv2.addWeighted(overlay, alpha, img_bgr, 1 - alpha, 0)

def put_pil_lines(img_bgr, lines, x, y, line_h=26):
    # lines: list[(texto, (b,g,r), bold:bool)]
    pil = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    for i, (txt, col, is_bold) in enumerate(lines):
        font = FONT_BOLD if is_bold else FONT
        draw.text((x, y + i*line_h), str(txt), font=font, fill=(col[2], col[1], col[0], 255))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

def draw_header(img, text, color=(255,255,255)):
    h, w = img.shape[:2]
    bar_h = HEADER_H
    img[:] = draw_alpha_rect(img, 0, 0, w, bar_h, HEADER_BG, ALPHA_HEADER)
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    draw.text((16, int(bar_h*0.5)-12), text, font=FONT_BOLD, fill=(color[2], color[1], color[0], 255))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

# ====== Mapeos legibles ======
EMO_ES = {
    "angry":"Enfado", "disgust":"Asco", "fear":"Miedo", "happy":"Alegría",
    "sad":"Tristeza", "surprise":"Sorpresa", "neutral":"Neutral"
}
def _emo_es(s):
    if not s: return None
    return EMO_ES.get(str(s).lower(), str(s).capitalize())

B_LABELS = {
    "neutral":"Neutral",
    "dedos_boca":"Dedos a la boca",
    "juegos_manos":"Juegos de manos",
    "manos_cabeza":"Manos a la cabeza",
    "manos_rostro":"Manos al rostro",
    "otros":"Otros",
    "inseguro":"Inseguro"
}
def _b_es(s):
    if not s: return "Inseguro"
    return B_LABELS.get(str(s).lower(), str(s).replace("_"," ").capitalize())

C_LABELS = {
    "calma":"Calma", "leve":"Leve", "repetitivo":"Repetitivo",
    "agitación":"Agitación", "agitacion":"Agitación", "intenso":"Intenso"
}
def _c_es(s):
    if not s: return "Inseguro"
    lab = str(s).lower()
    return C_LABELS.get(lab, lab.capitalize())

def _estres_title(s):
    s = str(s or "").strip().lower()
    if s in ("bajo","medio","alto"):
        return s.capitalize()
    return s or "—"

# ====== Render “bonito” por módulo ======
def render_mod_A(pa, show_details=False):
    # label de A = nivel de estrés facial (bajo/medio/alto)
    stress = _estres_title(pa.label)
    # intentamos extraer emoción dominante desde meta (distintas claves posibles)
    emo = None; pct = None
    try:
        if pa.meta:
            emo = pa.meta.get("dominant") or pa.meta.get("emotion") or pa.meta.get("dominant_emotion")
            pct = pa.meta.get("dominant_pct") or pa.meta.get("dominant_score") or pa.meta.get("emotion_score")
    except Exception:
        pass
    emo_txt = _emo_es(emo)
    if emo_txt:
        main = f"Mód. A — Emoción: {emo_txt}" + (f" ({pct:.0f}%)" if isinstance(pct,(int,float)) else "") + f" — Estrés facial: {stress}"
    else:
        main = f"Mód. A — Estrés facial: {stress}"
    lines = [(main, (220,220,255), True)]
    if show_details:
        lines.append((f"conf={pa.conf:.2f}  q={clamp01(pa.quality):.2f}", (190,190,200), False))
    return lines

def render_mod_B(pb, show_details=False):
    gesto = _b_es(pb.label)
    lines = [(f"Mód. B — Gesto: {gesto}", (220,255,220), True)]
    if show_details:
        lines.append((f"conf={pb.conf:.2f}  q={clamp01(pb.quality):.2f}", (190,190,200), False))
    return lines

def render_mod_C(pc, show_details=False):
    act = _c_es(pc.label)
    lines = [(f"Mód. C — Actividad: {act}", (255,220,220), True)]
    if show_details:
        pr = None
        try: pr = pc.meta.get("person_ratio") if pc.meta else None
        except: pr = None
        tail = f"  pr={pr:.2f}" if isinstance(pr,(int,float)) else ""
        lines.append((f"conf={pc.conf:.2f}  q={clamp01(pc.quality):.2f}{tail}", (190,190,200), False))
    return lines

# -------- Landmarks elegantes (como mod_B) --------
def _draw_landmarks_elegant(img, results):
    if results is None or not MP_AVAILABLE:
        return
    hol = mp.solutions.holistic
    h, w = img.shape[:2]

    def to_px(lm): return int(lm.x * w), int(lm.y * h)
    def visible_pose(lm):
        v = getattr(lm, "visibility", 1.0)
        return (v is None) or (v >= VIS_THR_POSE)
    def valid_norm(lm): return 0.0 <= lm.x <= 1.0 and 0.0 <= lm.y <= 1.0

    def draw_set(landmarks, connections, check_visibility=False):
        if not landmarks: 
            return
        for i, j in connections:
            lmi = landmarks.landmark[i]; lmj = landmarks.landmark[j]
            if check_visibility and not (visible_pose(lmi) and visible_pose(lmj)):
                continue
            if not (valid_norm(lmi) and valid_norm(lmj)):
                continue
            x1, y1 = to_px(lmi); x2, y2 = to_px(lmj)
            cv2.line(img, (x1, y1), (x2, y2), LINE_COLOR, LINE_THICK, lineType=cv2.LINE_AA)
        for lm in landmarks.landmark:
            if check_visibility and not visible_pose(lm):
                continue
            if not valid_norm(lm):
                continue
            cx, cy = to_px(lm)
            cv2.circle(img, (cx, cy), POINT_RADIUS + POINT_EDGE_THICK, POINT_EDGE_COLOR, -1, lineType=cv2.LINE_AA)
            cv2.circle(img, (cx, cy), POINT_RADIUS, POINT_FILL_COLOR, -1, lineType=cv2.LINE_AA)

    if getattr(results, "pose_landmarks", None):
        draw_set(results.pose_landmarks, hol.POSE_CONNECTIONS, check_visibility=True)
    if getattr(results, "left_hand_landmarks", None):
        draw_set(results.left_hand_landmarks, hol.HAND_CONNECTIONS, check_visibility=False)
    if getattr(results, "right_hand_landmarks", None):
        draw_set(results.right_hand_landmarks, hol.HAND_CONNECTIONS, check_visibility=False)

def _process_landmarks(frame_bgr, holistic):
    if holistic is None:
        return None
    rgb = frame_bgr[:, :, ::-1]
    return holistic.process(rgb)

# Posiciona el panel alrededor de la cara sin solaparla
def place_panel_around_face(face_xywh, img_w, img_h, pw, ph, header_h, margin):
    def clamp(v, lo, hi): return max(lo, min(hi, v))
    if not face_xywh:
        px = img_w - pw - margin
        py = clamp(img_h - ph - margin, header_h + margin, img_h - ph - margin)
        return px, py
    fx, fy, fw, fh = face_xywh
    # derecha
    px = fx + fw + margin; py = clamp(fy, header_h + margin, img_h - ph - margin)
    if (px + pw) <= (img_w - margin): return px, py
    # izquierda
    px = fx - pw - margin; py = clamp(fy, header_h + margin, img_h - ph - margin)
    if px >= margin: return px, py
    # abajo
    px = clamp(fx, margin, img_w - pw - margin); py = fy + fh + margin
    if (py + ph) <= (img_h - margin): return px, py
    # arriba
    px = clamp(fx, margin, img_w - pw - margin); py = fy - ph - margin
    if py >= (header_h + margin): return px, py
    # fallback: inferior derecha
    px = img_w - pw - margin
    py = clamp(img_h - ph - margin, header_h + margin, img_h - ph - margin)
    return px, py

# ------------------ Viewer principal ------------------
def run_viewer(cfg_path: str, frames_q, preds_q, reporter_q, stop_event: Event):
    cfg = load_cfg(cfg_path)
    tick = int(cfg.get("tick_size", 10))

    # Estado UI
    last_frame = None
    last_pred = {"A": None, "B": None, "C": None}
    last_face_region = None  # (x,y,w,h) si A lo envía

    # Payload del reporter (normalizado)
    last_global = {
        "label": "INSEGURO",
        "avg_level": None,
        "n_valid": 0,
        "n_ticks": 0,
        "ts_end": None
    }

    # Opciones runtime
    draw_landmarks = True   # tecla 'v'
    show_details   = False  # tecla 'i' para ver conf/q/pr
    pin_panel_to_face = True

    # MediaPipe Holistic SOLO si está disponible y si activas 'v'
    holistic = None
    mp_ok = MP_AVAILABLE
    if mp_ok:
        try:
            holistic = mp.solutions.holistic.Holistic(static_image_mode=False)
        except Exception:
            holistic = None
            mp_ok = False

    # Título sin acentos para evitar artefactos
    win = "SISTEMA INTELIGENTE NIVEL DE ESTRES"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, TARGET_W, TARGET_H)

    print("[UI] Iniciado.", flush=True)
    try:
        while not stop_event.is_set():
            # 1) Reporter
            try:
                while getattr(reporter_q, "_reader").poll(0.0):
                    rep = reporter_q.get()
                    label = str(rep.get("label", "INSEGURO"))
                    avg_level = rep.get("avg_level", None)
                    n_valid = int(rep.get("n_valid", 0))
                    n_ticks = int(rep.get("n_ticks", 0))
                    ts_end  = rep.get("ts_end", None)
                    last_global = {
                        "label": label,
                        "avg_level": avg_level,
                        "n_valid": n_valid,
                        "n_ticks": n_ticks,
                        "ts_end": ts_end
                    }
            except Exception:
                pass

            # 2) Preds por módulo
            try:
                while getattr(preds_q, "_reader").poll(0.0):
                    pd = preds_q.get()
                    try:
                        p = PredMsg(**pd)
                        last_pred[p.module] = p
                        if p.module == "A":
                            try:
                                reg = p.meta.get("region") if p.meta else None
                                if reg:
                                    last_face_region = (int(reg["x"]), int(reg["y"]), int(reg["w"]), int(reg["h"]))
                            except Exception:
                                pass
                    except Exception:
                        continue
            except Exception:
                pass

            # 3) Frames
            got_new = False
            try:
                while getattr(frames_q, "_reader").poll(0.0):
                    fm = frames_q.get()
                    try:
                        f = FrameMsg(**fm)
                        last_frame = f.frame.copy()
                        got_new = True
                    except Exception:
                        continue
            except Exception:
                pass

            if last_frame is None:
                time.sleep(0.01)
                continue

            vis = last_frame.copy()

            # 4) Landmarks (opcional)
            if draw_landmarks and holistic is not None:
                results = _process_landmarks(vis, holistic)
                try:
                    _draw_landmarks_elegant(vis, results)
                except Exception:
                    pass

            # 5) Header global (respuesta principal)
            glabel = str(last_global.get("label", "INSEGURO")).upper()
            gcol = COLORS.get(glabel, (255,255,255))
            avg = last_global.get("avg_level", None)
            n_valid = int(last_global.get("n_valid", 0))
            n_total = int(last_global.get("n_ticks", 0))
            extra = f" | avg={avg:.2f}" if isinstance(avg, (int, float)) else ""
            header_txt = f"Nivel global: {glabel}{extra}   (ticks válidos {n_valid}/{n_total})"
            vis = draw_header(vis, header_txt, gcol)

            # 6) Panel por módulos (alineado a la cara si disponible)
            ph = PANEL_H
            pw = PANEL_W
            h_img, w_img = vis.shape[:2]
            px, py = place_panel_around_face(
                last_face_region if pin_panel_to_face else None,
                w_img, h_img, pw, ph, HEADER_H, PANEL_MARGIN
            )

            # (opcional) dibujar bbox del rostro
            if pin_panel_to_face and last_face_region is not None:
                x, y, w, h = last_face_region
                cv2.rectangle(vis, (x, y), (x + w, y + h), (255, 180, 80), 2)

            vis = draw_alpha_rect(vis, px, py, pw, ph, PANEL_BG, ALPHA_PANEL)

            # 7) Texto del panel A/B/C (formato uniforme)
            lines = []
            pa = last_pred.get("A")
            pb = last_pred.get("B")
            pc = last_pred.get("C")

            if pa is not None:
                lines += render_mod_A(pa, show_details=show_details)
            else:
                lines.append(("Mód. A — (sin datos)", (180,180,180), False))

            if pb is not None:
                lines += render_mod_B(pb, show_details=show_details)
            else:
                lines.append(("Mód. B — (sin datos)", (180,180,180), False))

            if pc is not None:
                lines += render_mod_C(pc, show_details=show_details)
            else:
                lines.append(("Mód. C — (sin datos)", (180,180,180), False))

            lines.append(("v: landmarks  |  i: detalles  |  q: salir", (200,200,200), False))
            vis = put_pil_lines(vis, lines, px + 12, py + 14, line_h=LINE_H)

            # 8) Mostrar ventana
            cv2.imshow(win, vis)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                stop_event.set(); break
            elif key == ord('v'):
                draw_landmarks = not draw_landmarks
            elif key == ord('i'):
                show_details = not show_details

            if not got_new:
                time.sleep(0.01)

    except KeyboardInterrupt:
        stop_event.set()
    finally:
        if holistic is not None:
            try: holistic.close()
            except Exception: pass
        cv2.destroyAllWindows()
        print("[UI] Saliendo…", flush=True)
