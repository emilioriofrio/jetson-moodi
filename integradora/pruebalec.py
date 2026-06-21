import sys, time, importlib.util, re
PATH="/usr/lib/python3/dist-packages/Jetson/GPIO/gpio_pin_data.py"
spec=importlib.util.spec_from_file_location('Jetson.GPIO.gpio_pin_data',PATH)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
MY="NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super"
ks=list(m.jetson_gpio_data.keys()); pref=[k for k in ks if re.search(r"Orin\\s*Nano",k,re.I)]
tkey=pref[0] if pref else [k for k in ks if re.search(r"Orin",k,re.I)][0]
m.jetson_gpio_data[MY]=m.jetson_gpio_data[tkey]; m.get_model=lambda: MY
sys.modules['Jetson.GPIO.gpio_pin_data']=m

import Jetson.GPIO as GPIO
GPIO.setmode(GPIO.BOARD)
PIN=33
GPIO.setup(PIN, GPIO.IN)  # pull-up EXTERNO
print(f"[{tkey}] BOARD {PIN} pull-up ext (10k a 3V3). PRESIONADO=LOW.")
last=GPIO.input(PIN); print("Inicial:", last)
try:
    while True:
        v=GPIO.input(PIN)
        if v!=last:
            print("PRESIONADO" if v==0 else "SUELTO")
            last=v
        time.sleep(0.003)
except KeyboardInterrupt:
    GPIO.cleanup()
