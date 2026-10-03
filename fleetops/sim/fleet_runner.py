#!/usr/bin/env python3
"""Runs the fleet headless in real time and pushes it to the sim bridge. No Isaac needed.

Use it to develop the agents when the Isaac laptop is not connected, or run it on the GB10 itself:
  BRIDGE_TOKEN=... python3 fleet_runner.py --bridge http://127.0.0.1:3001
The Isaac sim (warehouse_live.py --bridge ...) pushes the same data while rendering it.
Needs numpy (fleet.py).
"""
import argparse, os, time
from fleet import Fleet
from pusher import Pusher

ap = argparse.ArgumentParser()
ap.add_argument("--bridge", default="http://127.0.0.1:3001")
ap.add_argument("--robots", type=int, default=20)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--speedup", type=float, default=1.0, help="sim seconds per wall second")
args = ap.parse_args()

fleet = Fleet(args.robots, seed=args.seed)
push = Pusher(fleet, args.bridge, os.environ.get("BRIDGE_TOKEN", ""), source="fleet_runner")
dt, t0, last_log = 1 / 30.0, time.time(), 0.0
print(f"fleet_runner: {args.robots} robots -> {args.bridge}", flush=True)
while True:
    for c in push.apply():
        print(f"[{fleet.t:7.1f}s] command {c}", flush=True)
    fleet.step(dt)
    push.tick()
    if time.time() - last_log > 10:
        last_log = time.time()
        print(f"[{fleet.t:7.1f}s] picks {fleet.picks} deliveries {fleet.deliveries} | pushes ok {push.sent} "
              f"failed {push.failed} {push.last_error}", flush=True)
    sleep = t0 + fleet.t / args.speedup - time.time()   # hold real time
    if sleep > 0:
        time.sleep(sleep)
