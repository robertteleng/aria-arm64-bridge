# NEXT_SESSION.md

## Contexto

Se creó el repo `aria-arm64-bridge` para investigar cómo correr el Aria SDK (binario cerrado x86_64) en Jetson Orin Nano (ARM64) usando FEX-Emu como traductor binario.

## Estado actual

- Fase 0 completa: documentación, research, scripts de setup
- Todo el research está en [RESEARCH.md](RESEARCH.md) — internals del SDK, alternativas evaluadas, decisión de usar FEX-Emu
- 4 scripts listos en `scripts/`: setup_fex_emu.sh, setup_rootfs.sh, test_import.sh, test_streaming.sh

## Siguiente paso inmediato

**Fase 1, Hito 1.1: Probar FEX-Emu + `import aria.sdk`**

No se necesitan las gafas Aria. El test es:

1. Instalar FEX-Emu en ARM64 (Jetson o cualquier aarch64)
2. Crear rootfs x86_64 con Python 3.12 + projectaria-client-sdk
3. Correr `test_import.sh` — si `import aria.sdk` carga `libsdk_core.so` (98 MB) sin crashear, el 80% está resuelto

```bash
./scripts/setup_fex_emu.sh   # Compilar FEX-Emu
./scripts/setup_rootfs.sh    # Crear rootfs + instalar SDK
./scripts/test_import.sh     # EL TEST CLAVE
```

## Qué puede salir mal

- `libsdk_core.so` usa instrucciones x86_64 que FEX-Emu no traduce bien → crash al import
- Syscalls raros de FastDDS/Proxygen fallan bajo emulación → crash al inicializar
- Memory ordering issues (ARM64 es más relajado que x86_64) → races, hangs

## Si falla

- Plan B: Relay proxy con miniPC x86_64 + ZMQ (~100€)
- Plan C: Reverse-engineering del protocolo Gen2 (HTTP/2 + FlatBuffers)
- Detalles en [RESEARCH.md](RESEARCH.md)

## Decisiones pendientes

- Mecanismo IPC para el bridge (ZMQ vs shared memory) — decidir después de que Fase 1 funcione
- Gen1 vs Gen2 streaming — Gen2 (HTTP/2) es más simple de emular que Gen1 (DDS/RTPS)
