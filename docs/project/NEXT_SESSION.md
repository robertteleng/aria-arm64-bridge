# NEXT_SESSION.md

## Estado actual (2026-02-26)

**Fase 1 ✓** — `import aria.sdk` funciona bajo FEX-Emu en Jetson
**Fase 1.5 ✓** — `projectaria_tools` compilado nativo ARM64 con Python 3.12 bindings
**Fase 3 (parcial) ✓** — ZMQ bridge funcional: mock → consumer a 30 FPS, ~24ms cross-process
**Fase 4 (parcial) ✓** — aria-guard integrado: `AriaBridgeObserver` + source `aria:bridge`

### Lo que funciona sin gafas
- `import aria.sdk`, `import aria.sdk_gen2` bajo FEX-Emu ✓
- `from projectaria_tools.core.sensor_data import ImageDataRecord` nativo ARM64 ✓
- ZMQ pipeline: `mock_receiver.py` → `frame_consumer.py` ✓ (>1000 FPS throughput)
- FEX-Emu → nativo cross-process ✓ (~24ms latencia)
- `AriaBridgeObserver` → BGR validation ✓
- aria-guard con source `aria:bridge` → menú option [5] ✓

---

## Siguiente paso inmediato: Fase 2 — Streaming real

### Preparación (5 min)

1. Gafas Aria encendidas + Mobile Companion App abierta en el móvil
2. Cable USB-C entre gafas y Jetson
3. Jetson y gafas en la misma red WiFi (para WiFi streaming)

### Paso 1: Auth pair (una sola vez)

```bash
# IMPORTANTE: PYTHONNOUSERSITE=1 evita que numpy ARM64 contamine el proceso
PYTHONNOUSERSITE=1 FEXBash -c "aria auth pair"
```

- Aprobar en la Mobile Companion App cuando aparezca el prompt
- Los certificados persisten hasta factory reset

### Paso 2: Diagnóstico

```bash
PYTHONNOUSERSITE=1 FEXBash -c "aria-doctor"
```

### Paso 3: Test streaming básico (USB)

```bash
# Verificar que llegan frames con el device_stream oficial
PYTHONNOUSERSITE=1 FEXBash -c "python3 -m device_stream --interface usb --update_iptables"
```

### Paso 4: Test con aria_receiver.py (nuestro código)

```bash
# Terminal 1: Receptor bajo FEX-Emu → envía por ZMQ
cd ~/Projects/aria-arm64-bridge
PYTHONNOUSERSITE=1 FEXBash -c "python3 src/receiver/aria_receiver.py --interface usb"

# Terminal 2: Consumer nativo → verifica que llegan frames
python3.12 src/bridge/frame_consumer.py
```

### Paso 5: Pipeline completo con aria-guard

```bash
# Terminal 1: aria_receiver.py bajo FEX-Emu (sigue corriendo)
cd ~/Projects/aria-arm64-bridge
PYTHONNOUSERSITE=1 FEXBash -c "python3 src/receiver/aria_receiver.py --interface usb"

# Terminal 2: aria-guard con source bridge
cd ~/Projects/aria-guard
python3 run.py
# → Seleccionar opción [5] "Aria Bridge (Jetson ARM64 via ZMQ)"
```

---

## Qué validar en Fase 2

- [ ] `aria auth pair` completa sin errores bajo FEX-Emu
- [ ] Confirmar ruta de certificados (`~/.aria/` o dentro del rootfs)
- [ ] `aria-doctor` pasa todas las verificaciones
- [ ] `device_stream` recibe frames por USB
- [ ] `aria_receiver.py` envía frames por ZMQ correctamente
- [ ] `frame_consumer.py` recibe y decodifica frames reales
- [ ] Pipeline completo: Aria → FEX-Emu → ZMQ → aria-guard → YOLO
- [ ] Medir latencia end-to-end (target: <50ms overhead de emulación)
- [ ] Sostener 30 FPS por >60 segundos sin crashes

## Notas técnicas

- **PYTHONNOUSERSITE=1** es obligatorio con FEX-Emu para evitar que numpy ARM64 de `~/.local/` contamine el proceso x86_64
- Perfiles: `profile18` (WiFi), `profile28` (USB) — `aria_receiver.py` auto-selecciona
- Si OOM: cerrar otros procesos. El Jetson tiene 8GB compartidos CPU+GPU
- ZMQ endpoint por defecto: `tcp://127.0.0.1:5555`
