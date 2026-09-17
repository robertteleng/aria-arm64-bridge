#!/usr/bin/env bash
# perf_probe_fastdds.sh — Bisección de adquisición Aria bajo FEX-Emu (single-box Jetson).
#
# Mide el TECHO de adquisición de un perfil/sensor con un observer COUNT-ONLY puro
# (callback solo hace n += 1: sin image.shape, sin np.copy, sin ZMQ, sin logging por frame).
# Aísla "¿cuántos frames es capaz de entregar el SDK bajo FEX antes de tocar nada?".
#
# Corresponde a la matriz de matriz de rendimiento (casos A/B/C/G/H Python del registro de investigación, no publicado).
# Para el caso I/J (observer C++ x86 count-only) se necesita un binario aparte — ver TODO al final.
#
# USO:
#   scripts/perf_probe_fastdds.sh [profile] [data_type] [interface] [seconds] [device_ip]
#     profile     perfil Aria (default: profile12). Lista con: aria streaming profiles --save-json
#     data_type   rgb | eye | imu | slam | rgb_eye | rgb_imu | all   (default: rgb)
#     interface   usb | wifi   (default: usb)
#     seconds     duración de la medición (default: 30 — mín 15 para FPS estable)
#     device_ip   IP de las gafas (solo wifi)
#
# EJEMPLOS (matriz §6bis):
#   scripts/perf_probe_fastdds.sh profile12 rgb        # caso A: RGB-only full-res, techo puro
#   scripts/perf_probe_fastdds.sh profile12 imu        # caso C: callback-rate alto, bytes mínimos
#   scripts/perf_probe_fastdds.sh profile12 all        # caso F: producción (RGB+Eye+IMU)
#   FASTDDS_BUILTIN_TRANSPORTS='LARGE_DATA?max_msg_size=8MB&sockets_size=16MB&non_blocking=true' \
#     scripts/perf_probe_fastdds.sh profile12 rgb      # §5.B: ¿el SDK respeta el env var?
#   PROBE_PROFILE=1 scripts/perf_probe_fastdds.sh profile12 rgb   # adjunta perf+strace al PID
#
# REQUISITOS: Jetson ARM64, FEXBash en PATH, rootfs x86 con projectaria-client-sdk, gafas conectadas.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

PROFILE="${1:-profile12}"
DATA_TYPE="${2:-rgb}"
INTERFACE="${3:-usb}"
SECONDS_RUN="${4:-30}"
DEVICE_IP="${5:-}"

LOG_DIR="$PROJECT_DIR/experiments/perf_$(date +%Y%m%d_%H%M%S 2>/dev/null || echo run)"
mkdir -p "$LOG_DIR"

echo "=== perf_probe_fastdds ==="
echo "  profile=$PROFILE  data_type=$DATA_TYPE  interface=$INTERFACE  seconds=$SECONDS_RUN"
echo "  FASTDDS_BUILTIN_TRANSPORTS=${FASTDDS_BUILTIN_TRANSPORTS:-<unset>}"
echo "  FEX_TSOEnabled=${FEX_TSOEnabled:-<default>}  log_dir=$LOG_DIR"
echo

# --- Suelo de medición: avisar si los clocks no están al máximo (no forzar) ---
if command -v nvpmodel >/dev/null 2>&1; then
  echo "[probe] nvpmodel actual: $(sudo nvpmodel -q 2>/dev/null | tr '\n' ' ' || echo '?')"
  echo "[probe] recuerda: 'sudo nvpmodel -m 0 && sudo jetson_clocks --fan' para suelo de medición"
fi

# --- Generar el observer count-only puro en el rootfs (caso A/H Python) ---
PROBE_PY="/tmp/aria_perf_probe.py"
cat > "$PROBE_PY" <<'PYEOF'
import sys, time, argparse
import aria.sdk as aria

# Mapa data_type -> StreamingDataType (bitwise OR para combos)
DT = {
    "rgb":  aria.StreamingDataType.Rgb,
    "eye":  aria.StreamingDataType.EyeTrack,
    "imu":  aria.StreamingDataType.Imu,
    "slam": aria.StreamingDataType.Slam,
}

