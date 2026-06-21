import sys, time, glob, os, subprocess, importlib.util, re

PATH = "/usr/lib/python3/dist-packages/Jetson/GPIO/gpio_pin_data.py"
spec = importlib.util.spec_from_file_location('Jetson.GPIO.gpio_pin_data', PATH)
gpio_pin_data = importlib.util.module_from_spec(spec); spec.loader.exec_module(gpio_pin_data)
MY_MODEL = "NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super"
keys = list(gpio_pin_data.jetson_gpio_data.keys())
preferred = [k for k in keys if re.search(r"Orin\\s*Nano", k, re.I)]
target_key = preferred[0] if preferred else [k for k in keys if re.search(r"Orin", k, re.I)][0]
gpio_pin_data.jetson_gpio_data[MY_MODEL] = gpio_pin_data.jetson_gpio_data[target_key]
gpio_pin_data.get_model = lambda: MY_MODEL
sys.modules['Jetson.GPIO.gpio_pin_data'] = gpio_pin_data

import Jetson.GPIO as GPIO

BUTTON_PIN     = 13                      
DEBOUNCE_MS    = 60
LONG_PRESS_MS  = 2000
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR      = os.path.join(BASE_DIR, "animaciones")

GPIO.setmode(GPIO.BCM)
GPIO.setup(BUTTON_PIN, GPIO.IN) 

def list_videos(folder):
    exts = ("*.mp4","*.mkv","*.avi","*.mov")
    files = []
    for e in exts:
        files.extend(glob.glob(os.path.join(folder, e)))
    files.sort()
    return files

def wait_press():
    """Espera flanco FALLING (1->0) con anti-rebote y clasifica duración."""
    while GPIO.input(BUTTON_PIN) == 1:
        time.sleep(0.002)
    t0 = time.time()
    time.sleep(DEBOUNCE_MS/1000.0)  # anti-rebote
    while GPIO.input(BUTTON_PIN) == 0:
        if (time.time() - t0)*1000 >= LONG_PRESS_MS:
            while GPIO.input(BUTTON_PIN) == 0:
                time.sleep(0.01)
            return "long"
        time.sleep(0.005)
    return "short"

def play(video_path):
    return subprocess.Popen(
        ["mpv", "--fs", "--really-quiet", "--no-terminal", video_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

def main():
    vids = list_videos(VIDEO_DIR)
    if not vids:
        print(f"[ERROR] No encontré videos en: {VIDEO_DIR}")
        return
    idx = 0
    print(f"[OK] Pinout base: '{target_key}'. Leyendo {len(vids)} archivos en {VIDEO_DIR}")
    print("[INFO] Pulsación corta: reproducir/avanzar. Mantén >2s: salir.")

    while True:
        ev = wait_press()
        if ev == "long":
            print("[SALIR] Pulsación larga en espera.")
            break

        video = vids[idx]
        print(f"[PLAY] {os.path.basename(video)}")
        proc = play(video)

        pressed = False
        t0 = None
        try:
            while proc.poll() is None:
                if GPIO.input(BUTTON_PIN) == 0:  # presionado (active-LOW)
                    if not pressed:
                        pressed = True
                        t0 = time.time()
                        time.sleep(DEBOUNCE_MS/1000.0)
                    else:
                        if (time.time() - t0)*1000 >= LONG_PRESS_MS:
                            proc.terminate()
                            try:
                                proc.wait(timeout=1.0)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                            print("[SALIR] Pulsación larga durante reproducción.")
                            return
                else:
                    pressed = False
                    t0 = None
                time.sleep(0.01)
        except KeyboardInterrupt:
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired: proc.kill()
            raise

        idx = (idx + 1) % len(vids)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CTRL+C] Saliendo…")
    finally:
        GPIO.cleanup()
