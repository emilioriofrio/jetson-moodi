"""Pruebas headless de lo tocado en S12 (widgets reales, sin hardware).

Cubre el vigilante de congelamiento del fondo animado, la mitigación de audio
saturado y la selección de variantes de clip. Se ejecuta sin cámara, sin serie
y sin display: QT_QPA_PLATFORM=offscreen.
"""
import os, sys, tempfile, shutil, types
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/home/jetson/bmo_unified")
from PyQt5.QtWidgets import QApplication
from PyQt5.QtMultimedia import QMediaPlayer
app = QApplication([])

ok = fail = 0
def check(name, cond):
    global ok, fail
    if cond: ok += 1; print(f"  OK   {name}")
    else:    fail += 1; print(f"  FALLA {name}")

import ui.animation_player as ap_mod
from ui.animation_player import AnimationPlayer


class FakePlayer:
    """Reproductor de mentira: permite simular un congelamiento sin depender de
    GStreamer ni de esperar minutos reales."""
    def __init__(self):
        self.pos = 0
        self._state = QMediaPlayer.PlayingState
        self.status = QMediaPlayer.BufferedMedia
        self.medias = []
        self.volumen = None
        self.plays = 0
    # --- API que usa AnimationPlayer ---
    def position(self): return self.pos
    def duration(self): return 8200
    def state(self): return self._state
    def mediaStatus(self): return self.status
    def setPosition(self, ms): self.pos = ms
    def setMedia(self, media): self.medias.append(media)
    def setMuted(self, _m): pass
    def setVolume(self, v): self.volumen = v
    def play(self): self.plays += 1


def nuevo_player(tmpdir):
    """AnimationPlayer real, con un clip de mentira y el reproductor sustituido."""
    open(os.path.join(tmpdir, "clip.mp4"), "wb").close()
    pl = AnimationPlayer(tmpdir)
    pl._loop_timer.stop()
    pl._watchdog.stop()          # el disparo lo controla la prueba, no el reloj
    pl._player = FakePlayer()
    pl._last_reload_ms = 0        # sin recarga previa: fuera del periodo de gracia
    pl._last_advance_ms = ap_mod._now_ms()
    pl._last_pos = -1
    return pl


tmp = tempfile.mkdtemp()

print("== Vigilante: reproduccion normal no interviene ==")
p = nuevo_player(tmp)
for i in range(6):
    p._player.pos = i * 1000
    p._check_watchdog()
check("no recarga si la posicion avanza", p._recuperaciones == 0)
check("no toco el medio", p._player.medias == [])

print("== Vigilante: detecta congelamiento y recarga ==")
p = nuevo_player(tmp)
p._player.pos = 4000
p._check_watchdog()                       # primera muestra: registra la posicion
ap_mod._now_ms = lambda base=ap_mod._now_ms(): base + 3000   # +3 s: aun por debajo del umbral
p._check_watchdog()
check("3 s parado todavia no dispara", p._recuperaciones == 0)
ahora = ap_mod._now_ms()
ap_mod._now_ms = lambda base=ahora: base + 2000              # 5 s en total
p._check_watchdog()
check("5 s parado dispara la recuperacion", p._recuperaciones == 1)
check("recargo el medio", len(p._player.medias) == 2)  # QMediaContent() vacio + clip
check("volvio a reproducir", p._player.plays >= 1)

print("== Vigilante: periodo de gracia tras recargar ==")
ahora = ap_mod._now_ms()
ap_mod._now_ms = lambda base=ahora: base + 1000
p._check_watchdog()
check("no vuelve a intervenir dentro de la gracia", p._recuperaciones == 1)

print("== Vigilante: no confunde 'cargando' con congelado ==")
p = nuevo_player(tmp)
p._player.status = QMediaPlayer.LoadingMedia
p._player.pos = 0
base = ap_mod._now_ms()
for salto in (0, 5000, 10000):
    ap_mod._now_ms = lambda b=base, s=salto: b + s
    p._check_watchdog()
check("cargando no dispara recuperacion", p._recuperaciones == 0)

