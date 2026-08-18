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


# utils 
def load_cfg(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_label_map_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        label_map = json.load(f)  # { "CALMA": 0, "LEVE":1, ... } 
    # normalizamos a id2label: id -> LABEL
    if all(isinstance(v, int) for v in label_map.values()):
        # {"CALMA":0,...} -> id2label
        id2label = {v: k.upper() for k, v in label_map.items()}
    else:
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
        return None, 0.0, None
    # COCO class 0 es "person"
    idxs = (inst.pred_classes == 0).nonzero().flatten()
    if len(idxs) == 0:
        return None, 0.0, None
    # escoger la persona con mayor score
    scores = inst.scores[idxs]
    best = idxs[scores.argmax()]
    if float(inst.scores[best].item()) < thr:
        return None, 0.0, None
    mask = inst.pred_masks[best].to("cpu").numpy().astype(np.uint8) * 255
    # bbox en píxeles, solo para overlay del panel embebido (5.7) -- no participa en la clasificación.
    # Boxes.__getitem__ exige un int puro (a diferencia de Tensor.__getitem__ con pred_masks/scores).
    x1, y1, x2, y2 = inst.pred_boxes[int(best)].tensor.to("cpu").numpy()[0].tolist()
    region = {"x": int(x1), "y": int(y1), "w": int(x2 - x1), "h": int(y2 - y1)}
    return mask, float(inst.scores[best].item()), region


# modelo BiLSTM
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


# worker C 
def run_worker_C(cfg_path: str, in_q, out_q, stop_event: Event):
    cfg = load_cfg(cfg_path)
    tick = int(cfg.get("tick_size", 10))

    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ccfg = cfg.get("modulo_c", {})

    bilstm_path      = os.path.join(BASE_DIR, ccfg.get("bilstm_path", "moduloC_dataset/vectores_numpy/resultados_entrenamiento/bilstm_mejor_modelo.pth"))
    label_map_path   = os.path.join(BASE_DIR, ccfg.get("label_map_path", "moduloC_dataset/vectores_numpy/label_map.json"))
    use_gpu          = bool(ccfg.get("use_gpu", True))
    thr_det          = float(ccfg.get("detectron_score_thr", 0.7))
    analyze_every    = int(ccfg.get("analyze_every", 1))   
    detect_every     = int(ccfg.get("detect_every", 3))    
    seq_len          = int(ccfg.get("seq_len", 11))        # 11 frames -> 10 flows
    vgg_w, vgg_h     = ccfg.get("vgg_input", [224, 224])
    stale_ticks      = int(ccfg.get("stale_ticks", 30))
    min_person_ratio = float(ccfg.get("min_person_ratio", 0.15))
    # Instrumentación de cadencia (S12): imprime, por cada predicción, en qué se
    # fue el tiempo desde la anterior (esperar frames / Detectron2 / flujo
    # óptico / VGG16 / BiLSTM). Sin esto, "C emite ~2 predicciones cada 130 s"
    # no dice CUÁL de las cinco etapas es la cara. Apagado por defecto.
    debug_timing     = bool(ccfg.get("debug_timing", False))
    # Paso con el que el ORQUESTADOR ya submuestrea los frames de este módulo
    # (S12). Si es > 0, aquí no se vuelve a filtrar por analyze_every: los
    # frames que llegan son exactamente los que hay que usar, y además están
    # uniformemente espaciados, que es como se entrenó el BiLSTM.
    frame_stride     = int(ccfg.get("frame_stride", 0) or 0)
    # Tolerancia de hueco: si entre dos frames de la ventana se perdieron más
    # de max_gap_factor pasos, la secuencia deja de ser uniforme y se descarta
    # la ventana en vez de alimentar al BiLSTM con algo que no se parece a sus
    # datos de entrenamiento.
    max_gap_factor   = float(ccfg.get("max_gap_factor", 2.5))
    # Lado corto al que Detectron2 reescala la imagen antes de detectar. Su
    # valor por defecto (800) AMPLÍA un frame de 640x360 a 800x1422, o sea ~5x
    # más píxeles de los que tiene la imagen real. 0 = dejar el del modelo.
    detectron_min_size = int(ccfg.get("detectron_min_size", 0) or 0)

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
    if detectron_min_size > 0:
        # Detectar a la resolución real del frame en vez de ampliarlo (S12):
        # con MIN_SIZE_TEST=800 (el defecto) un frame de 640x360 se reescala a
        # 800x1422 y la detección cuesta ~5x más píxeles de los que la imagen
        # tiene de verdad, sin información nueva que ganar.
        dcfg.INPUT.MIN_SIZE_TEST = detectron_min_size
        dcfg.INPUT.MAX_SIZE_TEST = max(detectron_min_size * 3, 1333)
    predictor = DefaultPredictor(dcfg)

    # VGG16 features 
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
    last_warmup_tick = -9999   # avisos de "calentando" (ver bucle principal)
    last_label = "inseguro"
    last_conf  = 0.0

    # Reutilización de máscara
    last_mask = None
    last_region = None
    detect_counter = 0

    def normalize_label(lbl: str) -> str:
        """Unifica etiquetas a la taxonomía usada en fusión."""
        L = (lbl or "").upper()
        if L in ("INTENSO", "INTENSE"):
            return "AGITACIÓN"
        return L

    # El dispositivo se imprime a propósito (S12): "use_gpu: true" en la config
    # no garantiza GPU -- si torch no ve CUDA en este proceso hijo, todo cae a
    # CPU en silencio y el módulo pasa a costar un orden de magnitud más. Sin
    # este dato, medir sus tiempos no dice si el problema es el modelo o el
    # dispositivo.
    print(f"[C] Iniciado (Detectron2 + VGG16 + BiLSTM) en {device} "
          f"(use_gpu={use_gpu}, cuda_disponible={torch.cuda.is_available()}).", flush=True)

    # Cronómetros de la instrumentación de cadencia (S12).
    t_ciclo = time.time()
    t_det = 0.0        # segundos dentro de Detectron2
    t_flow = 0.0       # segundos de flujo óptico Farneback
    t_vgg = 0.0        # segundos de extracción VGG16
    t_lstm = 0.0       # segundos de BiLSTM
    n_det = 0          # detecciones ejecutadas en este ciclo
    n_frames_ciclo = 0  # frames aceptados en este ciclo
    n_ventanas_rotas = 0  # ventanas descartadas por espaciado no uniforme
    mascara_lista = False  # ya se detectó la persona para la ventana en curso

    try:
        while not stop_event.is_set():
            # poll no bloqueante SimpleQueue
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

            # Cadencia. Con frame_stride > 0 el orquestador ya mandó solo los
            # frames que hay que usar (S12): volver a filtrar aquí tiraría 2 de
            # cada 3 y además rompería el espaciado uniforme.
            if frame_stride <= 0 and (fidx % analyze_every) != 0:
                continue

            # Espaciado uniforme (S12, pendiente 5 de S11): si se perdieron
            # demasiados frames entre éste y el anterior de la ventana, la
            # secuencia deja de ser regular en el tiempo y el BiLSTM vería algo
            # distinto a sus datos de entrenamiento. Se reinicia la ventana.
            if frame_stride > 0 and idx_buffer:
                salto = fidx - idx_buffer[-1]
                if salto > frame_stride * max_gap_factor:
                    n_ventanas_rotas += 1
                    if debug_timing:
                        print(f"[C][ROTA] salto={salto} frames (max {frame_stride*max_gap_factor:.0f}) "
                              f"con ventana={len(frames_gray)}/{seq_len}", flush=True)
                    frames_gray.clear()
                    masks.clear()
                    idx_buffer.clear()
                    mascara_lista = False

            # preparación
            frame_rgb = frame_bgr[:, :, ::-1]
            H, W = frame_bgr.shape[:2]
            gray = to_gray(frame_bgr)

            # máscara de persona
            # Se detecta al abrir una ventana nueva (S12): así cada secuencia
            # analizada empieza con una máscara fresca de la persona en vez de
            # heredar la de la ventana anterior, que puede ser de varios
            # segundos antes. Entre medias vale la cadencia de siempre.
            # OJO: "ventana vacía" no basta para decidir si hay que detectar. El
            # frame de la detección se descarta (ver más abajo), así que la
            # ventana sigue vacía después de detectar: sin esta marca, cada frame
            # nuevo volvía a disparar una detección y el módulo se quedaba
            # detectando en bucle, sin llenar jamás la ventana ni emitir nada
            # (medido: 0 predicciones y ni un solo frame aceptado en 80 s).
            ventana_vacia = (len(frames_gray) == 0) and not mascara_lista
            if frame_stride > 0:
                # Con submuestreo en el orquestador, la detección va SOLO al
                # abrir la ventana. Dejar además la cadencia por contador metía
                # una detección (segundos) en mitad de la ventana, y ese parón
                # abría un hueco que rompía la ventana entera -- otra vez el
                # círculo de "romper y volver a empezar" que dejaba al módulo sin
                # emitir nada. Una detección por ventana, siempre en el mismo
                # sitio, es lo único compatible con una secuencia uniforme.
                run_detect = ventana_vacia
            else:
                run_detect = ventana_vacia or (detect_counter % detect_every == 0)
            if run_detect:
                _t = time.time()
                mask, det_score, region = person_mask_from_detectron(predictor, frame_rgb, thr=thr_det)
                t_det += time.time() - _t
                n_det += 1
                if mask is None:
                    mask = np.zeros((H, W), dtype=np.uint8)
                last_mask = mask
                last_region = region
                if ventana_vacia:
                    mascara_lista = True
                    # Este frame se usa SOLO para sacar la máscara y se descarta:
                    # la detección tarda segundos, así que si además fuera el
                    # primer frame de la ventana, entre él y el siguiente habría
                    # un hueco enorme. Con la guardia de espaciado uniforme eso
                    # rompía la ventana, la ventana rota volvía a forzar otra
                    # detección, y el módulo se quedaba en ese círculo sin emitir
                    # NUNCA (medido: 0 predicciones en 150 s). Descartándolo, la
                    # ventana empieza a llenarse ya con la máscara lista y todos
                    # sus frames quedan regularmente espaciados.
                    detect_counter += 1
                    continue
            else:
                mask = last_mask if last_mask is not None else np.zeros((H, W), dtype=np.uint8)
            detect_counter += 1

            frames_gray.append(gray)
            masks.append(mask)
            idx_buffer.append(fidx)
            n_frames_ciclo += 1
            if debug_timing and (n_frames_ciclo % 5 == 0 or len(frames_gray) == seq_len):
                print(f"[C][LATIDO] fidx={fidx} ventana={len(frames_gray)}/{seq_len} "
                      f"aceptados={n_frames_ciclo} rotas={n_ventanas_rotas}", flush=True)

            # ventana completa
            if len(frames_gray) < seq_len:
                # Aviso de "aún calentando". Se dispara por DISTANCIA en frames
                # desde el último aviso y no por (fidx % tick == 0): ver el
                # comentario del gate de emisión real, más abajo.
                #
                # Y solo si NO hay una predicción reciente (S12). Este aviso
                # existía porque C tardaba ~30-48 s en dar su primera lectura y
                # había que decir algo mientras tanto. Ahora predice cada ~1.8 s,
                # así que entre predicción y predicción metía ~200 mensajes con
                # present=False y quality=0 por corrida: ruido en las colas y
                # parpadeo de "sin detección" en el panel, sin aportar nada.
                if (fidx - last_pred_tick) > stale_ticks and (fidx - last_warmup_tick) >= tick:
                    last_warmup_tick = fidx
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

            # Predicción con la ventana más reciente.
            #
            # El gate es la DISTANCIA en frames desde la última predicción, no
            # (fidx % tick == 0) como antes (S11). Motivo: desde que el
            # orquestador descarta frames cuando este worker va atrasado (ver
            # core/orchestrator.put_drop_if_full), C ya NO recibe todos los
            # frames en orden, así que exigir un índice exacto múltiplo de tick
            # se volvió una lotería -- medido: 2 predicciones en 130s, porque
            # casi nunca caía en la cola un frame con fidx múltiplo de 15
            # justo teniendo la ventana llena. Con la distancia se conserva la
            # intención original (no repetir la ráfaga de VGG16 más seguido que
            # cada 'tick' frames) sin depender de qué frames sobrevivieron.
            if (fidx - last_pred_tick) >= tick:
                # 1) flujo óptico en ROI 
                vectors = []
                person_pixels = []
                lote = []
                for i in range(seq_len - 1):
                    _t = time.time()
                    flow_img = calc_optical_flow(frames_gray[i], frames_gray[i+1], masks[i])
                    t_flow += time.time() - _t
                    person_pixels.append(float(np.count_nonzero(masks[i])) / float(masks[i].size + 1e-6))

                    _t = time.time()
                    img = Image.fromarray(cv2.cvtColor(flow_img, cv2.COLOR_BGR2RGB))
                    lote.append(tfm(img))
                    t_vgg += time.time() - _t

                # VGG16 en UN SOLO lote de seq_len-1 imágenes en vez de una
                # pasada por imagen (S12). Es matemáticamente idéntico (VGG16
                # features no tiene BatchNorm, cada muestra es independiente)
                # pero en GPU aprovecha el paralelismo en lugar de pagar 10
                # veces el coste de lanzar el mismo grafo. Esta ráfaga era el
                # pico de coste del módulo desde S11.
                _t = time.time()
                with torch.no_grad():
                    feats = vgg(torch.stack(lote).to(device))   # [T,512,7,7]
                    vectors = feats.flatten(1).cpu().numpy()    # [T,25088]
                t_vgg += time.time() - _t

                person_ratio = float(np.mean(person_pixels)) if person_pixels else 0.0

                # 2) BiLSTM
                _t = time.time()
                X = torch.tensor(vectors, dtype=torch.float32).unsqueeze(0).to(device)  # [1,10,25088]
                with torch.no_grad():
                    logits = model(X)
                    probs = torch.softmax(logits, dim=-1).cpu().numpy()[0]
                    pred_id = int(np.argmax(probs))
                    conf = float(probs[pred_id])
                    label = normalize_label(id2label.get(pred_id, "INSEGURO"))
                t_lstm += time.time() - _t

                # 3) calidad y present
                quality = float(np.clip(person_ratio / max(min_person_ratio, 1e-6), 0.0, 1.0))
                present = person_ratio >= min_person_ratio

                # 4) publicar
                last_pred_tick = fidx
                last_label = label
                last_conf = conf

                print(f"[C][{fidx}] {label} ({conf:.2f}) | person_ratio={person_ratio:.2f}", flush=True)

                if debug_timing:
                    total = time.time() - t_ciclo
                    # "esperar" = todo lo que no fue cómputo propio: frames que
                    # aún no habían llegado (o que se descartaron por cola llena).
                    esperar = max(0.0, total - (t_det + t_flow + t_vgg + t_lstm))
                    print(f"[C][TIEMPOS] ciclo={total:.1f}s | esperar_frames={esperar:.1f}s "
                          f"| detectron={t_det:.1f}s ({n_det} det) | flujo={t_flow:.1f}s "
                          f"| vgg={t_vgg:.1f}s | bilstm={t_lstm:.2f}s "
                          f"| frames_aceptados={n_frames_ciclo} "
                          f"| ventanas_rotas={n_ventanas_rotas}", flush=True)
                t_ciclo = time.time()
                t_det = t_flow = t_vgg = t_lstm = 0.0
                n_det = 0
                n_frames_ciclo = 0
                n_ventanas_rotas = 0

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
                        "seq_idx": list(idx_buffer),
                        "region": last_region,
                    }
                )
                try:
                    out_q.put(pred.to_dict())
                except Exception:
                    pass

                # Vaciar in_q y limpiar buffers locales para evitar acumulacion de lag
                try:
                    while getattr(in_q, "_reader").poll(0):
                        in_q.get()
                except Exception:
                    pass
                frames_gray.clear()
                masks.clear()
                idx_buffer.clear()
                mascara_lista = False

    except KeyboardInterrupt:
        pass
    except Exception:
        print("[C] Error inesperado fuera del bucle:\n" + traceback.format_exc(), flush=True)
    finally:
        print("[C] Saliendo…", flush=True)
