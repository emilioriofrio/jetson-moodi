# bmo_app.py
"""
Punto de entrada de Moodi / BMO OS. Ver REQUERIMIENTOS_APP_MOODI.md.

Uso:
    python bmo_app.py [--calibrate]

Seguridad (sección 0 de los requerimientos): este archivo y todo lo que
importa NUNCA tocan /boot, extlinux.conf, overlays de device tree, ni invocan
enable_csi_camera.sh/enable_usb_camera.sh. La cámara se abre exclusivamente
vía OpenCV/V4L2 sobre /dev/video0 (ver core/orchestrator.py, sin modificar).
No se ejecuta con sudo.
"""

import argparse
import logging
import os
import signal
import sys

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

# "Ubuntu" viene preinstalada en el panel Eleclab/Jetson y tiene más carácter
# que la sans-serif por defecto de Qt (que hacía sentir la app plana); se
# aplica a nivel de QApplication para que la hereden todos los QLabel que solo
# fijan font-size/color en su stylesheet, sin tener que tocar cada uno.
APP_FONT_FAMILY = "Ubuntu"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
ANIM_DIR = os.path.expanduser("~/integradora/animaciones")
LOG_PATH = os.path.join(BASE_DIR, "logs", "bmo_app.log")

sys.path.insert(0, BASE_DIR)

log = logging.getLogger("bmo.app")

_shutdown_requested = False


def _setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


def _handle_termination_signal(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True


def _courtesy_residual_check():
    """Chequeo de cortesía (7.2): advierte si quedó algo de visión vivo tras cerrar."""
    try:
        import psutil
    except ImportError:
        return

    keywords = ("Orchestrator", "WorkerA", "WorkerB", "WorkerC", "Fusion", "Reporter", "PredFanout")
    me = psutil.Process(os.getpid())
    survivors = []
    for child in me.children(recursive=True):
        try:
            name = child.name() or ""
            cmdline = " ".join(child.cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if any(k in name or k in cmdline for k in keywords):
            survivors.append((child.pid, name))

    if survivors:
        log.warning("Procesos de visión sobrevivientes tras el cierre: %s", survivors)
    else:
        log.info("Chequeo de cortesía: sin procesos de visión residuales.")


def main():
    # Imports diferidos a propósito (TODOS los de bmo_unified): si estuvieran a
    # nivel de módulo, multiprocessing (start method "spawn") los re-ejecutaría
    # dentro de CADA proceso hijo del motor de visión al rearrancar este script
    # como "__mp_main__", registrando el paquete bmo_unified/core en sys.modules
    # ANTES de que vision/engine.py pueda anteponer sistem_IA a sys.path -- y
    # como sistem_IA también tiene un paquete "core", el hijo terminaría
    # resolviendo "core.messages" contra el core equivocado.
    from core import i18n
    from core.app_settings import AppSettings
    from ui import theme
    from ui.main_window import MainWindow

    parser = argparse.ArgumentParser(description="Moodi / BMO OS")
    parser.add_argument("--calibrate", action="store_true", help="Arrancar en modo calibración de botones")
    args = parser.parse_args()

    _setup_logging()
    log.info("Iniciando bmo_app.py (calibrate=%s)", args.calibrate)

    app = QApplication(sys.argv)
    app.setFont(QFont(APP_FONT_FAMILY, 11))

    # Preferencias persistentes ANTES de construir la primera pantalla: idioma
    # y escala de fuente deben estar activos cuando los widgets pidan t()/fs().
    settings = AppSettings(os.path.join(CONFIG_DIR, "settings.json"))
    i18n.load(CONFIG_DIR)
    i18n.set_language(settings.language)
    theme.set_font_scale(settings.font_scale)
    log.info(
        "Preferencias cargadas: idioma=%s, volumen=%d, fuente=%s, apodo=%r",
        settings.language, settings.volume, settings.font_scale, settings.nickname,
    )

    window = MainWindow(CONFIG_DIR, ANIM_DIR, settings, calibrate=args.calibrate)
    window.showFullScreen()

    signal.signal(signal.SIGINT, _handle_termination_signal)
    signal.signal(signal.SIGTERM, _handle_termination_signal)

    poll_timer = QTimer()

    def _poll_shutdown():
        if _shutdown_requested:
            log.info("Señal de terminación recibida, cerrando…")
            window.close()

    poll_timer.timeout.connect(_poll_shutdown)
    poll_timer.start(200)

    exit_code = app.exec_()

    _courtesy_residual_check()
    log.info("bmo_app.py finalizado.")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
