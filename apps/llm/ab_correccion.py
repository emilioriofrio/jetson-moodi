#!/usr/bin/env python3
"""A/B de la corrección de oraciones contra el modelo REALMENTE cargado (S12).

S11 mejoró el prompt y lo validó con 10 casos. El usuario reporta en S12 que la
cohesión mejoró "pero por momentos no contextualiza como debería", así que hace
falta un banco de pruebas más amplio y, sobre todo, que cubra las tarjetas POCO
frecuentes (HOSPITAL, SIESTA, ESTOY LISTO, PUEDO AYUDARTE, Llavero, TBlanca…),
que son justo las que no aparecían en ningún ejemplo del prompt.

No arranca ningún modelo: habla con el llama-server que ya está corriendo, a
través del mismo bridge que usa la app, así que mide lo que el niño obtiene de
verdad.

Uso:
    python3 apps/llm/ab_correccion.py            # usa el bridge en :5000
    python3 apps/llm/ab_correccion.py --directo  # habla directo con llama-server
"""

import argparse
import json
import sys
import time

import requests

BRIDGE_URL = "http://127.0.0.1:5000/ask"

# Secuencias de tarjetas plausibles con el vocabulario real de
# bmo_unified/config/rfid_vocab.json (34 tarjetas).
CASOS = [
    # --- las de S11, para no perder lo ya validado ---
    "YO QUIERO COMIDA",
    "NO QUIERO DORMIR",
    "MAMÁ YO IR COLEGIO",
    "YO NECESITO AYUDA BAÑO",
    "PAPÁ QUIERO MÁS AGUA",
    "YO ESTOY BIEN",
    "HERMANO JUGAR PARQUE YO",
    "NO NECESITO AYUDA",
    "Marina COMER COCINA",
    "YO QUIERO IR DORMIR",
    # --- tarjetas poco frecuentes: aquí es donde el usuario ve fallos ---
    "YO IR HOSPITAL",
    "MAMÁ YO SIESTA",
    "YO ESTOY LISTO COLEGIO",
    "PAPÁ PUEDO AYUDARTE",
    "YO QUIERO Llavero",
    "Mario IR SALÓN",
    "YO QUIERO DORMIR DORMITORIO",
    "HERMANA ESTOY MAL",
    "NO QUIERO IR HOSPITAL",
    "YO QUIERO MÁS COMIDA COCINA",
    # --- casos difíciles de orden/negación ---
    "AGUA QUIERO YO",
    "BAÑO IR NECESITO AYUDA",
    "NO QUIERO JUGAR PARQUE",
    "YO HABLAR MAMÁ PAPÁ",
]


def pedir(texto: str, url: str) -> str:
    t0 = time.time()
    try:
        r = requests.post(url, json={"prompt": texto}, timeout=(10, 120))
        r.raise_for_status()
        data = r.json()
        respuesta = data.get("respuesta") or data.get("response") or json.dumps(data)
        marca = " [degradado]" if data.get("degraded") else ""
        return f"{respuesta}{marca}  ({time.time() - t0:.1f}s)"
    except Exception as e:
        return f"ERROR: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=BRIDGE_URL)
    args = ap.parse_args()

    print(f"Casos: {len(CASOS)} | endpoint: {args.url}\n")
    for caso in CASOS:
        print(f"  {caso:38s} -> {pedir(caso, args.url)}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