class CountOnly:
    """Callback mínimo absoluto: solo incrementa + captura timestamps RGB.

    PASO 0: los timestamps del ImageDataRecord dicen si el SDK SIQUIERA emite
    >11 FPS. Si los deltas RGB son ~90ms → el perfil ya topa en 11 y no hay tuning que
    saque 30. Si son ~33ms pero contamos 11 → el cuello es el callback, no el transporte.
    Esto es lo único que falsa todo el resto del plan, así que se mide siempre.
    """
    def __init__(self):
        self.n = 0
        self.imu = 0
        self._ts = []          # capture_timestamp_ns de los primeros N frames RGB
        self._last_ns = None
    def on_image_received(self, image, record):
        self.n += 1
        ns = getattr(record, "capture_timestamp_ns", None)
        if ns is not None and len(self._ts) < 200:
            self._ts.append(ns)
    def on_imu_received(self, samples, imu_idx):
        self.imu += 1
    def delta_ms(self):
        """Mediana del intervalo entre frames RGB según timestamps del SDK (no del callback)."""
        if len(self._ts) < 3:
            return None
        d = sorted((self._ts[i+1] - self._ts[i]) / 1e6 for i in range(len(self._ts) - 1))
        return d[len(d) // 2]

def resolve_types(name):
    if name == "all":
        return DT["rgb"] | DT["eye"] | DT["imu"]   # audio NUNCA (crash free())
    if name == "rgb_eye":
        return DT["rgb"] | DT["eye"]
    if name == "rgb_imu":
        return DT["rgb"] | DT["imu"]
    return DT[name]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interface", default="usb")
    ap.add_argument("--device-ip", default=None)
    ap.add_argument("--profile", default="profile12")
    ap.add_argument("--data-type", default="rgb")
    ap.add_argument("--seconds", type=float, default=30.0)
    a = ap.parse_args()

    client = aria.DeviceClient()
    cfg = aria.DeviceClientConfig()
    if a.interface == "wifi" and a.device_ip:
        cfg.ip_v4_address = a.device_ip
    client.set_client_config(cfg)
    print(f"[probe] connecting via {a.interface}...", flush=True)
    device = client.connect()

    mgr = device.streaming_manager
    scfg = aria.StreamingConfig()
    scfg.profile_name = a.profile
    if a.interface == "usb":
        scfg.streaming_interface = aria.StreamingInterface.Usb
    mgr.streaming_config = scfg
    mgr.start_streaming()
    print(f"[probe] streaming started (profile={a.profile}, data_type={a.data_type})", flush=True)

    client_ = mgr.streaming_client if hasattr(mgr, "streaming_client") else aria.StreamingClient()
    obs = CountOnly()
    client_.set_streaming_client_observer(obs)

    sub = aria.StreamingSubscriptionConfig()
    sub.subscriber_data_type = resolve_types(a.data_type)
    # latest-frame-only: descarta backlog (caso G de la matriz)
    try:
        sub.message_queue_size[DT["rgb"]] = 1
    except Exception:
        pass
    client_.subscription_config = sub
    client_.subscribe()

    t0 = time.monotonic()
    time.sleep(a.seconds)
    dt = time.monotonic() - t0

    dms = obs.delta_ms()
    verdict = ""
    if dms is not None:
        emitted_fps = 1000.0 / dms if dms > 0 else 0
        if dms > 70:
            verdict = f"  >> SDK EMITE ~{emitted_fps:.0f} FPS (delta {dms:.0f}ms): el perfil ya topa, NO hay tuning local para 30"
        elif dms < 45:
            verdict = f"  >> SDK EMITE ~{emitted_fps:.0f} FPS (delta {dms:.0f}ms) pero contamos {obs.n/dt:.0f}: MARGEN, el cuello es el callback/copia"
        else:
            verdict = f"  >> SDK emite ~{emitted_fps:.0f} FPS (delta {dms:.0f}ms): zona intermedia, repetir con 30s+"

    print(f"[probe] RESULT data_type={a.data_type} profile={a.profile} "
          f"img_frames={obs.n} imu_cb={obs.imu} seconds={dt:.1f} "
          f"img_fps={obs.n/dt:.2f} imu_rate={obs.imu/dt:.1f} "
          f"sdk_delta_ms={dms if dms is not None else 'n/a'}", flush=True)
    if verdict:
        print(f"[probe] PASO0{verdict}", flush=True)

    try:
        client_.unsubscribe()
        mgr.stop_streaming()
        device.disconnect()
    except Exception as e:
        print(f"[probe] cleanup warn: {e}", flush=True)

if __name__ == "__main__":
    main()
PYEOF

echo "[probe] count-only observer escrito en $PROBE_PY"

# --- Lanzar bajo FEX, capturar PID para perf/strace ---
ARGS="--interface $INTERFACE --profile $PROFILE --data-type $DATA_TYPE --seconds $SECONDS_RUN"
[ "$INTERFACE" = "wifi" ] && [ -n "$DEVICE_IP" ] && ARGS="$ARGS --device-ip $DEVICE_IP"

# FEX JIT naming para que perf vea regiones traducidas en vez de ruido
export FEX_LIBRARYJITNAMING="${FEX_LIBRARYJITNAMING:-1}"

echo "[probe] lanzando: PYTHONNOUSERSITE=1 FEXBash -c \"python3 $PROBE_PY $ARGS\""
echo

PYTHONNOUSERSITE=1 FEXBash -c "python3 $PROBE_PY $ARGS" 2>&1 | tee "$LOG_DIR/probe.log" &
PROBE_PID=$!

if [ "${PROBE_PROFILE:-0}" = "1" ]; then
  echo "[probe] PROBE_PROFILE=1 → adjuntando perf+strace al árbol de $PROBE_PID"
  # esperar a que arranque el streaming antes de medir steady-state
  sleep 8
  REAL_PID="$(pgrep -P "$PROBE_PID" -n python3 2>/dev/null || echo "$PROBE_PID")"
  echo "[probe] midiendo PID=$REAL_PID durante $((SECONDS_RUN - 12))s"
  sudo perf stat -d -d -d -p "$REAL_PID" -- sleep "$((SECONDS_RUN - 12))" \
      2>"$LOG_DIR/perf_stat.txt" || echo "[probe] perf stat falló (¿sudo? ¿perf instalado?)"
  sudo strace -f -c -p "$REAL_PID" -o "$LOG_DIR/strace_summary.txt" \
      -- sleep 5 2>/dev/null || echo "[probe] strace falló"
fi

wait "$PROBE_PID" || true

echo
echo "=== FIN. Resultados en $LOG_DIR ==="
grep -h "RESULT" "$LOG_DIR/probe.log" 2>/dev/null || echo "(sin línea RESULT — revisar probe.log)"
echo
echo "Lectura (matriz de rendimiento):"
echo "  - resolución menor sube img_fps → cuello = bytes/fragmentación (atacar §5.C)"
echo "  - rgb topa en ~11 incluso count-only → cuello = perfil/FastDDS/FEX (ir a subscriber DDS nativo §5.E)"
echo "  - imu rate absurdo en CPU pese a bytes mínimos → coste por-callback (§6bis-C)"
echo
echo "TODO siguiente: observer C++ x86 count-only (caso I/J) para separar C++→Python/GIL del transporte."
