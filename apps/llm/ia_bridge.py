"""
Puente Flask entre la pantalla Oraciones de Moodi y llama-server.

Recibe la secuencia de palabras que el niño formó con tarjetas RFID (CAA /
pictogramas) y devuelve UNA oración en español natural.

Calidad de la corrección (S11)
------------------------------
El modelo sigue siendo Qwen2.5-1.5B-Instruct Q4_K_M: es la elección correcta
para el presupuesto de RAM de esta Jetson (~1 GB, contra los 5 GB del
DeepSeek-R1-8B que también está en /home/jetson/models y que dejaría sin
memoria al motor de visión; y contra DeepSeek-R1-Distill-1.5B, que es un modelo
de razonamiento: gasta tokens en <think> y es más lento para una tarea que no
requiere razonar). Así que la calidad se sube SIN tocar la RAM, por prompt y
por muestreo:

1. Prompt few-shot con el vocabulario REAL de config/rfid_vocab.json. Un modelo
   de 1.5B es muy sensible al formato: con solo una instrucción abstracta
   ("corrige esta frase") divaga, saluda o inventa palabras; con ejemplos del
   dominio se vuelve consistente. Éste era el defecto principal.
2. Reglas explícitas contra la invención de palabras y contra perder la
   negación (el error más grave aquí: convertir "NO QUIERO COMER" en "Quiero
   comer" invierte lo que el niño pidió).
3. Muestreo casi determinista (temperature 0.15, top_p 0.85, repeat_penalty
   1.05) y max_tokens corto: esto NO es una tarea creativa, y a 0.3 el mismo
   conjunto de tarjetas daba frases distintas en cada intento.
4. Respaldo determinista si llama-server no responde: antes se devolvía un 500
   y el mensaje del niño se perdía. En un dispositivo de comunicación eso es
   inaceptable, así que se envía la frase armada por reglas.
"""

import json
import logging
import re

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
log = logging.getLogger("ia_bridge")

LLAMA_URL = "http://127.0.0.1:1234/v1/chat/completions"

# llama.cpp sirve un único modelo y IGNORA este campo; se deja explícito y
# correcto porque antes decía "deepseek-r1-0528-qwen3-8b", que no es el modelo
# que start_bmo.sh carga y despistaba al depurar.
MODEL_NAME = "Qwen2.5-1.5B-Instruct"

SYSTEM_PROMPT = (
    "Eres el asistente de comunicación de Moodi, un robot que acompaña a un niño "
    "que habla mediante tarjetas con pictogramas.\n"
    "Recibes las palabras de las tarjetas en el orden en que el niño las colocó y "
    "devuelves UNA sola oración en español natural.\n"
    "\n"
    "Reglas obligatorias:\n"
    "1. Usa ÚNICAMENTE la información de las tarjetas. No inventes personas, "
    "objetos, lugares ni acciones que no aparezcan.\n"
    "2. Si hay una negación, la oración DEBE seguir siendo negativa.\n"
    "3. Añade solo los artículos, preposiciones y conjugaciones necesarios para "
    "que suene natural.\n"
    "4. No agregues saludos, cortesías ni comentarios que no estén en las tarjetas.\n"
    "5. Responde con una sola oración breve, con mayúscula inicial y punto final.\n"
    "6. No expliques nada, no uses comillas, listas ni etiquetas."
)

# Ejemplos con el vocabulario REAL de las tarjetas (config/rfid_vocab.json):
# sujetos (YO, MAMÁ, PAPÁ, HERMANO/A, Marina, Mario), verbos (QUIERO, COMER,
# DORMIR, JUGAR, IR, HABLAR, NECESITO AYUDA, QUIERO MÁS), objetos (AGUA, JUGO,
# COMIDA), lugares (PARQUE, BAÑO, COCINA, COLEGIO...) y emociones.
FEW_SHOT = [
    ("YO QUIERO AGUA", "Quiero agua."),
    ("NO QUIERO COMER", "No quiero comer."),
    ("YO IR BAÑO", "Quiero ir al baño."),
    ("MAMÁ NECESITO AYUDA", "Mamá, necesito ayuda."),
    ("YO QUIERO MÁS JUGO", "Quiero más jugo."),
    ("PAPÁ HABLAR", "Papá, quiero hablar contigo."),
    ("YO ESTOY MAL", "Estoy mal."),
    ("HERMANA JUGAR PARQUE", "Quiero jugar en el parque con mi hermana."),
]

