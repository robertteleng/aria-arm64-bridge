# DEVELOPER_DIARY.md — Bitácora de Experimentos

Registro detallado de cada experimento: qué se probó, qué pasó, qué se aprendió.

---

## Exp 001: FEX-Emu + Aria SDK import en Jetson Orin Nano
**Fecha:** 2026-02-25
**Branch:** `main`
**Estado:** Éxito (con obstáculos significativos)

---

### Hipótesis
> "Si compilamos FEX-Emu en el Jetson y creamos un rootfs x86_64 con el SDK, `import aria.sdk` debería funcionar porque FEX-Emu traduce las instrucciones x86_64 de libsdk_core.so al vuelo."

### Setup
- Hardware: Jetson Orin Nano, 8 GB RAM, 6 cores ARM64 Cortex-A78AE
- OS: JetPack R36.5.0 (L4T R36), kernel 5.15.185-tegra
- FEX-Emu: compilado desde source (latest main)
- RootFS: Ubuntu 22.04 x86_64 via FEXRootFSFetcher (SquashFS extraído)
- Python: 3.10.12 (rootfs) + 3.12.12 (via deadsnakes PPA en host)
- SDK: projectaria-client-sdk 2.2.0 + projectaria-tools 2.1.1

### Ejecución — Obstáculos encontrados y soluciones

#### Obstáculo 1: cmake no encuentra Qt5
```
CMake Error: find_package(Qt5) failed
```
**Causa:** FEX-Emu intenta compilar FEXConfig (GUI de configuración) que necesita Qt5.
**Solución:** Añadir `-DBUILD_FEXCONFIG=OFF` a cmake. Solo necesitamos FEXInterpreter y FEXBash.
**Archivo:** `scripts/setup_fex_emu.sh`

#### Obstáculo 2: debootstrap --foreign falla en segunda etapa
```
W: Failure trying to run: chroot "/home/robert/.fex-emu/rootfs" /bin/true
```
**Causa:** `debootstrap --foreign` solo extrae .deb sin desempaquetar. La segunda etapa necesita ejecutar binarios x86_64 pero chroot no puede sin emulador. FEXInterpreter tampoco funcionó directamente con el script de debootstrap.
**Solución:** Abandonar debootstrap. Usar `FEXRootFSFetcher -y -x` que descarga un rootfs Ubuntu 22.04 x86_64 pre-armado (~1 GB SquashFS). Mucho más fiable.

#### Obstáculo 3: pip no encuentra el wheel x86_64
```
ERROR: No matching distribution found for projectaria-client-sdk==2.2.0
```
**Causa:** Python bajo FEX-Emu reporta `sysconfig.get_platform()` = `linux-aarch64` (porque el kernel es ARM64). pip busca wheels aarch64 y no encuentra ninguno para el SDK.
**Solución:** Forzar la plataforma con pip:
```bash
pip install --platform manylinux2014_x86_64 --only-binary=:all: --no-deps --target <path> projectaria-client-sdk==2.2.0
```
**Clave:** `--no-deps` es necesario porque las dependencias nativas (numpy, pyzmq) se instalan aparte.

#### Obstáculo 4: .so instalados fuera del rootfs no se cargan
```
ModuleNotFoundError: No module named 'aria'
```
**Causa:** pip con `--user` instala en `~/.local/lib/python3.X/site-packages/` que está en el filesystem del host. FEX-Emu/Python los veía en el path pero `dlopen()` no podía cargar los .so x86_64 desde fuera del rootfs.
**Solución:** Instalar directamente dentro del rootfs con `--target` apuntando al path **completo** del rootfs:
```bash
sudo pip install --platform manylinux2014_x86_64 --only-binary=:all: --no-deps \
    --target /home/robert/.fex-emu/RootFS/Ubuntu_22_04/usr/local/lib/python3.10/dist-packages \
    projectaria-client-sdk==2.2.0 projectaria-tools
```
**Nota:** Usar `sudo` porque el directorio del rootfs es propiedad de root.

#### Obstáculo 5: EXTENSION_SUFFIXES no incluye x86_64
```
ModuleNotFoundError: No module named '_core_pybinds'
```
**Causa:** Python bajo FEX-Emu busca `.cpython-310-aarch64-linux-gnu.so` pero los wheels tienen `.cpython-310-x86_64-linux-gnu.so`. Creamos symlinks `aarch64 → x86_64` pero resultó ser un red herring — el problema real era el Obstáculo 4 (archivos fuera del rootfs).
**Solución:** Cuando los paquetes están instalados **dentro del rootfs**, Python los carga correctamente sin necesidad de symlinks ni patches de EXTENSION_SUFFIXES. FEX-Emu intercepta `dlopen()` transparentemente para archivos dentro del rootfs.

