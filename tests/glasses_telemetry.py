"""Poll Aria glasses battery + skin temperature on a fixed interval.

Runs alongside a streaming session to correlate the SLAM decay with glasses
power/thermal state. Connects once via DeviceClient (does NOT start streaming),
then samples status every --interval seconds and prints a timestamped row.

The DeviceClient status channel is independent from the streaming session, so
this can run concurrently with the receiver without disturbing the DDS streams.

Usage (under FEX, the SDK is x86_64):
    PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 -u tests/glasses_telemetry.py --interval 10 --seconds 300"
"""

import argparse
import time

import aria.sdk as aria


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--seconds", type=float, default=300.0)
    args = parser.parse_args()

    dc = aria.DeviceClient()
    cfg = aria.DeviceClientConfig()
    dc.set_client_config(cfg)
    dev = dc.connect()

    start = time.monotonic()
    print(f"[telemetry] t_s battery_pct charging skin_temp_C thermal_mitig", flush=True)
    while True:
        t = time.monotonic() - start
        if t >= args.seconds:
            break
        try:
            st = dev.status
            batt = getattr(st, "battery_level", -1)
            chg = getattr(st, "charging", "?")
            temp = getattr(st, "skin_temp_celsius", -1.0)
            mit = getattr(st, "thermal_mitigation_triggered", "?")
            print(f"[telemetry] {t:6.0f} {batt:>3} {chg} {temp:6.2f} {mit}", flush=True)
        except Exception as e:
            print(f"[telemetry] {t:6.0f} ERROR {type(e).__name__}: {e}", flush=True)
        # busy-tolerant sleep
        nxt = start + (int(t // args.interval) + 1) * args.interval
        while time.monotonic() < nxt and time.monotonic() - start < args.seconds:
            time.sleep(0.2)


if __name__ == "__main__":
    main()
