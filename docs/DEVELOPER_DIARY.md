# DEVELOPER_DIARY.md — Bitácora de Experimentos

Registro detallado de cada experimento: qué se probó, qué pasó, qué se aprendió.

---

## Plantilla por Experimento

Copia esto para cada experimento nuevo:

```markdown
## Exp: [nombre]
**Fecha:** YYYY-MM-DD
**Branch:** `exp/...`
**Estado:** Éxito / Fracaso / Parcial

---

### Hipótesis
> "Si hago X, espero que pase Y porque Z"

### Setup
- Hardware: ...
- Software: ...
- Versiones: ...

### Ejecución
```
[comandos ejecutados y su output]
```

### Resultado
- ¿Funcionó? Sí/No/Parcial
- Métricas: latencia, FPS, errores...

### Análisis
- ¿Por qué funcionó/falló?
- ¿Qué syscalls/libs fueron problemáticos?

### Decisión
- [ ] Seguir por este camino
- [ ] Pivotar a: ...
- [ ] Abandonar porque: ...

### Reflexión
- **Lo que aprendí:** ...
- **Lo que haría diferente:** ...
```