#### Obstáculo 6: libstdc++ demasiado vieja
```
ImportError: /lib/x86_64-linux-gnu/libstdc++.so.6: version `GLIBCXX_3.4.31' not found
```
**Causa:** El rootfs Ubuntu 22.04 trae libstdc++6 de gcc-12 (GLIBCXX hasta 3.4.30). El SDK necesita GLIBCXX_3.4.31 (gcc-13+).
**Intento fallido:** Instalar libstdc++ de gcc-14 (Ubuntu 24.04) → falló con `GLIBC_2.38 not found` porque gcc-14 necesita glibc 2.38 pero el rootfs tiene glibc 2.35.
**Solución:** Descargar libstdc++6 de gcc-13 del PPA ubuntu-toolchain-r/test para jammy (22.04). Esta versión tiene GLIBCXX_3.4.31/3.4.32 y solo requiere glibc >= 2.34 (compatible).
```bash
wget "https://ppa.launchpadcontent.net/ubuntu-toolchain-r/test/ubuntu/pool/main/g/gcc-13/libstdc++6_13.1.0-8ubuntu1~22.04_amd64.deb"
dpkg-deb -x libstdc++6_*.deb extracted/
sudo cp extracted/usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.32 $ROOTFS/usr/lib/x86_64-linux-gnu/
sudo ln -sf libstdc++.so.6.0.32 $ROOTFS/usr/lib/x86_64-linux-gnu/libstdc++.so.6
```

### Resultado
```
$ FEXBash -c "python3 -c 'import aria.sdk; print(\"ARIA SDK LOADED\")'"
ARIA SDK LOADED
```
- `import aria.sdk` funciona (carga libsdk_core.so de 98 MB)
- `import projectaria_tools.core` funciona
- Python 3.10 x86_64 bajo FEX-Emu, Python 3.12 también disponible en host

### Análisis
- FEX-Emu traduce correctamente todas las instrucciones x86_64 de libsdk_core.so (98 MB, estáticamente linkeado)
- El mayor problema NO fue la emulación en sí, sino la infraestructura alrededor: pip, paths, librerías del rootfs
- El rootfs de FEXRootFSFetcher funciona pero es Ubuntu 22.04 con librerías viejas — hay que actualizar libstdc++ manualmente
- Los paquetes Python con extensiones .so nativas DEBEN estar dentro del rootfs, no en ~/.local/

### Decisión
- [x] Seguir por este camino — FEX-Emu funciona para el SDK
- [ ] Siguiente: probar streaming real con gafas Aria (Fase 2)
- [x] Siguiente: compilar projectaria-tools nativo ARM64 (Fase 1.5) → ver Exp 002

### Reflexión
- **Lo que aprendí:**
  - FEXRootFSFetcher > debootstrap para crear rootfs. Más rápido, más fiable.
  - pip bajo FEX-Emu reporta plataforma aarch64 — siempre usar `--platform manylinux2014_x86_64`
  - Los .so x86_64 solo funcionan si están dentro del rootfs (FEX-Emu intercepta dlopen para paths del rootfs)
  - La compatibilidad de libstdc++ es un tema de versiones: gcc-13 para 22.04 → OK, gcc-14 → necesita glibc más nuevo
  - `sudo` dentro de FEXBash ejecuta el binario del HOST, no del rootfs — cuidado con paths
- **Lo que haría diferente:**
  - Ir directo a FEXRootFSFetcher en vez de intentar debootstrap
  - Instalar todo dentro del rootfs desde el principio, no en ~/.local/
  - Verificar compatibilidad de glibc antes de actualizar libstdc++
  - No usar Python 3.12 del host con packages x86_64 — usar el Python del rootfs (3.10) que ya viene configurado

### Setup reproducible (pasos finales que funcionan)
```bash
# 1. Compilar FEX-Emu
./scripts/setup_fex_emu.sh  # con -DBUILD_FEXCONFIG=OFF

# 2. Descargar rootfs
FEXRootFSFetcher -y -x

