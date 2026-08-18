# Reporte de Progreso - Proyecto BMO Unified (Moodi)
**Semana:** S12 (la siguiente a la S11 del 20 al 24 de julio de 2026)
**Archivo:** PROGRESO_BMO_S12.md
**Ubicación:** `/home/jetson/Contexto/S12/`
**Sesión de trabajo:** 17 de agosto de 2026
**Punto de partida:** la sección 9 ("Pendiente para S12") de `Contexto/S11/PROGRESO_BMO_S11.md`
más tres defectos que el usuario detectó **usando el robot de verdad** esta semana.

---

## 1. Contexto y objetivo de la semana

S11 cerró con el motor de visión ya fluido (30 fps reales a la UI), el campo de visión de la cámara
corregido, el LLM de oraciones reescrito por prompt y una lista de nueve pendientes. Lo primero que
hizo S12 fue **contrastar esa lista con el robot funcionando**, y ahí aparecieron cosas que ningún
pendiente contemplaba:

1. **El video de la cara de Moodi se congela por completo** tras un rato largo en la misma pantalla,
   y solo vuelve a moverse al cambiar de pantalla y regresar. Debería ser eterno.
2. **La pantalla táctil no responde**: hay que manejar el robot con un ratón. El usuario precisa que
   *"a veces no funciona desde el arranque o se pierde a medio funcionamiento"*.
3. **El botón ✕ del monitor emocional es inaccesible.**

Y se cerró el frente que S11 dejó explícitamente por comprobar:

4. **Audio saturado**: el usuario subió el volumen de la pantalla al máximo — la hipótesis que S11
   dejó pendiente de verificar — y **la saturación sigue**. Descarta altavoces externos por ahora
   por espacio.

El orden de trabajo fue ése: primero lo que impide usar el robot (congelamiento y táctil), después
lo pendiente de S11. Se mantiene la restricción no negociable desde S06: **nada de `/boot`, overlays
de device tree ni scripts CSI**, y la app nunca corre como `sudo` — relevante esta semana, porque la
solución "de manual" para el problema del táctil habría sido un parámetro de arranque del kernel y
se resolvió por otra vía deliberadamente.

Método, igual que en S08-S11: **medir antes de tocar**. Esta semana eso cambió el diagnóstico dos
veces (el táctil no era un problema de software, y la saturación de audio no era de nivel sino de
picos).

---

## 2. Resumen ejecutivo

| # | Cambio | Estado |
|---|---|---|
| 1 | **Congelamiento del video: REPRODUCIDO y acotado.** Solo ocurre en los clips **con pista de audio**: 2 congelamientos en 5.5 min, contra 0 en 28 min del mismo clip sin audio | ✅ Causa acotada con medición |
| 1b | Corrección: los clips con audio dejan de usar el salto anticipado de loop y dan la vuelta al final real | ✅ Implementado y verificado en soak |
| 1c | Red de seguridad: vigilante que detecta que la posición dejó de avanzar y recarga el clip solo — la misma recuperación que el usuario hacía a mano cambiando de pantalla. **Ya demostrado funcionando**: recuperó los 2 congelamientos del soak en ~4 s cada uno, sin intervención | ✅ Implementado, con registro en el log |
| 1d | Recarga preventiva del pipeline cada 20 min, justo al dar la vuelta el clip (cubre el segundo modo de fallo posible, que el vigilante no puede ver) | ✅ Implementado |
| 2 | **Táctil**: no es la app — el panel (ILI 222a:0001 "HQEmbed Multi-Touch") **no se enumera en el bus USB**. Una suspensión + reanudación lo devuelve, y `reconectar_tactil.sh` hace lo mismo sin reiniciar | ✅ Diagnosticado con el dispositivo delante |
| 2b | La hipótesis del autosuspend **quedó descartada** al medirla sobre el dispositivo real (`control=on`): la regla udev pasa a ser red de seguridad, no el arreglo. Queda el vigilante en la app, que registra con hora cada aparición/desaparición | ✅ Corregido el diagnóstico — ver sección 4 |
| 3 | **Botón ✕ del monitor emocional**: la leyenda superior se comía los toques de su mitad de arriba | ✅ Corregido |
| 4 | **Audio saturado**: no era el nivel medio sino la excursión de picos (~22 dB). Clips recomprimidos: misma sonoridad, **pico 4.4 dB más bajo**; y el slider de volumen pasa a escala perceptual | ✅ Implementado y medido |
| 5 | **Módulo C (pendientes 4 y 5 de S11): de 2 predicciones cada 150 s a 70**, o sea de una cada 48 s a una cada 1.84 s, **sin perder un fps** de los 30 que llegan a la UI. Causa raíz: el módulo llevaba corriendo **en CPU** pese a `use_gpu: true`, porque `run.py` apaga CUDA para todo el proceso y los hijos lo heredan | ✅ Corregido y medido — ver sección 6 |
| 5b | **Contención de GPU (pendiente 9 de S11)**: medida con el LLM trabajando a la vez. La visión mantiene 29.99 fps y el LLM pasa de 0.4-0.9 s a 0.7 s de mediana | ✅ Medido; conviven |
| 5c | **`min_person_ratio` calibrado con una persona real (pendiente 3 de S11)**: 0.12 → 0.18. El valor viejo caía dentro del ruido de la sala vacía y marcaba "hay persona" en 20 de 208 lecturas sin nadie delante | ✅ Medido con 275 predicciones de las dos escenas |
| 6 | **LLM de oraciones, segunda vuelta**: el prompt lleva ahora las 34 tarjetas reales agrupadas por tipo (leídas del mismo `rfid_vocab.json` que usa la app) y 5 ejemplos con las tarjetas poco frecuentes, que es donde el usuario ve que "no contextualiza" | ✅ **A/B de 24 casos: 24/24 correctas**, 0.4-0.9 s |
| 6b | Incidente: una corrida devolvió basura en los 24 casos (con caracteres cirílicos incluidos) y no se ha podido reproducir. El puente ya **no se fía** del modelo: valida la respuesta, reintenta sin caché y si no, usa el respaldo determinista | ✅ Blindado; causa raíz **abierta** — ver 7.2 |
| 7 | Pantalla de carga: lluvia de piezas repartida por carriles para que cubra todo el ancho, y logo por ancho objetivo en vez de factor fijo | ✅ Implementado |
| 8 | Suite headless S12: **44/44 OK** | ✅ Ver sección 9 |
| 9 | Validación física en el robot | ⏳ Pendiente del usuario — ver sección 11 |

