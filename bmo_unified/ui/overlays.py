# ui/overlays.py
"""
Widgets de overlay reutilizables para MainWindow (S13):

- PopBadge: insignia emergente (título de clip en Caras, aviso de sonido en
  el resto de pantallas) que aparece/desaparece al instante en su lugar
  final y se autooculta sola.
- MarqueeLabel: la franja de leyenda superior, pero como banner -- si el
  texto no entra en el ancho disponible se desplaza en bucle en vez de
  recortarse.
- MarqueeButton: botón grande con el mismo desplazamiento de texto, para las
  listas de opciones de Configuraciones.
- ExitConfirmCard: tarjeta de confirmación para Salir por botón físico
  (primera presión arma la cuenta regresiva, segunda confirma).

HISTORIAL DE ESTA VENTANA (leer antes de tocar este archivo otra vez):

1. (S13, 3ª ronda) La primera versión pintaba fondos redondeados a mano con
   QPainter + `Qt.WA_TranslucentBackground`. Eso fuerza a Qt a componer ese
   widget con canal alfa real, y esta ventana tiene una restricción
   documentada (ver ui/ghost_ribbon.py): cualquier técnica así rompe el
   overlay de video nativo de AnimationPlayer -- se vio como pantallas
   "montadas" unas sobre otras. SOLUCIONADO cambiando a QSS + WA_StyledBackground.
2. (S13, 5ª ronda) Con QSS pero SIN selector, `setStyleSheet()` aplicaba las
   reglas también a los QLabel hijos (icono/texto) -- se vio como una línea
   divisoria entre ellos. SOLUCIONADO con un `objectName` + selector `#id`
   que limita la regla al widget exacto, más `border: none` explícito en los
   hijos como defensa doble.
3. (S13, 6ª ronda) Se probó una variante SIN fondo en absoluto (ni QSS ni
   QPainter, solo texto con contorno), asumiendo que un QWidget sin
   `autoFillBackground` no se auto-rellena. Se verificó offscreen
   (QT_QPA_PLATFORM=offscreen) y se veía bien -- pero en el robot real
   apareció igual una caja rectangular blanca/gris detrás del texto: el
   supuesto de "sin fondo por defecto" NO se sostuvo en la plataforma real
   (offscreen no reproduce fielmente cómo pinta el backend real del panel).
   Lección: **verificar offscreen ayuda a atrapar bugs obvios, pero no
   reemplaza la confirmación en el robot real -- sobre todo para cosas de
   fondo/composición.**
4. (S13, 7ª-8ª ronda) Conclusión: la única técnica que se probó SIN problemas en
   el robot real (los botones "grandes" de Configuraciones y el botón ✕ del
   monitor emocional, éste último ya en producción desde S11) es
   `QPushButton` + QSS con `border-radius`, sin `WA_TranslucentBackground` y
   sin depender de "no pintar nada" en las esquinas.
5. (S13, 10ª ronda) `PopBadge` se migró de `QWidget` a `QPushButton`:
   `QWidget` con `WA_StyledBackground` provocaba que Qt rellenara las esquinas
   exteriores de la pastilla redondeada con el fondo blanco de ventana por
   defecto. En cambio, `QPushButton` con QSS `#popBadgeRoot` y `border-radius`
   se compone de forma limpia y transparente sobre el video sin caja blanca de
   fondo, manteniendo los toques passthrough con `WA_TransparentForMouseEvents`.
   Una captura ESTÁTICA del resultado ya asentado se veía perfecta.
6. (S13, 11ª ronda) El usuario, viéndolo EN VIVO (no en una captura), seguía
   reportando fondo blanco. Diferencia encontrada respecto a `GhostRibbon`
   (la única otra insignia con fondo QSS sobre el video, estable desde hace
   semanas): `GhostRibbon` NUNCA mueve su propio rectángulo exterior (solo
   anima un hijo interno, `_reel`, vía `pos`); `PopBadge` sí animaba SU
   PROPIA posición completa (`QPropertyAnimation` sobre `pos`, deslizándose
   ~280ms en cada aparición) mientras está superpuesta al overlay nativo de
   video -- el único caso en toda esta ventana de un widget con fondo QSS
   reposicionándose sobre el video en vivo. Es plausible que el fondo blanco
   solo aparezca DURANTE ese movimiento (invisible en una captura tomada
   después de que el movimiento termina, pero visible a simple vista).
   SOLUCIONADO (se pensó) quitando la animación de posición: `PopBadge` fijó
   su geometría final UNA sola vez y apareció/desapareció al instante. El
   usuario probó de nuevo -- SEGUÍA viéndose el fondo blanco. Esto descarta
   definitivamente la animación como causa.
7. (S13, 12ª ronda) Con animación Y sin animación, con `QWidget` Y con
   `QPushButton`, el fondo blanco NO se fue -- lo único que las tres
   variantes tenían en común era depender de QSS/`border-radius` para dejar
   las esquinas "sin pintar" (y así ver el video a través). `MarqueeLabel`
   (la leyenda), en cambio, NUNCA mostró este problema en ninguna ronda, y
   es la única insignia de este archivo que NO usa QSS ni deja ninguna zona
   sin pintar: rellena el 100% de su propio rectángulo con `fillRect` en
   cada `paintEvent`. **`PopBadge` se reescribió para hacer EXACTAMENTE
   eso**: `QWidget` plano, sin QSS, sin `WA_StyledBackground`, sin
   `border-radius` -- un rectángulo de esquinas rectas, pintado al 100% con
   `painter.fillRect()`. Ya no es una "pastilla" redondeada (se sacrificó
   esa forma), pero no queda NINGÚN píxel de su rectángulo sin definir, así
   que no hay ninguna zona que pueda mostrar un fondo "residual" de ningún
   tipo -- es, por construcción, la misma garantía que ya tenía
   `MarqueeLabel`.

Si hace falta un widget nuevo con fondo/forma sobre el video en esta ventana:
preferir SIEMPRE `QWidget` + `QPainter.fillRect()` cubriendo el 100% del
rectángulo propio (la técnica de `MarqueeLabel`/`PopBadge`, la ÚNICA que
nunca mostró el fondo blanco) por sobre QSS/`border-radius`/`WA_StyledBackground`
(probado 3 veces, falló las 3). Nunca `QGraphicsEffect` ni
`Qt.WA_TranslucentBackground`. Si hace falta alguna forma no rectangular,
NO usar QSS para lograrlo -- ver "Si esto TODAVÍA no alcanza" más abajo.
Tampoco animar la posición/geometría del widget mientras esté visible sobre
el video -- si hace falta alguna animación, que sea sobre un hijo interno
cuyo padre no se mueva, como ya hace GhostRibbon.

## Si esto TODAVÍA no alcanza

Si incluso con `PopBadge` reescrita como rectángulo de relleno 100% (ronda
12) el fondo blanco SIGUE viéndose en el robot real, entonces el problema NO
es la técnica de pintado (ya se probaron QWidget, QPushButton, con QSS, sin
QSS, con animación, sin animación, y la MISMA técnica exacta que usa
`MarqueeLabel` sin problemas) -- hay que diagnosticar directamente en el
robot en vez de seguir adivinando desde el código:

- `xwininfo -root -tree` (con la app corriendo) para ver la jerarquía real de
  ventanas X11: `AnimationPlayer`/`QVideoWidget` casi seguro es una ventana
  X11 NATIVA propia (GStreamer necesita un Window ID real para el overlay de
  video vía Xv/`xvimagesink` -- confirmado en este equipo: `gst-inspect-1.0
  xvimagesink` expone `colorkey`/`autopaint-colorkey`, la técnica clásica de
  overlay por clave de color de X11). Si PopBadge NO es también una ventana
  nativa, Qt no puede garantizar su orden de apilamiento (`raise_()`) por
  encima de esa ventana nativa mediante los mecanismos normales de widgets
  software.
- Si `xwininfo` confirma que el video es una ventana nativa separada y
  PopBadge no: probar `self.setAttribute(Qt.WA_NativeWindow, True)` en
  PopBadge. Esto es DISTINTO de `WA_TranslucentBackground` (no activa canal
  alfa/composición offscreen, solo le da a Qt una ventana X11 real para
  poder apilarla correctamente) -- pero tampoco se probó nunca en este
  código, así que no se debe asumir que funciona sin verificarlo.
- Considerar también si el "fondo blanco" es en realidad la ventana nativa
  del VIDEO ganándole el apilamiento a CUALQUIER widget Qt normal en esa
  franja de pantalla, independientemente de qué se pinte -- en ese caso el
  problema no está en `PopBadge` en absoluto, sino en cómo `_animation` se
  apila contra sus hermanos, y valdría la pena revisar si `_animation` se
  puede insertar/recortar para dejar SIEMPRE libre la franja donde van estas
  insignias (en vez de cubrir literalmente toda la pantalla).
"""

