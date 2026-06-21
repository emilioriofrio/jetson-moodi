# modules/mod_c.py
import os, time, yaml, json, traceback
import numpy as np
from collections import deque
from multiprocessing import Event
from core.messages import PredMsg

# Torch / vision
import torch
import torchvision.models as models
import torchvision.transforms as T
from PIL import Image

# Detectron2
from detectron2.engine import DefaultPredictor
from detectron2.config import get_cfg
from detectron2 import model_zoo

# OpenCV
import cv2


# ----------------- utils -----------------
def load_cfg(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_label_map_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        label_map = json.load(f)  # { "CALMA": 0, "LEVE":1, ... } o al revés
    # normalizamos a id2label: id -> LABEL
    if all(isinstance(v, int) for v in label_map.values()):
        # {"CALMA":0,...} -> id2label
        id2label = {v: k.upper() for k, v in label_map.items()}
    else:
        # {"0":"CALMA",...} -> id2label
        id2label = {int(k): str(v).upper() for k, v in label_map.items()}
    return id2label

def to_gray(bgr):
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

def calc_optical_flow(prev_gray, next_gray, mask):
    # Farneback
    flow = cv2.calcOpticalFlowFarneback(prev_gray, next_gray, None,
                                        0.5, 3, 15, 3, 5, 1.2, 0)
    mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
    hsv = np.zeros((prev_gray.shape[0], prev_gray.shape[1], 3), dtype=np.uint8)
    hsv[..., 0] = (ang * 180 / np.pi / 2).astype(np.uint8)
    hsv[..., 1] = 255
    hsv[..., 2] = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    if mask is not None:
        return cv2.bitwise_and(rgb, rgb, mask=mask)
    return rgb

def person_mask_from_detectron(predictor, frame_rgb, thr=0.7):
    out = predictor(frame_rgb)
    inst = out["instances"]
    if len(inst) == 0:
        return None, 0.0
    # COCO class 0 es "person"
    idxs = (inst.pred_classes == 0).nonzero().flatten()
    if len(idxs) == 0:
        return None, 0.0
    # escoger la persona con mayor score
    scores = inst.scores[idxs]
    best = idxs[scores.argmax()]
    if float(inst.scores[best].item()) < thr:
        return None, 0.0
    mask = inst.pred_masks[best].to("cpu").numpy().astype(np.uint8) * 255
    return mask, float(inst.scores[best].item())


# ----------------- modelo BiLSTM -----------------
class BiLSTMStressNet(torch.nn.Module):
    def __init__(self, input_size=25088, hidden_size=256, num_layers=2, num_classes=4):
        super().__init__()
        self.lstm = torch.nn.LSTM(input_size, hidden_size, num_layers=num_layers,
                                  batch_first=True, bidirectional=True)
        self.dropout = torch.nn.Dropout(0.5)
        self.fc = torch.nn.Linear(hidden_size * 2, num_classes)

    def forward(self, x):
        # x: [B, T, 25088]
        out, _ = self.lstm(x)
        out = self.dropout(out[:, -1, :])
        return self.fc(out)


# ----------------- worker C -----------------
def run_worker_C(cfg_path: str, in_q, out_q, stop_event: Event):
    cfg = load_cfg(cfg_path)
    tick = int(cfg.get("tick_size", 10))

    # BASE_DIR = modelo_IA (tres niveles arriba de sistem_IA/modules/mod_c.py)
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ccfg = cfg.get("modulo_c", {})

    bilstm_path      = os.path.join(BASE_DIR, ccfg.get("bilstm_path", "moduloC_dataset/vectores_numpy/resultados_entrenamiento/bilstm_mejor_modelo.pth"))
    label_map_path   = os.path.join(BASE_DIR, ccfg.get("label_map_path", "moduloC_dataset/vectores_numpy/label_map.json"))
    use_gpu          = bool(ccfg.get("use_gpu", True))
    thr_det          = float(ccfg.get("detectron_score_thr", 0.7))
    analyze_every    = int(ccfg.get("analyze_every", 1))   # ⚠️ Para frames consecutivos, dejar en 1
    detect_every     = int(ccfg.get("detect_every", 3))    # Detectron2 cada N frames (reutiliza máscara entre medio)
    seq_len          = int(ccfg.get("seq_len", 11))        # 11 frames => 10 flows
    vgg_w, vgg_h     = ccfg.get("vgg_input", [224, 224])
    stale_ticks      = int(ccfg.get("stale_ticks", 30))
    min_person_ratio = float(ccfg.get("min_person_ratio", 0.15))

    # device
    device = torch.device("cuda" if (use_gpu and torch.cuda.is_available()) else "cpu")

    # id2label
    id2label = load_label_map_json(label_map_path)

    # Detectron2 predictor
    dcfg = get_cfg()
    dcfg.merge_from_file(model_zoo.get_config_file("COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml"))
    dcfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = thr_det
    dcfg.MODEL.WEIGHTS = model_zoo.get_checkpoint_url("COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml")
    dcfg.MODEL.DEVICE = "cuda" if (device.type == "cuda") else "cpu"
    predictor = DefaultPredictor(dcfg)

    # VGG16 features (congelado)
    vgg = models.vgg16(weights=models.VGG16_Weights.IMAGENET1K_V1).features.to(device).eval()
    for p in vgg.parameters():
        p.requires_grad = False

    tfm = T.Compose([
        T.Resize((vgg_h, vgg_w)),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406],
                    std=[0.229, 0.224, 0.225])
    ])

    # BiLSTM
    model = BiLSTMStressNet().to(device).eval()
    state = torch.load(bilstm_path, map_location=device)
    model.load_state_dict(state)

    # Buffers
    frames_gray = deque(maxlen=seq_len)
    masks       = deque(maxlen=seq_len)
    idx_buffer  = deque(maxlen=seq_len)

    last_pred_tick = -9999
    last_label = "inseguro"
    last_conf  = 0.0

    # Reutilización de máscara para aliviar Detectron2
    last_mask = None
    detect_counter = 0

    def normalize_label(lbl: str) -> str:
        """Unifica etiquetas a la taxonomía usada en fusión."""
        L = (lbl or "").upper()
        if L in ("INTENSO", "INTENSE"):
            return "AGITACIÓN"
        return L

    print("[C] Iniciado (Detectron2 + VGG16 + BiLSTM).", flush=True)

    try:
        while not stop_event.is_set():
            # poll no bloqueante (SimpleQueue)
            try:
                if not getattr(in_q, "_reader").poll(0.02):
                    time.sleep(0.01)
                    continue
                msg = in_q.get()
            except Exception:
                time.sleep(0.01)
                continue

            fidx = msg.frame_idx
            frame_bgr = msg.frame

            # Procesa según cadence deseada (para consecutividad, analyze_every=1)
            if (fidx % analyze_every) != 0:
                continue

            # preparación
            frame_rgb = frame_bgr[:, :, ::-1]
            H, W = frame_bgr.shape[:2]
            gray = to_gray(frame_bgr)

            # máscara de persona (Detectron cada detect_every frames, reutiliza entre medio)
            run_detect = (detect_counter % detect_every == 0)
            if run_detect:
                mask, det_score = person_mask_from_detectron(predictor, frame_rgb, thr=thr_det)
                if mask is None:
                    mask = np.zeros((H, W), dtype=np.uint8)
                last_mask = mask
            else:
                mask = last_mask if last_mask is not None else np.zeros((H, W), dtype=np.uint8)
            detect_counter += 1

            frames_gray.append(gray)
            masks.append(mask)
            idx_buffer.append(fidx)

            # ¿tenemos ventana completa?
            if len(frames_gray) < seq_len:
                # solo publicamos en tick, como “inseguro” por staleness si aplica
                if fidx % tick == 0:
                    if (fidx - last_pred_tick) > stale_ticks:
                        last_label = "inseguro"
                        last_conf = 0.0
                    pred = PredMsg(
                        module="C",
                        frame_idx=fidx,
                        ts=time.time(),
                        label=last_label,
                        conf=last_conf,
                        quality=0.0,
                        present=False,
                        meta={"ready": False, "need_frames": seq_len - len(frames_gray), "seq_idx": list(idx_buffer)}
                    )
                    try:
                        out_q.put(pred.to_dict())
                    except Exception:
                        pass
                continue

            # cuando llega un tick -> calculamos predicción con la ventana más reciente
            if fidx % tick == 0:
                # 1) flujos ópticos en ROI (máscara por frame i)
                vectors = []
                person_pixels = []
                for i in range(seq_len - 1):
                    flow_img = calc_optical_flow(frames_gray[i], frames_gray[i+1], masks[i])
                    person_pixels.append(float(np.count_nonzero(masks[i])) / float(masks[i].size + 1e-6))

                    # VGG features
                    img = Image.fromarray(cv2.cvtColor(flow_img, cv2.COLOR_BGR2RGB))
                    x = tfm(img).unsqueeze(0).to(device)  # [1,3,224,224]
                    with torch.no_grad():
                        feat = vgg(x)  # [1,512,7,7]
                        vectors.append(feat.flatten().cpu().numpy())  # 25088

                person_ratio = float(np.mean(person_pixels)) if person_pixels else 0.0

                # 2) BiLSTM
                X = torch.tensor(np.stack(vectors), dtype=torch.float32).unsqueeze(0).to(device)  # [1,10,25088]
                with torch.no_grad():
                    logits = model(X)
                    probs = torch.softmax(logits, dim=-1).cpu().numpy()[0]
                    pred_id = int(np.argmax(probs))
                    conf = float(probs[pred_id])
                    label = normalize_label(id2label.get(pred_id, "INSEGURO"))

                # 3) calidad y present
                quality = float(np.clip(person_ratio / max(min_person_ratio, 1e-6), 0.0, 1.0))
                present = person_ratio >= min_person_ratio

                # 4) publicar
                last_pred_tick = fidx
                last_label = label
                last_conf = conf

                print(f"[C][{fidx}] {label} ({conf:.2f}) | person_ratio={person_ratio:.2f}", flush=True)

                pred = PredMsg(
                    module="C",
                    frame_idx=fidx,
                    ts=time.time(),
                    label=label,
                    conf=conf,
                    quality=quality,
                    present=present,
                    meta={
                        "person_ratio": round(person_ratio, 3),
                        "seq_idx": list(idx_buffer)
                    }
                )
                try:
                    out_q.put(pred.to_dict())
                except Exception:
                    pass

    except KeyboardInterrupt:
        pass
    except Exception:
        print("[C] Error inesperado fuera del bucle:\n" + traceback.format_exc(), flush=True)
    finally:
        print("[C] Saliendo…", flush=True)
