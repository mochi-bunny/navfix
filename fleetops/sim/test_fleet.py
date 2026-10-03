"""Headless checks of fleet.py, in seconds, without Isaac Sim.

  python3 test_fleet.py [seconds] [seed]

Reports overlaps, throughput, inventory consistency, and how many "stuck" alarms Thiago's tool
service would raise. Its rule (tools_service.py): a robot with a pick/carry/charge task, more than
0.5 m (Manhattan) from its goal, that has not moved for STUCK_S = 2 s. With the yielding patch a
robot that is queuing for traffic is only flagged after YIELD_GRACE_S.
"""
import sys
from fleet import Fleet

STUCK_S, YIELD_GRACE_S = 2.0, 15.0
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 600
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
dt = 1 / 30.0
f = Fleet(20, seed=seed)

closest, overlap_steps, worst_wait, worst_who = 1e9, 0, 0.0, None
still = {b.i: 0.0 for b in f.bots}
alarms_raw = alarms_patched = 0            # stuck episodes flagged without / with the yielding patch
flag_raw, flag_patched = set(), set()
for k in range(int(secs / dt)):
    f.step(dt)
    sp = f.min_spacing()
    closest = min(closest, sp)
    overlap_steps += sp < 0.6
    for b in f.bots:
        r = b.telem()
        g = r["goal"]
        far = g is not None and abs(g[0] - r["x"]) + abs(g[1] - r["y"]) > 0.5
        needs = r["task"] in ("pick", "carry", "charge") and far
        still[b.i] = still[b.i] + dt if (needs and r["vel"] < 0.05) else 0.0
        if b.waited > worst_wait:
            worst_wait, worst_who = b.waited, (b.i, b.task, r["x"], r["y"], round(k * dt))
        raw = still[b.i] >= STUCK_S
        patched = raw and (not r["yielding"] or r["waiting_s"] >= YIELD_GRACE_S)
        alarms_raw += raw and b.i not in flag_raw
        alarms_patched += patched and b.i not in flag_patched
        flag_raw = (flag_raw | {b.i}) if raw else (flag_raw - {b.i})
        flag_patched = (flag_patched | {b.i}) if patched else (flag_patched - {b.i})

inv = f.inventory()
mismatched = sum(1 for z in inv["zones"] for b in z["bins"] if b["qty"] != b["expected"])
stuck_end = [b.i for b in f.bots if b.waited > 10]
print(f"seed {seed} | {secs:.0f}s | closest {closest:.2f} m | overlap steps {overlap_steps} | "
      f"picks {f.picks} | deliveries {f.deliveries} | longest wait {worst_wait:.1f}s {worst_who} | "
      f"stuck alarms {alarms_raw} unpatched / {alarms_patched} patched | "
      f"mismatched bins {mismatched} | stuck at end {stuck_end}")
