# RESEARCH.md — Investigación

> **Regla:** Lo que se investiga se documenta. Lo que se aprende no se pierde.

---

## Estado del Arte

### El Problema: Aria SDK en ARM64

`projectaria-client-sdk` es un paquete de PyPI sin source code. El binario principal `libsdk_core.so` (98 MB) contiene todo el networking stack de Meta compilado estáticamente:

| Librería interna | Versión | Función |
|---|---|---|
| eProsima Fast-DDS | 2.9.0 | Protocolo RTPS/DDS para streaming Gen1 |
| Proxygen | — | HTTP server para streaming Gen2 |
| Fizz | — | TLS 1.3 |
| MVFST | — | QUIC/HTTP3 |
| Folly | — | Utilidades C++ de Meta |
| Wangle | — | Networking framework |
| FlatBuffers | 1.12.0 | Serialización de datos de sensores |
| Protobuf | — | Protocolo "oatmeal_proto" para control |

**Dependencias externas** (todo lo demás está estáticamente linkeado):
```
libm.so.6, libc.so.6, ld-linux-x86-64.so.2, libatomic.so.1, libgcc_s.so.1
```

Esto es bueno para emulación: pocas dependencias externas = menos cosas que pueden fallar.

### Wheels disponibles (v2.2.0, diciembre 2025)

| Plataforma | Python | Existe |
|---|---|---|
| manylinux2014_x86_64 | cp310, cp311, cp312 | Sí |
| macosx_11_0_arm64 | cp310, cp311, cp312 | Sí |
| manylinux_aarch64 | cualquiera | **NO** |

No hay sdist. No hay aarch64 Linux. Nunca lo ha habido.

### Protocolos de Streaming

**Gen1 (DDS/RTPS):**
- Multicast UDP para discovery
- Peer-to-peer data transfer
- Certificados efímeros para seguridad

**Gen2 (HTTP/2 + QUIC):**
- Las gafas envían datos a un HTTP server en tu máquina (puerto 6768)
- FlatBuffers para serialización
- "oatmeal_proto" (Protobuf) para control

---

## Alternativas Evaluadas

### 1. Relay Proxy (x86_64 miniPC + ZMQ)
- **Viabilidad:** Alta — funciona hoy
- **Coste:** ~100-150€ (Beelink, NUC)
- **Latencia:** <5ms en red local
- **Contra:** Requiere dos máquinas, no es elegante
- **Veredicto:** Plan B si FEX-Emu falla

### 2. FEX-Emu (ELEGIDO)
- **Viabilidad:** Media-Alta — requiere validación
- **Coste:** 0€
- **Ventaja:** Una sola máquina, thunking nativo para libc/libssl
- **Riesgo:** Syscalls complejos de networking (multicast UDP, QUIC)
- **Veredicto:** Probar primero, es el approach óptimo si funciona

### 3. Box64
- **Viabilidad:** Media
- **Contra:** Enfocado a gaming/Wine, peor soporte para networking server
- **Veredicto:** Descartado en favor de FEX-Emu

### 4. QEMU user-mode
- **Viabilidad:** Baja
- **Contra:** 5-10x más lento, peor cobertura de syscalls
- **Veredicto:** Descartado

### 5. Reverse-engineering del protocolo
- **Viabilidad:** Baja-Media
- **Contra:** Semanas/meses de trabajo, frágil ante firmware updates
- **Veredicto:** Plan C, solo si todo lo demás falla

### 6. Pedir a Meta soporte ARM64
- **Viabilidad:** Desconocida
- **Contacto:** AriaOps@meta.com, GitHub issues
- **Veredicto:** Hacer en paralelo, no depender de ello

---

## Investigación Técnica

### FEX-Emu — Cómo funciona

FEX-Emu traduce instrucciones x86_64 a ARM64 mediante JIT (Just-In-Time compilation).

**Thunking:** Característica clave — puede redirigir llamadas a librerías del sistema (libc, libssl, libpthread) a las versiones nativas ARM64 del host. Solo el código propio del binario se emula.

**Para nuestro caso:** `libsdk_core.so` tiene todo linkeado estáticamente excepto libc, libm, libatomic, libgcc. Eso significa:
- libc → thunked a ARM64 nativo (syscalls nativos)
- El código de Meta (FastDDS, Proxygen, etc.) → emulado vía JIT
- Networking (sockets, UDP, TCP) → syscalls van nativos via libc thunking

**Esto es favorable:** los syscalls de red van nativos, solo la lógica de protocolo se emula.

### Riesgos identificados

1. **Multicast UDP (FastDDS discovery):** Puede necesitar `setsockopt` con flags raros
2. **QUIC (MVFST):** Protocolo complejo, mucho estado en userspace — debería funcionar si los syscalls pasan
3. **Threads:** Fast-DDS es muy multithreaded — FEX-Emu soporta threads pero puede haber edge cases
4. **Memory ordering:** ARM64 tiene modelo de memoria más relajado que x86_64 — FEX-Emu añade barreras pero puede haber races

---

## Recursos Útiles

- [FEX-Emu GitHub](https://github.com/FEX-Emu/FEX) — Código fuente y docs
- [FEX-Emu RootFS](https://github.com/FEX-Emu/RootFS) — Imágenes de rootfs x86_64
- [projectaria-client-sdk PyPI](https://pypi.org/project/projectaria-client-sdk/) — Wheels x86_64
- [projectaria-tools GitHub](https://github.com/facebookresearch/projectaria_tools) — Open source tools
- [Project Aria SDK Docs](https://facebookresearch.github.io/projectaria_tools/docs/ARK/sdk) — Documentación oficial
- [Project Aria Streaming Sample](https://facebookresearch.github.io/projectaria_tools/docs/ARK/sdk/samples/device_stream) — Ejemplo de streaming
