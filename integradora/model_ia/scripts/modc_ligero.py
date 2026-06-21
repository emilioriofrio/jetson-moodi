import os, time, json
import cv2, numpy as np
import torch
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image
from detectron2.engine import DefaultPredictor
from detectron2.config import get_cfg
from detectron2 import model_zoo

# Ajustes de rendimiento PyTorch 
torch.backends.cudnn.benchmark = True
try:
    torch.set_float32_matmul_precision("high")
except Exception:
    pass

# LABEL MAP
with open("moduloC_dataset/vectores_numpy/label_map.json", "r") as f:
    label_map = json.load(f)
id2label = {v: k.upper() for k, v in label_map.items()}

# DEVICE 
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# VGG16 (features) 
vgg = models.vgg16(weights=models.VGG16_Weights.IMAGENET1K_V1).features.to(DEVICE).eval()
transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                         std=[0.229, 0.224, 0.225])
])

# Detectron2 más ligero 
cfg = get_cfg()
cfg.merge_from_file(model_zoo.get_config_file(
    "COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml"
))
cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = 0.85  
cfg.MODEL.WEIGHTS = model_zoo.get_checkpoint_url(
    "COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml"
)
cfg.MODEL.DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Reducir tamaño de entrada en test
cfg.INPUT.MIN_SIZE_TEST = 360   
cfg.INPUT.MAX_SIZE_TEST = 640   

predictor = DefaultPredictor(cfg)

# LSTM 
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

# Parámetros
def calc_optical_flow(prev_small, next_small, mask_small):
    flow = cv2.calcOpticalFlowFarneback(
        prev_small, next_small, None,
        0.5, 3, 9, 2, 5, 1.1, 0
    )
    mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
    hsv = np.zeros((*prev_small.shape, 3), dtype=np.uint8)
    hsv[..., 0] = (ang * 180 / np.pi / 2).astype(np.uint8)
    hsv[..., 1] = 255
    hsv[..., 2] = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return cv2.bitwise_and(rgb, rgb, mask=mask_small)

# Captura (USB cam con V4L2 + MJPG)
def open_camera(device_index=0, w=640, h=480, fps=30):
    cap = cv2.VideoCapture(device_index, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return cap


MASK_EVERY = 5   # recalcular máscara cada 5 frames
SMALL_W, SMALL_H = 320, 240  # para flujo óptico

def capturar_secuencia(cap):
    frames_gray = []
    masks = []
    last_mask = None
    i = 0
    while len(frames_gray) < 11:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # cada N frames
        if (i % MASK_EVERY) == 0 or last_mask is None:
            # Detectron2 espera RGB
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            outputs = predictor(frame_rgb)
            instances = outputs["instances"]
            mask = np.zeros_like(gray, dtype=np.uint8)
            # cls==0 → person en COCO
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
    return frames_gray, masks

# Inferencia
@torch.inference_mode()
def predecir_estrés(frames_gray, masks):
    vectores = []
    for i in range(10):
        # reescalar a small para flujo
        prev_small = cv2.resize(frames_gray[i], (SMALL_W, SMALL_H))
        next_small = cv2.resize(frames_gray[i+1], (SMALL_W, SMALL_H))
        mask_small = cv2.resize(masks[i], (SMALL_W, SMALL_H), interpolation=cv2.INTER_NEAREST)

        flow_img = calc_optical_flow(prev_small, next_small, mask_small)

        # 224×224 (VGG)
        img = Image.fromarray(flow_img).convert("RGB")
        tensor = transform(img).unsqueeze(0).to(DEVICE, non_blocking=True)

        with torch.cuda.amp.autocast(enabled=DEVICE.type == "cuda"):
            feat = vgg(tensor)           # [1, 512, 7, 7]
        vectores.append(feat.flatten().cpu().numpy())

    entrada = torch.tensor(np.stack(vectores), dtype=torch.float32).unsqueeze(0).to(DEVICE, non_blocking=True)
    with torch.cuda.amp.autocast(enabled=DEVICE.type == "cuda"):
        salida = model(entrada)
    pred = salida.argmax(dim=1).item()
    return id2label[pred]

def main():
    cap = open_camera(0, 640, 480, 30)  # open_camera_csi()
    if not cap.isOpened():
        print("No se pudo abrir la cámara.")
        return

    # Ventana
    cv2.namedWindow("Nivel de estrés", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Nivel de estrés", 640, 480)

    print("\n Presiona 'q' para salir...")

    # Warm-up ligero 
    dummy = np.zeros((224, 224, 3), dtype=np.uint8)
    x = transform(Image.fromarray(dummy)).unsqueeze(0).to(DEVICE)
    with torch.cuda.amp.autocast(enabled=DEVICE.type == "cuda"):
        _ = vgg(x)

    while True:
        t0 = time.time()
        frames, masks = capturar_secuencia(cap)
        if len(frames) < 11:
            print("Secuencia incompleta.")
            break

        pred = predecir_estrés(frames, masks)

        # Overlay
        frame_disp = cv2.cvtColor(frames[-1], cv2.COLOR_GRAY2BGR)
        frame_disp = cv2.resize(frame_disp, (640, 480))
        color = (0, 255, 0) if pred == "CALMA" else (0, 165, 255) if pred == "LEVE" else (255, 0, 0) if pred == "REPETITIVO" else (0, 0, 255)
        cv2.putText(frame_disp, f"Estres: {pred}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
        cv2.imshow("Nivel de estrés", frame_disp)

        #latencia por ciclo
        print(f"Latencia ciclo: {time.time() - t0:.2f}s | Pred: {pred}")

        # Tecla
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
