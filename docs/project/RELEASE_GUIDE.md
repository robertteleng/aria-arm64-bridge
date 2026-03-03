# Release Guide — aria-arm64-bridge

Cómo publicar una nueva versión como GitHub Release e instalarla en el Jetson.

---

## Requisitos previos

```bash
pip install build
gh auth login  # si no está autenticado
```

---

## Pasos para publicar

### 1. Actualizar versión

En [pyproject.toml](../../pyproject.toml):
```toml
version = "0.1.1"
```

En [src/aria_arm64_bridge/__init__.py](../../src/aria_arm64_bridge/__init__.py):
```python
__version__ = "0.1.1"
```

### 2. Build

```bash
python -m build
```

Genera en `dist/`:
- `aria_arm64_bridge-0.1.1-py3-none-any.whl`
- `aria_arm64_bridge-0.1.1.tar.gz`

### 3. Verificar el wheel

```bash
python -m zipfile -l dist/*.whl
```

Comprueba que incluye `receiver.py`, `observer.py`, `bridge.py`, `protocol.py`.

### 4. Crear el release en GitHub

```bash
gh release create v0.1.1 dist/*.whl dist/*.tar.gz \
  --title "v0.1.1 — descripción corta" \
  --notes "Cambios en esta versión."
```

---

## Instalar en el Jetson

### Desde GitHub Releases (recomendado)

```bash
pip install https://github.com/robertteleng/aria-arm64-bridge/releases/download/v0.1.1/aria_arm64_bridge-0.1.1-py3-none-any.whl
```

### Desde el repo local (desarrollo)

```bash
git clone https://github.com/robertteleng/aria-arm64-bridge
pip install -e aria-arm64-bridge/
```

---

## Notas

- El wheel es `py3-none-any` — no contiene binarios nativos, funciona en cualquier plataforma
- El receiver (`receiver.py`) corre bajo FEX-Emu (x86_64), no como binario ARM64
- Ver [ARIA_CONNECTION_GUIDE.md](ARIA_CONNECTION_GUIDE.md) para setup de FEX-Emu y pairing
