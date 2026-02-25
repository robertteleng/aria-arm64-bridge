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
- [ ] Siguiente: compilar projectaria-tools nativo ARM64 (Fase 1.5)

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
