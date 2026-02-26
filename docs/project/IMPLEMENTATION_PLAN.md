# IMPLEMENTATION_PLAN.md

Roadmap del proyecto organizado por fases. Cada fase se valida con test real antes de avanzar.

> **Regla:** No empezar Fase N+1 hasta que Fase N esté validada.

---

## Fase 0: Foundation

Documentación, investigación, setup del entorno.

- [x] Investigar Aria SDK internals (binario cerrado, 98 MB, FastDDS + Proxygen)
- [x] Evaluar alternativas (relay proxy, QEMU, Box64, FEX-Emu, reverse-engineering)
- [x] Decisión: FEX-Emu como approach principal
- [x] Crear repo y documentación base
- [x] Investigar FEX-Emu en Jetson Orin Nano (compatibilidad JetPack 6.x)

---

## Fase 1: FEX-Emu + Aria SDK Import Test ✓ COMPLETADA (2026-02-25)

**Goal:** `import aria.sdk` funciona bajo FEX-Emu en el Jetson.

### Hito 1.1 — FEX-Emu en Jetson ✓

- [x] Instalar FEX-Emu en Jetson Orin Nano (compilado desde source, -DBUILD_FEXCONFIG=OFF)
- [x] Crear x86_64 rootfs (FEXRootFSFetcher, Ubuntu 22.04 SquashFS)
- [x] Verificar que Python 3.10 x86_64 funciona bajo FEX
- [ ] Benchmark: overhead de FEX-Emu para operaciones básicas

### Hito 1.2 — Aria SDK bajo emulación ✓

- [x] Instalar projectaria-client-sdk en rootfs x86_64 (pip --platform manylinux2014_x86_64)
- [x] `import aria.sdk` sin errores
- [x] `import aria.sdk_gen2` sin errores (Gen2 HTTP streaming)
- [x] Verificar que libsdk_core.so carga correctamente (requirió actualizar libstdc++ a gcc-13)
- [x] Instalar projectaria-tools en rootfs (necesario — aria.sdk importa projectaria_tools.core)

### Hito 1.3 — Validación ✓

- [x] Verificación manual exitosa de todos los imports
- [x] Documentar en DEVELOPER_DIARY.md (Exp 001)
- [ ] Script automatizado de verificación: `test_import.sh` (actualizado pero no ejecutado como script)

---

## Fase 1.5: projectaria-tools nativo ARM64 ✓ COMPLETADA (2026-02-26)

**Goal:** `projectaria_tools` compilado y funcionando nativo en el Jetson (sin emulación).

**Por qué:** aria-guard usa types de `projectaria_tools.core` para parsear frames y calibración:
- `ImageDataRecord`, `MotionData` (sensor_data)
- `device_calibration_from_json_string` (calibration)
- `StreamId`, `data_provider` (stream IDs)

### Hito 1.5.1 — Build desde source ✓

- [x] Instalar dependencias de build (FFmpeg-dev, Boost, GCC 13 del PPA)
- [x] Clonar https://github.com/facebookresearch/projectaria_tools
- [x] Compilar con cmake + GCC 13 en Jetson ARM64 (3 parches NEON para Ocean)
- [x] Python bindings funcionales con Python 3.12 (pybind11)

### Hito 1.5.2 — Validación ✓

- [x] `from projectaria_tools.core.sensor_data import ImageDataRecord` funciona
- [x] Documentar en DEVELOPER_DIARY.md (Exp 002)

---

## Fase 2: Streaming Test

**Goal:** Recibir frames reales de las Aria glasses bajo FEX-Emu.

### Hito 2.1 — Device Discovery

- [ ] Aria glasses detectadas desde proceso emulado
- [ ] WiFi streaming handshake funciona (FastDDS/RTPS para Gen1)
- [ ] HTTP streaming handshake funciona (Proxygen para Gen2)

### Hito 2.2 — Frame Reception

- [ ] Recibir al menos 1 frame RGB del stream
- [ ] Verificar integridad de datos (resolución, formato, timestamps)
- [ ] Medir latencia: frame capturado → frame disponible en Python
- [ ] Sostener 30 FPS por >60 segundos sin crashes

### Hito 2.3 — Validación

- [ ] Comparar frames con los mismos recibidos en x86_64 nativo (pixel-level)
- [ ] Documentar en DEVELOPER_DIARY.md

---

## Fase 3: IPC Bridge (parcialmente avanzada)

**Goal:** Frames fluyen de proceso emulado a proceso nativo ARM64.

### Hito 3.1 — Transporte (prototipo listo)

- [x] Elegir mecanismo IPC: **ZMQ PUSH/PULL** (tcp://127.0.0.1:5555)
- [x] Implementar sender: `src/receiver/aria_receiver.py` (real) + `src/receiver/mock_receiver.py` (mock)
- [x] Implementar receiver: `src/bridge/frame_consumer.py`
- [x] Protocolo binario: header(24B) + raw pixels — ARIA magic + timestamp + dimensions
- [x] Test unitario: `tests/test_zmq_pipeline.py` — PASS
- [x] Benchmark mock: 30 FPS @ 320x240 con ~6ms latencia
- [x] Benchmark throughput: **>1000 FPS** con frames de 5.9MB (ZMQ no es bottleneck)
- [ ] Benchmark con receiver real bajo FEX-Emu (necesita gafas → Fase 2 primero)

### Hito 3.2 — Robustez

- [ ] Reconexión automática si el proceso emulado muere
- [ ] Graceful shutdown
- [ ] Test de stress: 30 min continuo

---

## Fase 4: Integration con aria-guard

**Goal:** aria-guard funciona en Jetson con Aria glasses via el bridge.

### Hito 4.1 — Integración

- [ ] Nuevo source type en aria-guard: `aria-fex` o `aria-bridge`
- [ ] AriaProcess de aria-guard usa el bridge en lugar del SDK directo
- [ ] Pipeline completo: Aria → FEX → bridge → YOLO → alerts

### Hito 4.2 — Producción

- [ ] Script de setup one-liner para Jetson
- [ ] Documentación de usuario
- [ ] Benchmark: FPS, latencia end-to-end, CPU/GPU usage

---

## Fallback: Si FEX-Emu falla

Si la Fase 1 o 2 fracasa (syscalls incompatibles, crashes irrecuperables):

1. **Plan B:** Relay proxy con miniPC x86_64 + ZMQ (~100€)
2. **Plan C:** Reverse-engineering del protocolo Gen2 (HTTP/2 + FlatBuffers)
3. **Plan D:** Pedir a Meta soporte ARM64 (AriaOps@meta.com)

---

## Notas

- FEX-Emu thunking puede redirigir libc/libssl a versiones nativas ARM64 → mejor rendimiento
- El binario libsdk_core.so tiene todo linkeado estáticamente → menos dependencias externas = menos problemas
- FastDDS usa multicast UDP para discovery → puede necesitar configuración de red especial
- Gen2 streaming (HTTP/2) es más simple de emular que Gen1 (DDS/RTPS)