# 3. Actualizar libstdc++ en rootfs
ROOTFS=$(FEXGetConfig RootFS)  # o ~/.fex-emu/RootFS/Ubuntu_22_04
wget "https://ppa.launchpadcontent.net/ubuntu-toolchain-r/test/ubuntu/pool/main/g/gcc-13/libstdc++6_13.1.0-8ubuntu1~22.04_amd64.deb"
dpkg-deb -x libstdc++6_*.deb /tmp/libstdcpp/
sudo cp /tmp/libstdcpp/usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.32 $ROOTFS/usr/lib/x86_64-linux-gnu/
sudo ln -sf libstdc++.so.6.0.32 $ROOTFS/usr/lib/x86_64-linux-gnu/libstdc++.so.6

# 4. Instalar SDK dentro del rootfs
sudo pip install --platform manylinux2014_x86_64 --only-binary=:all: --no-deps \
    --target $ROOTFS/usr/local/lib/python3.10/dist-packages \
    projectaria-client-sdk==2.2.0 projectaria-tools

# 5. Instalar dependencias normales
FEXBash -c "curl -sS https://bootstrap.pypa.io/get-pip.py | python3"
FEXBash -c "python3 -m pip install numpy pyzmq"

# 6. Test
FEXBash -c "python3 -c 'import aria.sdk; print(\"ARIA SDK LOADED\")'"
```

---

## Exp 002: Compilar projectaria-tools nativo ARM64 en Jetson
**Fecha:** 2026-02-26
**Branch:** `main`
**Estado:** Éxito (GCC 13, Python bindings funcionales)

---

### Hipótesis
> "Si compilamos projectaria-tools desde source en ARM64 nativo, podemos usar las herramientas de datos (lectura VRS, calibración, MPS) sin emulación, dejando FEX-Emu solo para el streaming SDK."

### Motivación
- `projectaria-tools` es open-source (a diferencia de `projectaria-client-sdk`)
- Compilarlo nativo ARM64 elimina overhead de emulación para procesamiento de datos
- Separa concerns: FEX-Emu solo para el SDK de streaming cerrado, tools nativas para todo lo demás

### Setup
- Hardware: Jetson Orin Nano, 8 GB RAM, 6 cores ARM64 Cortex-A78AE
- OS: Ubuntu 22.04 (JetPack R36.5.0)
- Source: `~/projectaria_tools` (clonado con `--recursive`)
- cmake 4.2.1 (via pip, en `/usr/local/lib/python3.10/dist-packages/cmake/data/bin/cmake`)
- GCC: 11.4.0 (sistema) → 12.3.0 (instalado para resolver ICE) → **13.1.0 (build final exitoso)**
- Python: 3.12.12 (bindings compilados para 3.12)

### Ejecución — Obstáculos encontrados y soluciones

#### Obstáculo 1: cmake del sistema demasiado viejo
```
CMake 3.26 or higher is required. You are running version 3.22.1
```
**Causa:** La dependencia "Ocean" (Facebook) requiere cmake >= 3.26. El sistema trae cmake 3.22.1.
**Solución:** Usar cmake 4.2.1 instalado via pip. El binario está en una ruta no estándar:
```bash
/usr/local/lib/python3.10/dist-packages/cmake/data/bin/cmake
```
**Nota:** `which cmake` devuelve `/usr/bin/cmake` (3.22.1). El de pip no está en PATH por defecto.

#### Obstáculo 2: Eigen falla al clonar desde GitLab
```
Failed to clone repository: 'https://gitlab.com/libeigen/eigen.git'
```
**Causa:** Error de red intermitente con GitLab durante FetchContent.
**Solución:** Pre-clonar Eigen manualmente antes de cmake:
```bash
mkdir -p build/_deps
git clone https://gitlab.com/libeigen/eigen.git build/_deps/eigen-src --branch 3.4.0 --depth 1
```
cmake detecta que ya existe y no intenta clonarlo de nuevo.

#### Obstáculo 3: FFmpeg headers no encontrados
```
FFMPEG_INCLUDE_DIR-NOTFOUND
```
**Causa:** Las librerías runtime de FFmpeg estaban instaladas (`libavcodec58`) pero faltaban los paquetes `-dev` con headers y `.pc` files.
**Solución:**
```bash
sudo apt-get install -y libavcodec-dev libavformat-dev libavutil-dev libswscale-dev libswresample-dev
```

#### Obstáculo 4: Errores NEON type mismatch en Ocean (GCC 11)
```
cannot convert 'const uint16x8_t' to 'uint8x16_t'
cannot convert 'uint16x8_t' to 'int16x8_t'
```
**Causa:** El código NEON de Ocean tiene bugs de tipos que GCC 11 en ARM64 nativo detecta como errores (en x86_64 no se compila esta ruta). Errores en:
- `FrameConverterY10_Packed.h`: `vget_low_u8()` llamado con `uint16x8_t` (debería ser `vget_low_u16()`)
- `FrameConverter.h`: `vget_low_u8()` llamado con `int8x16_t` y `int16x8_t`
- `FrameConverter.cpp`: `vrshrq_n_s16()` llamado con `uint16x8_t` (debería ser `vrshrq_n_u16()`)

**Intento 1 — Actualizar Ocean a main:** El commit viejo (`5fca1d27`, Nov 2025) tenía los bugs. Actualicé a `c8a79b9a` (latest main) que usa `NEON::create_*()` helpers. Arregló los type mismatches pero...

#### Obstáculo 5: Internal Compiler Error (ICE) en GCC 11 con constexpr NEON
```
constexpr uint8x8_t shuffleC_u_8x8 = NEON::create_uint8x8(6u, ...);
internal compiler error: in tsubst_copy, at cp/pt.c:17118
```
**Causa:** GCC 11 tiene un bug conocido con `constexpr` + NEON intrinsics dentro de templates. GCC 12 también lo tiene (ICE en `cp/pt.cc:17267`). Se necesita GCC 13+ para soporte completo, o parchear el código.
**Intento 2 — Parchear manualmente:** Volver al Ocean viejo y parchear solo los type bugs. Pero el Ocean viejo también usa `constexpr uint8x8_t = {...}` en templates → mismo ICE.
**Solución:** Instalar GCC 12 + parchear `constexpr` → `const` en todos los NEON types de Ocean CV:
```bash
sudo apt-get install -y g++-12
# Parchear Ocean CV files (constexpr NEON causa ICE en GCC 11 y 12)
cd build/_deps/ocean-src/impl/ocean/cv
for f in FrameConverter.h FrameConverterY10_Packed.h FrameInterpolatorBilinear.h \
         FrameInterpolatorBilinear.cpp FrameInterpolatorNearestPixel.cpp FrameShrinker.cpp; do
    sed -i 's/constexpr \(uint8x8_t\)/const \1/g; s/constexpr \(uint8x16_t\)/const \1/g; \
            s/constexpr \(int8x16_t\)/const \1/g; s/constexpr \(int16x8_t\)/const \1/g; \
            s/constexpr \(uint16x8_t\)/const \1/g' "$f"
