#!/usr/bin/env python3
"""Runs the warehouse fleet for N simulated minutes and writes one plain JSON log for the agents.

  python3 make_fleet_log.py [--minutes 5] [--seed 7] [--out logs/fleet_log_5min.json] [--no-faults]

The file is a single JSON object (no JSON Lines, no encoding), so it loads with json.load() or can
be pasted into an LLM prompt as-is:
  meta       what was run, units, the layout, what each event type means, the scripted demo faults
  summary    totals, per robot, per zone, event counts
  events     every discrete thing that happened, in time order
  snapshots  the whole fleet every SNAPSHOT_S seconds
"""
import argparse, json, os, time
from collections import Counter, defaultdict
from fleet import Fleet

SNAPSHOT_S = 5.0
FAULTS = [  # (sim second, command): the same commands the tool service's /demo/* endpoints send
    (45.0,  {"kind": "shrink", "zone": 2, "units": 6}),
    (90.0,  {"kind": "inject", "robot": 7, "fault": "stuck"}),
    (130.0, {"kind": "clear"}),
    (160.0, {"kind": "inject", "robot": 3, "fault": "overheat"}),
    (200.0, {"kind": "clear"}),
]
EVENT_TYPES = {
    "job_assigned": "a robot got a job: task pick (bin, sku, system_qty) or charge (dock)",
    "picked": "unit taken from the bin; qty_left is physical, system_qty_left is the system count",
    "station_assigned": "loaded robot got a pack-station slot",
    "stations_full": "every pack-station slot was taken; the robot drives the holding loop and retries",
    "delivered": "unit handed over at a pack station; ends a pick job",
    "charging_started": "robot parked on a dock and started charging",
    "charging_done": "battery back to 95%+; ends a charge job",
    "waiting": "robot has been stopped by traffic for 2 s (normal queuing, not a fault)",
    "resumed": "robot moving again after a logged wait; waited_s is how long",
    "error_raised": "robot error code set: DRIVE_FAULT, OVERHEAT, LOW_BATTERY or BIN_EMPTY",
    "error_cleared": "robot error code cleared",
    "bin_count_corrected": "robot found a bin empty that the system counted as stocked; system count set to 0",
    "restocked": "replenishment refilled bins that ran out normally",
    "idle": "robot had nothing to pick in its zone",
    "demo_command": "a scripted or presenter demo fault (inject / clear / shrink) and its result",
}

ap = argparse.ArgumentParser()
ap.add_argument("--minutes", type=float, default=5.0)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--robots", type=int, default=20)
ap.add_argument("--out", default="logs/fleet_log_5min.json")
ap.add_argument("--no-faults", action="store_true")
args = ap.parse_args()

f = Fleet(args.robots, seed=args.seed)
faults = [] if args.no_faults else list(FAULTS)
dt, end = 1 / 30.0, args.minutes * 60.0
snapshots, next_snap = [], 0.0


def zone_stock():
    return [{"zone": z["zone"], "total": z["total"], "expected_total": z["expected_total"],
             "bins_short": sum(1 for b in z["bins"] if b["qty"] != b["expected"])}
            for z in f.inventory()["zones"]]


def snapshot():
    robots = []
    for r in f.telemetry()["robots"]:
        robots.append({"id": r["id"], "zone": r["zone"], "x": r["x"], "y": r["y"], "theta": r["theta"],
                       "vel": r["vel"], "task": r["task"], "job_id": r["job_id"], "goal": r["goal"],
                       "battery": r["battery"], "temp": r["temp"], "error": r["error"],
                       "carrying": r["carrying"]["sku"] if r["carrying"] else None,
                       "yielding": r["yielding"], "waiting_s": r["waiting_s"]})
    return {"t": round(f.t, 1), "picks": f.picks, "deliveries": f.deliveries,
            "robots": robots, "zone_stock": zone_stock()}


wall0 = time.time()
while f.t < end - 1e-9:
    while faults and f.t >= faults[0][0]:
        f.command(dict(faults.pop(0)[1], source="scripted"))
    if f.t >= next_snap - 1e-9:
        snapshots.append(snapshot())
        next_snap += SNAPSHOT_S
    f.step(dt)
snapshots.append(snapshot())

# ---------------- summary ----------------
per_robot = defaultdict(lambda: Counter())
for e in f.events:
    if e["robot"] is not None and e["type"] != "demo_command":
        per_robot[e["robot"]][e["type"]] += 1
wait_s = defaultdict(float)
for e in f.events:
    if e["type"] == "resumed":
        wait_s[e["robot"]] += e["waited_s"]
robots = []
for b in f.bots:
    c = per_robot[b.i]
    robots.append({"id": b.i, "zone": b.zone, "deliveries": c["delivered"], "picks": c["picked"],
                   "charges": c["charging_done"], "errors_raised": c["error_raised"],
                   "logged_waits": c["waiting"], "logged_wait_s": round(wait_s[b.i], 1),
                   "battery_end": round(b.battery), "task_end": b.task})
zones = []
for zs in zone_stock():
    z = zs["zone"]
    zones.append(dict(zs, robots=[b.i for b in f.bots if b.zone == z],
                      deliveries=sum(1 for e in f.events if e["type"] == "delivered" and e["zone"] == z)))

log = {
    "meta": {
        "what": "FleetOps warehouse fleet log: 20 Nova Carter AMRs, one-way traffic, pick -> pack station -> charge",
        "generated_by": "make_fleet_log.py (fleet.py, the same logic the Isaac Sim scene renders)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sim_seconds": round(f.t, 1), "robots": args.robots, "seed": args.seed,
        "units": {"position": "metres", "theta": "degrees", "vel": "m/s", "temp": "C", "battery": "%",
                  "t": "seconds since the start of the run"},
        "layout": f.layout(),
        "zones": "zone N = aisle N; each robot picks only from its own zone",
        "error_codes": {"DRIVE_FAULT": "robot cannot move", "OVERHEAT": "motor above 80 C",
                        "LOW_BATTERY": "battery below 15%", "BIN_EMPTY": "arrived at a bin the system counts as stocked, found it empty"},
        "event_types": EVENT_TYPES,
        "scripted_faults": [] if args.no_faults else [{"t": t, **c} for t, c in FAULTS],
        "snapshot_every_s": SNAPSHOT_S,
    },
    "summary": {
        "picks": f.picks, "deliveries": f.deliveries,
        "event_counts": dict(Counter(e["type"] for e in f.events).most_common()),
        "robots": robots, "zones": zones,
    },
    "events": f.events,
    "snapshots": snapshots,
}
os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
with open(args.out, "w") as fh:
    json.dump(log, fh, indent=2)
print(f"wrote {args.out}: {len(f.events)} events, {len(snapshots)} snapshots, "
      f"{os.path.getsize(args.out) / 1024:.0f} KB, {time.time() - wall0:.1f}s to simulate {f.t:.0f}s")
