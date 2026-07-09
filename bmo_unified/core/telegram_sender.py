# core/telegram_sender.py
"""Envío de mensajes a Telegram en un hilo aparte (nunca bloquea la UI)."""

import json
import logging
import threading

import requests

log = logging.getLogger("bmo.telegram_sender")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _load_config(config_path: str) -> dict:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        log.exception("No se pudo leer config de Telegram en %s", config_path)
        return {}


def _do_send(text: str, config_path: str):
    cfg = _load_config(config_path)
    token = (cfg.get("bot_token") or "").strip()
    chat_id = (cfg.get("chat_id") or "").strip()

    if not token or not chat_id:
        log.warning("Telegram no configurado (config/telegram.json vacío); mensaje no enviado: %r", text)
        return

    url = TELEGRAM_API.format(token=token)
    try:
        resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=15)
        if resp.ok:
            log.info("Mensaje enviado a Telegram: %r", text)
        else:
            log.error("Telegram respondió %s: %s", resp.status_code, resp.text)
    except Exception:
        log.exception("Error enviando mensaje a Telegram")


def send_message_async(text: str, config_path: str = "config/telegram.json"):
    """Lanza el envío en un hilo daemon; no bloquea al llamador."""
    threading.Thread(target=_do_send, args=(text, config_path), daemon=True).start()