REQUEST_TIMEOUT = (10, 90)


def build_messages(prompt: str):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for cards, sentence in FEW_SHOT:
        messages.append({"role": "user", "content": cards})
        messages.append({"role": "assistant", "content": sentence})
    messages.append({"role": "user", "content": prompt})
    return messages


def strip_reasoning(text: str) -> str:
    """Limpia restos de razonamiento y adornos.

    Qwen2.5-Instruct no emite <think>, pero el manejo se conserva: este bridge
    ya sirvió modelos DeepSeek-R1 y es barato seguir siendo tolerante si alguien
    vuelve a apuntar llama-server a uno de ellos.
    """
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"<think>.*$", "", text, flags=re.S)
    text = text.replace("**", "").strip()
    text = text.strip('"').strip("'").strip()
    return re.sub(r"\s+", " ", text).strip()


def first_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    m = re.search(r"(.+?[\.!\?])(\s|$)", text)
    if m:
        return m.group(1).strip()
    return text[:140].strip()


def fallback_sentence(prompt: str) -> str:
    """Frase armada por reglas, sin LLM: se usa si llama-server no responde.

    No pretende ser gramatical -- pretende que el mensaje del niño NO se pierda
    (antes, un fallo del LLM devolvía 500 y la frase no llegaba a nadie)."""
    words = [w for w in re.split(r"\s+", prompt.strip()) if w]
    if not words:
        return ""
    sentence = " ".join(words).lower()
    sentence = sentence[0].upper() + sentence[1:]
    if sentence[-1] not in ".!?":
        sentence += "."
    return sentence


def call_llama(messages, max_tokens=48, temperature=0.15, extra=None,
               timeout=REQUEST_TIMEOUT):
    payload = {
        "model": MODEL_NAME,
        "temperature": temperature,
        "top_p": 0.85,
        "repeat_penalty": 1.05,
        "max_tokens": max_tokens,
        # Una sola oración: cortar en el salto de línea evita que el modelo
        # encadene una segunda frase o empiece a explicarse.
        "stop": ["\n", "\n\n"],
        "messages": messages,
    }
    if extra:
        payload.update(extra)
    r = requests.post(LLAMA_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


@app.route("/health", methods=["GET"])
def health():
    """Sonda para el arranque/diagnóstico: dice si llama-server contesta."""
    try:
        requests.get("http://127.0.0.1:1234/health", timeout=3).raise_for_status()
        return jsonify({"ok": True, "model": MODEL_NAME})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/ask", methods=["POST"])
def ask_ai():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt vacío"}), 400

    try:
        raw = call_llama(build_messages(prompt))
        sent = first_sentence(strip_reasoning(raw))

        # Reintento en JSON si el modelo devolvió algo vacío o inservible.
        if not sent:
            json_sys = (
                SYSTEM_PROMPT
                + "\n\nDevuelve SOLO un objeto JSON válido con la clave "
                  "'respuesta' y la oración como valor."
            )
            raw2 = call_llama(
                [{"role": "system", "content": json_sys},
                 {"role": "user", "content": prompt}],
                max_tokens=64,
                temperature=0.1,
                extra={"response_format": {"type": "json_object"}},
            )
            obj = None
            try:
                obj = json.loads(raw2)
            except Exception:
                m = re.search(r"\{.*\}", raw2, flags=re.S)
                if m:
                    try:
                        obj = json.loads(m.group(0))
                    except Exception:
                        obj = None
            if isinstance(obj, dict) and "respuesta" in obj:
                sent = first_sentence(strip_reasoning(str(obj["respuesta"])))

        if not sent:
            sent = fallback_sentence(prompt)

        return jsonify({"response": sent})

    except requests.exceptions.Timeout:
        # El mensaje del niño no se pierde por un timeout del modelo.
        log.warning("Timeout de llama-server; se devuelve la frase de respaldo")
        return jsonify({"response": fallback_sentence(prompt), "degraded": True})
    except Exception as e:
        log.exception("Fallo consultando llama-server")
        return jsonify({"response": fallback_sentence(prompt),
                        "degraded": True, "detail": str(e)})


if __name__ == "__main__":
    # En producción se sirve con gunicorn (ver start_bmo.sh).
    app.run(host="0.0.0.0", port=5000)
