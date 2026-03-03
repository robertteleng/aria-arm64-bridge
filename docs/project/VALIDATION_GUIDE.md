# Validation Guide — Exp 005 CPU Optimisation

Guía para validar las optimizaciones de CPU de la sesión 2026-03-03 en el Jetson con gafas reales.

## Antes de empezar

```bash
cd ~/aria-arm64-bridge
git pull origin main
```

---

## 1. Medir CPU baseline (sin pipeline)

```bash
htop
```

Anota en papel o en un terminal aparte:
- CPU idle por core (normalmente <5%)
- RAM libre

---

## 2. Lanzar el pipeline

```bash
./scripts/launch_pipeline.sh usb
```

Espera hasta ver en el log:
```
[receiver] First frame! cam=rgb shape=(1408, 1408, 3) ...
[BRIDGE] AriaBridgeObserver conectado a tcp://127.0.0.1:5555
```

---

## 3. Medir CPU con pipeline activo

En otro terminal, deja correr 30 segundos y anota:

```bash
# Ver CPU por proceso en tiempo real
htop
```

Busca y anota el % de CPU de:
- `FEXBash` — el receiver bajo emulación
- `python3` nativo — el observer ARM64

También puedes capturar una muestra puntual:
```bash
ps aux | grep -E 'FEXBash|python3' | grep -v grep
```

---

## 4. Verificar FPS y latencia

El receiver imprime stats cada 90 frames (~7.5s a 12 FPS):
```
[receiver] rgb=12 fps (total=90)
```

El observer imprime stats cada 300 frames (~25s a 12 FPS):
```
[aria-bridge] rgb=12.0 fps (total=300)
```

**Valores esperados:**
| Métrica | Esperado |
|---------|---------|
| RGB FPS | ~12 |
| CPU FEXBash | ~80-100% (1 core) — no optimizable |
| CPU python3 observer | Notablemente menor que antes |

---

## 5. Test de estabilidad

Deja correr **5 minutos**. Comprueba:
- [ ] No hay crashes (`free(): invalid size` sería regresión)
- [ ] FPS estable (no baja a <8 de forma sostenida)
- [ ] RAM no crece continuamente (no hay memory leak)

```bash
# Monitorizar RAM cada 10s durante 5 minutos
watch -n 10 'free -h'
```

---

## 6. Si algo falla

### FPS cae a <5 de forma sostenida
Puede ser `message_queue_size=1` demasiado agresivo. Revertir a 5:

```bash
# En src/receiver/aria_receiver.py y src/aria_arm64_bridge/receiver.py
# cambiar línea 141:
sub_config.message_queue_size[aria.StreamingDataType.Rgb] = 5
```

### Crash o frames corruptos
Probable problema con `recv_multipart`. Anotar el error exacto y reportar.

### Observer no recibe frames (timeout en start)
Comprobar que el receiver arrancó correctamente:
```bash
ps aux | grep FEXBash
```

---

## 7. Documentar resultados

Una vez validado, actualizar `DEVELOPER_DIARY.md` con los resultados reales:

```markdown
### Resultado validado en Jetson (Exp 005)
**Fecha:** YYYY-MM-DD

| Métrica | Antes | Después |
|---------|-------|---------|
| CPU FEXBash | X% | X% |
| CPU python3 observer | X% | X% |
| RGB FPS | ~12 | ~12 |
| Estabilidad 5min | - | OK / FAIL |
```

---

## 8. Commit y push tras validación

```bash
git add docs/DEVELOPER_DIARY.md
git commit -m "docs: add Exp 005 validation results from Jetson"
git push origin main
```

Si todo OK, publicar:
```bash
python -m build
gh release create v0.1.0 dist/*.whl dist/*.tar.gz \
  --title "v0.1.0 — 12 FPS, reduced CPU on Jetson" \
  --notes "Validated on Jetson Orin Nano 8GB + Aria gen1 glasses via USB."
```
