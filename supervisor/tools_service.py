"""Supervisor tool service: the API the Warehouse Supervisor agent calls as its tools.

Code enforces, the model explains. Detectors, event identity, order risk, ticket states and the
commissioning gate are all computed here. The agent reads the results and decides what to say,
what to escalate and where to route. Reads the fleet sim over HTTP. Standard library only.
"""
import json
import math
import os
import random
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import bandit

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.environ.get("SIM_URL", "http://127.0.0.1:3001")  # sim_bridge.py: Isaac Sim (or fleet_runner.py) pushes JSON there
PORT = int(os.environ.get("TOOLS_PORT", "8090"))
BIND = os.environ.get("TOOLS_BIND", "127.0.0.1,172.18.0.1")  # loopback + the OpenShell sandbox network gateway
DATA = os.environ.get("TOOLS_DATA", os.path.join(HERE, "data"))
SCHEDULER_WEBHOOK = os.environ.get("SCHEDULER_WEBHOOK_URL", "")    # Megha's Scheduler: woken on supervisor changes
# Our agent is woken through OpenClaw's POST /hooks/agent (forwarded to the host at 127.0.0.1:18789).
SUPERVISOR_WEBHOOK = os.environ.get("SUPERVISOR_WEBHOOK_URL", "")
SUPERVISOR_DELIVER_TO = os.environ.get("SUPERVISOR_DELIVER_TO", "")  # e.g. channel:<discord channel id>; empty = don't post
SUPERVISOR_THINKING = os.environ.get("SUPERVISOR_THINKING", "low")