from PyQt5.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF,
                          Qt, QTimer, pyqtProperty, pyqtSignal)
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from core.i18n import t
from ui.theme import ACCENT_ORANGE, BG_TEAL, CORAL, CREAM, TEXT_NAVY, fs

EMOJI_FONT = "Noto Color Emoji"
POP_MS = 280


class PopBadge(QWidget):
    """Insignia emergente (título de clip en Caras, aviso de silenciar/
    video-sin-cortes fuera de Oraciones).

    S13 (12ª ronda) -- REESCRITA desde cero tras que las tres técnicas
    anteriores (QWidget+WA_StyledBackground, QPushButton+QSS,
    QPushButton+QSS sin animación) siguieran mostrando un fondo blanco en el
    robot real. Las tres compartían lo mismo: dependían de QSS/border-radius
    para dejar las esquinas "sin pintar" y que se vea el video a través.
    Esta versión NO deja NADA sin pintar: `paintEvent` rellena el 100% de su
    propio rectángulo con QPainter puro (`fillRect`, sin QSS, sin
    `border-radius`, esquinas rectas) -- exactamente la misma técnica que ya
    usa `MarqueeLabel` (la leyenda superior), la única insignia de este
    archivo que NUNCA mostró este problema en ninguna ronda. Si el
    rectángulo de esta insignia no tiene ninguna zona transparente/sin
    pintar, no puede haber ningún fondo "residual" que se filtre --
    cualquier técnica que dependa de dejar zonas sin pintar (QSS,
    WA_TranslucentBackground, "no pintar nada") ya se probó y falló.

    Sacrifica las esquinas redondeadas (ya no es una "pastilla", es una
    placa rectangular, igual que la leyenda) a cambio de una garantía real:
    no hay ningún caso en que un píxel de su rectángulo quede sin definir.

    Misma paleta "activo" que ya usan los botones seleccionados de
    Configuraciones (navy + borde naranja + texto crema). Tamaño calculado a
    partir del contenido real (icono + texto) cada vez que se muestra un
    mensaje, así que nunca recorta texto largo. Aparece/desaparece al
    instante en su posición final -- sin animación de posición (ver ronda
    anterior: era la única insignia que movía su propio rectángulo sobre el
    video en vivo, y se sospechaba causante del defecto)."""

    PAD_H = 18
    PAD_V = 10
    ICON_TEXT_GAP = 8
    BORDER_PX = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._icon = ""
        self._text = ""
        self._icon_font = QFont(EMOJI_FONT, 16)
        self._text_font = QFont()
        self._anchor = QPoint()  # esquina/punto de referencia del borde superior, destino final
        self._anchor_align = "center"  # "center" | "right" -- cómo se ubica respecto a _anchor.x()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self.setVisible(False)

    def set_anchor_top_center(self, x: int, y: int):
        """Punto (x, y) donde debe quedar centrado horizontalmente el borde
        superior de la insignia, calculada según el contenido de cada
        mensaje (la fija MainWindow en _restyle_all)."""
        self._anchor = QPoint(x, y)
        self._anchor_align = "center"

    def set_anchor_top_right(self, x: int, y: int):
        """Punto (x, y) donde debe quedar el borde superior DERECHO de la
        insignia -- para avisos anclados a una esquina en vez de centrados."""
        self._anchor = QPoint(x, y)
        self._anchor_align = "right"

    def show_message(self, icon: str, text: str, duration_ms: int = 1800):
        self._icon = icon
        self._text = text
        self._icon_font = QFont(EMOJI_FONT, fs(20))
        self._text_font = QFont()
        self._text_font.setPixelSize(fs(17))
        self._text_font.setBold(True)

        fm_icon = QFontMetrics(self._icon_font)
        fm_text = QFontMetrics(self._text_font)
        icon_w = fm_icon.horizontalAdvance(icon) if icon else 0
        text_w = fm_text.horizontalAdvance(text) if text else 0
        gap = self.ICON_TEXT_GAP if (icon_w and text_w) else 0
        content_h = max(fm_icon.height() if icon else 0, fm_text.height() if text else 0)

        pad_h, pad_v = fs(self.PAD_H), fs(self.PAD_V)
        w = pad_h * 2 + icon_w + gap + text_w
        h = pad_v * 2 + content_h
        if self._anchor_align == "right":
            target_x = self._anchor.x() - w
        else:
            target_x = self._anchor.x() - w // 2
        target = QRect(target_x, self._anchor.y(), w, h)

        # Geometría final fijada UNA sola vez, nunca animada/movida después.
        self.setGeometry(target)
        self.setVisible(True)
        self.raise_()
        self.update()
        self._hide_timer.start(duration_ms)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        # Rellena el 100% del rectángulo, sin excepción -- ver docstring de
        # la clase. Nada queda "sin pintar" en ningún píxel de este widget.
        painter.fillRect(rect, QColor(TEXT_NAVY))
        border = QPen(QColor(ACCENT_ORANGE), self.BORDER_PX)
        painter.setPen(border)
        painter.setBrush(Qt.NoBrush)
        half = self.BORDER_PX / 2.0
        painter.drawRect(QRectF(rect).adjusted(half, half, -half, -half))

        if not self._icon and not self._text:
            painter.end()
            return

        fm_icon = QFontMetrics(self._icon_font)
        fm_text = QFontMetrics(self._text_font)
        icon_w = fm_icon.horizontalAdvance(self._icon) if self._icon else 0
        text_w = fm_text.horizontalAdvance(self._text) if self._text else 0
        gap = self.ICON_TEXT_GAP if (icon_w and text_w) else 0
        pad_h = fs(self.PAD_H)
        x = pad_h

        if self._icon:
            painter.setFont(self._icon_font)
            painter.setPen(QColor("white"))
            painter.drawText(QRectF(x, 0, icon_w, rect.height()),
                             Qt.AlignVCenter | Qt.AlignLeft, self._icon)
            x += icon_w + gap

        if self._text:
            painter.setFont(self._text_font)
            painter.setPen(QColor(CREAM))
            painter.drawText(QRectF(x, 0, text_w + 4, rect.height()),
                             Qt.AlignVCenter | Qt.AlignLeft, self._text)
        painter.end()


