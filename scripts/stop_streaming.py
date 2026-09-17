"""Stop any stale streaming session on the Aria glasses.

A receiver killed with SIGKILL leaves the streaming session active on the
device; the next start_streaming() then fails with error 940. Run this
(under FEX) to clear it:

    PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 scripts/stop_streaming.py"
"""
import sys

import aria.sdk as aria


def main() -> int:
    client = aria.DeviceClient()
    client.set_client_config(aria.DeviceClientConfig())
    try:
        device = client.connect()
    except Exception as e:
        print(f"[stop_streaming] no device: {e}")
        return 1
    try:
        device.streaming_manager.stop_streaming()
        print("[stop_streaming] stop sent")
    except Exception as e:
        # No active session is fine — that's the desired end state
        print(f"[stop_streaming] {e}")
    finally:
        client.disconnect(device)
    return 0


if __name__ == "__main__":
    sys.exit(main())
