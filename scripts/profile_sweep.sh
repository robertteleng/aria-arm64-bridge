#!/bin/bash
# Profile sweep: mide FPS RGB real de cada streaming profile bajo el stack actual.
# Uso: ./scripts/profile_sweep.sh [duración_por_profile_segundos] [profiles...]
# Ej:  ./scripts/profile_sweep.sh 60 profile12 profile18 profile15
set -u

DURATION="${1:-60}"
shift 2>/dev/null || true
PROFILES=("${@:-profile12 profile18 profile15}")
[ $# -eq 0 ] && PROFILES=(profile12 profile18 profile15)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RESULTS="$PROJECT_DIR/logs/profile_sweep_$(date +%Y%m%d_%H%M%S).txt"
mkdir -p "$PROJECT_DIR/logs"

echo "Profile sweep — ${DURATION}s por profile" | tee "$RESULTS"
echo "FEX: $(strings /usr/local/bin/FEXInterpreter | grep -m1 -oE 'FEX-[0-9]+[^ ]*')" | tee -a "$RESULTS"
echo "" | tee -a "$RESULTS"

for P in "${PROFILES[@]}"; do
    echo "=== $P ===" | tee -a "$RESULTS"
    LOG=$(mktemp)
    # /usr/bin/python3 explícito: bajo FEXBash, "python3" puede resolver al .venv ARM64 del host (sin SDK)
    # -u: sin buffer de stdout — si no, los prints del receiver no llegan al log a tiempo
    PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 -u $PROJECT_DIR/src/aria_arm64_bridge/receiver.py --interface usb --profile $P" > "$LOG" 2>&1 &
    PID=$!

    # Esperar a primer frame (max 60s)
    WAITED=0
    until grep -q "First frame" "$LOG" 2>/dev/null || [ $WAITED -ge 60 ]; do
        sleep 2; WAITED=$((WAITED+2))
        kill -0 $PID 2>/dev/null || break
    done

    if ! grep -q "First frame" "$LOG" 2>/dev/null; then
        echo "  SIN FRAMES en ${WAITED}s — profile no viable" | tee -a "$RESULTS"
    else
        # Medir: contar frames del log durante DURATION
        sleep "$DURATION"
        FPS_LINES=$(grep -E "\[receiver\] rgb=" "$LOG" | tail -3)
        echo "$FPS_LINES" | sed 's/^/  /' | tee -a "$RESULTS"
    fi

    # SIGINT primero: el receiver hace stop_streaming() en su cleanup.
    # kill -9 deja la sesión colgada EN LAS GAFAS (error 940 en el siguiente arranque).
    kill -INT $PID 2>/dev/null
    sleep 8
    kill -9 $PID 2>/dev/null
    pkill -9 -f aria_receiver 2>/dev/null

    # Cinturón y tirantes: limpiar sesión zombie en las gafas vía SDK
    PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 $SCRIPT_DIR/stop_streaming.py" >/dev/null 2>&1
    sleep 5
    rm -f "$LOG"
    echo "" | tee -a "$RESULTS"
done

echo "Resultados en: $RESULTS"