class MarqueeLabel(QWidget):
    """Banner de texto: si entra en el ancho disponible se muestra centrado y
    quieto (igual que la leyenda de siempre); si NO entra, se desplaza en
    bucle continuo en vez de recortarse. Pinta el 100% de su rectángulo en
    cada frame (relleno + texto), así que no necesita WA_TranslucentBackground
    -- Qt ya compone su fondo opaco con normalidad."""

    SPEED_PX_PER_TICK = 1.6
    TICK_MS = 30
    GAP_PX = 70

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._text = ""
        self._font = QFont()
        self._color = QColor("white")
        self._bg = QColor(0, 0, 0, 120)
        self._offset = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)

    def set_style(self, font: QFont, color: QColor, bg: QColor):
        self._font = font
        self._color = color
        self._bg = bg
        self.update()

    def setText(self, text: str):
        self._text = text or ""
        self._offset = 0.0
        self._sync_scroll_state()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_scroll_state()

    def _text_width(self) -> int:
        return QFontMetrics(self._font).horizontalAdvance(self._text) if self._text else 0

    def _sync_scroll_state(self):
        overflow = self._text_width() > self.width() - 16
        if overflow and not self._timer.isActive():
            self._timer.start()
        elif not overflow and self._timer.isActive():
            self._timer.stop()
            self._offset = 0.0

    def _tick(self):
        tw = self._text_width()
        self._offset -= self.SPEED_PX_PER_TICK
        if -self._offset >= tw + self.GAP_PX:
            self._offset = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self._bg)
        if not self._text:
            painter.end()
            return
        painter.setFont(self._font)
        painter.setPen(self._color)
        fm = QFontMetrics(self._font)
        tw = fm.horizontalAdvance(self._text)
        y = (self.height() + fm.ascent() - fm.descent()) // 2
        if tw <= self.width() - 16:
            painter.drawText(self.rect(), Qt.AlignCenter, self._text)
        else:
            x = int(self._offset)
            while x < self.width():
                painter.drawText(x, y, self._text)
                x += tw + self.GAP_PX
        painter.end()


