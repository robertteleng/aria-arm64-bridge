# Guía de conexión: Aria Glasses → Jetson Orin Nano

Cómo emparejar, conectar y hacer streaming desde las gafas Meta Aria al Jetson.

> **Importante:** El Aria Client SDK solo soporta x64 Linux oficialmente. En Jetson ARM64 todo se ejecuta bajo FEX-Emu.

---

## Requisitos previos

### Hardware
- Meta Aria glasses (con setup completado via Mobile Companion App)
- Jetson Orin Nano con FEX-Emu + Aria SDK instalados (ver Exp 001 en DEVELOPER_DIARY.md)
- Cable USB-C para pairing inicial
- Router WiFi 6 (5 GHz) para streaming inalámbrico — NO redes corporativas/universitarias

### Software en Jetson
- FEX-Emu con rootfs Ubuntu 22.04 x86_64
- `projectaria-client-sdk` instalado en el rootfs (hecho en Phase 1)
- Mobile Companion App en el teléfono (iOS/Android)

### Routers recomendados (de la doc oficial)
- Asus, Netgear, TP-Link con WiFi 6
- Sin firewalls ni aislamiento de clientes
- Red doméstica directa (el Jetson y las gafas en la misma red)

---

## Paso 1: Diagnóstico — aria-doctor

Ejecutar el diagnóstico para verificar que el SDK funciona antes de intentar emparejar:

```bash
FEXBash -c "aria-doctor"
```

Esto verifica:
- Que el SDK está correctamente instalado
- Que las dependencias están presentes
- Que hay conectividad básica

> **Si falla:** Verificar que `projectaria-client-sdk` está instalado dentro del rootfs FEX-Emu, no en `~/.local/`.

---

## Paso 2: Pairing (una sola vez)

El pairing genera certificados de autenticación entre el Jetson y las gafas. Solo hay que hacerlo una vez — los certificados persisten hasta factory reset o revocación manual.

### Procedimiento

1. **Conectar las gafas por USB** al Jetson
2. **Encender las gafas** (LED debe estar activo)
3. **Abrir la Mobile Companion App** en el teléfono
4. **Ejecutar el pairing:**

```bash
FEXBash -c "aria auth pair"
```

5. **Aprobar en la app del móvil** — aparecerá un prompt. Verificar que el hash coincide entre la terminal y la app
6. **Confirmación** — la terminal mostrará éxito

### Certificados

Los certificados se guardan localmente. Verificar dónde:

```bash
# Buscar certificados generados por el SDK
FEXBash -c "find ~/.aria -type f 2>/dev/null || find ~/aria -type f 2>/dev/null"
```

> **TODO:** Confirmar la ruta exacta de certificados después del primer pairing real. Posiblemente `~/.aria/` o dentro del rootfs en `$ROOTFS/home/robert/.aria/`.

> **Nota sobre FEX-Emu:** Los certificados podrían guardarse en el rootfs (`~/.fex-emu/RootFS/Ubuntu_22_04/root/.aria/`) o en el home del host. Hay que verificar cuál usa el SDK y asegurar que persistan entre sesiones.

---

## Paso 3: Verificar conexión USB

Después del pairing, verificar que el Jetson ve las gafas:

```bash
# Ver si aparece como dispositivo USB
lsusb | grep -i "meta\|aria\|facebook\|oculus"

# Si aparece como interfaz de red USB (ethernet gadget)
ip link show | grep usb
```

> **TODO:** Documentar el vendor ID / product ID de las gafas Aria por USB después de la primera conexión.

---

## Paso 4: Streaming por USB

La forma más directa — sin depender de WiFi:

```bash
FEXBash -c "python3 -m device_stream --interface usb --update_iptables"
```

El flag `--update_iptables` configura las reglas de firewall necesarias para recibir el stream.

### Desde Python (para integración con aria-guard)

```python
# Esto corre bajo FEX-Emu (x86_64 emulado)
import aria.sdk as aria

# Descubrir dispositivos
device_client = aria.DeviceClient()
device = device_client.connect()  # USB por defecto

# Obtener streaming manager
streaming_manager = device.streaming_manager

# Configurar
config = streaming_manager.streaming_config
config.profile_name = "profile18"  # perfil por defecto
config.use_ephemeral_certs = True  # protección contra eavesdropping

# Iniciar streaming
streaming_manager.start_streaming()

# Crear observer para recibir frames
class FrameObserver(aria.BaseStreamingClientObserver):
    def on_image_received(self, image, record):
        # Aquí se procesan los frames
        # En producción: enviar via ZMQ al proceso nativo ARM64
        pass

# Registrar observer y suscribirse
streaming_client = streaming_manager.streaming_client
observer = FrameObserver()
streaming_client.set_streaming_client_observer(observer)
streaming_client.subscribe()
```

