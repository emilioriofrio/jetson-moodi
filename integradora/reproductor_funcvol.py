import os, glob, json, socket, subprocess, time, sys

try:
    import serial
except ImportError:
    print("Instala pyserial: pip3 install --user pyserial")
    sys.exit(1)

# CONFIGURACIÓN 
ANIM_DIR = os.path.expanduser('~/integradora/animaciones')
EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.mp3', '.wav')
SERIAL_CANDIDATES = ["/dev/ttyUSB0", "/dev/ttyACM0"]  
BAUD = 115200
MPV_SOCK = '/tmp/mpv_anim.sock'

# Volumen
VOL_STEP = 8          
VOL_MIN = 0
VOL_MAX = 130
VOL_INIT = 80         

def list_media():
    files = []
    for ext in EXTS:
        files += glob.glob(os.path.join(ANIM_DIR, f'*{ext}'))
    files = [f for f in files if os.path.isfile(f)]
    files.sort(key=lambda s: s.lower())
    return files

def start_mpv():
    try:
        os.remove(MPV_SOCK)
    except FileNotFoundError:
        pass
    cmd = [
        'mpv', '--fs', '--force-window=yes', '--no-terminal', '--really-quiet',
        f'--input-ipc-server={MPV_SOCK}',
        '--idle=yes', '--keep-open=yes',
        '--reset-on-next-file=pause',
        f'--volume={VOL_INIT}'
    ]
    return subprocess.Popen(cmd)

def mpv_cmd(cmd_list):
    # espera hasta 5 s a que aparezca el socket
    for _ in range(50):
        if os.path.exists(MPV_SOCK):
            break
        time.sleep(0.1)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(MPV_SOCK)
    payload = json.dumps({"command": cmd_list}).encode() + b"\n"
    s.sendall(payload)
    s.close()

def get_prop(prop):
    # lee una propiedad de mpv (devuelve None si falla)
    for _ in range(50):
        if os.path.exists(MPV_SOCK):
            break
        time.sleep(0.1)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(MPV_SOCK)
    payload = json.dumps({"command": ["get_property", prop]}).encode() + b"\n"
    s.sendall(payload)
    data = s.recv(4096)
    s.close()
    try:
        resp = json.loads(data.decode(errors="ignore").splitlines()[-1])
        return resp.get("data", None)
    except Exception:
        return None

def clamp(x, lo, hi): return max(lo, min(hi, x))

def adjust_volume(delta):
    # lee volumen actual, ajusta y fija
    cur = get_prop("volume")
    if cur is None:
        cur = VOL_INIT
    newv = clamp(float(cur) + delta, VOL_MIN, VOL_MAX)
    mpv_cmd(['set_property', 'volume', newv])
    print(f"[VOL] {cur:.0f} -> {newv:.0f}")

def load_file(path):
    mpv_cmd(['set_property', 'pause', False])   # si quedó pausado al final
    mpv_cmd(['loadfile', path, 'replace'])

def open_serial():
    last_err = None
    for dev in SERIAL_CANDIDATES:
        try:
            return serial.Serial(dev, BAUD, timeout=0.2)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"No pude abrir puerto serie. Intenté {SERIAL_CANDIDATES}. Último error: {last_err}")

def main():
    media = list_media()
    if not media:
        print(f"[ERROR] No hay archivos en {ANIM_DIR} con extensiones {EXTS}")
        sys.exit(1)

    print("[INFO] Archivos encontrados:")
    for i, f in enumerate(media):
        print(f"  {i+1:02d}: {os.path.basename(f)}")

    # Serial
    print("[INFO] Abriendo puerto serie…")
    ser = open_serial()
    print(f"[OK] Serial abierto en {ser.port}")

    mpv_proc = None
    idx = -1  # nada reproduciéndose al inicio

    print("[LISTO] Esperando BTN:START / BTN:NEXT / BTN:PREV / BTN:VOLUP / BTN:VOLDOWN")
    try:
        while True:
            line = ser.readline().decode(errors="ignore").strip()
            if not line:
                continue

            if line == "BTN:START":
                if mpv_proc is None or mpv_proc.poll() is not None:
                    mpv_proc = start_mpv()
                idx = 0
                load_file(media[idx])
                print(f"[START] {idx+1:02d}/{len(media)} → {os.path.basename(media[idx])}")

            elif line == "BTN:NEXT":
                if mpv_proc is None or mpv_proc.poll() is not None:
                    mpv_proc = start_mpv()
                    idx = 0
                else:
                    idx = (idx + 1) % len(media)
                load_file(media[idx])
                print(f"[NEXT]  {idx+1:02d}/{len(media)} → {os.path.basename(media[idx])}")

            elif line == "BTN:PREV":
                if mpv_proc is None or mpv_proc.poll() is not None:
                    mpv_proc = start_mpv()
                    idx = 0
                else:
                    idx = (idx - 1) % len(media)
                load_file(media[idx])
                print(f"[PREV]  {idx+1:02d}/{len(media)} → {os.path.basename(media[idx])}")

            elif line == "BTN:VOLUP":
                if mpv_proc is None or mpv_proc.poll() is not None:
                    mpv_proc = start_mpv()
                adjust_volume(+VOL_STEP)

            elif line == "BTN:VOLDOWN":
                if mpv_proc is None or mpv_proc.poll() is not None:
                    mpv_proc = start_mpv()
                adjust_volume(-VOL_STEP)

            # ignorar otras líneas

    except KeyboardInterrupt:
        print("\n[EXIT] Saliendo…")
    finally:
        try:
            mpv_cmd(['quit'])
        except Exception:
            pass
        try:
            if mpv_proc:
                mpv_proc.terminate()
        except Exception:
            pass

if __name__ == "__main__":
    main()