done
cmake ... -DCMAKE_CXX_COMPILER=g++-12 -DCMAKE_C_COMPILER=gcc-12
```
**Nota:** GCC 13 del PPA `ubuntu-toolchain-r/test` eliminaría la necesidad de estos parches. Pendiente instalarlo para futuras recompilaciones.

#### Obstáculo 6: cmake 4.x no propaga CMAKE_POLICY_VERSION_MINIMUM a subdependencias
```
Compatibility with CMake < 3.5 has been removed (Sophus)
```
**Causa:** cmake 4.x requiere `cmake_minimum_required(VERSION 3.5+)` pero Sophus tiene versión más vieja. El flag `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` no se propagaba correctamente sin limpiar el cache.
**Solución:** Limpiar build completamente (`rm -rf build`) y pasar el flag desde cero. cmake 4.x con el flag funciona.

#### Obstáculo 7: Type mismatches NEON persisten en Ocean main
```
cannot convert 'const int8x16_t' to 'uint8x16_t'
cannot convert 'const int16x8_t' to 'uint8x16_t'
```
**Causa:** Incluso en Ocean main (`c8a79b9a`), quedan bugs de tipos NEON no corregidos:
- `FrameConverter.h:3570`: `vget_low_u8(int8x16_t)` → debería ser `vget_low_s8()`
- `FrameConverter.h:3577`: `vget_low_u8(int16x8_t)` → debería ser `vget_low_s16()`
- `FrameConverterY10_Packed.h:509-512`: `vget_low/high_u8(uint16x8_t)` → `vget_low/high_u16()`
- `FrameConverter.cpp:2642-44,2875-77`: `vrshrq_n_s16(vrhaddq_u16(...))` → `vrshrq_n_u16()`

**Solución:** Parchear manualmente con `sed` (ver setup reproducible abajo).

#### Obstáculo 8: `pip install` usa cmake del sistema (3.22.1)
```
CMake 3.26 or higher is required
```
**Causa:** `setup.py` llama `cmake` directamente → usa `/usr/bin/cmake` (3.22.1) en vez del de pip (4.2.1).
**Solución:** Anteponer el cmake de pip al PATH:
```bash
PATH="/usr/local/lib/python3.10/dist-packages/cmake/data/bin:$PATH" pip3 install -e . --no-build-isolation
```

### Setup reproducible (GCC 13 — versión final)
```bash
# 1. Pre-requisitos
sudo add-apt-repository -y ppa:ubuntu-toolchain-r/test
sudo apt-get install -y gcc-13 g++-13 libavcodec-dev libavformat-dev libavutil-dev \
    libswscale-dev libswresample-dev libopus-dev libboost-all-dev