def _read_secret(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


SUPERVISOR_WEBHOOK_TOKEN = os.environ.get("SUPERVISOR_WEBHOOK_TOKEN") or _read_secret(os.path.join(DATA, "hooks.token"))
# When code wakes the agent on its own: a digest of new problems, and a periodic fleet brief.
AUTOPILOT = {"triage": os.environ.get("TRIAGE", "1") == "1", "triage_every_s": int(os.environ.get("TRIAGE_EVERY_S", "30")),
             "min_age_s": 5, "cooldown_s": 300, "brief_every_s": int(os.environ.get("BRIEF_EVERY_S", "0"))}

# Thresholds live in code, never in the prompt. Durations are simulator seconds when the sim reports its clock.
BATTERY_LOW, TEMP_LIMIT, TEMP_CRITICAL, LOW_STOCK = 15, 80, 88, 30
STUCK_S, YIELD_GRACE_S, MOVE_EPS = 2.0, 15.0, 0.05      # a robot queuing for traffic is not stuck until 15 s
OFF_TRACK_S, OFF_LANE_M, DEADLOCK_RADIUS = 3.0, 0.8, 1.5
REPEAT_ERRORS, REPEAT_WINDOW_S = 3, 300
# throughput drop: deliveries in the last RECENT_S vs the zone's own earlier rate, as a Poisson tail test
RECENT_S, BASELINE_S, MIN_BASELINE_S, MIN_BASELINE_COUNT, DROP_RATIO, DROP_P = 180, 600, 180, 4, 0.5, 0.02
CLEAR_GRACE_S, POLL_S, STALE_S = 2.0, 0.125, 3.0
RATE_WINDOW_S, DEFAULT_RATE_PER_MIN = 300, float(os.environ.get("DEFAULT_RATE_PER_MIN", "1.5"))
ORDERS_PER_ZONE, ORDER_MINUTES, DUE_SLACK = 2, (3, 6), (1.5, 2.2)

DRAWER = "libero_sim/open_the_middle_drawer_of_the_cabinet"
SOUP = "libero_sim/LIVING_ROOM_SCENE2_put_both_the_alphabet_soup_and_the_tomato_sauce_in_the_basket"
GR00T_PORTS = {"libero_10": 5555, "libero_goal": 5556}
CATALOG = {
    "drawer_skill": {"backend": "gr00t", "env": DRAWER, "arms": GR00T_PORTS, "current": "libero_10",
                     "what": "open the middle drawer (GR00T N1.7 policies in LIBERO)"},
    "soup_sauce_skill": {"backend": "gr00t", "env": SOUP, "arms": GR00T_PORTS, "current": "libero_10",
                         "what": "put the soup and sauce in the basket (GR00T N1.7 policies in LIBERO)"},
    "route_b": {"backend": "sim", "arms": {"current_route": 0.05, "proposed_route": 0.95}, "current": "current_route",
                "what": "proposed aisle route (simulated trials, no GPU)"},
}

TICKET_FLOW = {
    "open": {"acknowledged", "escalated", "failed", "resolved"},
    "acknowledged": {"rescheduled", "resolved", "escalated", "failed"},
    "rescheduled": {"resolved", "escalated", "failed"},
    "failed": {"escalated"},
    "escalated": {"acknowledged", "resolved"},
    "resolved": set(),
}
PRIORITIES = ("low", "medium", "high", "critical")
ACTORS = ("supervisor", "scheduler", "human")

LOCK = threading.Lock()
STATE = {"started": time.time(), "sim_ok": False, "sim_error": "not polled yet", "sim_tick": None, "sim_picks": 0,
         "sim_deliveries": None, "last_poll": None, "last_sample": None, "layout": None, "metres": False,
         "inventory": None, "inventory_ok": False, "stale_s": None, "sim_restarts": 0}
ROBOTS, TRACKS = {}, {}
DELIVERIES = deque()     # (sim t, zone)
ZONE_CONDITIONS = {}     # recomputed once a sim second
ROBOT_CONDITIONS = {}    # recomputed every sample; kept as they were while the feed is stale
SIM_EVENTS = []
CLOCK = {"sim": None, "wall": None, "first": None}  # newest sample's sim time, when it arrived, first sim time seen


def now_s():
    return time.time()


def hhmmss(t):
    return time.strftime("%H:%M:%S", time.localtime(t)) if t else None


def sim_now():
    """The clock for robot behaviour: the simulator's own seconds when it reports them (Isaac can run slower or
    faster than real time, and its pushes can be sparse), otherwise wall-clock seconds."""
    return CLOCK["sim"] if CLOCK["sim"] is not None else now_s()


def sim_first():
    return CLOCK["first"] if CLOCK["first"] is not None else STATE["started"]


def sim_hhmmss(ts):
    """Time of day for a sim timestamp, for people."""
    if ts is None:
        return None
    return hhmmss(ts if CLOCK["sim"] is None else CLOCK["wall"] + (ts - CLOCK["sim"]))


def sim_get(path, timeout=2):
    with urllib.request.urlopen(SIM + path, timeout=timeout) as r:
        return json.load(r)


def sim_post(path, body):
    req = urllib.request.Request(SIM + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


def load_json(name, default):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(name, obj):
    os.makedirs(DATA, exist_ok=True)
    tmp = os.path.join(DATA, name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, os.path.join(DATA, name))


# ---- robot tracking: pose, velocity, timeline ----

def carried_sku(c):
    """Isaac/fleet telemetry carries a SKU string; the older sim sent {sku, qty}."""
    return c if isinstance(c, str) else (c or {}).get("sku")


def yield_excused(row):
    """Queuing behind traffic is normal on a one-way floor; only a long wait counts as stuck."""
    return bool(row.get("yielding")) and (row.get("waiting_s") or 0) < YIELD_GRACE_S


class Track:
    def __init__(self):
        self.pos = self.t = self.goal = self.task = self.carrying = self.pick_goal = self.from_bin = None
        self.heading, self.speed, self.moving, self.error = 0.0, 0.0, False, ""
        self.last_move, self.stopped_noted = sim_now(), False
        self.dist = deque(maxlen=120)        # (sim t, distance to goal)
        self.history = deque(maxlen=40)      # timeline that answers "why did robot N stop?"
        self.err_onsets = deque(maxlen=50)   # (sim t, error code)

    def note(self, ts, what):
        self.history.append({"t": sim_hhmmss(ts), "what": what})


def needs_to_move(row, tr):
    return row["task"] in ("pick", "carry", "charge") and bool(tr.dist) and tr.dist[-1][1] > 0.5


def update_track(row, ts):
    tr = TRACKS.setdefault(row["id"], Track())
    x, y = row["x"], row["y"]
    goal = tuple(row["goal"]) if row.get("goal") else None
    moved = 0.0 if tr.pos is None else math.hypot(x - tr.pos[0], y - tr.pos[1])
    if row.get("vel") is not None:  # the simulator reports speed and heading itself
        tr.speed = float(row["vel"])
        if row.get("theta") is not None:
            tr.heading = float(row["theta"])
        tr.moving = tr.speed > MOVE_EPS or moved > MOVE_EPS
    else:                           # derive them from successive samples
        tr.speed = moved / max(1e-3, ts - tr.t) if tr.t is not None else 0.0
        if moved > MOVE_EPS:
            tr.heading = math.degrees(math.atan2(y - tr.pos[1], x - tr.pos[0])) % 360
        tr.moving = moved > MOVE_EPS
    if tr.moving or tr.pos is None:
        if tr.stopped_noted:
            tr.note(ts, f"moving again at ({x:.1f}, {y:.1f})")
        tr.last_move, tr.stopped_noted = ts, False
    tr.pos, tr.t = (x, y), ts
    if goal != tr.goal:  # a new leg: time parked before it (charging, dwelling) is not a stop
        tr.dist.clear()
        tr.goal, tr.last_move, tr.stopped_noted = goal, ts, False
    tr.dist.append((ts, abs(goal[0] - x) + abs(goal[1] - y) if goal else 0.0))
    if row["task"] != tr.task:
        if tr.task is not None:
            tr.note(ts, f"task {tr.task} -> {row['task']}" + (f", goal {list(goal)}" if goal else "")
                    + (f", job {row['job_id']}" if row.get("job_id") else ""))
        if row["task"] == "carry":
            tr.from_bin = tr.pick_goal
        tr.task = row["task"]
    if row["task"] == "pick":
        tr.pick_goal = goal
    err = row.get("error") or ""
    if err != tr.error:
        if err:
            tr.err_onsets.append((ts, err))
            tr.note(ts, f"error {err}")
        else:
            tr.note(ts, f"error {tr.error} cleared")
        tr.error = err
    carrying = carried_sku(row.get("carrying"))
    if tr.carrying and carrying != tr.carrying:  # the previous load left the robot: delivered
        DELIVERIES.append((ts, row["zone"]))
        progress_orders(row["zone"], ts)
    tr.carrying = carrying
    if needs_to_move(row, tr) and not tr.stopped_noted and ts - tr.last_move >= STUCK_S and not yield_excused(row):
        why = " waiting for traffic" if row.get("yielding") else ""
        tr.note(tr.last_move, f"stopped at ({x:.1f}, {y:.1f}) during {row['task']}"
                + (f" toward {list(goal)}" if goal else "") + why)
        tr.stopped_noted = True


def where(x, y):
    """Where on the floor a position is, from the lane map (Isaac/fleet layout); None without one."""
    lay = STATE["layout"] or {}
    aisles = [(a.get("zone"), a["x"]) for a in lay.get("aisles", []) if isinstance(a, dict) and "x" in a]
    lanes = lay.get("cross_lanes") or {}
    if not aisles or "bottom_y" not in lanes or "top_y" not in lanes:
        return None
    if lanes["bottom_y"] + 1.0 < y < lanes["top_y"] - 1.0:
        z, ax = min(aisles, key=lambda a: abs(x - a[1]))
        return f"aisle {z} (x={ax:g} m)" if abs(x - ax) <= OFF_LANE_M else "between aisles"
    if abs(y - lanes["bottom_y"]) <= 1.0:
        return "bottom cross lane"
    if abs(y - lanes["top_y"]) <= 1.0:
        return "top cross lane"
    return "pack stations" if y < lanes["bottom_y"] else "charge docks"


def off_track(row, tr, ts):
    lay = STATE["layout"] or {}
    aisles = [a["x"] for a in lay.get("aisles", []) if isinstance(a, dict) and "x" in a]
    lanes = lay.get("cross_lanes") or {}
    if aisles and "bottom_y" in lanes and "top_y" in lanes:
        # Lane network known (Isaac/fleet): between the cross lanes a robot must be on an aisle centre line.
        if lanes["bottom_y"] + 1.0 < row["y"] < lanes["top_y"] - 1.0:
            return min(abs(row["x"] - ax) for ax in aisles) > OFF_LANE_M
        return False
    # No lane map: moving, but no closer to its goal than OFF_TRACK_S seconds ago.
    if not tr.moving or not tr.dist or ts - tr.dist[0][0] < OFF_TRACK_S:
        return False
    then = next(d for t0, d in reversed(tr.dist) if ts - t0 >= OFF_TRACK_S)
    return tr.dist[-1][1] >= then - 0.5


def robot_view(rid):
    row, tr = ROBOTS[rid], TRACKS[rid]
    sku, action = carried_sku(row.get("carrying")), None
    if row["task"] == "carry" and sku:
        qty = row["carrying"].get("qty", 1) if isinstance(row.get("carrying"), dict) else 1
        action = {"action": "deliver", "sku": sku, "qty": qty,
                  "from_bin": list(tr.from_bin) if tr.from_bin else None, "to_station": row.get("goal")}
    elif row["task"] == "pick" and row.get("goal"):
        b = bin_info(tuple(row["goal"]))
        action = {"action": "pick", "bin": row["goal"], "sku": b and b["sku"], "qty": 1,
                  "bin_qty": b and b["qty"], "system_qty": b and b["expected"]}
    view = {"id": rid, "zone": row["zone"], "pose": {"x": row["x"], "y": row["y"], "theta_deg": round(tr.heading)},
            "velocity": round(tr.speed, 2), "task": row["task"], "job_id": row.get("job_id"), "goal": row.get("goal"),
            "battery_pct": row["battery"], "motor_temp_c": row["temp"], "error_code": row.get("error") or None,
            "inventory_action": action, "moving": tr.moving, "location": where(row["x"], row["y"])}
    if "yielding" in row:
        view.update(waiting_for_traffic=bool(row["yielding"]), waiting_s=row.get("waiting_s"))
    return view


# ---- inventory and orders ----

def bin_info(cell):
    inv = STATE["inventory"]
    if not inv:
        return None
    key = tuple(round(float(v), 2) for v in cell)
    for z in inv["zones"]:
        for b in z["bins"]:
            if tuple(round(float(v), 2) for v in b["bin"]) == key:
                return b
    return None


ORDERS = []
ORDER_SEQ = [1000]


def zone_rate_per_min(zone, window=RATE_WINDOW_S):
    """Deliveries per sim minute in the zone over the last `window` seconds; None until a minute of data."""
    ts = sim_now()
    span = min(window, ts - sim_first())
    if span < 60:
        return None
    return sum(1 for t0, z in DELIVERIES if z == zone and ts - t0 <= span) * 60 / span


def new_order(zone, ts):
    ORDER_SEQ[0] += 1
    rate = zone_rate_per_min(zone) or DEFAULT_RATE_PER_MIN
    units = max(3, round(rate * random.uniform(*ORDER_MINUTES)))
    backlog = sum(o["units"] - o["picked"] for o in ORDERS if o["zone"] == zone and o["status"] == "open")
    due = ts + 60 * (backlog + units) / rate * random.uniform(*DUE_SLACK)
    return {"id": f"O-{ORDER_SEQ[0]}", "zone": zone, "units": units, "picked": 0, "created": ts, "due": due,
            "status": "open"}


def ensure_orders(ts):
    for z in sorted({row["zone"] for row in ROBOTS.values()}):
        while sum(1 for o in ORDERS if o["zone"] == z and o["status"] == "open") < ORDERS_PER_ZONE:
            ORDERS.append(new_order(z, ts))


def progress_orders(zone, ts):
    open_ = sorted((o for o in ORDERS if o["zone"] == zone and o["status"] == "open"), key=lambda o: o["due"])
    if open_:
        o = open_[0]
        o["picked"] += 1
        if o["picked"] >= o["units"]:
            o.update(status="shipped", shipped_at=ts, late=ts > o["due"])


def order_views(zone=None):
    """Open orders with code-computed risk: each zone works its orders in due order at its recent rate."""
    ts, out = sim_now(), []
    for z in sorted({o["zone"] for o in ORDERS}):
        if zone is not None and z != zone:
            continue
        measured = zone_rate_per_min(z)
        rate = measured if measured is not None else DEFAULT_RATE_PER_MIN
        cum = 0
        for o in sorted((o for o in ORDERS if o["zone"] == z and o["status"] == "open"), key=lambda o: o["due"]):
            cum += o["units"] - o["picked"]
            finish = ts + cum / rate * 60 if rate > 0 else None
            out.append({"id": o["id"], "zone": z, "units": o["units"], "picked": o["picked"],
                        "due": sim_hhmmss(o["due"]), "minutes_to_due": round((o["due"] - ts) / 60, 1),
                        "zone_rate_per_min": round(rate, 2), "rate_is_estimate": measured is None,
                        "projected_finish": sim_hhmmss(finish) if finish else "never at the current rate",
                        "at_risk": finish is None or finish > o["due"], "late": ts > o["due"]})
    return out


# ---- detectors: code flags, the LLM narrates ----

class Events:
    """Stable ids for problems. first_seen/last_seen are wall-clock, for people and the clearing grace."""
    def __init__(self):
        self.seq, self.active, self.recent = 0, {}, deque(maxlen=400)

    def observe(self, conditions, t):
        for key, c in conditions.items():
            ev = self.active.get(key)
            if ev is None:
                self.seq += 1
                ev = {"id": f"E-{self.seq:04d}", "status": "active", "first_seen": t, "key": repr(key)}
                self.active[key] = ev
                self.recent.append(ev)
                for rid in c.get("robot_ids") or ([c["robot_id"]] if c.get("robot_id") is not None else []):
                    if rid in TRACKS:
                        TRACKS[rid].note(sim_now(), f"event {ev['id']} {c['type']}: {c['detail']}")
            ev.update(c)
            ev["last_seen"] = t
        for key, ev in list(self.active.items()):
            if key not in conditions and t - ev["last_seen"] >= CLEAR_GRACE_S:
                ev.update(status="cleared", cleared_at=t)
                del self.active[key]
                for rid in ev.get("robot_ids") or ([ev["robot_id"]] if ev.get("robot_id") is not None else []):
                    if rid in TRACKS:
                        TRACKS[rid].note(sim_now(), f"event {ev['id']} {ev['type']} cleared")

    def view(self, ev, t):
        out = {k: v for k, v in ev.items() if k not in ("first_seen", "last_seen", "cleared_at", "key")}
        out.update(first_seen=hhmmss(ev["first_seen"]), active_for_s=round((ev.get("cleared_at") or t) - ev["first_seen"]))
        if ev.get("cleared_at"):
            out["cleared_at"] = hhmmss(ev["cleared_at"])
        if ev.get("zone") is not None:
            out["orders_in_zone"] = order_views(ev["zone"])[:3]
        tickets = [tk["id"] for tk in TICKETS.values() if tk.get("event_id") == ev["id"]]
        if tickets:
            out["tickets"] = tickets
        return out


EVENTS = Events()


def clusters(ids, radius):
    groups, seen = [], set()
    for i in ids:
        if i in seen:
            continue
        group, todo = [], [i]
        seen.add(i)
        while todo:
            a = todo.pop()
            group.append(a)
            for b in ids:
                if b not in seen and math.dist(TRACKS[a].pos, TRACKS[b].pos) <= radius:
                    seen.add(b)
                    todo.append(b)
        groups.append(group)
    return groups


def known_cause(row, target):
    if row.get("error") == "DRIVE_FAULT":
        return "the robot reports DRIVE_FAULT: it cannot move"
    if row.get("yielding"):
        return f"waiting for traffic for {row.get('waiting_s', 0):.0f}s, longer than the {YIELD_GRACE_S:.0f}s allowed"
    if target and target["qty"] == 0:
        return "sent to pick from an empty bin" + (f" the system still counts at {target['expected']}"
                                                   if target["expected"] else "")
    return None


def robot_conditions(ts):
    cond, stuck = {}, []
    for rid, row in ROBOTS.items():
        tr, base = TRACKS[rid], {"robot_id": rid, "zone": row["zone"]}
        if row["battery"] < BATTERY_LOW:
            cond[("low_battery", rid)] = dict(base, type="low_battery", severity="critical",
                                             detail=f"battery {row['battery']}% (limit {BATTERY_LOW}%)",
                                             facts={"battery_pct": row["battery"], "heading_to_charger": row["task"] == "charge"})
        hot_limit = TEMP_LIMIT - 5 if ("overheat", rid) in EVENTS.active else TEMP_LIMIT  # hysteresis: no flapping
        if row["temp"] > hot_limit:
            cond[("overheat", rid)] = dict(base, type="overheat",
                                          severity="critical" if row["temp"] >= TEMP_CRITICAL else "warning",
                                          detail=f"motor {row['temp']}C (limit {TEMP_LIMIT}C)",
                                          facts={"motor_temp_c": row["temp"], "limit_c": TEMP_LIMIT, "task": row["task"]})
        if needs_to_move(row, tr):
            still = ts - tr.last_move
            if still >= STUCK_S and not yield_excused(row):
                stuck.append(rid)
                target = bin_info(tuple(row["goal"])) if row["task"] == "pick" and row.get("goal") else None
                facts = {"stopped_for_s": round(still), "task": row["task"], "job_id": row.get("job_id"),
                         "goal": row.get("goal"), "position": [row["x"], row["y"]], "location": where(row["x"], row["y"]),
                         "carrying": carried_sku(row.get("carrying")), "error_code": row.get("error") or None}
                if "yielding" in row:
                    facts.update(waiting_for_traffic=bool(row["yielding"]), waiting_s=row.get("waiting_s"))
                if target:
                    facts["target_bin"] = target
                cause = known_cause(row, target)
                if cause:
                    facts["known_cause"] = cause
                cond[("stuck", rid)] = dict(base, type="stuck",
                                           severity="critical" if row.get("error") == "DRIVE_FAULT" else "warning",
                                           detail=f"no movement for {still:.0f}s during {row['task']}"
                                                  + (f" ({cause})" if cause else ""), facts=facts)
            elif off_track(row, tr, ts):
                cond[("off_trajectory", rid)] = dict(base, type="off_trajectory", severity="warning",
                                                    detail="off its lane" if STATE["metres"] else
                                                    f"moving but no closer to its goal for {OFF_TRACK_S:.0f}s",
                                                    facts={"task": row["task"], "goal": row.get("goal"),
                                                           "position": [row["x"], row["y"]]})
        if row.get("error") == "DRIVE_FAULT" and ("stuck", rid) not in cond:
            # A robot that breaks down while parked (charging, at a station, dwelling at a bin) is never "stuck":
            # it has nowhere to go yet. Flag the fault itself, so it is seen before the robot blocks a dock or slot.
            at = where(row["x"], row["y"])
            cond[("drive_fault", rid)] = dict(base, type="drive_fault", severity="critical",
                                             detail=f"reports DRIVE_FAULT while {row['task']}" + (f" at {at}" if at else "")
                                                    + "; it cannot move when its next job starts",
                                             facts={"task": row["task"], "job_id": row.get("job_id"), "goal": row.get("goal"),
                                                    "position": [row["x"], row["y"]], "location": at,
                                                    "carrying": carried_sku(row.get("carrying")), "battery_pct": row["battery"]})
        errs = [code for t0, code in tr.err_onsets if ts - t0 <= REPEAT_WINDOW_S]
        if len(errs) >= REPEAT_ERRORS:
            cond[("repeated_errors", rid)] = dict(base, type="repeated_errors", severity="warning",
                                                 detail=f"{len(errs)} errors in the last {REPEAT_WINDOW_S // 60} min: "
                                                        + ", ".join(sorted(set(errs))),
                                                 facts={"errors": [h for h in tr.history if h["what"].startswith("error ")
                                                                   and not h["what"].endswith("cleared")][-len(errs):]})
    for group in clusters(stuck, DEADLOCK_RADIUS):
        if len(group) >= 2:
            # One jam, one event: keyed on the robot that stopped first (usually the cause), so the event keeps
            # its id while the queue behind it grows. Queued robots' own stuck events are symptoms of it.
            ids = sorted(group)
            first = min(group, key=lambda i: TRACKS[i].last_move)
            queued = [i for i in ids if i != first and ROBOTS[i].get("yielding")]
            for i in queued:
                cond.pop(("stuck", i), None)
            cause = known_cause(ROBOTS[first], None) or "cause not reported"
            at = where(ROBOTS[first]["x"], ROBOTS[first]["y"])
            cond[("deadlock", first)] = {
                "type": "deadlock", "robot_id": None, "robot_ids": ids, "zone": ROBOTS[first]["zone"], "severity": "critical",
                "detail": f"{len(ids)} robots stopped within {DEADLOCK_RADIUS} m of each other"
                          + (f" in {at}" if at else "") + f"; robot {first} stopped first ({cause})",
                "facts": {"first_stopped": first, "first_stopped_cause": cause, "queued_behind": queued, "location": at,
                          "positions": {i: [ROBOTS[i]["x"], ROBOTS[i]["y"]] for i in ids}}}
    return cond


def poisson_cdf(k, lam):
    term = total = math.exp(-lam)
    for i in range(1, k + 1):
        term *= lam / i
        total += term
    return total


def zone_conditions(ts):
    cond = {}
    zones = sorted({row["zone"] for row in ROBOTS.values()})
    base_span = min(BASELINE_S, ts - sim_first() - RECENT_S)
    for z in zones:
        recent = sum(1 for t0, zz in DELIVERIES if zz == z and ts - t0 <= RECENT_S)
        if base_span >= MIN_BASELINE_S:
            n = sum(1 for t0, zz in DELIVERIES if zz == z and RECENT_S < ts - t0 <= RECENT_S + base_span)
            expected = n * RECENT_S / base_span
            p = poisson_cdf(recent, expected)
            if n >= MIN_BASELINE_COUNT and recent < DROP_RATIO * expected and p < DROP_P:
                cond[("throughput_drop", z)] = {
                    "type": "throughput_drop", "robot_id": None, "zone": z, "severity": "warning",
                    "detail": f"zone {z}: {recent} deliveries in the last {RECENT_S // 60} min, "
                              f"{expected:.1f} expected from its earlier rate",
                    "facts": {"recent_deliveries": recent, "expected": round(expected, 1), "window_min": RECENT_S / 60,
                              "p_value": round(p, 4), "robots_in_zone": [r for r, row in ROBOTS.items() if row["zone"] == z]}}
    inv = STATE["inventory"]
    if inv:
        for zone in inv["zones"]:
            z = zone["zone"]
            off = [b for b in zone["bins"] if b["qty"] != b["expected"]]
            if off:
                missing = sum(b["expected"] - b["qty"] for b in off)
                found_empty = sorted(rid for rid, tr in TRACKS.items() if ROBOTS.get(rid, {}).get("zone") == z
                                     and any(code == "BIN_EMPTY" and ts - t0 <= 600 for t0, code in tr.err_onsets))
                cond[("inventory_mismatch", z)] = {
                    "type": "inventory_mismatch", "robot_id": None, "zone": z, "severity": "warning",
                    "detail": f"zone {z}: {len(off)} bins differ from the system count ({missing:+d} units missing)",
                    "facts": {"bins": off[:10], "units_missing": missing, "robots_that_found_empty_bins": found_empty}}
            if zone["total"] < LOW_STOCK:
                cond[("low_stock", z)] = {"type": "low_stock", "robot_id": None, "zone": z, "severity": "warning",
                                         "detail": f"zone {z} stock {zone['total']} units (limit {LOW_STOCK})",
                                         "facts": {"units": zone["total"]}}
    else:  # older sims only report low stock as their own events
        for e in SIM_EVENTS:
            if e.get("type") == "inventory":
                cond[("low_stock", e["zone"])] = {"type": "low_stock", "robot_id": None, "zone": e["zone"],
                                                 "severity": e.get("sev", "warning"), "detail": e.get("detail", ""),
                                                 "facts": {}}
    return cond


def stale_condition(stale_s):
    return {("telemetry_stale", "sim"): {
        "type": "telemetry_stale", "robot_id": None, "zone": None, "severity": "critical",
        "detail": f"no new data from the simulator for {stale_s:.0f}s; robot problems are frozen at the last known state",
        "facts": {"stale_s": round(stale_s, 1), "source": SIM}}}


def poll_loop():
    last_zone, last_inv = -1e9, 0.0
    while True:
        tw = now_s()
        try:
            try:
                tel = sim_get("/api/telemetry")
            except urllib.error.HTTPError as e:
                if e.code == 503:
                    raise RuntimeError("waiting for the first push from the simulator") from None
                raise
            if STATE["layout"] is None:
                lay = sim_get("/api/layout")
                STATE.update(layout=lay, metres=lay.get("units") == "metres")
            inv = None
            if tw - last_inv >= 1.0:
                last_inv = tw
                try:
                    inv = sim_get("/api/inventory")
                except Exception:
                    inv = False
            with LOCK:
                STATE.update(last_poll=tw, stale_s=tel.get("stale_s"))
                if inv is not None:
                    STATE["inventory"], STATE["inventory_ok"] = (inv or None), bool(inv)
                fresh = tel.get("t") != STATE["sim_tick"]
                silent = (tel.get("stale_s") or 0) if tel.get("stale_s") is not None else (
                    0 if fresh or STATE["last_sample"] is None else tw - STATE["last_sample"])
                if silent > STALE_S:
                    # The source answers, but nothing new arrives: don't read frozen positions as stuck robots.
                    STATE.update(sim_ok=False, sim_error=f"no new data from the simulator for {silent:.0f}s")
                    EVENTS.observe({**ROBOT_CONDITIONS, **ZONE_CONDITIONS, **stale_condition(silent)}, tw)
                elif fresh:
                    if STATE["metres"] and tel.get("t") is not None:
                        ts = float(tel["t"])
                        if CLOCK["sim"] is not None and ts < CLOCK["sim"] - 1.0:  # the simulator restarted
                            TRACKS.clear()
                            DELIVERIES.clear()
                            STATE["sim_restarts"] += 1
                            CLOCK["first"] = None
                        CLOCK.update(sim=ts, wall=tw)
                        if CLOCK["first"] is None:
                            CLOCK["first"] = ts
                    ts = sim_now()
                    STATE.update(sim_ok=True, sim_error="", sim_tick=tel.get("t"), sim_picks=tel.get("picks", 0),
                                 sim_deliveries=tel.get("deliveries"), last_sample=tw)
                    SIM_EVENTS[:] = tel.get("events", [])
                    for row in tel["robots"]:
                        ROBOTS[row["id"]] = row
                        update_track(row, ts)
                    while DELIVERIES and ts - DELIVERIES[0][0] > RECENT_S + BASELINE_S:
                        DELIVERIES.popleft()
                    ensure_orders(ts)
                    if ts - last_zone >= 1.0 or ts < last_zone:
                        last_zone = ts
                        ZONE_CONDITIONS.clear()
                        ZONE_CONDITIONS.update(zone_conditions(ts))
                    ROBOT_CONDITIONS.clear()
                    ROBOT_CONDITIONS.update(robot_conditions(ts))
                    EVENTS.observe({**ROBOT_CONDITIONS, **ZONE_CONDITIONS}, tw)
        except Exception as e:
            with LOCK:
                STATE.update(sim_ok=False, sim_error=str(e))
        time.sleep(max(0.0, POLL_S - (now_s() - tw)))


# ---- tickets: the contract with the Scheduler agent ----

TICKETS = {t["id"]: t for t in load_json("tickets.json", [])}
# Event ids must not repeat across restarts, because tickets refer to them.
EVENTS.seq = max([int(t["event_id"][2:]) for t in TICKETS.values() if t.get("event_id")] + [0])
OUTBOX = deque(maxlen=200)
NOTIFY_Q = deque()
NOTIFY_EVENT = threading.Event()


def ticket_line(tk):
    who = f"robot {tk['robot_id']}" if tk.get("robot_id") is not None else f"zone {tk['zone']}"
    return (f"TICKET {tk['id']} {tk['status']} | {tk['event_type']} | {who} | {tk['reason']} | "
            f"priority {tk['priority']} | needed by {tk.get('needed_by') or 'n/a'}")


def notify(target, message, payload, name="supervisor-tools"):
    url = {"scheduler": SCHEDULER_WEBHOOK, "supervisor": SUPERVISOR_WEBHOOK}[target]
    NOTIFY_Q.append((target, url, message, payload, name))
    NOTIFY_EVENT.set()


WAKE_SEQ = [0]


def agent_hook_body(message, name):
    """OpenClaw POST /hooks/agent: runs our agent with this message; posts its reply if a channel is set.
    Each wake gets its own session: OpenClaw drops a hook run whose session is still busy with the last one."""
    WAKE_SEQ[0] += 1
    body = {"message": message, "name": name, "agentId": "main", "wakeMode": "now",
            "sessionKey": f"hook:{name}:{int(STATE['started'])}-{WAKE_SEQ[0]}",
            "thinking": SUPERVISOR_THINKING, "timeoutSeconds": 600, "deliver": bool(SUPERVISOR_DELIVER_TO)}
    if SUPERVISOR_DELIVER_TO:
        body.update(channel="discord", to=SUPERVISOR_DELIVER_TO)
    return body


def notify_loop():
    while True:
        NOTIFY_EVENT.wait()
        NOTIFY_EVENT.clear()
        while NOTIFY_Q:
            target, url, message, payload, name = NOTIFY_Q.popleft()
            entry = {"at": hhmmss(now_s()), "to": target, "name": name, "message": message, "result": "no webhook configured"}
            if url:
                headers = {"Content-Type": "application/json"}
                if target == "supervisor":
                    body = agent_hook_body(message, name)
                    if SUPERVISOR_WEBHOOK_TOKEN:
                        headers["Authorization"] = f"Bearer {SUPERVISOR_WEBHOOK_TOKEN}"
                else:
                    body = {"message": message, **payload}
                try:
                    req = urllib.request.Request(url, data=json.dumps(body, default=str).encode(), headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=10) as r:
                        entry["result"] = f"HTTP {r.status}"
                except Exception as e:
                    entry["result"] = f"failed: {e}"
            OUTBOX.append(entry)


def open_ticket(body):
    for k in ("event_type", "reason", "priority"):
        if not body.get(k):
            raise ValueError(f"'{k}' is required")
    if body["priority"] not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    if body.get("robot_id") is None and body.get("zone") is None:
        raise ValueError("give robot_id or zone")
    ev = None
    if body.get("event_id"):
        ev = next((e for e in EVENTS.recent if e["id"] == body["event_id"]), None)
        if ev is None:
            raise ValueError(f"unknown event_id {body['event_id']}")
    zone = body.get("zone") if body.get("zone") is not None else (ev or {}).get("zone")
    robot = body.get("robot_id") if body.get("robot_id") is not None else (ev or {}).get("robot_id")
    for tk in TICKETS.values():  # one live ticket per problem, even across event ids
        same_event = ev is not None and tk.get("event_id") == ev["id"]
        same_subject = (tk["event_type"] == body["event_type"] and tk.get("robot_id") == robot
                        and (robot is not None or tk.get("zone") == zone))
        if tk["status"] != "resolved" and (same_event or same_subject):
            return tk, True
    t = now_s()
    evidence = dict(body.get("evidence") or {})
    if ev is not None:  # evidence from code, not from the model
        evidence["event"] = EVENTS.view(ev, t)
    tk = {"id": f"T-{len(TICKETS) + 1}", "event_type": body["event_type"], "robot_id": body.get("robot_id"),
          "zone": body.get("zone") if body.get("zone") is not None else (ev or {}).get("zone"),
          "reason": body["reason"], "evidence": evidence, "needed_by": body.get("needed_by"),
          "priority": body["priority"], "status": "open", "event_id": ev and ev["id"], "assignee": None, "eta": None,
          "created_at": hhmmss(t), "updated_at": hhmmss(t),
          "history": [{"at": hhmmss(t), "actor": "supervisor", "status": "open", "note": body["reason"]}]}
    TICKETS[tk["id"]] = tk
    save_json("tickets.json", list(TICKETS.values()))
    notify("scheduler", ticket_line(tk), {"ticket": tk})
    return tk, False


def patch_ticket(tid, body):
    tk = TICKETS.get(tid)
    if tk is None:
        raise KeyError(tid)
    actor = body.get("actor", "supervisor")
    if actor not in ACTORS:
        raise ValueError(f"actor must be one of {ACTORS}")
    status = body.get("status")
    if status and status != tk["status"] and status not in TICKET_FLOW[tk["status"]]:
        raise ValueError(f"illegal transition {tk['status']} -> {status}; allowed: {sorted(TICKET_FLOW[tk['status']]) or 'none'}")
    if body.get("priority") and body["priority"] not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    for k in ("assignee", "eta", "needed_by", "priority"):
        if body.get(k) is not None:
            tk[k] = body[k]
    if status:
        tk["status"] = status
    t = now_s()
    tk["updated_at"] = hhmmss(t)
    tk["history"].append({"at": hhmmss(t), "actor": actor, "status": tk["status"], "note": body.get("note", "")})
    save_json("tickets.json", list(TICKETS.values()))
    line = ticket_line(tk) + (f" | {actor}: {body['note']}" if body.get("note") else "")
    if actor == "supervisor":
        notify("scheduler", line, {"ticket": tk})
    else:
        notify("supervisor", f"TICKET UPDATE from the {actor}: {line}\n"
               "Follow your standing orders (warehouse-supervisor skill). If it is resolved, check the robot or zone with "
               "the tools and confirm in one line. If it failed, decide whether a human must step in, and if so PATCH it "
               "to escalated with a note. Otherwise acknowledge it in one line.", {"ticket": tk}, name="ticket-update")
    return tk


# ---- commissioning: the trust gate ----

TRUST = load_json("trust.json", {})
JOBS = {}


def trust_view(subject):
    spec = CATALOG[subject]
    tr = TRUST.get(subject) or {"policy": spec["current"], "successes": 0, "trials": 0}
    p = bandit.p_at_least(tr["successes"], tr["trials"] - tr["successes"])
    return {"subject": subject, "what": spec["what"], "policy": tr["policy"], "successes": tr["successes"],
            "trials": tr["trials"], "p_rate_at_least_80": round(p, 3), "trusted": p >= bandit.CONF,
            "rule": "trusted only when P(success rate >= 0.8) >= 0.95",
            "last_commissioned": tr.get("at"), "last_job": tr.get("job")}


def save_clips(job, arm, container_dir):
    dest = os.path.join(DATA, "clips", job["id"], arm)
    os.makedirs(dest, exist_ok=True)
    subprocess.run(["docker", "cp", f"cell:{container_dir}/.", dest], capture_output=True, timeout=120)
    with LOCK:
        job["clips"][arm] = sorted(f"/clips/{job['id']}/{arm}/{f}" for f in os.listdir(dest) if f.endswith(".mp4"))


def run_job(job):
    spec = CATALOG[job["subject"]]
    arms = list(spec["arms"])
    if spec["backend"] == "gr00t":
        runner = bandit.gr00t_runner(spec["env"], spec["arms"], on_clips=lambda arm, d: save_clips(job, arm, d))
    else:
        runner = bandit.sim_runner(spec["arms"])

    def on_batch(entry):
        with LOCK:
            job["batches"].append(entry)

    try:
        res = bandit.commission(arms, runner, on_batch=on_batch)
        t = now_s()
        with LOCK:
            s, f = res["successes"], res["failures"]
            best = res.get("policy") or max(arms, key=lambda a: bandit.p_at_least(s[a], f[a]))
            if res["decision"] == "pass":
                TRUST[job["subject"]] = {"policy": best, "successes": s[best], "trials": s[best] + f[best],
                                         "at": hhmmss(t), "job": job["id"]}
            else:  # the gate stays closed; keep the current policy with no credit
                TRUST[job["subject"]] = {"policy": spec["current"], "successes": 0, "trials": 0, "at": hhmmss(t),
                                         "job": job["id"]}
            save_json("trust.json", TRUST)
            job.update(status="done", decision=res["decision"], policy=res.get("policy"), finished=hhmmss(t),
                       trials=sum(s.values()) + sum(f.values()), counts=res["log"][-1]["counts"] if res["log"] else {},
                       trust=trust_view(job["subject"]))
            msg = (f"COMMISSION {job['id']} {job['subject']}: {res['decision']} after {job['trials']} trials | "
                   + (res["log"][-1]["line"] if res["log"] else "") + f" | gate {'OPEN' if job['trust']['trusted'] else 'CLOSED'}\n"
                   "Report it in at most three lines (warehouse-supervisor skill): the decision, the evidence (counts and "
                   "probability) and what it means for putting it into service. Call it trusted only if /trust says so.")
    except Exception as e:
        with LOCK:
            job.update(status="error", error=str(e), finished=hhmmss(now_s()))
            msg = (f"COMMISSION {job['id']} {job['subject']} failed to run: {e}\n"
                   "Say plainly that commissioning could not run and the gate stays closed.")
    notify("supervisor", msg, {"commission": job_view(job)}, name="commission")


def job_view(job):
    return {k: v for k, v in job.items()}


def start_commission(subject):
    if subject not in CATALOG:
        raise ValueError(f"unknown subject '{subject}'; known: {sorted(CATALOG)}")
    if any(j["status"] == "running" for j in JOBS.values()):
        raise RuntimeError("a commissioning run is already in progress")
    used = [int(j[2:]) for j in JOBS] + [int(tr["job"][2:]) for tr in TRUST.values() if tr.get("job")]
    job = {"id": f"C-{max(used + [0]) + 1}", "subject": subject, "backend": CATALOG[subject]["backend"], "status": "running",
           "started": hhmmss(now_s()), "batches": [], "clips": {}}
    JOBS[job["id"]] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return job


# ---- metrics ----

def metrics(t):
    rows = list(ROBOTS.values())
    faulted = {e["robot_id"] for e in EVENTS.active.values() if e.get("robot_id") is not None}
    for e in EVENTS.active.values():
        faulted.update(e.get("robot_ids") or [])
    moving = [r for r in rows if TRACKS[r["id"]].moving]
    zones = sorted({r["zone"] for r in rows})
    orders = order_views()
    shipped = [o for o in ORDERS if o["status"] == "shipped"]
    by_type = {}
    for e in EVENTS.active.values():
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    ts = sim_now()
    return {
        "time": hhmmss(t), "service_uptime_min": round((t - STATE["started"]) / 60, 1),
        "sim": {"ok": STATE["sim_ok"], "sim_time_s": CLOCK["sim"], "stale_s": STATE["stale_s"], "source": SIM},
        "robots": {"total": len(rows), "moving": len(moving), "working": sum(r["task"] in ("pick", "carry") for r in rows),
                   "charging": sum(r["task"] == "charge" for r in rows),
                   "waiting_for_traffic": sum(bool(r.get("yielding")) for r in rows),
                   "with_active_problems": len(faulted)},
        "throughput": {"deliveries_last_min": sum(1 for t0, _ in DELIVERIES if ts - t0 <= 60),
                       "per_zone_per_min": {z: round(zone_rate_per_min(z) or 0, 2) for z in zones},
                       "sim_picks_total": STATE["sim_picks"], "sim_deliveries_total": STATE["sim_deliveries"]},
        "orders": {"open": len(orders), "at_risk": [o["id"] for o in orders if o["at_risk"]],
                   "late": [o["id"] for o in orders if o["late"]], "shipped": len(shipped),
                   "shipped_late": sum(1 for o in shipped if o.get("late"))},
        "events": {"active_by_type": by_type, "raised_total": EVENTS.seq},
        "tickets": {s: sum(1 for tk in TICKETS.values() if tk["status"] == s) for s in TICKET_FLOW},
        "commissioning": [{"id": j["id"], "subject": j["subject"], "status": j["status"], "decision": j.get("decision")}
                          for j in JOBS.values()][-5:],
    }


# ---- autopilot: when code wakes the agent ----

TRIAGED = {}  # event key -> when it was last handed to the agent


def triage_message(events, t):
    groups = {}
    for e in events:
        groups.setdefault(e["type"], []).append(e)
    lines = []
    for typ, evs in groups.items():
        if len(evs) >= 4:
            robots = sorted({r for e in evs for r in (e.get("robot_ids") or ([e["robot_id"]] if e.get("robot_id") is not None else []))})
            lines.append(f"- {typ} x{len(evs)}, one systemic issue: robots {robots}, events {evs[0]['id']} to {evs[-1]['id']}")
            continue
        for e in evs:
            who = (f"robot {e['robot_id']}" if e.get("robot_id") is not None
                   else f"robots {e['robot_ids']}" if e.get("robot_ids") else f"zone {e['zone']}")
            lines.append(f"- {e['id']} {typ} | {who} | zone {e.get('zone')} | {e['severity']} | {e['detail']}")
    return (f"NEW PROBLEMS flagged by code at {hhmmss(t)}:\n" + "\n".join(lines) + "\n"
            "Triage them per your Warehouse Supervisor rules: GET /events once for their facts and orders_in_zone, then "
            "POST /tickets for each one that needs the Scheduler (pass event_id; urgency comes from the orders) and log the "
            "rest. Reply with one line per problem: ticketed (T-n, priority, why) or logged (why no ticket).")


def autopilot_loop():
    last_triage, last_brief = 0.0, now_s()
    while True:
        time.sleep(2)
        t = now_s()
        with LOCK:
            if not SUPERVISOR_WEBHOOK:
                continue
            if AUTOPILOT["triage"] and t - last_triage >= AUTOPILOT["triage_every_s"]:
                ticketed = {tk.get("event_id") for tk in TICKETS.values()}
                fresh = [e for e in EVENTS.active.values()
                         if e["id"] not in ticketed and t - e["first_seen"] >= AUTOPILOT["min_age_s"]
                         and t - TRIAGED.get(e["key"], -1e9) >= AUTOPILOT["cooldown_s"]]
                if fresh:
                    for e in fresh:
                        TRIAGED[e["key"]] = t
                    last_triage = t
                    notify("supervisor", triage_message(fresh, t), {}, name="triage")
            if AUTOPILOT["brief_every_s"] and t - last_brief >= AUTOPILOT["brief_every_s"]:
                last_brief = t
                notify("supervisor", f"HEARTBEAT {hhmmss(t)}: post the fleet brief now (warehouse-supervisor skill): robots "
                       "active, deliveries per minute, orders at risk, open problems and tickets. Five lines at most.",
                       {}, name="brief")


# ---- HTTP ----

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p]
        t = now_s()
        try:
            if u.path == "/health":
                return self.send(200, health(t))
            if u.path == "/tools.json":
                with open(os.path.join(HERE, "tools.json"), "rb") as f:
                    return self.send(200, f.read())
            if parts and parts[0] == "clips":
                path = os.path.normpath(os.path.join(DATA, *parts))
                if not path.startswith(os.path.join(DATA, "clips")) or not os.path.isfile(path):
                    return self.send(404, {"error": "no such clip"})
                with open(path, "rb") as f:
                    return self.send(200, f.read(), "video/mp4")
            with LOCK:
                if u.path == "/fleet":
                    return self.send(200, fleet(t))
                if len(parts) == 2 and parts[0] == "robots":
                    return self.send(*robot_detail(int(parts[1]), t))
                if u.path == "/events":
                    evs = list(EVENTS.recent) if q.get("status") == "all" else list(EVENTS.active.values())
                    return self.send(200, {"events": [EVENTS.view(e, t) for e in evs][-100:]})
                if parts and parts[0] == "zones":
                    return self.send(*zone_inventory(int(parts[1]) if len(parts) > 1 else None, t))
                if u.path == "/orders":
                    return self.send(200, {"orders": order_views()})
                if u.path == "/metrics":
                    return self.send(200, metrics(t))
                if u.path == "/tickets":
                    tks = [tk for tk in TICKETS.values() if not q.get("status") or tk["status"] == q["status"]]
                    return self.send(200, {"tickets": tks})
                if len(parts) == 2 and parts[0] == "tickets":
                    tk = TICKETS.get(parts[1])
                    return self.send(200, tk) if tk else self.send(404, {"error": f"no ticket {parts[1]}"})
                if u.path == "/commission":
                    return self.send(200, {"jobs": [job_view(j) for j in JOBS.values()],
                                           "subjects": {s: CATALOG[s]["what"] for s in CATALOG}})
                if u.path == "/commission/latest":
                    j = list(JOBS.values())[-1] if JOBS else None
                    return self.send(200, job_view(j) if j else {"status": "none"})
                if len(parts) == 2 and parts[0] == "commission":
                    j = JOBS.get(parts[1])
                    return self.send(200, job_view(j)) if j else self.send(404, {"error": f"no job {parts[1]}"})
                if u.path == "/trust":
                    return self.send(200, {"subjects": [trust_view(s) for s in CATALOG]})
                if u.path == "/notifications":
                    return self.send(200, {"outbox": list(OUTBOX)})
                if u.path == "/autopilot":
                    return self.send(200, {**AUTOPILOT, "wakes_agent": bool(SUPERVISOR_WEBHOOK),
                                           "posts_to": SUPERVISOR_DELIVER_TO or None, "problems_handed_over": len(TRIAGED)})
            self.send(404, {"error": "not found"})
        except (ValueError, KeyError) as e:
            self.send(400, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self.body()
            if u.path == "/tickets":
                with LOCK:
                    tk, dup = open_ticket(body)
                return self.send(200 if dup else 201, {"ticket": tk, "duplicate": dup})
            if u.path == "/commission":
                with LOCK:
                    job = start_commission(body.get("subject", ""))
                return self.send(202, job_view(job))
            if u.path == "/demo/inject":  # presenter controls, not agent tools
                return self.send(200, sim_post("/api/inject", body))
            if u.path == "/demo/clear":
                return self.send(200, sim_post("/api/clear", {}))
            if u.path == "/demo/shrink":
                return self.send(200, sim_post("/api/shrink", body))
            if u.path == "/demo/autopilot":
                with LOCK:
                    for k in ("triage", "triage_every_s", "brief_every_s", "cooldown_s"):
                        if k in body:
                            AUTOPILOT[k] = bool(body[k]) if k == "triage" else int(body[k])
                    return self.send(200, dict(AUTOPILOT))
            self.send(404, {"error": "not found"})
        except (ValueError, KeyError) as e:
            self.send(400, {"error": str(e)})
        except RuntimeError as e:
            self.send(409, {"error": str(e)})

    def do_PATCH(self):
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        try:
            if len(parts) == 2 and parts[0] == "tickets":
                with LOCK:
                    return self.send(200, patch_ticket(parts[1], self.body()))
            self.send(404, {"error": "not found"})
        except KeyError as e:
            self.send(404, {"error": f"no ticket {e}"})
        except ValueError as e:
            self.send(400, {"error": str(e)})


def health(t):
    def port_open(port):
        import socket
        with socket.socket() as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", port)) == 0
    try:  # sim_bridge.py reports who is pushing (Isaac Sim or fleet_runner) and how fresh it is
        source = sim_get("/health", timeout=1)
    except Exception:
        source = None
    return {"sim": {"ok": STATE["sim_ok"], "error": STATE["sim_error"] or None, "url": SIM, "sim_time_s": STATE["sim_tick"],
                    "stale_s": STATE["stale_s"], "units": "metres" if STATE["metres"] else "grid cells",
                    "inventory_endpoint": STATE["inventory_ok"], "restarts_seen": STATE["sim_restarts"],
                    "bridge": source and {k: source.get(k) for k in ("source", "pushes", "stale_s", "pending_commands")}},
            "gr00t": {arm: port_open(p) for arm, p in GR00T_PORTS.items()},
            "webhooks": {"scheduler": bool(SCHEDULER_WEBHOOK), "supervisor": bool(SUPERVISOR_WEBHOOK)},
            "time": hhmmss(t)}


SEVERITY_RANK = {"critical": 0, "warning": 1}
FLEET_EVENT_DETAIL = 12


def fleet(t):
    views = [robot_view(rid) for rid in sorted(ROBOTS)]
    m = metrics(t)
    active = sorted(EVENTS.active.values(), key=lambda e: (SEVERITY_RANK.get(e["severity"], 2), -e["first_seen"]))
    groups = {}
    for e in active:
        g = groups.setdefault(e["type"], {"type": e["type"], "count": 0, "event_ids": [], "robot_ids": [], "zones": []})
        g["count"] += 1
        g["event_ids"].append(e["id"])
        for rid in e.get("robot_ids") or ([e["robot_id"]] if e.get("robot_id") is not None else []):
            g["robot_ids"].append(rid)
        if e.get("zone") is not None and e["zone"] not in g["zones"]:
            g["zones"].append(e["zone"])
    # Widespread types are summarised so one systemic problem doesn't crowd out the rest.
    detailed, per_type = [], {}
    for e in active:
        per_type[e["type"]] = per_type.get(e["type"], 0) + 1
        if per_type[e["type"]] <= 3 and len(detailed) < FLEET_EVENT_DETAIL:
            detailed.append(EVENTS.view(e, t))
    units = ({"position": "metres", "velocity": "m/s", "theta": "degrees"} if STATE["metres"]
             else {"position": "grid cells", "velocity": "cells/s", "theta": "degrees"})
    return {"time": hhmmss(t), "sim_ok": STATE["sim_ok"], "sim_error": STATE["sim_error"] or None, "units": units,
            "summary": m["robots"],
            "event_summary": list(groups.values()),
            "active_events": detailed,
            "note": "active_events shows up to 3 of each type; event_summary lists every id. GET /events for all details.",
            "robots": views}


def robot_detail(rid, t):
    if rid not in ROBOTS:
        return 404, {"error": f"no robot {rid}; robots are {sorted(ROBOTS)}"}
    tr = TRACKS[rid]
    evs = [EVENTS.view(e, t) for e in EVENTS.recent if e.get("robot_id") == rid or rid in (e.get("robot_ids") or [])]
    return 200, {**robot_view(rid), "stopped_for_s": round(sim_now() - tr.last_move) if needs_to_move(ROBOTS[rid], tr) else 0,
                 "timeline": list(tr.history)[-20:], "events": evs[-10:],
                 "tickets": [tk for tk in TICKETS.values() if tk.get("robot_id") == rid][-5:]}


def zone_inventory(zone, t):
    inv = STATE["inventory"]
    if not inv:
        return 503, {"error": "the sim does not expose bin inventory (/api/inventory)"}
    zones = [z for z in inv["zones"] if zone is None or z["zone"] == zone]
    if not zones:
        return 404, {"error": f"no zone {zone}; zones are {[z['zone'] for z in inv['zones']]}"}
    out = []
    for z in zones:
        off = [b for b in z["bins"] if b["qty"] != b["expected"]]
        out.append({"zone": z["zone"], "units": z["total"], "system_units": z["expected_total"], "bins": len(z["bins"]),
                    "empty_bins": sum(1 for b in z["bins"] if b["qty"] == 0), "mismatched_bins": off[:20],
                    "open_orders": order_views(z["zone"])} | ({"all_bins": z["bins"]} if zone is not None else {}))
    return 200, {"zones": out}


def main():
    os.makedirs(DATA, exist_ok=True)
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=notify_loop, daemon=True).start()
    threading.Thread(target=autopilot_loop, daemon=True).start()
    servers = []
    for addr in [a.strip() for a in BIND.split(",") if a.strip()]:
        try:
            servers.append(ThreadingHTTPServer((addr, PORT), Handler))
            print(f"tool service listening on {addr}:{PORT}", flush=True)
        except OSError as e:
            print(f"could not bind {addr}:{PORT}: {e}", flush=True)
    if not servers:
        raise SystemExit("no address to listen on")
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
