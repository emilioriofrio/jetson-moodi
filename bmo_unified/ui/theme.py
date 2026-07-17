# ui/theme.py
"""
Tema centralizado: escala de fuente configurable (Configuraciones -> Tamaño de
fuente) + paleta Moodi compartida.

Todos los tamaños de fuente de ui/*.py pasan por fs(base_px) en vez de px
hardcodeados dispersos. El nivel "normal" ya es más grande que los tamaños de
la iteración anterior (pedido explícito del usuario: el texto actual se veía
pequeño), por eso los llamadores usan bases nuevas más generosas y los
factores escalan alrededor de 1.0.

Los factores están acotados (0.85-1.30) para que "Muy grande" no desborde la
resolución fija 1024x600: los textos largos (leyenda, instrucción de
Oraciones) usan wordwrap y los contenedores tienen holgura vertical calculada
para el peor caso 1.30.

El cambio en caliente sigue el mismo patrón que i18n: AppSettings emite
font_scale_changed, MainWindow llama restyle() en cada panel y cada panel
vuelve a construir sus stylesheets consultando fs() ya actualizado.
"""

# Paleta muestreada de los clips reales de Moodi (ver memoria de proyecto y
# ui/pecs_panel.py) -- compartida aquí para que Configuraciones use exactamente
# los mismos tonos que Oraciones.
BG_TEAL = "#93CDD6"
TEXT_NAVY = "#20323F"
TEXT_NAVY_SOFT = "#3B586A"
CREAM = "#EBEBDE"
CORAL = "#EFA082"
ACCENT_ORANGE = "#FF9500"

FONT_SCALE_FACTORS = {
    "small": 0.85,
    "normal": 1.0,
    "large": 1.15,
    "xlarge": 1.30,
}

_factor = 1.0


def set_font_scale(level: str):
    """Fija el nivel activo ('small'|'normal'|'large'|'xlarge')."""
    global _factor
    _factor = FONT_SCALE_FACTORS.get(level, 1.0)


def fs(base_px: int) -> int:
    """Tamaño de fuente en px para el nivel activo. 'base_px' es el tamaño de
    diseño del nivel Normal."""
    return max(9, round(base_px * _factor))