---

## 3. El congelamiento del video de la cara

### 3.1 Qué se descartó primero

El síntoma ("se queda congelado del todo y hay que cambiar de pantalla y volver") admite varias
explicaciones y casi todas se pueden descartar sin tocar código:

- **Ahorro de energía / salvapantallas de X11.** Descartado midiendo: `xset q` da `timeout 0`,
  `DPMS Standby/Suspend/Off = 0`, y `gsettings` da `idle-delay 0`. Nada apaga ni redibuja la
  pantalla por inactividad.
- **Ráfaga de seeks del loop sin corte.** Ésta era la sospecha con más fundamento: `_check_seamless_loop()`
  sondea la posición cada 40 ms y salta a 0 al acercarse al final; si `position()` tardara en
  reflejar el salto, se dispararían decenas de seeks por vuelta y esa metralla podría atascar el
  pipeline a la larga. **Medido y descartado**: en 28 minutos de reproducción continua hubo
  exactamente **206 seeks en 206 vueltas — 1.00 por vuelta**.
- **Fuga de memoria.** Descartado: la memoria residente se mantuvo plana en 224 MB durante los 28
  minutos.

### 3.2 Reproducido: es el AUDIO del clip, no el tiempo

Se construyó `bmo_unified/tests/soak_animacion.py`, que reproduce el **mismo `AnimationPlayer` de la
app** (no una copia) sobre el display real y registra una vez por segundo posición, estado,
`mediaStatus`, vueltas, seeks y memoria.

La primera corrida, con el clip **sin pista de audio** (que es lo que se reproduce en Home,
Oraciones y Video), salió impecable. La segunda, idéntica salvo que reproduce la variante **con
pista de audio** (lo que se reproduce en Caras), falló dos veces en cinco minutos y medio:

| Corrida | Duración | Vueltas | Seeks/vuelta | Congelamientos |
|---|---|---|---|---|
| **Sin** pista de audio | 28 min | 206 | 1.00 | **0** |
| **Con** pista de audio | 5.5 min | 44 | 1.00 | **2** (a los 119 s y a los 316 s) |

Eso convierte "se congela tras un rato largo" en algo mucho más concreto: **no es el tiempo, es el
audio**. La forma del fallo es siempre la misma y muy característica:

```
315.2s  pos=7831  Playing/Buffered   <- último frame antes del final
316.2s  pos=0     Playing/Buffered   <- da la vuelta
317.2s  pos=0     Playing/Buffered   <- y se queda clavado en 0
318.2s  pos=0     Playing/Buffered
319.2s  pos=0     Playing/Buffered   <- 3 s parado, sigue diciendo "Playing"
```

Es decir: **el pipeline se atasca justo al dar la vuelta**, se queda en la posición 0 y el
reproductor sigue reportando `PlayingState` tan tranquilo. Encaja exactamente con lo que el usuario
ve (imagen pegada, la app respondiendo con normalidad) y también con la familia de problemas que ya
documentaron S10 y S11 en esta misma Jetson: **lo que este backend no tolera es tocar el pipeline
mientras hay una pista de audio viva** (entonces fue `setMuted()`/`setVolume()`, ahora es el salto
anticipado del loop).

### 3.3 La corrección: los clips con audio dan la vuelta al final real

El loop "sin corte" de S11 salta a 0 unos 120 ms **antes** del final, con el pipeline en plena
reproducción, para evitar el manejo de fin de stream de GStreamer (que se nota como un salto duro).
Ese truco es correcto y estable... mientras no haya pista de audio.

Ahora `_check_seamless_loop()` **no hace el salto anticipado en los clips con audio**: los deja
llegar al final de verdad y reinicia desde `EndOfMedia`. Los clips silenciosos —o sea Home,
Oraciones y Video, que es donde la cara está la mayor parte del tiempo— conservan el salto
anticipado, que es el que no se nota y que 28 minutos de soak demostraron estable.

El precio es que en la pantalla Caras la vuelta puede notarse un poco más. A cambio desaparece un
congelamiento de varios segundos cada pocos minutos: el cambio es claramente favorable, y está en
una constante (`SEAMLESS_LOOP_WITH_AUDIO`) por si se quisiera revisar.

**Verificación**, misma máquina, mismo clip con audio, misma medición:

| Clip con audio | Duración | Vueltas | Congelamientos |
|---|---|---|---|
| Loop con salto anticipado (como estaba) | 5.5 min | 44 | **2** |
| Loop esperando al final real (S12) | 13.2 min | **95** | **0** |

Es decir: el modo anterior se atascaba dos veces antes de la vuelta 44, y el nuevo llegó a la vuelta
95 sin un solo incidente (ni un solo segundo de posición estancada).

### 3.4 El vigilante (red de seguridad, ya probada en fallo real)

`AnimationPlayer._check_watchdog()` (nuevo, en `ui/animation_player.py`), una vez por segundo:

- El criterio **no es el estado del reproductor sino el avance real de la posición**. Esto es
  deliberado: en el congelamiento reportado el reproductor sigue diciendo `PlayingState` — por eso
  es silencioso y no hay ningún error que capturar. Si la posición lleva 4 s sin moverse cuando
  debería estar reproduciendo, está congelado.
- La recuperación es **exactamente la que hacía el usuario a mano**: recargar el clip. Cambiar de
  pantalla y volver acaba llamando a `_set_media()`, o sea reconstruir el pipeline; el vigilante
  hace eso mismo sin que el usuario tenga que notarlo.
- Cada intervención se registra en `logs/bmo_app.log` con nivel WARNING, incluyendo cuántos
  segundos estuvo parado y en qué clip. **Es la única forma de saber cada cuánto pasa de verdad.**
- Falsos positivos cubiertos: periodo de gracia de 6 s tras cada recarga, y se ignora mientras el
  medio está en `LoadingMedia`/`NoMedia` (donde la posición es legítimamente 0).

