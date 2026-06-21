# -*- coding: utf-8 -*-
"""
modc_realtime_gpu.py — Jetson Orin Nano (Yahboom)
- Flujo óptico en GPU (NVOF si está, si no Farnebäck CUDA).
- Recalcula máscara cada frame (sin tracker).
- Tres presets: RAPIDO / BALANCE / CALIDAD.
- Mantiene VGG16 (features) + BiLSTM tal como entrenaste.

Requisitos:
- OpenCV con CUDA (y ojalá NVOF).
- PyTorch + torchvision.
- Detectron2 instalado para aarch64 con CUDA.
"""

import os, time, json
import cv2
import numpy as np
from PIL import Image
import torch
import torchvision.models as models
import torchvision.transforms as transforms

from detectron2.engine import DefaultPredictor
from detectron2.config import get_cfg
from detectron2 import model_zoo


#        PRESETS
PRESET = "BALANCE"   # "RAPIDO", "BALANCE", "CALIDAD"

PRESETS = {
    "RAPIDO": {
        "cam_w": 640, "cam_h": 480, "cam_fps": 30,
        "flow_w": 320, "flow_h": 240,            # más pequeño = más rápido
        "mask_every": 1,                         # recalc máscara cada frame
        "det_min_size": 360, "det_max_size": 640,
        "det_thresh": 0.75,                      # umbral persona
        "farneback": dict(numLevels=3, pyrScale=0.5, fastPyramids=False,
                          winSize=11, numIters=2, polyN=5, polySigma=1.2, flags=0),
        "use_amp": False                         # FP32 para paridad máxima con entrenamiento
    },
    "BALANCE": {
        "cam_w": 640, "cam_h": 480, "cam_fps": 30,
        "flow_w": 480, "flow_h": 360,            
        "mask_every": 1,
        "det_min_size": 448, "det_max_size": 640,
        "det_thresh": 0.75,
        "farneback": dict(numLevels=3, pyrScale=0.5, fastPyramids=False,
                          winSize=15, numIters=3, polyN=7, polySigma=1.3, flags=0),
        "use_amp": False
    },
    "CALIDAD": {
        "cam_w": 1280, "cam_h": 720, "cam_fps": 30,   
        "flow_w": 640, "flow_h": 480,                 
        "mask_every": 1,
        "det_min_size": 512, "det_max_size": 800,     
        "det_thresh": 0.70,
        "farneback": dict(numLevels=3, pyrScale=0.5, fastPyramids=False,
                          winSize=19, numIters=3, polyN=7, polySigma=1.35, flags=0),
        "use_amp": False                              
    },
}

CFG = PRESETS[PRESET]


#  Utilidades CUDA / NVOF
def has_nvof():
    return hasattr(cv2, "cuda_NvidiaOpticalFlow_1_0") or hasattr(cv2, "cuda_NvidiaOpticalFlow_2_0")

def has_cuda():
    try:
        return cv2.cuda.getCudaEnabledDeviceCount() > 0
    except Exception:
        return False


