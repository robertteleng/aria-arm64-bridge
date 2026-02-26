# NEXT_SESSION.md

## Estado actual (2026-02-26)

**Todo el pipeline funciona end-to-end:**
- Aria glasses → FEX-Emu receiver → ZMQ → Docker aria-guard (YOLO TRT + Depth TRT)
- 8-11 FPS RGB, 14 FPS detector, 43ms latency
- Detecta personas con distancia (medium/far)
- Librería empaquetada: `aria-arm64-bridge v0.1.0`

### Fases completadas
- Phase 1 ✓ — `import aria.sdk` bajo FEX-Emu
- Phase 1.5 ✓ — `projectaria_tools` compilado ARM64 nativo
- Phase 2 ✓ — Streaming real: profile12 @ 11 FPS RGB (1408x1408)
- Phase 3 ✓ — ZMQ bridge funcional (~24ms latency)
- Phase 4 ✓ — aria-guard integrado con YOLO TRT + Depth TRT
- Library ✓ — `from aria_arm64_bridge import AriaBridge` empaquetado y publicado en GitHub

---

## Pendientes para próxima sesión

### 1. Headless setup (liberar 3+ GB RAM)
```bash
# Desactivar escritorio
sudo systemctl set-default multi-user.target && sudo reboot
# Volver al escritorio:
sudo systemctl set-default graphical.target && sudo reboot
```
- El sistema con desktop usa 5.3/7.4 GB + 2.9 GB swap
- Headless libera ~3 GB (VSCode, gnome, Xorg, NoMachine)
- Esperado: receiver sube de 8 a 11 FPS, detector a 20+ FPS

### 2. NeMo TTS en Docker
- `aria-demo:jetson` no tiene NeMo instalado
- Opción A: rebuild Docker image con NeMo
- Opción B: instalar en runtime (`pip install nemo_toolkit[tts]`) — tarda y usa mucha RAM
- Audio feedback no funciona en Docker (no tiene dispositivo de sonido)
- Necesita `--device /dev/snd` o PulseAudio passthrough

### 3. Publicar en PyPI
```bash
pip install build twine
python -m build
twine upload dist/*
```
- Verificar que `pip install aria-arm64-bridge` funciona limpio
- Necesita cuenta PyPI

### 4. Gaze engine — recompilar TRT
- `gaze.engine` falla (compilado en otra plataforma)
- No hay ONNX source — buscar cómo exportar el modelo de gaze
- Sin esto, `gaze: null` en la detección

### 5. WiFi streaming
- No probado aún (solo USB testado)
- Necesita router WiFi 6, 5 GHz, sin AP isolation
- Comando: `AriaBridge(interface="wifi", device_ip="192.168.1.X")`

### 6. Optimizaciones opcionales
- profile12 + SLAM + IMU combinado (no probado)
- Reducir resolución si 11 FPS no es suficiente
- Evaluar si gen2 Aria hardware da más FPS con HTTP streaming

---

## Cómo lanzar la pipeline

### Opción A: Script (2 terminales en 1)
```bash
./scripts/launch_pipeline.sh              # USB
./scripts/launch_pipeline.sh wifi 192.168.1.42  # WiFi
```

### Opción B: Manual (2 terminales)
```bash
# Terminal 1: Receiver
PYTHONNOUSERSITE=1 FEXBash -c "python3 src/receiver/aria_receiver.py"

# Terminal 2: aria-guard en Docker
docker run --runtime nvidia --network host -it --rm \
  -v ~/Projects/aria-arm64-bridge/src/bridge:/bridge \
  -v ~/Projects/aria-guard:/app \
  aria-demo:jetson bash -c "pip3 install pyzmq 'numpy<2' --force-reinstall && PYTHONPATH=/bridge python3 run.py aria:bridge"
```

Dashboard: http://<jetson-ip>:5000

---

## Gotchas importantes

- `numpy<2` obligatorio en Docker (OpenCV ABI mismatch)
- `PYTHONNOUSERSITE=1` obligatorio bajo FEX-Emu
- `profile12` es el único perfil viable (~11 FPS)
- NUNCA subscribirse a audio (crash `free(): invalid size`)
- TRT engines deben compilarse EN el Orin Nano
- Daemon threads crashean silenciosamente — siempre try/except
- trtexec path en Docker: `/usr/src/tensorrt/targets/aarch64-linux-gnu/bin/trtexec`