---

## Paso 5: Streaming por WiFi

Requiere que las gafas y el Jetson estén en la misma red WiFi.

### Obtener IP de las gafas
- Abrir la **Mobile Companion App** → Dashboard → ver IP de las gafas

### Lanzar streaming

```bash
FEXBash -c "python3 -m device_stream --interface wifi --device-ip <IP_GAFAS> --update_iptables"
```

### Requisitos de red
- WiFi 6 (802.11ax) en 5 GHz recomendado
- Sin firewalls entre dispositivos
- Sin aislamiento de clientes (AP isolation OFF)
- NO funciona en redes corporativas, universitarias ni públicas

---

## Paso 6: Arquitectura en producción (aria-guard)

El streaming final para aria-guard usa la arquitectura de bridge:

```
┌─────────────────────────┐     ┌──────────────────────────┐
│  FEX-Emu (x86_64)       │     │  Nativo ARM64             │
│                          │     │                            │
│  aria.sdk → frames ──────┼─ZMQ─┼──→ aria-guard pipeline    │
│  (receiver.py)           │     │     (CUDA, ML, alertas)   │
│                          │     │                            │
└─────────────────────────┘     └──────────────────────────┘
```

- **Proceso emulado:** Solo recibe frames del SDK y los pone en una cola ZMQ
- **Proceso nativo:** aria-guard consume frames, ejecuta ML con CUDA, sin overhead de emulación
- **Latencia esperada:** <50ms overhead de emulación (WiFi es el bottleneck real a ~30 FPS)

---

## Troubleshooting

### "No device found"
- Verificar USB: `lsusb` debe mostrar el dispositivo
- Verificar que las gafas están encendidas y con batería
- Re-ejecutar `aria-doctor`

### "Certificate error" o "Not paired"
- Re-ejecutar `FEXBash -c "aria auth pair"` con USB conectado
- Verificar que la Mobile Companion App está abierta durante el pairing

### WiFi streaming lento o con drops
- Verificar que usa 5 GHz (no 2.4 GHz)
- Acercar el router al Jetson y las gafas
- Desactivar AP isolation en el router
- Preferir USB para testing inicial

### FEX-Emu: "command not found" para aria-doctor
- Verificar que el SDK está dentro del rootfs:
  ```bash
  FEXBash -c "pip show projectaria-client-sdk"
  ```
- Si no está, reinstalar:
  ```bash
  sudo pip install --platform manylinux2014_x86_64 --only-binary=:all: --no-deps \
      --target $ROOTFS/usr/local/lib/python3.10/dist-packages \
      projectaria-client-sdk==2.2.0
  ```

---

## Perfiles de streaming disponibles

| Perfil | Interfaz | Descripción |
|--------|----------|-------------|
| `profile18` | WiFi | RGB + SLAM cams + IMU (usado en aria-guard vía WiFi) |
| `profile28` | USB | RGB + SLAM cams + IMU (usado en aria-guard vía USB) |
| (otros) | — | Consultar doc oficial para lista completa |

> **Nota:** El receiver (`aria_receiver.py`) selecciona automáticamente profile28 para USB y profile18 para WiFi.

> **TODO:** Completar tabla de perfiles después de probar con las gafas reales.

---

## Checklist Phase 2

- [ ] Ejecutar `aria-doctor` bajo FEX-Emu
- [ ] Hacer pairing por USB con `aria auth pair`
- [ ] Verificar dónde se guardan los certificados
- [ ] Probar streaming USB básico (`device_stream --interface usb`)
- [ ] Probar streaming WiFi (`device_stream --interface wifi`)
- [ ] Medir latencia del stream bajo FEX-Emu
- [x] `src/receiver/aria_receiver.py` implementado (envía frames por ZMQ, protocol v2)
- [x] `src/bridge/frame_consumer.py` implementado (consumer nativo ARM64)
- [x] `src/bridge/aria_bridge_observer.py` implementado (drop-in para aria-guard)
- [ ] Test pipeline completo: Aria → FEX-Emu → ZMQ → aria-guard

> **Importante:** Usar siempre `PYTHONNOUSERSITE=1` al ejecutar bajo FEX-Emu.