**No es teoría: ya se le vio funcionar.** Los dos congelamientos del soak con audio ocurrieron con
el vigilante activo, y en ambos casos quedó esto en el log y la imagen volvió sola en ~4 s:

```
Video congelado: 4.0s sin avanzar (pos=0 estado=1 status=6 clip=Animación Audio 12 - Sonreir.mp4).
Recargando el clip -- recuperación nº1 de esta sesión.
```

Es decir que aunque quedara alguna variante del fallo sin cubrir por el arreglo de 3.3, el video ya
no se queda muerto esperando a que el usuario cambie de pantalla.

### 3.5 La recarga preventiva (el segundo modo de fallo)

Hay un modo de fallo que el vigilante **no puede detectar**: que lo que se quede pegado sea la
superficie de video (el plano de overlay de X11/GStreamer) y no el pipeline. En ese caso la posición
avanza con normalidad, para el reproductor todo va bien, y sin embargo la pantalla no se actualiza.

Encaja con un detalle del reporte: al usuario le basta con cambiar de pantalla y volver, y entrar a
Oraciones **también cambia la geometría** del widget de video (pasa al recuadro chico de la cara),
que es justo lo que fuerza a redibujar ese plano. No se puede distinguir desde dentro del proceso:
capturar la ventana de un overlay nativo devuelve el color de clave, no el video.

Como no se puede detectar, se previene: el pipeline se renueva cada 20 minutos por su cuenta, y
**solo justo después de que el clip dio la vuelta** (posición < 1.5 s). El clip ya reinicia solo cada
~8 s, así que reiniciarlo en ese punto se ve igual que una vuelta normal. Está en una constante
(`PREVENTIVE_RELOAD_MS`) para poder desactivarla en un segundo si resultara visible.

### 3.6 Qué hace falta para cerrarlo

Que el usuario use el robot normalmente y, si vuelve a congelarse, mire el log:

- Si aparece `Video congelado: … Recargando el clip` → el vigilante lo vio y lo arregló solo; queda
  saber cada cuánto pasa y con qué correlaciona.
- Si **no** aparece nada y la imagen igual se quedó pegada → es el segundo modo (la superficie), y
  entonces la vía es la recarga preventiva (bajarla de 20 min) o forzar el redibujado del plano.

---

## 4. El táctil: no era la app

**Síntoma:** el táctil no responde en absoluto; hay que usar el ratón. Y a veces funciona al
arrancar y se pierde a mitad de sesión.

**Diagnóstico (medido en el momento del fallo).** No hay ningún dispositivo táctil en el sistema:

- `/proc/bus/input/devices` lista solo `gpio-keys`, el receptor Logitech (teclado/ratón), la webcam
  y las salidas de audio HDMI. **Ningún dispositivo con ejes multitáctil.**
- `lsusb` no muestra ningún controlador táctil: solo los dos concentradores Realtek, el Bluetooth,
  el CP210x del ESP32, la webcam y el receptor Logitech.
- `xinput list` confirma lo mismo del lado de X11: los únicos punteros son el ratón Logitech y los
  virtuales.

O sea: **el kernel no ve el panel táctil, así que ningún programa puede recibir toques.** No hay nada
que arreglar en Qt ni en la app. Es el panel el que desaparece del bus USB.

**El dispositivo, ya identificado.** Al final de la sesión el usuario suspendió la Jetson y al
reanudarla **el táctil volvió a funcionar**. Con él vivo se pudo ver por fin qué es y dónde está:

```
HQEmbed Multi-Touch  --  ILI Technology Corp. Multi-Touch Screen (USB 222a:0001)
puerto 1-2.3 del concentrador interno   ·   X11 lo ve como pointer id=11
```

**Y eso descartó la primera hipótesis.** La sospecha inicial era el autosuspend de USB, que en esta
Jetson está en 2 segundos (`/sys/module/usbcore/parameters/autosuspend = 2`). Comprobado sobre el
dispositivo real:

```
1-2.3: control=on   runtime_status=active   autosuspend_delay_ms=2000
```

`control=on` significa que **usbhid ya lo exime de la suspensión por su cuenta**, igual que al ratón.
O sea que el autosuspend no lo estaba tocando y la regla udev, por sí sola, no habría arreglado
nada. Queda instalable como red de seguridad, pero **no es el arreglo** y así está escrito ahora en
la cabecera del propio script, para que nadie la instale creyendo que resuelve el problema.

**Lo que sí queda establecido:** el táctil **no se enumera en el bus** algunas veces —ni en
`/proc/bus/input/devices` ni en `lsusb`— y una **suspensión + reanudación lo devuelve**. Eso apunta
al enlace y a la enumeración (arranque en frío, cable/conector, puerto del concentrador), no a la
gestión de energía en marcha. Es exactamente el caso que cubre `reconectar_tactil.sh`, que fuerza la
re-enumeración sin tener que reiniciar ni suspender: **la próxima vez que pase, hay que probar ese
script antes que la suspensión**, porque es lo que convierte una molestia de minutos en un comando.

> Corrección de método: la primera versión de `diagnostico_tactil.sh` decía "NINGUNO" **con el
> táctil delante**. La expresión regular usaba intervalos (`{9,}`), que mawk no admite sin
> `--re-interval`. Un falso negativo en la herramienta que existe justo para decidir "¿es el
> hardware o es la app?" es peor que no tener herramienta: se corrigió comprobando los bits 53/54
> (ABS_MT_POSITION_X/Y) dígito a dígito, y se verificó contra el dispositivo real.

**Lo que se entrega** (tres piezas, ninguna toca `/boot`; la solución "de manual" habría sido el
parámetro de arranque `usbcore.autosuspend=-1`, y eso está prohibido en este proyecto):

1. `bmo_unified/tests/diagnostico_tactil.sh` — responde en un vistazo si hay táctil, si lo ve X11 y
   qué hacer en cada caso. Es la herramienta para no volver a discutir si "es la app".
2. `bmo_unified/instalar_regla_tactil.sh` — instala una regla udev que impide suspender los
   dispositivos USB de clase HID (la clase del táctil). **Requiere `sudo` una sola vez** y es
   reversible borrando el archivo de regla.
