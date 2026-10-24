#!/usr/bin/env python3
"""Replay a run's schedule.json on a fresh fleet (no LLM) and check it reproduces the run exactly.

  python replay_check.py runs/agents_local
Exit 0 only if picks, deliveries and the final telemetry hash match the metrics the pipeline recorded.
"""
import hashlib, json, sys

from fleet import Fleet

DT = 1 / 30.0


def replay(run_dir):
    m = json.load(open(f"{run_dir}/metrics.json"))
    sched = json.load(open(f"{run_dir}/schedule.json"))
    fleet = Fleet(20, seed=m["seed"])
    n = int(m["sim_seconds"] / DT)
    i = 0
    for step in range(n):
        while i < len(sched) and sched[i]["step"] <= step:
            fleet.command(sched[i]["cmd"])
            i += 1
        fleet.step(DT)
    return fleet, m


def tel_hash(fleet):
    return hashlib.md5(json.dumps(fleet.telemetry(), sort_keys=True).encode()).hexdigest()


if __name__ == "__main__":
    fleet, m = replay(sys.argv[1])
    saved = [json.loads(l) for l in open(f"{sys.argv[1]}/fleet_events.jsonl")]
    same_events = json.loads(json.dumps(fleet.events)) == saved
    print(f"fleet event log: {len(saved)} saved, {len(fleet.events)} replayed, identical={same_events}")
    ok = fleet.picks == m["picks"] and fleet.deliveries == m["deliveries"] and same_events
    print(f"replay: picks {fleet.picks} (run {m['picks']}), deliveries {fleet.deliveries} (run {m['deliveries']}), "
          f"telemetry md5 {tel_hash(fleet)}")
    print("REPLAY MATCHES" if ok else "REPLAY DIVERGED")
    sys.exit(0 if ok else 1)
