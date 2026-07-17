# core/i18n.py
"""
Internacionalización centralizada (español/inglés). Todos los textos visibles
de la UI pasan por t("clave") -- nada hardcodeado en los módulos de ui/*.py
(requisito del bloque de Configuraciones: idioma con efecto inmediato sobre
toda la interfaz).

Diseño mínimo a propósito: un diccionario plano por idioma cargado de
config/strings_es.json / strings_en.json. El cambio de idioma en caliente NO
lo notifica este módulo (no tiene señales): AppSettings.language_changed es la
señal; MainWindow la recibe y llama retranslate() en cada panel, que vuelve a
consultar t() con el idioma ya actualizado. Así este módulo no depende de Qt y
puede usarse igual desde código no-Qt (p. ej. core/voice.py o tests).

Fallbacks: clave ausente en el idioma activo -> español -> la clave misma
(visible en pantalla, delata la falta de traducción en vez de esconderla).
"""

import json
import logging
import os

log = logging.getLogger("bmo.i18n")

_strings = {}   # lang -> {clave: texto}
_lang = "es"
_loaded_dir = None


def load(config_dir: str):
    """Carga (o recarga) los archivos de strings. Llamar una vez al arranque,
    antes de construir cualquier widget."""
    global _strings, _loaded_dir
    _loaded_dir = config_dir
    _strings = {}
    for lang in ("es", "en"):
        path = os.path.join(config_dir, f"strings_{lang}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            _strings[lang] = {k: v for k, v in data.items() if not k.startswith("_")}
        except Exception:
            log.exception("No se pudo cargar %s", path)
            _strings[lang] = {}


def set_language(lang: str):
    global _lang
    if lang in ("es", "en"):
        _lang = lang


def language() -> str:
    return _lang


def t(key: str, **fmt) -> str:
    """Texto traducido para 'key' en el idioma activo, con .format(**fmt)
    opcional. Nunca lanza por placeholders faltantes: devuelve el texto crudo
    antes que romper la UI."""
    text = _strings.get(_lang, {}).get(key)
    if text is None:
        text = _strings.get("es", {}).get(key)
    if text is None:
        log.warning("Clave i18n sin traducción: %s", key)
        return key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            log.warning("Placeholders inválidos para clave i18n %s", key)
            return text
    return text


def label(raw: str) -> str:
    """Traducción de una etiqueta emitida por el motor de visión (bajo/medio/
    alto, dedos_boca, calma, ...) para mostrar en pantalla. Si no hay clave
    'label.<raw>', se muestra la etiqueta cruda tal cual -- las etiquetas del
    modelo no deben romperse por una traducción faltante."""
    key = f"label.{str(raw).strip().lower()}"
    text = _strings.get(_lang, {}).get(key) or _strings.get("es", {}).get(key)
    return text if text is not None else str(raw)