class GPUFlowEngine:
    """ Flujo óptico acelerado en GPU:
        - Prioriza NVOF; si no, Farnebäck CUDA; si no, fallback CPU.
        - Coloriza en HSV y aplica máscara (mask) al final.
    """
    def __init__(self, w=320, h=240, farne_params=None, prefer_nvof=True):
        self.w, self.h = w, h
        self.stream = cv2.cuda.Stream() if has_cuda() else None

        self.use_nvof = False
        self.use_cuda_farneback = False

        # Intenta NVOF primero
        self.nvof = None
        if prefer_nvof and has_cuda() and has_nvof():
            try:
                # perfPreset 1=FAST, 2=MEDIUM, 3=QUALITY
                self.nvof = cv2.cuda_NvidiaOpticalFlow_1_0.create(self.w, self.h, perfPreset=2)
                self.use_nvof = True
            except Exception:
                self.nvof = None
                self.use_nvof = False

        # Si no hay NVOF salta a Farnebäck CUDA
        self.fb = None
        if (not self.use_nvof) and has_cuda():
            p = farne_params or dict(numLevels=3, pyrScale=0.5, fastPyramids=False,
                                     winSize=15, numIters=3, polyN=7, polySigma=1.3, flags=0)
            try:
                self.fb = cv2.cuda_FarnebackOpticalFlow.create(**p)
                self.use_cuda_farneback = True
            except Exception:
                self.fb = None
                self.use_cuda_farneback = False

        # Buffers host
        self._hsv = np.zeros((self.h, self.w, 3), np.uint8)

    def _upload_resize_gray(self, gray_np):
        g_src = cv2.cuda_GpuMat()
        g_src.upload(gray_np, stream=self.stream)
        g_small = cv2.cuda.resize(g_src, (self.w, self.h), stream=self.stream)
        return g_small

    def _resize_mask_cpu(self, mask_np):
        return cv2.resize(mask_np, (self.w, self.h), interpolation=cv2.INTER_NEAREST)

    def calc_and_colorize(self, prev_gray, next_gray, mask_np):
        """ Devuelve RGB uint8 (hsv->bgr) con máscara aplicada, en tamaño (flow_w x flow_h). """
        if self.use_nvof or self.use_cuda_farneback:
            g_prev_small = self._upload_resize_gray(prev_gray)
            g_next_small = self._upload_resize_gray(next_gray)

        if self.use_nvof:
            g_flow = self.nvof.calc(g_prev_small, g_next_small, None)  # denso
            flow = g_flow.download(self.stream)
            if self.stream is not None:
                self.stream.waitForCompletion()
        elif self.use_cuda_farneback:
            g_flow = self.fb.calc(g_prev_small, g_next_small, None, stream=self.stream)
            flow = g_flow.download(self.stream)
            if self.stream is not None:
                self.stream.waitForCompletion()
        else:
            # CPU fallback
            prev_small = cv2.resize(prev_gray, (self.w, self.h))
            next_small = cv2.resize(next_gray, (self.w, self.h))
            flow = cv2.calcOpticalFlowFarneback(prev_small, next_small, None,
                                                0.5, 3, 15, 3, 7, 1.3, 0)

        # Colorización 
        mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
        hsv = self._hsv
        hsv[..., 0] = (ang * 180 / np.pi / 2).astype(np.uint8)
        hsv[..., 1] = 255
        hsv[..., 2] = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

        # Aplica máscara reducida
        mask_small = self._resize_mask_cpu(mask_np)
        return cv2.bitwise_and(rgb, rgb, mask=mask_small)


#   Modelos / Transform
torch.backends.cudnn.benchmark = True
try:
    torch.set_float32_matmul_precision("high")
except Exception:
    pass

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Label map
with open("moduloC_dataset/vectores_numpy/label_map.json", "r") as f:
    label_map = json.load(f)
id2label = {v: k.upper() for k, v in label_map.items()}

# VGG16 (features) 
vgg = models.vgg16(weights=models.VGG16_Weights.IMAGENET1K_V1).features.to(DEVICE).eval()
transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                         std=[0.229, 0.224, 0.225])
])

# BiLSTM
class BiLSTMStressNet(torch.nn.Module):
    def __init__(self, input_size=25088, hidden_size=256, num_layers=2, num_classes=4):
        super().__init__()
        self.lstm = torch.nn.LSTM(input_size, hidden_size, num_layers=num_layers,
                                  batch_first=True, bidirectional=True)
        self.dropout = torch.nn.Dropout(0.5)
        self.fc = torch.nn.Linear(hidden_size * 2, num_classes)
    def forward(self, x):
        out, _ = self.lstm(x)
        out = self.dropout(out[:, -1, :])
        return self.fc(out)

model = BiLSTMStressNet().to(DEVICE)
state = torch.load(
    "moduloC_dataset/vectores_numpy/resultados_entrenamiento/bilstm_mejor_modelo.pth",
    map_location=DEVICE
)
model.load_state_dict(state)
model.eval()

USE_AMP = CFG["use_amp"]  # por paridad con entrenamiento FP32


#   Detectron2 (Mask R-CNN)
def build_predictor(min_size, max_size, score_thresh):
    cfg = get_cfg()
    cfg.merge_from_file(model_zoo.get_config_file(
        "COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml"
    ))
    cfg.MODEL.WEIGHTS = model_zoo.get_checkpoint_url(
        "COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml"
    )
    cfg.MODEL.DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    cfg.INPUT.MIN_SIZE_TEST = int(min_size)
    cfg.INPUT.MAX_SIZE_TEST = int(max_size)
    cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = float(score_thresh)
    return DefaultPredictor(cfg)

predictor = build_predictor(CFG["det_min_size"], CFG["det_max_size"], CFG["det_thresh"])