class MarqueeButton(QWidget):
    """Botón grande con texto que se desplaza si no entra en el ancho
    disponible (S13, 3ª ronda): algunos textos traducidos (p. ej. la acción
    "Silenciar / activar sonido de Moodi" o el nombre de una pista de música)
    no entran en un botón de la columna angosta de Configuraciones y se
    recortaban.

    El fondo/borde/radio se pintan vía QSS (WA_StyledBackground), exactamente
    igual que el resto de los botones de esta pantalla (`_big_button_style`
    en settings_panel.py) -- ver la nota al principio de este archivo sobre
    por qué NUNCA un fondo redondeado pintado a mano con QPainter aquí. El
    texto se dibuja ENCIMA llamando primero a super().paintEvent(event) (deja
    que Qt pinte el fondo con estilos) y recién después el texto propio con
    QPainter -- técnica estándar de Qt para combinar QSS con dibujo custom,
    sin tocar transparencia ni composición de la ventana."""

    clicked = pyqtSignal()
    MARGIN_PX = 18
    GAP_PX = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self._text = ""
        self._font = QFont()
        self._color = QColor("black")
        self._offset = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    def setText(self, text: str):
        self._text = text or ""
        self._offset = 0.0
        self._sync_scroll_state()
        self.update()

    def set_style(self, stylesheet: str, font: QFont, color: QColor):
        self.setStyleSheet(stylesheet)
        self._font = font
        self._color = QColor(color)
        self._sync_scroll_state()
        self.update()

    def set_text_color(self, color: QColor):
        """Cambia solo el color del texto (S13): usado para resaltar la
        opción seleccionada sin reconstruir fuente/hoja de estilos -- antes
        el texto se dibujaba siempre del mismo color que la opción SIN
        seleccionar, así que quedaba oscuro sobre oscuro (invisible) al
        seleccionar."""
        self._color = QColor(color)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_scroll_state()

    def _content_rect(self):
        return self.rect().adjusted(self.MARGIN_PX, 0, -self.MARGIN_PX, 0)

    def _text_width(self) -> int:
        return QFontMetrics(self._font).horizontalAdvance(self._text) if self._text else 0

    def _sync_scroll_state(self):
        overflow = self._text_width() > self._content_rect().width()
        if overflow and not self._timer.isActive():
            self._timer.start()
        elif not overflow and self._timer.isActive():
            self._timer.stop()
            self._offset = 0.0

    def _tick(self):
        tw = self._text_width()
        self._offset -= 1.4
        if -self._offset >= tw + self.GAP_PX:
            self._offset = 0.0
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)  # fondo/borde/radio vía QSS
        if not self._text:
            return
        cr = self._content_rect()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setClipRect(cr)
        painter.setFont(self._font)
        painter.setPen(self._color)
        fm = QFontMetrics(self._font)
        tw = fm.horizontalAdvance(self._text)
        y = cr.top() + (cr.height() + fm.ascent() - fm.descent()) // 2
        if tw <= cr.width():
            painter.drawText(cr, Qt.AlignVCenter | Qt.AlignLeft, self._text)
        else:
            x = cr.left() + int(self._offset)
            while x < cr.right():
                painter.drawText(x, y, self._text)
                x += tw + self.GAP_PX
        painter.end()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class ExitConfirmCard(QWidget):
    """Tarjeta de confirmación para Salir (S13): el botón físico del panel
    derecho funciona como Enter/Confirmar -- la primera presión ARMA esta
    tarjeta (con cuenta regresiva visible), una segunda presión dentro de la
    ventana confirma y cierra la app; si no llega, se descarta sola. Es un
    camino ADICIONAL por botón físico -- el mantener presionado ~3s sobre el
    icono de la cinta fantasma (ver ui/ghost_ribbon.py) sigue funcionando
    igual, sin cambios.

    Pinta SIEMPRE el velo de pantalla completa antes que nada más, sin
    condición alguna: eso cubre el 100% del widget en todo momento (no hay
    huecos que dependan de transparencia real), y la tarjeta central se
    dibuja ENCIMA de ese velo ya opaco -- por eso no necesita
    WA_TranslucentBackground tampoco: sus esquinas redondeadas solo necesitan
    mostrar el velo (ya pintado un instante antes en el mismo paintEvent),
    nunca el video de más abajo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._scale = 0.0
        self._remaining_frac = 1.0
        self._duration_ms = 4000
        self._elapsed_ms = 0

        self._anim = QPropertyAnimation(self, b"_cardScale", self)
        self._anim.setDuration(POP_MS)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutBack)

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(30)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)

        self.setVisible(False)

    def is_armed(self) -> bool:
        return self.isVisible()

    def arm(self, duration_ms: int = 4000):
        self._duration_ms = duration_ms
        self._elapsed_ms = 0
        self._remaining_frac = 1.0
        self.setVisible(True)
        self.raise_()
        self._anim.stop()
        self._anim.start()
        self._dismiss_timer.start(duration_ms)
        self._countdown_timer.start()
        self.update()

    def dismiss(self):
        self._countdown_timer.stop()
        self._dismiss_timer.stop()
        self.setVisible(False)

    def _get_card_scale(self):
        return self._scale

    def _set_card_scale(self, v):
        self._scale = float(v)
        self.update()

    _cardScale = pyqtProperty(float, _get_card_scale, _set_card_scale)

    def _on_countdown_tick(self):
        self._elapsed_ms += self._countdown_timer.interval()
        self._remaining_frac = max(0.0, 1.0 - self._elapsed_ms / float(self._duration_ms))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Velo de pantalla completa: se pinta SIEMPRE, cubre el 100% del
        # widget de entrada, así que todo lo que se dibuje después (la
        # tarjeta) ya está sobre una superficie propia totalmente opaca.
        painter.fillRect(self.rect(), QColor(10, 16, 20, 165))

        if self._scale <= 0.001:
            painter.end()
            return

        card_w, card_h = 460, 240
        cx, cy = self.width() / 2.0, self.height() / 2.0
        rect = QRectF(cx - card_w / 2, cy - card_h / 2, card_w, card_h)

        painter.translate(cx, cy)
        painter.scale(self._scale, self._scale)
        painter.translate(-cx, -cy)

        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor(BG_TEAL).lighter(108))
        grad.setColorAt(1.0, QColor(CORAL))
        painter.setBrush(grad)
        painter.setPen(QPen(QColor(TEXT_NAVY), 3))
        painter.drawRoundedRect(rect, 26, 26)

        # anillo de cuenta regresiva alrededor de la puerta
        ring_d = 56.0
        ring_rect = QRectF(cx - ring_d / 2, rect.top() + 26, ring_d, ring_d)
        pen_track = QPen(QColor(255, 255, 255, 90), 5)
        painter.setPen(pen_track)
        painter.drawEllipse(ring_rect)
        pen_arc = QPen(QColor(TEXT_NAVY), 5)
        pen_arc.setCapStyle(Qt.RoundCap)
        painter.setPen(pen_arc)
        span_16ths = int(360 * 16 * self._remaining_frac)
        painter.drawArc(ring_rect, 90 * 16, -span_16ths)
        painter.setFont(QFont(EMOJI_FONT, 22))
        painter.setPen(QColor(TEXT_NAVY))
        painter.drawText(ring_rect, Qt.AlignCenter, "🚪")

        title_font = QFont()
        title_font.setPixelSize(fs(24))
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(TEXT_NAVY))
        painter.drawText(QRectF(rect.left() + 16, ring_rect.bottom() + 12, rect.width() - 32, 40),
                         Qt.AlignHCenter | Qt.AlignTop, t("exit.confirm_title"))

        sub_font = QFont()
        sub_font.setPixelSize(fs(15))
        painter.setFont(sub_font)
        painter.setPen(QColor(TEXT_NAVY).lighter(140))
        painter.drawText(QRectF(rect.left() + 16, ring_rect.bottom() + 56, rect.width() - 32, 60),
                         Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, t("exit.confirm_subtitle"))
        painter.end()