# 2. Preparar build
cd ~/projectaria_tools
rm -rf build && mkdir build
CMAKE=/usr/local/lib/python3.10/dist-packages/cmake/data/bin/cmake

# 3. cmake (GCC 13 + Python bindings)
cd build && $CMAKE .. \
    -DCMAKE_CXX_COMPILER=g++-13 \
    -DCMAKE_C_COMPILER=gcc-13 \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_PYTHON_BINDINGS=ON \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5

# 4. Parchear Ocean NEON type mismatches (bugs en Ocean, NO del compilador)
#    GCC 13 resuelve el ICE de constexpr, pero los type bugs persisten
cd _deps/ocean-src/impl/ocean/cv
sed -i 's/vget_low_u8(leftShifts_s_8x16)/vget_low_s8(leftShifts_s_8x16)/g; \
        s/vget_low_u8(rightShifts_s_16x8)/vget_low_s16(rightShifts_s_16x8)/g' FrameConverter.h
sed -i 's/vget_low_u8(unpackedAB_u_16x8)/vget_low_u16(unpackedAB_u_16x8)/g; \
        s/vget_high_u8(unpackedAB_u_16x8)/vget_high_u16(unpackedAB_u_16x8)/g; \
        s/vget_low_u8(unpackedCD_u_16x8)/vget_low_u16(unpackedCD_u_16x8)/g; \
        s/vget_high_u8(unpackedCD_u_16x8)/vget_high_u16(unpackedCD_u_16x8)/g' FrameConverterY10_Packed.h
sed -i 's/vrshrq_n_s16(vrhaddq_u16/vrshrq_n_u16(vrhaddq_u16/g' FrameConverter.cpp
cd ~/projectaria_tools/build

# 5. Compilar (usar -j1 o -j2 en Jetson 8GB para evitar OOM)
make -j1

# 6. Instalar numpy para Python 3.12 (cmake detecta 3.12)
python3.12 -m pip install --user numpy

# 7. Validar
PYTHONPATH="$PWD/core/python" python3.12 -c \
    "from projectaria_tools.core.sensor_data import ImageDataRecord; print('OK')"
```

### Resultado
```
$ PYTHONPATH=".../build/core/python" python3.12 -c \
    "from projectaria_tools.core.sensor_data import ImageDataRecord; print('OK')"