print("== Recarga preventiva ==")
ap_mod._now_ms = lambda: base
p = nuevo_player(tmp)
p._last_reload_ms = base - (ap_mod.PREVENTIVE_RELOAD_MS + 1000)
p._player.pos = 500          # recien dio la vuelta
p._check_watchdog()
check("recarga preventiva al vencer el plazo", len(p._player.medias) == 2)
ap_mod._now_ms = lambda: base
p = nuevo_player(tmp)
p._last_reload_ms = base - (ap_mod.PREVENTIVE_RELOAD_MS + 1000)
p._player.pos = 5000         # en mitad del clip: se espera a la vuelta
p._check_watchdog()
check("no recarga en mitad del clip", p._player.medias == [])

print("== Volumen perceptual ==")
p = nuevo_player(tmp)
valores = []
for v in (0, 20, 50, 80, 100):
    p._volume = v
    valores.append(p._volumen_lineal())
check("0 -> 0 y 100 -> 100", valores[0] == 0 and valores[-1] == 100)
check("es monotona creciente", all(a < b for a, b in zip(valores, valores[1:])))
check("80 atenua de verdad (antes 80, ahora ~35)", 25 <= valores[3] <= 45)
check("50 esta muy por debajo del lineal", valores[2] < 25)

print("== Seleccion de variante de clip ==")
p = nuevo_player(tmp)
ruta = "/home/jetson/integradora/animaciones/Animación Audio 12 - Sonreir.mp4"
if os.path.exists(ruta):
    p._muted = True
    check("silenciado -> variante sin audio", ap_mod._MUTED_SUBDIR in p._playback_path(ruta))
    p._muted = False
    p._volume = 80
    check("con audio -> variante normalizada", ap_mod._NORM_SUBDIR in p._playback_path(ruta))
    p._volume = 0
    check("volumen 0 -> variante sin audio", ap_mod._MUTED_SUBDIR in p._playback_path(ruta))
    norm = os.path.join(os.path.dirname(ruta), ap_mod._NORM_SUBDIR)
    originales = [f for f in os.listdir(os.path.dirname(ruta)) if f.endswith(".mp4")]
    check("hay variante normalizada de cada clip",
          os.path.isdir(norm) and set(os.listdir(norm)) >= set(originales))
else:
    check("clips de animacion presentes", False)

print("== Vigilante del tactil ==")
import core.touch_monitor as tm_mod
BITS_MT = hex((1 << 0x35) | (1 << 0x36))[2:]   # ABS_MT_POSITION_X/Y
check("reconoce por ejes multitactil", tm_mod._es_tactil("loquesea", BITS_MT))
check("reconoce por nombre", tm_mod._es_tactil("ILITEK Multi-Touch", ""))
check("no confunde un raton", not tm_mod._es_tactil("Logitech USB Receiver Mouse", "143"))
falso = os.path.join(tmp, "devices")
open(falso, "w").write(
    'I: Bus=0003\nN: Name="Logitech USB Receiver Mouse"\nB: ABS=143\n\n'
    'I: Bus=0003\nN: Name="Panel Touchscreen"\nB: ABS=%s\n' % BITS_MT)
tm_mod.DEVICES_PATH = falso
check("detecta el tactil del archivo", tm_mod.detectar_tactil() == "Panel Touchscreen")
open(falso, "w").write('I: Bus=0003\nN: Name="Logitech USB Receiver Mouse"\nB: ABS=143\n')
check("sin tactil devuelve None", tm_mod.detectar_tactil() is None)
tm_mod.DEVICES_PATH = "/proc/bus/input/devices"

print("== Modulo C: emision por distancia y espaciado uniforme ==")
sys.path.insert(0, "/home/jetson/integradora/model_ia/sistem_IA")
import yaml
cfg = yaml.safe_load(open("/home/jetson/integradora/model_ia/sistem_IA/config/runtime.yaml"))
ccfg = cfg["modulo_c"]
check("claves nuevas presentes en runtime.yaml",
      all(k in ccfg for k in ("frame_stride", "detectron_min_size", "max_gap_factor",
                              "queue_maxsize", "debug_timing")))
check("submuestreo y resolucion nativa activos (medidos en el robot)",
      int(ccfg["frame_stride"]) == 3 and int(ccfg["detectron_min_size"]) == 360)
check("la cola de C aguanta su propia deteccion",
      int(ccfg["queue_maxsize"]) >= ccfg["seq_len"] * 2)
check("debug_timing apagado fuera de las mediciones", ccfg["debug_timing"] is False)
check("fps sigue en 30 o 25 (regla de la camara)", cfg["fps"] in (25, 30))