3. `bmo_unified/core/touch_monitor.py` — vigilante dentro de la app: cada 5 s comprueba si el táctil
   está presente, **registra con hora exacta cuándo aparece y cuándo desaparece** (sin esto, "a veces
   se pierde" es irreproducible) y, al reaparecer, lo vuelve a mapear a la salida de video con
   `xinput map-to-output` — un dispositivo que se reconecta a mitad de sesión vuelve sin esa
   asociación y sus toques caerían desplazados.

Y `bmo_unified/reconectar_tactil.sh`, que fuerza la re-enumeración del bus USB sin reiniciar la
Jetson, para cuando el táctil ya se perdió (con la app cerrada: del mismo concentrador cuelgan la
cámara y el ESP32).

---

## 5. Audio saturado: cerrado por software hasta donde da

**Lo que S11 dejó por comprobar y ya está comprobado:** subir el volumen de la pantalla al máximo
**no** elimina la saturación. El usuario descarta altavoces externos por ahora por espacio.

**El diagnóstico cambió al medir.** La suposición era que el problema es el nivel; los números dicen
que no. Medido con `loudnorm`/`volumedetect` sobre los cuatro clips:

| Clip | Sonoridad integrada | Pico real |
|---|---|---|
| Sonreir | -17.9 LUFS | **-1.5 dBTP** |
| Perro | -21.7 LUFS | -5.7 dBTP |
| Gato | -22.8 LUFS | -9.6 dBTP |
| Color Manzana | -19.0 LUFS | -3.7 dBTP |

El nivel medio es discreto, pero el pico llega a 1.5 dB del techo digital: **~22 dB de factor de
cresta**. Un altavoz pequeño como el del panel ElecLab no reproduce esa excursión sin distorsionar, y
eso se oye como saturación aunque el volumen medio sea bajo. Además había **4.9 LU de diferencia**
entre el clip más fuerte y el más flojo, así que el usuario ajustaba el volumen para uno y le
quedaba mal para otro.

**Corrección (la estándar para altavoces chicos): comprimir los picos contra la media y renormalizar
a la misma sonoridad con un techo mucho más bajo.** Se genera una variante de cada clip
(`_audio_normalizado/`, cacheada, igual que ya se hacía con `_sin_audio/`), re-codificando **solo el
audio** (`-c:v copy`: el video queda bit a bit idéntico, así que esto no puede afectar al loop):

| Clip | Antes | Después |
|---|---|---|
| Sonreir | -17.9 LUFS / -1.5 dBTP | **-17.3 LUFS / -5.9 dBTP** |
| Perro | -21.7 LUFS / -5.7 dBTP | -16.3 LUFS / -5.9 dBTP |
| Gato | -22.8 LUFS / -9.6 dBTP | -16.4 LUFS / -5.8 dBTP |
| Color Manzana | -19.0 LUFS / -3.7 dBTP | -17.2 LUFS / -5.9 dBTP |

Es decir: **se oye igual de fuerte o más, golpeando el altavoz 4.4 dB menos**, y los cuatro clips
quedan a menos de 1 LU entre sí (antes 4.9 LU).

> Detalle que costó una iteración y conviene no repetir: el primer intento añadía `alimiter` como
> red de seguridad y el resultado salió **peor** que el original (-13.9 LUFS / -0.4 dBTP). La opción
> `level` de `alimiter` viene **activada por defecto** y re-nivela la salida hasta el techo,
> deshaciendo justo lo que `loudnorm` acababa de hacer. Se quitó.

**Segundo cambio: el slider de volumen pasa a escala perceptual.** `QMediaPlayer.setVolume()` es
amplitud lineal, no sonoridad percibida: la mitad del recorrido son solo -6 dB y bajar de 100 a 80
quitaba **1.9 dB**, o sea que el usuario no tenía control fino justo donde lo necesita. Con la
conversión de Qt (`QAudio.convertVolume`), medido: 80 → **-9.1 dB**, 92 → -5.2 dB, 50 → -16.4 dB. El
slider se vuelve utilizable de punta a punta.

> Nota práctica: `settings.json` tiene ahora `volume: 100`, que el usuario subió buscando
> inteligibilidad. Con la escala perceptual, 100 es literalmente el techo digital y no deja ningún
> margen. Con los clips recomprimidos conviene volver a bajarlo (80-90) y comprobar: ahí el slider
> ya sí quita dB de verdad.

**Esto es mitigación, no cura.** La solución real sigue siendo hardware (un amplificador pequeño o
altavoces mejores) y el usuario ya decidió posponerla. Lo que queda por software estaba en los picos
y ya se aplicó.

---

## 6. Módulo C: de una predicción cada 48 s a una cada 1.8 s

Éste era el pendiente 4 de S11 ("el trabajo más valioso pendiente sobre el motor de visión"), junto
con el 5 (frames uniformemente espaciados). Resultó que el cuello de botella no era ninguno de los
dos sospechosos que S11 apuntaba.

### 6.1 La medición que lo cambió todo

Lo primero fue instrumentar el módulo para que dijera **en qué se le va el tiempo** entre predicción
y predicción, en vez de seguir ajustando parámetros a ciegas. La primera corrida ya señaló al
culpable:

```
[C][TIEMPOS] ciclo=49.5s | esperar_frames=0.7s | detectron=32.0s (2 det) |
             flujo=1.2s | vgg=15.2s | bilstm=0.41s | frames_aceptados=11
```

O sea: **de los 48 s de ciclo, 32 se iban en dos detecciones de persona (16 s cada una) y 15 en la
ráfaga de VGG16**. Esperar frames —lo que S11 suponía que era el problema, "rellenar su ventana de
11 frames"— costaba **0.7 s**. La hipótesis heredada era falsa.

Un rato después, al imprimir el dispositivo real desde dentro del worker, apareció el porqué:

```
[C] Iniciado (Detectron2 + VGG16 + BiLSTM) en cpu (use_gpu=True, cuda_disponible=False)
```

**El Módulo C llevaba corriendo entero en CPU**, pese a `use_gpu: true` en `runtime.yaml` y pese a
que `CLAUDE.md` lo describe como el módulo que usa GPU. La causa está en la **línea 3 de `run.py`**:

```python
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
```

Eso apaga la GPU para todo el proceso, y con `multiprocessing` en modo *spawn* **cada hijo hereda
ese entorno**. Los Módulos A y B la apagan además por su cuenta, cada uno en su módulo y con el
motivo documentado (TF y Torch peleándose por la GPU compartida); pero C, que era justamente el que
debía usarla —el reparto "híbrido" que declara la configuración—, nunca llegó a verla.

### 6.2 Correcciones

1. **Devolverle la GPU al Módulo C** (`run.py`, `entry_worker_c`): antes de importar `mod_c` se
   quita `CUDA_VISIBLE_DEVICES` **si su config pide GPU**. Tiene que ser antes del import porque
   torch fija la visibilidad de dispositivos al cargarse. A y B siguen en CPU, intactos. Con
   `use_gpu: false` todo vuelve al comportamiento anterior.
2. **Detectron2 a la resolución real** (`detectron_min_size: 360`): por defecto reescala el lado
   corto a 800 px, o sea que **ampliaba** el frame de 640x360 a 800x1422 para detectar — ~5x más
   píxeles de los que la imagen tiene.
3. **El orquestador submuestrea para C** (`frame_stride: 3`) en vez de mandarle todos los frames
   para que tirase 2 de cada 3. La secuencia le llega uniformemente espaciada, que es como se
   entrenó el BiLSTM (pendiente 5 de S11), y su cola deja de llenarse de frames destinados a la
   basura.
4. **Guardia de uniformidad**: si aun así se pierden frames y el hueco supera `max_gap_factor` pasos,
   la ventana se descarta en vez de alimentar al BiLSTM con una secuencia irregular. Se cuenta
   (`ventanas_rotas`): en régimen ocurre en ~1 de cada 4 ciclos.
5. **Cola propia y honda para C** (`queue_maxsize: 32`): es el único worker que necesita una
   *secuencia*, no el último frame. Mientras corre su detección, los frames que llegan tienen que
   esperar en la cola en vez de descartarse, o al reanudar se encuentra un salto que le rompe la
   ventana.
6. **VGG16 en un solo lote** en vez de `seq_len - 1` pasadas secuenciales (equivalente exacto: no
   hay BatchNorm).
7. **Una sola detección por ventana, sobre un frame que se descarta**. Detalle que costó tres
   corridas: la detección dura segundos, así que si el frame detectado fuese el primero de la
   ventana, entre él y el siguiente habría un hueco enorme → la guardia rompía la ventana → la
   ventana rota forzaba otra detección → **el módulo se quedaba en ese círculo sin emitir nunca
   nada** (medido: 0 predicciones en 150 s, ni un frame aceptado). Ahora el frame de la detección se
   usa solo para sacar la máscara y se tira, y la ventana empieza a llenarse ya con la máscara lista.
8. **Menos ruido**: el aviso de "aún calentando" solo se emite si no hay una predicción reciente.
   Existía porque C tardaba 30-48 s en dar la primera lectura; con una cada 1.8 s metía ~200
   mensajes con `present=False` por corrida (ruido en las colas y parpadeo de "sin detección" en el
   panel). Bajó de **202 a 2** por corrida.

### 6.3 Resultado medido (corridas reales de 150 s, A+B+C, cámara física)

| Métrica | S11 (línea base medida hoy) | S12 |
|---|---|---|
| Predicciones del Módulo C | 2 en 150 s | **70 en 150 s** |
| Intervalo entre predicciones | 48.1 s | **1.84 s** (max 2.12 s) |
| Detectron2 por ciclo | 32.0 s (2 det) | **0.3 s** (1 det) |
| VGG16 por ciclo | 15.2 s | **0.3 s** |
| Flujo óptico por ciclo | 1.2 s | 1.1 s (ahora es el mayor coste) |
| Avisos de "calentando" | 10-19 | 2 |
| **fps a la UI** | 30.00 (gap max 0.07 s) | **30.00 (gap max 0.06 s)** |
| Predicciones del Módulo B | 34 | 78 |

**26 veces más predicciones, sin pagar un solo fps de fluidez.** El Módulo B casi dobla las suyas de
propina, porque C ha dejado de acaparar los núcleos de CPU.

El coste dominante ahora es el flujo óptico Farneback (1.1 s por ciclo, en CPU): es el siguiente
candidato si alguna vez hiciera falta más cadencia, pero a 1.8 s por predicción el módulo ya está
holgadamente en tiempo real para lo que mide.

### 6.4 La contención de GPU que S11 dejó abierta (su pendiente 9)

Poner a C en la GPU obliga a comprobar justo lo que la guarda de `CUDA_VISIBLE_DEVICES` protegía:
`llama-server` corre con `-ngl 999`, o sea con todas sus capas en esa misma GPU. Se midió el caso
real: motor de visión completo corriendo **mientras** el LLM procesaba los 24 casos del banco de
oraciones.

| | Visión sola | Visión + LLM a la vez |
|---|---|---|
| fps a la UI | 30.00 (gap max 0.07 s) | **29.99 (gap max 0.06 s)** |
| Módulo C | 71 pred., 1.82 s | **67 pred., 1.85 s** (max 4.44 s) |
| Latencia del LLM | 0.4-0.9 s (solo) | **mediana 0.7 s, max 1.4 s** |

Conviven sin problema: la visión no pierde un frame y el LLM se encarece unas décimas. Queda dicho
con todas las letras que **son 150 s de medición, no una garantía de estabilidad de horas**: la
guarda de `CUDA_VISIBLE_DEVICES` existía por inestabilidad observada en su día, así que si el robot
diera problemas raros con el monitor emocional abierto, el primer experimento es poner
`modulo_c.use_gpu: false` y ver si desaparecen.

### 6.5 `min_person_ratio` calibrado con una persona real (pendiente 3 de S11)

Se hizo la corrida que faltaba, con el usuario sentado frente al robot a distancia normal de uso, y
se comparó con las corridas anteriores de sala vacía. **275 predicciones reales:**

| Escena | n | mediana | p95 | mínimo / máximo |
|---|---|---|---|---|
| **Vacía** | 208 | 0.053 | 0.124 | 0.000 / **0.132** |
| **Persona sentada** | 67 | 0.441 | 0.493 | **0.367** / 0.529 |

Las dos distribuciones no se rozan: hay un hueco entre 0.132 y 0.367. Y ahí aparece un defecto que
solo se ve con datos de las dos escenas: **el umbral que traía (0.12) cae DENTRO del ruido de la
sala vacía** — 20 de las 208 lecturas sin nadie delante se marcaban como "hay persona". El 0.12 de
S11 no salía de una medición del caso de uso, sino de escalar un valor de laboratorio por un factor
geométrico.

**Nuevo valor: `min_person_ratio: 0.18`**, con el que no hay ni un falso presente ni un falso
ausente en los 275 casos. Se elige 0.18 y no el centro del hueco (~0.25) a propósito: el usuario
final es un **niño**, más pequeño que el adulto medido y que puede sentarse más lejos. 0.18 deja
1.4x de margen sobre el ruido y sigue aceptando a alguien que ocupe **la mitad** de área que el
adulto medido. Si alguna vez aparecen falsos presentes con la sala vacía, se sube hacia 0.25.

De propina, la corrida con persona confirma el resto de S12 en el caso de uso real: 67 predicciones
de C (intervalo mediano 2.04 s), 30.00 fps a la UI y ciclos de 1.8-2.1 s con el mismo desglose
(Detectron2 0.3 s, VGG16 0.3 s, flujo óptico 1.2 s). Las etiquetas que emitió C con una persona
sentada quieta fueron 55 LEVE, 7 REPETITIVO y 5 CALMA — que el estado de reposo se clasifique
mayoritariamente como LEVE y no como CALMA es una cuestión del modelo entrenado, no del pipeline, y
queda anotada para S13.

---

## 7. Calidad del LLM de oraciones (segunda vuelta)

**Reporte del usuario sobre lo de S11:** *"mejoró quizá un poco en cohesión pero por momentos no
contextualiza como debería"*.

**Diagnóstico.** El prompt de S11 lleva 8 ejemplos, todos con tarjetas frecuentes (YO, QUIERO, AGUA,
BAÑO, PARQUE, MAMÁ…). Pero el vocabulario real tiene **34 tarjetas**, y de las que no aparecían en
ningún ejemplo —HOSPITAL, SIESTA, ESTOY LISTO, PUEDO AYUDARTE, DORMITORIO, SALÓN, Llavero— el modelo
no tenía ninguna referencia: ni qué son, ni qué papel juegan en la frase. Un modelo de 1.5B no lo
deduce solo. Además hay tarjetas que son de **varias palabras** ("NO NECESITO AYUDA", "PUEDO
AYUDARTE", "ESTOY LISTO"): sin decírselo, el modelo las puede partir y cambiar el sentido.

**Cambios (siguen sin costar RAM: mismo modelo, mismo muestreo):**

1. El prompt del sistema ahora incluye **la lista completa de tarjetas agrupadas por tipo**
   (personas / acciones / objetos / lugares / estados / conectores), leída **del mismo
   `config/rfid_vocab.json` que usa la app** — si el usuario graba una tarjeta nueva, el prompt la
   conoce sin tocar código.
2. Se advierte explícitamente cuáles son tarjetas de varias palabras y que hay que respetarlas
   enteras.
3. Cinco ejemplos nuevos (13 en total) que cubren justo los huecos: un lugar poco usado
   (`YO IR HOSPITAL`), un estado que es tarjeta entera (`YO ESTOY LISTO COLEGIO`), una tarjeta que
   habla del otro y no del niño (`MAMÁ PUEDO AYUDARTE`), una frase en tercera persona
   (`Mario DORMIR DORMITORIO`) y un objeto suelto sin verbo (`AGUA`).
4. `apps/llm/ab_correccion.py`: banco de pruebas de **24 casos** contra el modelo realmente cargado,
   a través del mismo bridge que usa la app. Los 10 de S11 más 14 nuevos centrados en las tarjetas
   poco frecuentes y en órdenes difíciles. Sirve para volver a medir esto sin rehacer el montaje.

### 7.1 Resultado del A/B

**24 de 24 correctas**, con latencias de 0.4 a 0.9 s (sin regresión frente a S11). Las que
interesan son las que S11 no cubría:

| Tarjetas | Resultado |
|---|---|
| `YO IR HOSPITAL` | Quiero ir al hospital. |
| `YO ESTOY LISTO COLEGIO` | Estoy listo para ir al colegio. |
| `PAPÁ PUEDO AYUDARTE` | Papá, puedo ayudarte. |
| `Mario IR SALÓN` | Mario quiere ir al salón. |
| `YO QUIERO Llavero` | Quiero un llavero. |
| `MAMÁ YO SIESTA` | Mamá, quiero siesta. |
| `YO QUIERO DORMIR DORMITORIO` | Quiero dormir en el dormitorio. |
| `BAÑO IR NECESITO AYUDA` | Quiero ir al baño, pero necesito ayuda. |
| `AGUA QUIERO YO` (orden invertido) | Quiero agua, yo también. |
| `YO HABLAR MAMÁ PAPÁ` | Estoy hablando con mamá y papá. |

Las negaciones se conservan en los cuatro casos que las llevan, y las tarjetas de varias palabras
(`PUEDO AYUDARTE`, `ESTOY LISTO`, `NO NECESITO AYUDA`) ya no se parten.

### 7.2 Un incidente que obligó a blindar el puente

La **primera** corrida del banco salió con basura en los **24 casos**:

| Tarjetas | Lo que devolvió |
|---|---|
| `YO QUIERO COMIDA` | `No, I want to eat.` |
| `PAPÁ QUIERO MÁS AGUA` | `Papayá!` |
| `MAMÁ YO IR COLEGIO` | `No, no me asospeaks.` |
| `Marina COMER COCINA` | `Mezclar con los signo, mezcla, mezclar.` |
| `NO QUIERO IR HOSPITAL` | `No quiero morrger ni mamaro… no me at`**`инuando`** |

Nótese el cirílico en la última: eso no es un modelo respondiendo mal, es una salida corrompida.

**No se ha podido reproducir.** Se descartó una por una: el servidor responde bien en aislado; el
prompt viejo y el nuevo dan resultados correctos consultando directamente a `llama-server` con el
mismo muestreo; forzar el caso sospechoso (cambiar el prompt y recargar el bridge, por si la caché
de prompt de llama.cpp se envenenaba al cambiar el prefijo) tampoco lo reprodujo; y las tres
corridas posteriores del mismo banco salieron limpias. **La causa sigue abierta.**

**Pero la causa importa menos que la consecuencia.** Esto es el dispositivo con el que se comunica un
niño: una frase inventada se lee en voz alta y se envía por Telegram como si fuera suya. Eso es peor
que no responder. Así que el puente ya no se fía de lo que le devuelve el modelo
(`respuesta_plausible()`), con dos comprobaciones baratas y objetivas:

1. **Alfabetos imposibles** en español (cirílico, griego, CJK, árabe) → salida corrupta, seguro.
2. **La frase debe conservar al menos una palabra de contenido de las tarjetas** (sin acentos y por
   raíz de 4 letras, para que "COMIDA" valga por "comer" y "QUIERO" por "quiero"). Una respuesta que
   no comparte ni una palabra con lo que el niño puso no es una corrección: es otra frase. Las
   palabras cortas (YO, NO, IR) no cuentan, porque aparecen en cualquier frase.

Si falla, se reintenta **una vez con la caché de prompt desactivada** (si lo que falla es un estado
reutilizado del servidor, repetir con él puesto daría lo mismo) y, si vuelve a fallar, se devuelve
la frase de respaldo determinista marcada como `degraded` — el mensaje del niño llega, aunque sea
sin pulir.

Contra la basura real capturada: **rechaza 12 de los 13 casos** y **no rechaza ninguna de las 11
respuestas correctas** probadas. El que se le escapa es `YO ESTOY LISTO COLEGIO` → *"Estoy bien, pero
necesito una copas"*, que sí comparte la palabra "estoy": es el límite conocido de una comprobación
léxica, que detecta corrupción pero no juzga significado. Los 13 casos están en la suite como
regresión.

---

## 8. Ajustes de interfaz

### 8.1 El botón ✕ del monitor emocional

**Causa medida.** El botón está en (940, 16) con 64x64 px, o sea ocupa de y=16 a y=80. La leyenda de
botones de `MainWindow` es una franja de ancho completo de **39 px** de alto (`fs(18)+16` con la
escala de fuente "Muy grande", que es la que el usuario tiene puesta) y se dibuja **encima** del
panel. Un `QLabel` no maneja clics, pero sí los intercepta: el evento sube a su padre, **no pasa al
widget que hay debajo**. Resultado: la mitad superior del botón no recibía toques.

**Corrección, en dos capas** (porque una sola dejaría el problema latente para el siguiente overlay):

1. `_legend` y `_caption` pasan a `WA_TransparentForMouseEvents`. Son etiquetas informativas: no
   deben interceptar nada, nunca.
2. El margen superior del panel de video pasa de 16 a 48 px, para que el botón no quede además
   *debajo de un texto* aunque ya no lo bloquee.

### 8.2 Pantalla de carga

Al usuario le gusta el resultado de S11 y pidió dos cosas:

- **Que la lluvia de piezas se vea en toda la pantalla** ("ahora se ve un poco recortada"). Causa: la
  posición horizontal era completamente al azar, así que se formaban grupos y quedaban franjas
  vacías — se ve en la captura de S11. Ahora hay **34 piezas (antes 22) repartidas por carriles**:
  cada pieza tiene su carril vertical y lo conserva al reaparecer arriba, así que la caída cubre todo
  el ancho sin dejar de verse orgánica (dentro del carril siguen siendo aleatorios la posición, el
  tamaño, el giro y la velocidad). También se amplió el rango de tamaños (0.30-0.85 contra 0.28-0.72).
- **Un logo de mayor definición** (lo va a proveer). El código ya está preparado: el logo se dibujaba
  con un factor de escala fijo (2.4x) atado al PNG de 149x51, así que sustituirlo por una imagen
  grande lo habría pintado gigante en vez de nítido. Ahora se dibuja a un **ancho objetivo**
  (`LOGO_TARGET_W = 360 px`), de modo que basta con dejar el archivo nuevo en `assets/LOGO.png`.

---

## 9. Pruebas realizadas

- **Soak del fondo animado** (`tests/soak_animacion.py`, `AnimationPlayer` real sobre el display
  real, muestreo por segundo): 28 min sin audio (206 vueltas, 0 congelamientos, memoria plana en
  224 MB) y con audio (2 congelamientos en 5.5 min), más la corrida de verificación del arreglo.
- **Descarte de ahorro de energía**: `xset q` (timeout 0, DPMS 0/0/0) y `gsettings` (idle-delay 0).
- **Descarte de ráfaga de seeks**: 206 seeks en 206 vueltas, exactamente 1.00 por vuelta.
- **Diagnóstico del táctil** (`tests/diagnostico_tactil.sh`): `/proc/bus/input/devices`, `lsusb`,
  `xinput list` y el estado de `power/control` de cada dispositivo USB.
- **Medición de audio** con `loudnorm` y `volumedetect` sobre los cuatro clips, antes y después, y
  verificación de que el flujo de video queda intacto (`ffprobe`: mismo códec, 1920x1080, 205
  frames).
- **Curva de volumen** comprobada punto por punto contra `QAudio.convertVolume`.
- **Geometría real del botón ✕** medida con el panel construido a 1024x600 y escala de fuente
  "Muy grande" (la del usuario): botón en (940,16,64,64), leyenda de 39 px de alto encima.
- **Suite headless S12, 44/44** (`tests/test_s12.py`, widgets reales, `QT_QPA_PLATFORM=offscreen`):
  vigilante de congelamiento (reproducción normal, congelamiento real, periodo de gracia, no
  confundir "cargando"), recarga preventiva, curva de volumen, selección de variante de clip,
  detección del táctil y configuración del Módulo C.
- **Renderizado offscreen de la pantalla de carga** antes y después, para comparar la cobertura de
  la lluvia de piezas.
- **A/B del LLM**: 24 casos contra el modelo real a través del bridge, cuatro corridas (una de ellas
  la que destapó el incidente de 7.2), más consultas directas a `llama-server` para separar
  responsabilidades entre modelo, prompt y puente.
- **Guardia de respuestas corruptas**: 13 casos de basura real y 11 respuestas correctas, en la
  suite como regresión.
- **Seis corridas reales del motor de visión** (A+B+C, cámara física, 80-150 s cada una) para el
  Módulo C: línea base, dos regresiones propias detectadas y corregidas por el camino (el módulo se
  quedó sin emitir nada en dos de ellas), la verificación final y la prueba de contención con el LLM.
  Cero procesos residuales tras cada corrida.

---

## 10. Mapa de archivos nuevos/modificados

```
bmo_unified/
├── ui/
│   ├── animation_player.py     # vigilante de congelamiento + recarga preventiva,
│   │                           #   loop por EndOfMedia en clips con audio,
│   │                           #   variantes de audio normalizado, volumen perceptual
│   ├── main_window.py          # leyenda/caption transparentes al ratón, TouchMonitor
│   ├── emo_monitor_panel.py    # margen superior 16 -> 48 (botón ✕ despejado)
│   └── splash.py               # lluvia por carriles (34 piezas), logo por ancho objetivo
├── core/
│   └── touch_monitor.py        # NUEVO -- presencia del táctil, registro y remapeo
├── tests/
│   ├── soak_animacion.py       # NUEVO -- arnés de soak del fondo animado
│   ├── medir_modulos.py        # NUEVO -- cadencia por módulo del motor de visión
│   ├── diagnostico_tactil.sh   # NUEVO -- ¿hay táctil? ¿lo ve X11?
│   └── test_s12.py             # NUEVO -- suite headless de S12
├── instalar_regla_tactil.sh    # NUEVO -- regla udev anti-autosuspend (sudo, una vez)
└── reconectar_tactil.sh        # NUEVO -- re-enumerar el USB sin reiniciar (sudo)

integradora/model_ia/sistem_IA/
├── run.py                      # entry_worker_c devuelve la GPU al Módulo C (causa raíz
│                               #   de su lentitud) + cola propia y honda para C
├── core/orchestrator.py        # submuestreo propio del Módulo C (frame_stride)
├── modules/mod_c.py            # VGG16 por lotes, Detectron2 a resolución nativa,
│                               #   una detección por ventana sobre un frame que se
│                               #   descarta, guardia de espaciado uniforme, menos
│                               #   avisos de "calentando", instrumentación de tiempos
│                               #   y del dispositivo real
└── config/runtime.yaml         # claves nuevas del Módulo C, medidas

bmo_unified/vision/engine.py    # cola propia y honda para el Módulo C

apps/llm/ab_correccion.py       # NUEVO -- banco A/B de corrección de oraciones (24 casos)
.gitignore                      # ignora la caché _audio_normalizado/
integradora/animaciones/_audio_normalizado/   # NUEVO (caché, no versionado)
```

Sin cambios en: firmware ESP32, scripts de cámara, `/boot` ni overlays.

---

## 11. Pendiente para S13

**Lo que quedó a medias esta misma semana y se puede cerrar en una sesión corta de robot:**

0a. **Etiquetas del Módulo C en reposo**: con una persona sentada quieta, C devolvió 55 LEVE, 7
    REPETITIVO y 5 CALMA. Que el reposo se clasifique mayoritariamente como LEVE apunta al modelo
    entrenado (o a su calibración de umbrales), no al pipeline, que ya entrega secuencias limpias y
    uniformes. Es el siguiente frente natural del Módulo C ahora que la cadencia dejó de ser el
    problema.
0b. **Vigilar el incidente del LLM** (sección 7.2): la salida corrupta no se reprodujo y su causa
    sigue abierta. El puente ahora la detecta y la registra (`Respuesta inverosímil para …`), así
    que basta con buscar esa línea en el log del bridge cada tanto: si reaparece, ya habrá contexto
    (hora, tarjetas, respuesta) para atacarla en serio.

**El resto:**

1. **Validación física de lo de esta semana** (todo lo de S12 está probado en banco, no en el robot
   en uso real):
   - Dejar la cara animada corriendo horas y confirmar que ya no se congela. Si vuelve a pasar,
     mirar `logs/bmo_app.log`: si aparece `Video congelado … Recargando el clip`, el vigilante lo
     está cubriendo; si **no** aparece nada y la imagen igual se quedó pegada, es el segundo modo
     (la superficie de video) y hay que bajar `PREVENTIVE_RELOAD_MS`.
   - Escuchar los clips de Caras con el audio recomprimido y decidir si hace falta bajar aún más el
     techo de picos.
   - Comprobar que el botón ✕ del monitor emocional ya responde.
2. **Táctil**: la próxima vez que se pierda, probar `sudo bash bmo_unified/reconectar_tactil.sh`
   **antes** de suspender o reiniciar — si lo recupera, queda confirmado que es enumeración y el
   problema pasa a ser una molestia de un comando. El vigilante de la app deja en el log la hora
   exacta de cada pérdida, que es lo que permitirá ver si tiene patrón (siempre en frío, tras horas,
   al arrancar el motor de visión...). Si acaba siendo sistemático, el siguiente paso es físico:
   probar un puerto USB directo de la Jetson en vez del concentrador interno.
3. **Logo del splash en alta definición**: el usuario lo va a proveer; basta con dejarlo en
   `bmo_unified/assets/LOGO.png` (el código ya lo dibuja por ancho objetivo, no por factor fijo).
5. **Voz de Moodi**: sigue siendo espeak-ng. Piper TTS como upgrade cuando haya margen de RAM.
6. **Animaciones reactivas al estado emocional**: el punto de entrada existe
   (`MainWindow._on_stress_stats()`), faltan clips nuevos — hoy solo hay 4.
7. **Causa física del evento espurio de `EMO_TOGGLE` (GPIO13)** y confirmar los 10 GPIO con
   `--calibrate` tras el recableado (heredado de S10/S11).
8. **Vigilar la estabilidad del Módulo C en GPU**: la guarda de `CUDA_VISIBLE_DEVICES` que se
   levantó existía por inestabilidad observada en su día. Los 150 s medidos (incluso con el LLM a la
   vez) fueron limpios, pero si aparecen rarezas con el monitor emocional abierto, el primer
   experimento es `modulo_c.use_gpu: false`.
