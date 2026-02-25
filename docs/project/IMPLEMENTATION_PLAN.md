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
- [ ] Investigar FEX-Emu en Jetson Orin Nano (compatibilidad JetPack 6.x)

---

## Fase 1: FEX-Emu + Aria SDK Import Test

**Goal:** `import aria.sdk` funciona bajo FEX-Emu en el Jetson.

### Hito 1.1 — FEX-Emu en Jetson

- [ ] Instalar FEX-Emu en Jetson Orin Nano
- [ ] Crear x86_64 rootfs (Ubuntu 22.04 base)
- [ ] Verificar que Python 3.12 x86_64 funciona bajo FEX
- [ ] Benchmark: overhead de FEX-Emu para operaciones básicas

### Hito 1.2 — Aria SDK bajo emulación

- [ ] Instalar projectaria-client-sdk en rootfs x86_64
- [ ] `import aria.sdk` sin errores
- [ ] `import aria.sdk_gen2` sin errores (Gen2 HTTP streaming)
- [ ] Verificar que libsdk_core.so carga correctamente (dependencias resueltas)
- [ ] Documentar cualquier syscall que falle

### Hito 1.3 — Validación

- [ ] Script automatizado de verificación: `test_import.sh`
- [ ] Documentar en DEVELOPER_DIARY.md

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

## Fase 3: IPC Bridge

**Goal:** Frames fluyen de proceso emulado a proceso nativo ARM64.

### Hito 3.1 — Transporte

- [ ] Elegir mecanismo IPC (ZMQ vs shared memory vs pipe)
- [ ] Implementar sender en x86_64 emulado
- [ ] Implementar receiver en ARM64 nativo
- [ ] Benchmark: latencia IPC, throughput, CPU overhead

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
