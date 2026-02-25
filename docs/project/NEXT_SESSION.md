# NEXT_SESSION.md

## Estado actual (2026-02-25)

**Fase 1 COMPLETADA:** `import aria.sdk` funciona bajo FEX-Emu en Jetson Orin Nano.

### Lo que funciona
- FEX-Emu compilado e instalado en Jetson (JetPack R36.5.0)
- RootFS Ubuntu 22.04 x86_64 con libstdc++ actualizada (gcc-13)
- `import aria.sdk` — OK (libsdk_core.so 98 MB cargado)
- `import aria.sdk_gen2` — OK (HTTP/2 streaming)
- `from projectaria_tools.core.sensor_data import ImageDataRecord` — OK
- Python 3.10 x86_64 bajo FEX-Emu

### Lo que falta
- No se ha probado streaming real con gafas Aria (necesita glasses + WiFi)
- projectaria-tools no compilado nativo ARM64 (para aria-guard)
- Scripts de setup no reflejan el proceso real (se hizo manual)

## Siguiente paso inmediato

### Opción A: Fase 1.5 — projectaria-tools nativo ARM64
Compilar projectaria-tools desde source en el Jetson para que aria-guard pueda usar los types nativamente sin emulación. Ver IMPLEMENTATION_PLAN.md Fase 1.5.

### Opción B: Fase 2 — Streaming test con gafas Aria
Necesita:
1. Gafas Aria encendidas y pareadas
2. Jetson y gafas en la misma red WiFi
3. Ejecutar `./scripts/test_streaming.sh`

### Opción C: Actualizar scripts
Reescribir `setup_rootfs.sh` para que refleje el proceso que realmente funciona (FEXRootFSFetcher + pip con --platform + libstdc++ update). Ver DEVELOPER_DIARY.md Exp 001 para los pasos exactos.

## Decisiones pendientes
- Python 3.10 vs 3.12: el rootfs usa 3.10 (viene con Ubuntu 22.04). Funciona con el SDK. ¿Vale la pena instalar 3.12 dentro del rootfs o nos quedamos con 3.10 para el bridge?
- Mecanismo IPC para el bridge (ZMQ vs shared memory) — decidir en Fase 3
