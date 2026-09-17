# Auditoría de streaming Aria (Jetson) — 2026-06-30

Medidas en device. Objetivo: elegir el profile de arranque y mapear el sistema
sin sesgo. **Conclusión rápida: usar WiFi (no USB) + profile9 o profile21.**

## Metodología (para que sea sólido, no sesgado)
- WiFi, gafas en la LAN, 6 streams (`rgb,slam,eye,imu,mag,baro`).
- **120 s por profile** → ~10 ventanas de 10 s; se descarta el ramp-up (~15 s) y se
  mira el steady-state + la variabilidad (no un número suelto).
- **Auto-validación**: se cuenta `Streaming active` + `First frame`; si faltan, la
  medida es **inválida** y se repite.
- **Conteo de fallos** por profile: eventos DDS `sample lost`, errores.
- Limpieza de sesión entre profiles (el receiver auto-recupera el error 940).

## 1. Transporte: WiFi >> USB (hallazgo grande)
| | USB-CDC | WiFi |
|---|---|---|
| RGB | ~8 (multi-stream) | ~10-20 |
| SLAM | 1-6 (colapsa) | 9-15 |
| `sample lost` | masivo | mínimo |
| **Edad del resultado del detector**¹ | **486 ms** | **178 ms** (depth on) / **54 ms** (off) |

¹ *Nota posterior (2026-09):* esta cifra es la edad del último resultado del detector cuando el
pipeline de aria-guard lo lee, no una latencia de extremo a extremo (no incluye captura ni transporte).

El "USB" de Aria es una **red por USB (USB-CDC, 192.168.42.x)**, no un cable directo.
Ese adaptador del Jetson **se satura con los 6 streams** → era el cuello de SLAM/FPS/
latencia. **No era FEX.** WiFi (red real, más ancho de banda) lo resuelve.

## 2. Profiles — datos sólidos (120 s, WiFi, 6 streams)
| Profile | RGB | eye | SLAM | Estable | sample-lost (120s) | Veredicto |
|---|---|---|---|---|---|---|
| **9**  | **20** (clavado) | 10 | 10 | ✅ | 20 | **RGB máximo, limpio, estable** |
| **21** | 15 | **30** | 15 | ✅ | **9** | **balanceado-alto, el más limpio** |
| 12 | ~10 | ~10 | ~9 | ✅ | bajo | base balanceada (de la campaña depth) |
| 15 | 23→**14** (degrada) | 6 | 18 | ❌ | **2875** | satura DDS → descartado |
| 23 | 28→**19** (degrada) | 6 | 7 | ❌ | **1452** | satura DDS → descartado |
| 25 | — | — | — | ❌ | error **954** | incompatible WiFi (solo USB) |
| 28 | ~6 | 27 | 15 | (corto) | — | RGB demasiado bajo para detección |

**Lección clave:** los profiles de RGB alto (15, 23) **ganaban en una ventana corta**
(RGB 23-28) pero a 120 s **se saturan**: degradan (→14-19) y tiran miles de muestras.
Solo el test largo lo revela. profile9 (RGB 20) y profile21 (balanceado) se mantienen
estables y limpios — son los buenos.

## 3. DepthAnything (e2e, profile12, WiFi)
| | FPS detector | Edad del resultado¹ |
|---|---|---|
| Depth ON | 9.88 | 178 ms |
| Depth OFF | 9.94 | **54 ms** |

→ DepthAnything **no cuesta FPS** (el techo ~10 es el input rate, no el cómputo;
la GPU está al ~18%, input-bound) pero **cuesta ~124 ms de latencia**. Sin depth el
sistema responde en 54 ms pero pierde la distancia métrica.

## 4. Fallos detectados
1. **Medida inválida de profile12 en el batch** (sesión pillada del profile anterior,
   no cerró a tiempo) → la auto-validación lo cazó. *Gap:* el teardown entre runs a
   veces no confirma el cierre de sesión.
2. **profile15/23 saturan el DDS** (2875 / 1452 sample-lost) y degradan en el tiempo.
3. **Audio BT frágil entre relanzamientos**: el contenedor nuevo manda el audio al sink
   analógico hasta que se reenruta al Shokz a mano (mover sink-input).
4. **Sesión Aria se queda pillada (940)** si no se cierra limpio — mitigado por el
   self-heal del receiver, pero el cleanup SIGKILL (timeout) lo reintroduce.

## 5. Mejoras propuestas
- **Reenrutar audio al Shokz automáticamente** tras cada arranque (mover sink-input en
  el launch), para no perder el sonido en cada relanzamiento.
- **Teardown de sesión robusto** entre runs (esperar cierre confirmado, no SIGKILL).
- **WiFi por defecto** para multi-stream (no USB).
- Si se necesita SLAM en vivo, **sub-pipeline a menor rate** (no saturar el DDS).
- El detector capa ~10-12 FPS → RGB 15 (profile21) ya lo satura; RGB 20 (profile9) da
  headroom para el x86.

## 6. Recomendación
- **Profile de arranque:** **profile9** (RGB 20 máximo para detección, estable, limpio)
  o **profile21** (RGB 15 + eye 30 + SLAM 15, el más limpio — mejor para gaze + paneles).
  Descartar 15/23 (saturan), 25 (solo USB), 28 (RGB bajo).
- **Transporte:** WiFi.
- **Implicación plataforma (actualiza la ADR-0001 de aria-guard):** el Jetson **por WiFi es viable**
  (178 ms, RGB 10-20). El 486 ms / colapso de SLAM era del **USB-CDC, no de FEX** → la
  migración a x86 sigue valiendo para **FPS > cap del detector + profundidad métrica**,
  pero es **menos urgente** de lo que decía el ADR. Re-evaluar con el detector e2e por
  WiFi antes de decidir.

## Pendiente (siguiente: testeo a fondo del sistema, ya con el profile elegido)
- FPS del **detector e2e** con profile9 vs profile21 por WiFi (¿la GPU pasa de 10?).
- Audit de cada componente: tracker/ego-motion/looming, AlertArbiter, audio/voz, gaze,
  estabilidad larga, latencia detección→sonido.
