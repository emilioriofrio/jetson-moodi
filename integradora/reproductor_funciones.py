#!/usr/bin/env python3
import os, glob, json, socket, subprocess, time, sys

try:
    import serial
except ImportError:
    print("Instala pyserial: pip3 install --user pyserial")
    sys.exit(1)

# === CONFIGURACIÓN ===
ANIM_DIR = os.path.expanduser('~/integradora/animaciones')
EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.mp3', '.wav')
SERIAL_CANDIDATES = ["/dev/ttyUSB0", "/dev/ttyACM0"]  # ajusta si tu ESP aparece en otro
BAUD = 115200
MPV_SOCK = '/tmp/mpv_anim.sock'

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
        '--reset-on-next-file=pause'  # limpia estado de 'pause' al cambiar de archivo
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

def load_file(path):
    # asegúrate de que no esté en pausa
    mpv_cmd(['set_property', 'pause', False])
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

    # Abre serie
    print("[INFO] Abriendo puerto serie…")
    ser = open_serial()
    print(f"[OK] Serial abierto en {ser.port}")

    mpv_proc = None
    idx = -1  # nada reproduciéndose al inicio

    print("[LISTO] Esperando BTN:START / BTN:NEXT / BTN:PREV (Ctrl+C para salir).")
    try:
        while True:
            line = ser.readline().decode(errors="ignore").strip()
            if not line:
                continue

            if line == "BTN:START":
                if mpv_proc is None or mpv_proc.poll() is not None:
                    mpv_proc = start_mpv()
                idx = 0  # siempre arranca en el primero
                load_file(media[idx])
                print(f"[START] {idx+1:02d}/{len(media)} → {os.path.basename(media[idx])}")

            elif line == "BTN:NEXT":
                if mpv_proc is None or mpv_proc.poll() is not None:
                    mpv_proc = start_mpv()
                    idx = 0  # si no estaba iniciado, empieza por el primero
                else:
                    idx = (idx + 1) % len(media)
                load_file(media[idx])
                print(f"[NEXT]  {idx+1:02d}/{len(media)} → {os.path.basename(media[idx])}")

            elif line == "BTN:PREV":
                if mpv_proc is None or mpv_proc.poll() is not None:
                    mpv_proc = start_mpv()
                    idx = 0  # si no estaba iniciado, empieza por el primero
                else:
                    idx = (idx - 1) % len(media)
                load_file(media[idx])
                print(f"[PREV]  {idx+1:02d}/{len(media)} → {os.path.basename(media[idx])}")

            # Si llega cualquier otra cosa, la ignoramos

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
