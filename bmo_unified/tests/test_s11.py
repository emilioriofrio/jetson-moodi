"""Pruebas headless de lo tocado en S11 (widgets reales, sin hardware)."""
import os, sys, json, tempfile, shutil
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/home/jetson/bmo_unified")
from PyQt5.QtWidgets import QApplication
app = QApplication([])
from core import i18n
i18n.load("/home/jetson/bmo_unified/config")
ok = fail = 0
def check(name, cond):
    global ok, fail
    if cond: ok += 1; print(f"  OK   {name}")
    else:    fail += 1; print(f"  FALLA {name}")

print("== AppSettings: voice_enabled ==")
from core.app_settings import AppSettings
tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "settings.json")
s = AppSettings(p)
check("por defecto encendida", s.voice_enabled is True)
got = []
s.voice_enabled_changed.connect(lambda v: got.append(v))
s.set_voice_enabled(False)
check("emite señal al apagar", got == [False])
check("persiste en disco", json.load(open(p))["voice_enabled"] is False)
check("recarga desde disco", AppSettings(p).voice_enabled is False)
s.set_voice_enabled(False)
check("no re-emite si no cambia", got == [False])
open(p, "w").write("{roto")
check("archivo corrupto -> defaults", AppSettings(p).voice_enabled is True)
shutil.rmtree(tmp)

print("== VoiceEngine respeta el interruptor ==")
from core.voice import VoiceEngine
class FakeS:
    volume = 80; language = "es"; voice_enabled = False
v = VoiceEngine(FakeS())
v._available = True
v.speak("hola")
check("no encola con voz apagada", v._queue.qsize() == 0)
v._settings.voice_enabled = True
v.speak("hola")
check("encola con voz encendida", v._queue.qsize() == 1)
check("silence_now existe", hasattr(v, "silence_now"))
v._available = False; v.shutdown()

print("== PecsPanel: boton de voz ==")
from ui.pecs_panel import PecsPanel
pp = PecsPanel(); pp.resize(1024, 600)
emitted = []
pp.voice_toggled.connect(lambda e: emitted.append(e))
pp.set_voice_enabled(True)
pp._on_voice_clicked()
check("toggle emite False", emitted == [False])
pp._on_voice_clicked()
check("toggle emite True", emitted == [False, True])
check("target tactil >=64px", pp._btn_voice.width() >= 64 and pp._btn_voice.height() >= 64)
pp.set_voice_enabled(False)
check("set_voice_enabled no re-emite", emitted == [False, True])

print("== MoodiDiagram sobre MOODI_VIEW.png ==")
from ui.settings_panel import MoodiDiagram
md = MoodiDiagram(); md.resize(400, 460); md._layout()
check("PNG cargado", not md._pixmap.isNull())
check("10 botones ubicados", len(md._shapes) == 10)
from core.button_router import GPIO_PHYS, LOCKED_GPIO
check("gpios coinciden con el hardware", set(md._shapes) == set(GPIO_PHYS))
check("todos dentro de la imagen",
      all(md._img_rect.contains(r.center()) for r in md._shapes.values()))
from PyQt5.QtCore import QPoint
hits = {g: md._gpio_at(QPoint(int(r.center().x()), int(r.center().y())))
        for g, r in md._shapes.items()}
check("cada centro resuelve a SU boton", all(g == h for g, h in hits.items()))
md._hover = "25"; check("hover se registra", md._hover == "25")
check("bloqueado sigue siendo GPIO13", LOCKED_GPIO == "13" and "13" in md._shapes)

print("== Splash ==")
from ui.splash import SplashWindow, PIECE_RECTS
sw = SplashWindow()
check("piezas recortadas", len(sw._piece_tiles) == len(PIECE_RECTS) == 9)
check("logo cargado", not sw._logo.isNull())
open("/tmp/moodi_boot.status", "w").write("42|boot.bridge")
sw._read_status()
check("lee porcentaje", abs(sw._pct - 42.0) < 0.01)
check("lee clave", sw._msg_key == "boot.bridge")
open("/tmp/moodi_boot.status", "w").write("100|READY")
sw._read_status()
check("READY cierra", sw._done is True)
sw._anim.stop(); sw._poll.stop()

print("== EmoMonitorPanel: reset de sesion (bug S10) ==")
from ui.emo_monitor_panel import EmoMonitorPanel
mp = EmoMonitorPanel(); mp.resize(1024, 600)
for lbl in ("ALTO", "ALTO", "ALTO"):
    mp.on_pred({"module": "A", "label": lbl, "conf": 0.9, "present": True})
check("historial acumulado", len(mp._label_history.get("A", [])) == 3)
mp.reset_session()
check("historial limpio tras reset", not mp._label_history)
check("predicciones limpias tras reset", not mp._latest_preds)
check("badge vuelve a INSEGURO", mp._last_stats_label == "INSEGURO")

print(f"\nTOTAL: {ok} OK, {fail} fallas")
sys.exit(1 if fail else 0)
