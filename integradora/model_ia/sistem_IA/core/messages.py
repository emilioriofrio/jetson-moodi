from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional
import numpy as np
import time

# Mensaje de frame que envía el orquestador a cada módulo
@dataclass
class FrameMsg:
    frame_idx: int
    ts: float
    frame: np.ndarray  # BGR

    @staticmethod
    def build(frame_idx: int, frame: np.ndarray) -> "FrameMsg":
        return FrameMsg(frame_idx=frame_idx, ts=time.time(), frame=frame)

# Mensaje de predicción que cada módulo envía al fusionador
@dataclass
class PredMsg:
    module: str             # "A" | "B" | "C"
    frame_idx: int          # tick
    ts: float               # time.time()
    label: str              # salida detect
    conf: float             # 0..1
    quality: float          # 0..1
    present: bool           # hay rostro/persona/gesto válido
    meta: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

# mensaje que emite el fusionador por tick hacia el reporter
@dataclass
class FusionTickMsg:
    frame_idx: int
    ts: float
    level: Optional[int]     # 0..3 o None si nseguro
    state: str               # "init"|"estable"|"sube"|"baja"|"espera"|"inseguro"
    target: Optional[int] = None  # target calculado ese tick -> debug

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