#   Cámara (USB V4L2 + MJPG)
def open_camera(device_index=0, w=640, h=480, fps=30):
    cap = cv2.VideoCapture(device_index, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return cap



# Flujo óptico (GPU)
FLOW_W, FLOW_H = CFG["flow_w"], CFG["flow_h"]
flow_engine = GPUFlowEngine(
    w=FLOW_W, h=FLOW_H,
    farne_params=CFG["farneback"],
    prefer_nvof=True
)


# Captura / Máscara
MASK_EVERY = CFG["mask_every"]  # = 1 
FRAME_STRIDE = 1                

def capturar_secuencia(cap):
    frames_gray, masks = [], []
    last_mask = None
    count = 0
    i = 0
    while len(frames_gray) < 11:
        ok, frame = cap.read()
        if not ok:
            break

        if (count % FRAME_STRIDE) != 0:
            count += 1
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # recalcula máscara según MASK_EVERY
        if (i % MASK_EVERY) == 0 or last_mask is None:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            outputs = predictor(frame_rgb)
            instances = outputs["instances"]
            mask = np.zeros_like(gray, dtype=np.uint8)
            # clase 0 en COCO = person
            for k, cls in enumerate(instances.pred_classes):
                if int(cls.item()) == 0:
                    mask = (instances.pred_masks[k].detach().cpu().numpy().astype(np.uint8) * 255)
                    break
            last_mask = mask
        else:
            mask = last_mask

        frames_gray.append(gray)
        masks.append(mask)
        i += 1
        count += 1

    return frames_gray, masks


# Inferencia
@torch.inference_mode()
def predecir_estres(frames_gray, masks):
    vectores = []
    for i in range(10):
        # Flujo óptico en GPU 
        flow_img_small = flow_engine.calc_and_colorize(frames_gray[i], frames_gray[i+1], masks[i])

        # 224x224 para VGG 
        img224 = cv2.resize(flow_img_small, (224, 224), interpolation=cv2.INTER_AREA)
        tensor = transform(Image.fromarray(img224).convert("RGB")).unsqueeze(0).to(DEVICE, non_blocking=True)

        with torch.cuda.amp.autocast(enabled=USE_AMP and (DEVICE.type == "cuda")):
            feat = vgg(tensor)  # [1, 512, 7, 7]
        vectores.append(feat.flatten().cpu().numpy())

    entrada = torch.tensor(np.stack(vectores), dtype=torch.float32).unsqueeze(0).to(DEVICE, non_blocking=True)

    with torch.cuda.amp.autocast(enabled=USE_AMP and (DEVICE.type == "cuda")):
        salida = model(entrada)

    pred_idx = salida.argmax(dim=1).item()
    return id2label[pred_idx]


# MAIN
def main():
    print(f"Preset activo: {PRESET}")
    print("CUDA devices:", getattr(cv2.cuda, "getCudaEnabledDeviceCount", lambda:0)())
    print("Tiene NVOF:", has_nvof())

    cap = open_camera(0, CFG["cam_w"], CFG["cam_h"], CFG["cam_fps"])
    if not cap.isOpened():
        print(" No se pudo abrir la cámara.")
        return

    # Ventana 
    cv2.namedWindow("Nivel de estrés", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Nivel de estrés", 640, 480)

    # Warm-up VGG
    dummy = np.zeros((224, 224, 3), dtype=np.uint8)
    x = transform(Image.fromarray(dummy)).unsqueeze(0).to(DEVICE)
    with torch.cuda.amp.autocast(enabled=False):
        _ = vgg(x)

    print("\n Presiona 'q' para salir...")

    try:
        while True:
            t0 = time.time()
            frames, masks = capturar_secuencia(cap)
            if len(frames) < 11:
                print("Secuencia incompleta.")
                break

            t1 = time.time()
            pred = predecir_estres(frames, masks)
            t2 = time.time()

            # Mostrar 
            frame_disp = cv2.cvtColor(frames[-1], cv2.COLOR_GRAY2BGR)
            frame_disp = cv2.resize(frame_disp, (640, 480))
            color = (0, 255, 0) if pred == "CALMA" else (0, 165, 255) if pred == "LEVE" else (255, 0, 0) if pred == "REPETITIVO" else (0, 0, 255)
            cv2.putText(frame_disp, f"Estres: {pred}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
            cv2.imshow("Nivel de estrés", frame_disp)

            print(f" captura+mask: {t1 - t0:.2f}s | inferencia: {t2 - t1:.2f}s | ciclo: {t2 - t0:.2f}s | pred: {pred}")

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
