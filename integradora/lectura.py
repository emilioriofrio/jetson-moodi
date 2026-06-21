import sys, time, importlib.util, re
# parche temporal del modelo
PATH = "/usr/lib/python3/dist-packages/Jetson/GPIO/gpio_pin_data.py"
spec = importlib.util.spec_from_file_location('Jetson.GPIO.gpio_pin_data', PATH)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
MY_MODEL = "NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super"
keys = list(m.jetson_gpio_data.keys())
pref = [k for k in keys if re.search(r"Orin\\s*Nano", k, re.I)]
target_key = pref[0] if pref else [k for k in keys if re.search(r"Orin", k, re.I)][0]
m.jetson_gpio_data[MY_MODEL] = m.jetson_gpio_data[target_key]
m.get_model = lambda: MY_MODEL
sys.modules['Jetson.GPIO.gpio_pin_data'] = m

import Jetson.GPIO as GPIO

USE_BOARD = True         
PIN       = 33           

ACTIVE_LOW = True       

mode = GPIO.BOARD if USE_BOARD else GPIO.BCM
GPIO.setmode(mode)
GPIO.setup(PIN, GPIO.IN)

print(f"[OK] Pinout '{target_key}'. Probando pin {('BOARD' if USE_BOARD else 'BCM')} {PIN} "
      f"({'LOW' if ACTIVE_LOW else 'HIGH'}=presionado). Ctrl+C para salir.")

def is_pressed(val): 
    return (val == 0) if ACTIVE_LOW else (val == 1)

try:
    last = GPIO.input(PIN)
    print("Inicial:", last, "(0=GND, 1=3V3)")
    while True:
        v = GPIO.input(PIN)
        if v != last:
            if is_pressed(v):
                print("PRESIONADO")
            else:
                print("SUELTO")
            last = v
        time.sleep(0.005)
except KeyboardInterrupt:
    pass
finally:
    GPIO.cleanup()
