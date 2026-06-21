from flask import Flask, request, jsonify
import requests
import re
import json

app = Flask(__name__)

LLAMA_URL = "http://127.0.0.1:1234/v1/chat/completions"

SYSTEM_PROMPT_BASE = (
    "Eres un asistente que toma frases desordenadas hechas con pictogramas y "
    "devuelve una oración en español correcta, breve y clara, sin cambiar el significado. "
    "Responde con una sola oración final, sin explicaciones ni etiquetas <think>."
)

def strip_reasoning(text: str) -> str:
    if not text:
        return ""
    # Quita bloque <think>…</think> completo si existe
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    # Si aún hay un <think> sin cerrar, corta desde ahí hasta el final
    text = re.sub(r"<think>.*$", "", text, flags=re.S)
    # Limpieza leve
    text = text.replace("**", "").strip()
    text = text.strip('"').strip("'").strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text

def first_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    # Intenta tomar la primera oración acabada en . ! o ?
    m = re.search(r'(.+?[\.!\?])(\s|$)', text)
    if m:
        return m.group(1).strip()
    # Si no hay puntuación, devuelve hasta 140 chars como tope
    return text[:140].strip()

def call_llama(messages, max_tokens=96, temperature=0.3, extra=None, timeout=(10, 90)):
    payload = {
        "model": "deepseek-r1-0528-qwen3-8b",
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages
    }
    if extra:
        payload.update(extra)
    r = requests.post(LLAMA_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    return j["choices"][0]["message"]["content"]

@app.route("/ask", methods=["POST"])
def ask_ai():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt vacío"}), 400

    try:
        # INTENTO 1: pedir directamente solo la frase final (sin <think>)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {"role": "user",   "content": prompt}
        ]
        raw = call_llama(messages, max_tokens=96, temperature=0.3, timeout=(10, 90))
        cleaned = strip_reasoning(raw)
        sent = first_sentence(cleaned)

        # Si quedó vacío (el modelo solo mandó <think> o algo irrelevante), Fallback JSON
        if not sent:
            json_sys = (
                "Devuelve SOLO un objeto JSON válido con la clave 'respuesta' "
                "que contenga una sola oración breve y clara en español. "
                "No incluyas etiquetas <think> ni explicaciones, solo JSON."
            )
            messages_json = [
                {"role": "system", "content": json_sys},
                {"role": "user",   "content": prompt}
            ]
            # Forzamos formato JSON; si el servidor no lo soporta, el modelo igual tenderá a cumplir.
            raw2 = call_llama(
                messages_json,
                max_tokens=88,
                temperature=0.2,
                extra={"response_format": {"type": "json_object"}},
                timeout=(10, 90)
            )

            # Intenta parsear; si llega texto con basura, intenta extraer el primer bloque {...}
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

            if obj and isinstance(obj, dict) and "respuesta" in obj:
                sent = first_sentence(strip_reasoning(str(obj["respuesta"])))

        # Última limpieza
        sent = sent.replace("**", "").strip()

        if not sent:
            # Si aún está vacío, devolvemos lo que haya del intento 1, ya limpiado (puede ser útil para debug)
            sent = cleaned or "(sin respuesta)"

        return jsonify({"response": sent})

    except requests.exceptions.Timeout as e:
        return jsonify({"error": "timeout al consultar llama-server", "detail": str(e)}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # En producción, usa gunicorn (ver abajo)
    app.run(host="0.0.0.0", port=5000)
