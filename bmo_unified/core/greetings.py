# core/greetings.py
"""
Selector del saludo variable de la pantalla Oraciones (5.6), extendido con
personalización (bloque Apodo de Configuraciones): idioma activo, apodo del
usuario y franja horaria del reloj del sistema.

Reglas:
- Sin apodo registrado -> solo plantillas genéricas (nunca un "{name}" vacío
  o roto en pantalla).
- Con apodo -> se combinan genéricas + con nombre; las de franja horaria
  entran al mismo sorteo, así el saludo sigue siendo variable (patrón "saludo
  de bienvenida aleatorio" al abrir la pantalla) y no siempre el mismo de la
  franja.
- Formato antiguo del JSON ({"greetings": [...]}) se sigue aceptando como
  fallback, por robustez ante una config vieja.
"""

import datetime
import json
import logging
import random

log = logging.getLogger("bmo.greetings")

# Franjas horarias (hora local): [inicio, fin) -- evening cruza medianoche.
_MORNING = range(6, 12)
_AFTERNOON = range(12, 19)


def load_greetings(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        log.exception("No se pudieron cargar los saludos desde %s", path)
        return {}


def _time_slot(hour: int) -> str:
    if hour in _MORNING:
        return "morning"
    if hour in _AFTERNOON:
        return "afternoon"
    return "evening"


def pick_greeting(data: dict, lang: str, nickname: str = "", now=None) -> str:
    """Elige un saludo al azar según idioma, apodo y hora local."""
    nickname = (nickname or "").strip()

    # Fallback: formato antiguo {"greetings": [...]} (una sola lista, español).
    if "greetings" in data and lang not in data:
        pool = list(data.get("greetings") or [])
        return random.choice(pool) if pool else ""

    lang_data = data.get(lang) or data.get("es") or {}
    if not isinstance(lang_data, dict):
        return ""

    slot = _time_slot((now or datetime.datetime.now()).hour)

    pool = list(lang_data.get("generic") or []) + list(lang_data.get(slot) or [])
    if nickname:
        pool += list(lang_data.get("named") or [])
        pool += list(lang_data.get(f"{slot}_named") or [])

    if not pool:
        return ""

    template = random.choice(pool)
    try:
        return template.format(name=nickname)
    except (KeyError, IndexError, ValueError):
        log.warning("Plantilla de saludo inválida: %r", template)
        return template