OK - projectaria_tools importado correctamente
```
- **Librerías C++ compiladas al 100%** con GCC 13.1.0
- **Python bindings funcionales** con Python 3.12 (pybind11)
- **Sin parches de constexpr** — GCC 13 resuelve el ICE nativamente
- **Solo 3 parches NEON** necesarios (bugs en Ocean, no del compilador)

### Análisis
- **8 obstáculos superados** para compilar en ARM64 nativo
- La cadena de dependencias es profunda: Eigen → Ocean → VRS → Sophus → Dispenso → nlohmann-json → CLI11
- Ocean es la dependencia más problemática: código NEON no portado a ARM64, bugs de tipos + ICE constexpr
- cmake del sistema (3.22.1) es insuficiente; el de pip (4.2.1) funciona pero necesita flags de compatibilidad
- FFmpeg es requerido por VRS para decodificación de video en archivos .vrs
- GCC 11 y 12 tienen ICE con constexpr NEON — **GCC 13 lo resuelve nativamente**
- Los type mismatches NEON son bugs en Ocean, no del compilador — persisten con cualquier GCC
- `make -j4` puede causar OOM en Jetson 8GB — usar `-j1` o `-j2`
- Python bindings se compilan para la versión que cmake detecte — verificar que coincida con el runtime

### Decisión
- [x] Compilación C++ exitosa con GCC 13
- [x] Python bindings funcionales con Python 3.12
- [ ] Siguiente: Fase 2 — streaming real con gafas Aria

### Reflexión
- **Lo que aprendí:**
  - **GCC 13 desde el principio** — elimina los parches de constexpr, solo quedan 3 parches de tipos NEON
  - cmake via pip instala binario en ruta no obvia — verificar con `pip show cmake`
  - Pre-clonar dependencias FetchContent evita fallos de red intermitentes
  - Ocean (Meta) no es oficialmente soportado en ARM64 nativo — los bugs NEON lo confirman
  - Limpiar build completo (`rm -rf build`) es necesario al cambiar compilador
  - `make -j4` causa OOM en 8GB RAM — compilar C++ con templates pesados consume mucha RAM
  - cmake detectó Python 3.12 aunque `python3` apunta a 3.10 — verificar siempre qué Python se usa
  - numpy del sistema (1.21.5 para 3.10) no sirve para 3.12 — instalar con `python3.12 -m pip install --user numpy`
- **Lo que haría diferente:**
  - Instalar GCC 13 como primer paso, antes de cualquier intento de compilación
  - Usar `-j1` desde el principio en Jetson 8GB para evitar reinicios por OOM
  - Instalar todos los `-dev` packages de una vez (FFmpeg, Boost, Opus)
  - Compilar directamente con `-DBUILD_PYTHON_BINDINGS=ON` en vez de separar C++ y pip install

---

## Exp 003: ZMQ Bridge + Integración con aria-guard
**Fecha:** 2026-02-26
**Branch:** `main`
**Estado:** Éxito (pipeline mock validado, integración lista)

---

### Hipótesis
> "Si creamos un bridge ZMQ entre FEX-Emu (receiver) y ARM64 nativo (consumer), podemos integrar Aria con aria-guard sin modificar el pipeline de detección."

### Setup
- Protocolo v2: header 28B (magic + camera_id + timestamp + dimensions) + raw pixels
- ZMQ PUSH/PULL sobre tcp://127.0.0.1:5555
- `aria_receiver.py` corre bajo FEX-Emu, `AriaBridgeObserver` corre nativo

### Ejecución

#### Pipeline ZMQ
- Mock nativo 320x240 @ 30 FPS: **~6ms latencia**, 29.9 FPS reales
- Throughput ZMQ puro: **>1000 FPS** para frames de 5.9MB (no es cuello de botella)
- Cross-process FEX-Emu → nativo: **~24ms latencia**, 0 errores de protocolo
- numpy bajo FEX-Emu es lento para generar frames, pero el SDK real solo pasa buffers

#### Integración aria-guard
- `AriaBridgeObserver` implementa misma interfaz que `AriaDemoObserver`:
  - `get_frame("rgb")` → BGR uint8 (rotado 90° CW, RGB→BGR)
  - `get_frame("eye")` → BGR uint8 (rotado 180°, gray→BGR)
  - `get_stats()` → dict con fps por cámara
  - `fov_h = 1.919` (110° Aria RGB)
- Integrado en `aria-guard/run.py` como opción [5] "Aria Bridge"
- Integrado en `aria-guard/src/web/main.py` como source `aria:bridge`

#### Obstáculo: PYTHONNOUSERSITE
- FEX-Emu Python 3.10 carga numpy ARM64 de `~/.local/` en vez del x86_64 del rootfs
- **Solución:** `PYTHONNOUSERSITE=1` al lanzar bajo FEX-Emu

### Resultado
- Pipeline mock: PASS
- Observer test: PASS (RGB→BGR, stats, multi-camera)
- aria-guard integrado: `python3 run.py aria:bridge`

### Análisis
- ZMQ es más que suficiente para 30 FPS con frames grandes
- La barrera FEX-Emu ↔ nativo no introduce problemas — ZMQ usa TCP loopback
- El bridge añade ~24ms al frame delivery (FEX-Emu overhead + ZMQ)
- Con frames reales del SDK (buffer directo, sin numpy), la latencia será menor

### Decisión
- [x] Bridge ZMQ funcional
- [x] aria-guard integrado
- [ ] Probar con gafas reales (Fase 2)

### Reflexión
- **Lo que aprendí:**
  - ZMQ PUSH/PULL es trivial de implementar y performante
  - El protocolo binario simple (header + raw bytes) es mejor que serializar con pickle/protobuf
  - `PYTHONNOUSERSITE=1` es necesario para evitar conflictos ARM64/x86_64 en packages de usuario
  - aria-guard ya tenía la abstracción perfecta (`BaseObserver`) — solo añadir un observer nuevo
- **Lo que haría diferente:**
  - Incluir camera_id en el protocolo desde el principio (no como v2)
  - Testear cross-process FEX-Emu antes de escribir el observer