# La GPU del Modulo C: run.py apaga CUDA para todo el proceso y los hijos
# heredan ese entorno, asi que C corria en CPU pese a use_gpu: true. Esta
# comprobacion evita que la correccion se pierda en un merge distraido.
fuente_run = open("/home/jetson/integradora/model_ia/sistem_IA/run.py").read()
check("entry_worker_c devuelve la GPU cuando use_gpu es true",
      "CUDA_VISIBLE_DEVICES" in fuente_run.split("def entry_worker_c")[1].split("def ")[0])
check("use_gpu del Modulo C activado", ccfg["use_gpu"] is True)

sys.path.insert(0, "/home/jetson/integradora/model_ia/sistem_IA/modules")
fuente_c = open("/home/jetson/integradora/model_ia/sistem_IA/modules/mod_c.py").read()
check("VGG16 se ejecuta en un solo lote", "vgg(torch.stack(lote)" in fuente_c)
check("hay deteccion al abrir cada ventana", "len(frames_gray) == 0" in fuente_c)
check("guardia de espaciado uniforme presente", "max_gap_factor" in fuente_c)
fuente_o = open("/home/jetson/integradora/model_ia/sistem_IA/core/orchestrator.py").read()
check("el orquestador sabe submuestrear para C", "stride_c" in fuente_o)

print("== Prompt del LLM: vocabulario real ==")
sys.path.insert(0, "/home/jetson/apps/llm")
try:
    import ia_bridge
    vocab = ia_bridge.cargar_vocabulario()
    check("carga el vocabulario de las tarjetas", bool(vocab))
    check("incluye tarjetas poco frecuentes",
          all(p in vocab for p in ("HOSPITAL", "ESTOY LISTO", "PUEDO AYUDARTE", "DORMITORIO")))
    check("descarta las tarjetas UNKNOWN", "TBlanca" not in vocab)
    check("el prompt lleva la lista", "Tarjetas disponibles" in ia_bridge.SYSTEM_PROMPT)
    check("hay ejemplos de las poco frecuentes",
          any("HOSPITAL" in c for c, _ in ia_bridge.FEW_SHOT))

    print("== Guardia contra respuestas corruptas del LLM ==")
    # Basura REAL capturada en la corrida fallida del 17/08/2026 (ver
    # PROGRESO_BMO_S12.md 7.2): el mismo servidor devolvio esto en los 24 casos
    # y minutos despues respondia bien.
    basura = [
        ("YO QUIERO COMIDA", "No, I want to eat."),
        ("NO QUIERO DORMIR", "No me denunclaro."),
        ("MAMÁ YO IR COLEGIO", "No, no me asospeaks."),
        ("HERMANO JUGAR PARQUE YO", "Soy yo soy."),
        ("Marina COMER COCINA", "Mezclar con los signo, mezcla, mezclar."),
        ("NO QUIERO IR HOSPITAL", "No quiero morrger ni mamaro, pero no me atинuando."),
        ("AGUA QUIERO YO", "Soy yo mismo."),
        ("Mario IR SALÓN", "No, nada."),
    ]
    buenas = [
        ("YO QUIERO COMIDA", "Quiero comer."),
        ("PAPÁ QUIERO MÁS AGUA", "Papá, quiero más agua."),
        ("HERMANA ESTOY MAL", "Estoy mal."),
        ("NO NECESITO AYUDA", "No necesito ayuda."),
        ("Mario IR SALÓN", "Mario quiere ir al salón."),
        ("YO IR", "Quiero ir."),   # solo palabras cortas: no hay nada que comprobar
        ("YO NECESITO AYUDA BAÑO", "Necesito ayuda para ir al baño."),
    ]
    check("rechaza toda la basura capturada",
          all(not ia_bridge.respuesta_plausible(p, s) for p, s in basura))
    check("no rechaza ninguna respuesta correcta",
          all(ia_bridge.respuesta_plausible(p, s) for p, s in buenas))
    check("rechaza alfabetos imposibles en español",
          not ia_bridge.respuesta_plausible("YO QUIERO AGUA", "Quiero agua привет"))
    check("rechaza la respuesta vacia", not ia_bridge.respuesta_plausible("YO QUIERO AGUA", ""))
except ImportError:
    check("ia_bridge importable (necesita el venv de ia_bridge)", False)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\nTOTAL: {ok} OK, {fail} fallas")
sys.exit(1 if fail else 0)
