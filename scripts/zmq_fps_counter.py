"""Native ZMQ PULL counter: measures real delivered FPS from the receiver.

Run natively (ARM64) while the receiver streams:

    .venv/bin/python3 scripts/zmq_fps_counter.py [seconds]

Prints FPS every 5s and a final summary. Acts as the consumer the
receiver's PUSH socket needs — without it, the receiver drops every
frame with zmq.Again and its own FPS counter stays at zero.
"""
import sys
import time

import zmq

ENDPOINT = "tcp://127.0.0.1:5555"
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 60

ctx = zmq.Context()
sock = ctx.socket(zmq.PULL)
sock.setsockopt(zmq.RCVHWM, 10)
sock.connect(ENDPOINT)

poller = zmq.Poller()
poller.register(sock, zmq.POLLIN)

count = 0
t_first = None
t_last_report = time.monotonic()
deadline = time.monotonic() + DURATION + 90  # margen para el arranque del streaming

while time.monotonic() < deadline:
    if not dict(poller.poll(timeout=1000)):
        continue
    sock.recv_multipart(copy=False)
    now = time.monotonic()
    if t_first is None:
        t_first = now
        deadline = now + DURATION  # la medición empieza en el primer frame
        print(f"[counter] primer frame recibido, midiendo {DURATION}s...", flush=True)
    count += 1
    if now - t_last_report >= 5:
        fps = count / (now - t_first)
        print(f"[counter] {fps:.1f} FPS ({count} frames)", flush=True)
        t_last_report = now

if t_first is None:
    print("[counter] RESULTADO: 0 frames recibidos", flush=True)
else:
    elapsed = time.monotonic() - t_first
    print(f"[counter] RESULTADO: {count / elapsed:.1f} FPS medios ({count} frames en {elapsed:.0f}s)", flush=True)
