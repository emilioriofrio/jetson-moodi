# core/rfid_vocab.py
"""Carga y consulta del diccionario UID RFID -> palabra (config/rfid_vocab.json)."""

import json
import logging

log = logging.getLogger("bmo.rfid_vocab")

UID = tuple  # (b0, b1, b2, b3)


def load_vocab(path: str) -> dict:
    """Devuelve {(b0,b1,b2,b3): {"palabra": str, "tipo": str}}."""
    vocab = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        log.exception("No se pudo cargar rfid_vocab desde %s", path)
        return vocab

    for entry in data:
        try:
            uid = tuple(int(b) for b in entry["uid"])
            if len(uid) != 4:
                continue
            vocab[uid] = {
                "palabra": str(entry.get("palabra", "")).strip(),
                "tipo": str(entry.get("tipo", "")).strip(),
            }
        except Exception:
            log.warning("Entrada de vocab inválida, se ignora: %r", entry)
            continue

    log.info("Vocabulario RFID cargado: %d tarjetas", len(vocab))
    return vocab


def lookup_word(vocab: dict, uid) -> str | None:
    """Devuelve la palabra asociada al UID, o None si la tarjeta no está registrada."""
    entry = vocab.get(tuple(uid))
    return entry["palabra"] if entry else None
