"""Fleet logic for the FleetOps warehouse, independent of Isaac Sim.

Traffic: one-way circulation, the way real AMR floors run. The bottom cross lane flows east, the
top cross lane flows west, and the aisles alternate north/south, so the floor is one loop with no
head-on traffic. Junctions and bay entrances are locked: one robot inside at a time.

Work: each robot belongs to a zone (one aisle). It picks one unit from a shelf bin in its zone,
carries it to a pack station below the bottom lane, and charges at the docks above the top lane
when its battery runs low. Bins hold a physical count (qty) and a system count (expected); they
only differ when stock goes missing (shrink), which is the "inventory misses something" beat.

The data shapes match the contract in Thiago's tool service README (~/hack/src/app on the GB10):
telemetry(), layout() and inventory() are served by sim_bridge.py, and inject/clear/shrink are the
demo faults. The Isaac sim imports this and renders the poses; test_fleet.py runs it headless.
Positions are metres on the warehouse floor (x across aisles, y along them).
"""
import heapq, math, random

AIS, PITCH, LEN = 6, 5.0, 28.0
centers = [a * PITCH for a in range(AIS)]
XMIN, XMAX = centers[0], centers[-1]
YBOT, YTOP = -LEN / 2 - 1.5, LEN / 2 + 1.5         # cross lanes joining the aisles
NORTHBOUND = {a: a % 2 == 1 for a in range(AIS)}    # 0 S, 1 N, 2 S, 3 N, 4 S, 5 N: a closed loop
BAY = 2.4                                           # bays sit this far off the cross lanes
PICK_X = centers[:3]                                # pack stations below the eastbound bottom lane
DOCK_X = centers[3:]                                # charge docks above the westbound top lane
# parking slots per bay, downstream of its junction so they are reachable one-way:
PICK_DX = (0.0, 1.1, 2.2)                           # bottom lane flows east
DOCK_DX = (0.0, -1.1, -2.2)                         # top lane flows west
EXIT_GAP = 1.1                                      # bay exit lane runs this far behind the slots
REJOIN = 3.3                                        # exit lanes rejoin the main lane this far downstream
BIN_Y = [round(-12.0 + 1.0 * k, 2) for k in range(25)]   # pick points along each aisle
RACK_SIDE = 1.9                                     # shelf faces sit this far either side of a lane

SAFE_GAP = 1.15      # clearance kept to the robot ahead in the same lane
HARD_GAP = 0.70      # no move may bring two robots closer than this (Carter footprint ~0.6 m)
PICK_DWELL = 1.5     # seconds stopped at a bin to pick
DROP_DWELL = 1.0     # seconds parked at a pack station to hand over the unit
HOLD_POINT = (centers[0], YBOT)   # doorstep of the pack stations, where loaded robots retry for a slot
EMPTY_BIN_WAIT = 3.0 # seconds a robot reports BIN_EMPTY before giving up on that bin
CHARGE_RATE = 5.0    # battery % per second on a dock
DRAIN = 0.08         # battery % per second while working: a full pack lasts ~20 sim minutes
CHARGE_BELOW = 30    # robots go to charge below this %: the longest jobs (~2 min) use ~10%
TEMP_LIMIT = 80      # motor temperature that raises OVERHEAT, matching the tool service
FAST_PER_ZONE = 3    # fast-moving bins per zone
FAST_SHARE = 0.6     # share of picks that go to fast-moving bins
WAIT_LOG_S = 2.0     # traffic waits shorter than this are not logged (they happen constantly)
FAST_QTY, SLOW_QTY = (2, 7), (12, 40)
RESTOCK_EVERY = 30.0 # seconds between replenishment passes


def _k(p):
    return (round(p[0], 2), round(p[1], 2))


# ---------------- road network ----------------
class Network:
    def __init__(self):
        self.adj = {}
        bot_x = sorted(set(centers) | {x + dx for x in PICK_X for dx in PICK_DX} | {x + REJOIN for x in PICK_X})
        top_x = sorted(set(centers) | {x + dx for x in DOCK_X for dx in DOCK_DX} | {x - REJOIN for x in DOCK_X})
        for xs, y, east in ((bot_x, YBOT, True), (top_x, YTOP, False)):
            for a, b in zip(xs, xs[1:]):
                self.edge((a, y), (b, y)) if east else self.edge((b, y), (a, y))
        for a, cx in enumerate(centers):
            lo, hi = (cx, YBOT), (cx, YTOP)
            self.edge(lo, hi) if NORTHBOUND[a] else self.edge(hi, lo)
        # drive-through bays: in from the main lane, out the far side onto a one-way exit lane behind
        # the slots, back onto the main lane downstream. No spur is ever driven in both directions.
        for xs_, dxs, y, sgn in ((PICK_X, PICK_DX, YBOT, 1), (DOCK_X, DOCK_DX, YTOP, -1)):
            for x in xs_:
                ys, ye = y - sgn * BAY, y - sgn * (BAY + EXIT_GAP)   # slot row, exit lane
                exits = sorted({x + dx for dx in dxs} | {x + sgn * REJOIN}, reverse=sgn < 0)
                for dx in dxs:
                    self.edge((x + dx, y), (x + dx, ys))
                    self.edge((x + dx, ys), (x + dx, ye))
                for a, b in zip(exits, exits[1:]):
                    self.edge((a, ye), (b, ye))
                self.edge((x + sgn * REJOIN, ye), (x + sgn * REJOIN, y))

    def edge(self, a, b):
        self.adj.setdefault(_k(a), []).append(_k(b))
        self.adj.setdefault(_k(b), [])

    def path(self, a, b):
        """Shortest directed path from node a to node b (Dijkstra)."""
        a, b = _k(a), _k(b)
        dist, prev, q = {a: 0.0}, {}, [(0.0, a)]
        while q:
            d, u = heapq.heappop(q)
            if u == b:
                break
            if d > dist[u]:
                continue
            for v in self.adj[u]:
                nd = d + math.hypot(v[0] - u[0], v[1] - u[1])
                if nd < dist.get(v, 1e18):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(q, (nd, v))
        if b not in dist:
            raise ValueError(f"no route {a} -> {b}")
        out = [b]
        while out[-1] != a:
            out.append(prev[out[-1]])
        return out[::-1]

    @staticmethod
    def aisle_ends(a):
        lo, hi = (centers[a], YBOT), (centers[a], YTOP)
        return (lo, hi) if NORTHBOUND[a] else (hi, lo)   # (entry, exit)


class Zone:
    """A junction or bay entrance. Only the robot holding the lock may be inside."""
    def __init__(self, x0, x1, y, depth):
        self.x0, self.x1, self.y0, self.y1 = x0, x1, y - depth, y + depth
        self.holder = None

    def contains(self, p):
        return self.x0 <= p[0] <= self.x1 and self.y0 <= p[1] <= self.y1


def build_zones():
    zones = []
    for y, bay_x, sgn in ((YBOT, PICK_X, 1), (YTOP, DOCK_X, -1)):
        for cx in centers:
            if cx in bay_x:   # bay junctions also cover the slot spurs and the rejoin point
                lo, hi = (cx - 0.9, cx + REJOIN + 0.45) if sgn > 0 else (cx - REJOIN - 0.45, cx + 0.9)
            else:
                lo, hi = cx - 0.9, cx + 0.9
            zones.append(Zone(lo, hi, y, 1.25))
    return zones


class Slots:
    """Parking slots in the bays. A robot reserves one before driving to it."""
    def __init__(self):
        self.owner = {("pick", x, dx): None for x in PICK_X for dx in PICK_DX}
        self.owner.update({("dock", x, dx): None for x in DOCK_X for dx in DOCK_DX})

    def reserve(self, kind, bot, rng):
        free = [k for k, o in self.owner.items() if k[0] == kind and o is None]
        if not free:
            return None
        k = rng.choice(free)
        self.owner[k] = bot.i
        return k

    def release(self, key):
        if key is not None:
            self.owner[key] = None

    @staticmethod
    def pos(key):
        kind, x, dx = key
        return (x + dx, YBOT - BAY) if kind == "pick" else (x + dx, YTOP + BAY)


class Inventory:
    """Shelf bins. Bin coordinates are the pick points on the aisle centre line, so a robot's pick
    goal equals its bin's coordinate exactly (the tool service looks bins up by that)."""
    def __init__(self, rng):
        self.bins = {}
        n = 0
        for z, cx in enumerate(centers):
            fast = set(rng.sample(range(len(BIN_Y)), FAST_PER_ZONE))   # popular SKUs, running low
            for j, y in enumerate(BIN_Y):
                q = rng.randint(*FAST_QTY) if j in fast else rng.randint(*SLOW_QTY)
                self.bins[_k((cx, y))] = {"bin": [cx, y], "zone": z, "sku": f"SKU-{1000 + n}",
                                          "qty": q, "expected": q, "side": "left" if j % 2 else "right",
                                          "fast": j in fast, "reserved": 0}
                n += 1

    def zone_bins(self, z):
        return [b for b in self.bins.values() if b["zone"] == z]

    def view(self):
        zones = []
        for z, cx in enumerate(centers):
            bins = [{k: b[k] for k in ("bin", "sku", "qty", "expected", "side", "fast")} for b in self.zone_bins(z)]
            zones.append({"zone": z, "aisle_x": cx, "total": sum(b["qty"] for b in bins),
                          "expected_total": sum(b["expected"] for b in bins), "bins": bins})
        return {"zones": zones}

    def restock(self, rng):
        """Replenishment: bins that ran out legitimately get refilled; a shrunk bin (qty != expected)
        is left alone, because nobody knows it is short until a robot scans it."""
        done = []
        for b in self.bins.values():
            if b["qty"] == b["expected"] == 0 and b["reserved"] == 0:
                b["qty"] = b["expected"] = rng.randint(*(FAST_QTY if b["fast"] else SLOW_QTY))
                done.append({"bin": b["bin"], "sku": b["sku"], "zone": b["zone"], "qty": b["qty"]})
        return done

    def shrink(self, zone, units, rng):
        """Stock disappears without a recorded pick: qty drops, expected does not."""
        gone = 0
        for _ in range(units):
            stocked = [b for b in self.zone_bins(zone) if b["qty"] > 0]
            if not stocked:
                break
            # loss shows up in the busy, low bins first, so a few go physically empty while the
            # system still counts stock there: those are the bins robots get sent to and find empty
            b = min(stocked, key=lambda b: (not b["fast"], b["qty"], rng.random()))
            b["qty"] -= 1
            gone += 1
        return gone


# ---------------- robots ----------------
class Bot:
    def __init__(self, i, aisle, y, fleet, rng):
        self.i, self.fleet, self.rng = i, fleet, rng
        self.zone = aisle                        # home zone: this robot picks from this aisle
        self.pos = [float(centers[aisle]), float(y), 0.0]
        entry, exit_ = Network.aisle_ends(aisle)
        self.node_next = exit_                   # mid-aisle: must continue to this aisle's exit
        self.yaw = math.pi / 2 if NORTHBOUND[aisle] else -math.pi / 2
        self.vel = 0.0
        self.battery = rng.uniform(40, 100)
        self.temp = 38.0
        self.speed = rng.uniform(1.3, 1.8)
        self.task, self.goal, self.carry, self.error = "idle", None, None, ""
        self.wp, self.slot, self.dwell, self.waited = [], None, 0.0, 0.0
        self.after_stop = None                   # node to continue to after a mid-aisle stop
        self.bin = None
        self.fault = None                        # injected demo fault: stuck / overheat / low_battery
        self.jobs = 0
        self.job_id = None                       # links job_assigned -> picked -> delivered in the log
        self.logged_conditions = set()           # last error conditions written to the log

    # ----- planning -----
    def plan(self, goal, extra=()):
        start = self.node_next
        self.wp = [start] + self.fleet.net.path(start, goal)[1:] + list(extra)

    def new_job(self):
        self.fleet.slots.release(self.slot)
        self.slot, self.bin = None, None
        if self.battery < CHARGE_BELOW:
            key = self.fleet.slots.reserve("dock", self, self.rng)
            if key is not None:
                self.slot, self.task = key, "charge"
                self.goal = list(Slots.pos(key))
                self.job_id = self.fleet.next_job()
                self.fleet.log("job_assigned", self.i, job_id=self.job_id, task="charge", zone=self.zone,
                               dock=self.goal, battery=round(self.battery))
                return self.plan(Slots.pos(key))
        # pick from a bin the SYSTEM believes is stocked: a shrunk bin can be physically empty
        cands = [b for b in self.fleet.inv.zone_bins(self.zone) if b["expected"] - b["reserved"] > 0]
        fast = [b for b in cands if b["fast"]]
        if fast and self.rng.random() < FAST_SHARE:   # popular SKUs get most of the picks
            cands = fast
        if not cands:
            if self.task != "idle":
                self.fleet.log("idle", self.i, zone=self.zone, reason="no bin in the zone has unreserved system stock")
            self.task, self.goal, self.wp, self.job_id = "idle", None, [], None
            return
        b = self.rng.choice(cands)
        b["reserved"] += 1                       # one unit is promised to this robot
        self.bin, self.task, self.goal = b, "pick", list(b["bin"])
        self.job_id = self.fleet.next_job()
        self.fleet.log("job_assigned", self.i, job_id=self.job_id, task="pick", zone=self.zone,
                       bin=b["bin"], sku=b["sku"], system_qty=b["expected"], fast_mover=b["fast"],
                       battery=round(self.battery))
        entry, exit_ = Network.aisle_ends(self.zone)
        self.plan(entry, extra=[tuple(b["bin"])])
        self.after_stop = exit_

    def go_drop(self):
        """Head for a pack station. If every slot is taken, drive the holding loop and try again
        at the stations' doorstep: a loaded robot never parks in a lane waiting for a slot
        (that is what gridlocked the first version)."""
        self.task = "carry"
        key = self.fleet.slots.reserve("pick", self, self.rng)
        if key is not None:
            self.slot, self.goal = key, list(Slots.pos(key))
            self.fleet.log("station_assigned", self.i, job_id=self.job_id, station=self.goal,
                           sku=(self.carry or {}).get("sku"))
            self.plan(Slots.pos(key))
        else:
            self.fleet.log("stations_full", self.i, job_id=self.job_id, action="driving the holding loop, will retry",
                           sku=(self.carry or {}).get("sku"))
            self.slot, self.goal = None, list(HOLD_POINT)
            if _k(self.node_next) == _k(HOLD_POINT):   # already at the doorstep: drive one full loop
                loop_top = (centers[0], YTOP)
                self.plan(loop_top)
                self.wp += self.fleet.net.path(loop_top, HOLD_POINT)[1:]
            else:
                self.plan(HOLD_POINT)

    # ----- motion -----
    def in_lane_ahead(self, heading):
        for o in self.fleet.bots:
            if o is self:
                continue
            v = (o.pos[0] - self.pos[0], o.pos[1] - self.pos[1])
            ahead = v[0] * heading[0] + v[1] * heading[1]
            lateral = abs(float(v[0] * heading[1] - v[1] * heading[0]))
            if 0.0 < ahead < SAFE_GAP and lateral < 0.55:
                return True
        return False

    def gap_ok(self, newpos):
        for o in self.fleet.bots:
            if o is self:
                continue
            nd = math.hypot(o.pos[0] - newpos[0], o.pos[1] - newpos[1])
            if nd < HARD_GAP and nd <= math.hypot(o.pos[0] - self.pos[0], o.pos[1] - self.pos[1]):
                return False
        return True

    def zones_ok(self, newpos):
        for z in self.fleet.zones:
            if z.contains(newpos) and z.holder not in (None, self.i):
                return False
        return True

    def take_zones(self, newpos):
        for z in self.fleet.zones:
            if z.contains(newpos):
                z.holder = self.i
            elif z.holder == self.i:
                z.holder = None

    def conditions(self):
        """Every active problem, not just the one the single telemetry error code can show."""
        c = set()
        if self.fault == "stuck":
            c.add("DRIVE_FAULT")
        if self.temp > TEMP_LIMIT:
            c.add("OVERHEAT")
        if self.battery < 15:
            c.add("LOW_BATTERY")
        if self.error == "BIN_EMPTY":
            c.add("BIN_EMPTY")
        return c

    def step(self, dt):
        # compare against what was last LOGGED, not the state at the start of this step: demo faults
        # are injected between steps, and a start-of-step comparison never sees them change
        w0, j0, c0 = self.waited, self.job_id, self.logged_conditions
        self._step(dt)
        if self.fault is None and w0 < WAIT_LOG_S <= self.waited:
            self.fleet.log("waiting", self.i, job_id=self.job_id, task=self.task,
                           at=[round(float(self.pos[0]), 2), round(float(self.pos[1]), 2)],
                           reason="yielding to traffic (robot ahead, junction lock, or spacing rule)")
        if w0 >= WAIT_LOG_S and self.waited == 0.0:
            self.fleet.log("resumed", self.i, job_id=self.job_id, waited_s=round(w0, 1))
        c1 = self.conditions()
        self.logged_conditions = c1
        for err in sorted(c0 - c1):
            self.fleet.log("error_cleared", self.i, error=err, job_id=j0,
                           battery=round(self.battery), temp=round(self.temp, 1))
        for err in sorted(c1 - c0):
            self.fleet.log("error_raised", self.i, error=err, job_id=self.job_id, task=self.task,
                           goal=self.goal, battery=round(self.battery), temp=round(self.temp, 1),
                           at=[round(float(self.pos[0]), 2), round(float(self.pos[1]), 2)])

    def _step(self, dt):
        before = (self.pos[0], self.pos[1])
        if self.fault == "stuck":
            self.waited += dt                    # a drive fault: it simply stops where it is
        elif not self.wp:
            self.waited = 0.0
            self.arrived(dt)
        else:
            tx, ty = self.wp[0]
            d = (tx - self.pos[0], ty - self.pos[1])
            dist = math.hypot(d[0], d[1])
            if dist < 1e-6:
                self.reach()
            else:
                heading = (d[0] / dist, d[1] / dist)
                step = min(self.speed * dt, dist)
                newpos = [self.pos[0] + step * heading[0], self.pos[1] + step * heading[1]]
                if (not self.in_lane_ahead(heading) and self.gap_ok(newpos)
                        and self.zones_ok(newpos)):
                    self.pos[:2] = newpos
                    self.take_zones(newpos)
                    self.yaw = math.atan2(heading[1], heading[0])
                    self.waited = 0.0
                    if math.hypot(tx - newpos[0], ty - newpos[1]) < 1e-6:
                        self.reach()
                else:
                    self.waited += dt
        self.vel = math.hypot(self.pos[0] - before[0], self.pos[1] - before[1]) / dt
        self.update_health(dt)

    def update_health(self, dt):
        charging = self.task == "charge" and not self.wp
        drain = 0.0 if charging else DRAIN * (25.0 if self.fault == "low_battery" else 1.0)
        self.battery = max(0.0, self.battery - drain * dt)
        if self.fault == "overheat":
            self.temp = min(95.0, self.temp + 3.0 * dt)
        else:
            moving = self.vel > 0.05
            self.temp += (0.6 if moving else -1.2) * dt
            self.temp = max(36.0, min(76.0, self.temp)) if self.temp <= 76.0 else self.temp - 1.5 * dt
        if self.fault == "stuck":
            self.error = "DRIVE_FAULT"
        elif self.error == "BIN_EMPTY":
            pass                                 # set and cleared by the pick logic
        elif self.temp > TEMP_LIMIT:
            self.error = "OVERHEAT"
        elif self.battery < 15:
            self.error = "LOW_BATTERY"
        else:
            self.error = ""

    def reach(self):
        self.node_next = _k(self.wp.pop(0))

    def arrived(self, dt):
        if self.task == "pick":
            b = self.bin
            self.dwell += dt
            if b["qty"] <= 0:                     # nothing on the shelf although the system says so
                self.error = "BIN_EMPTY"
                if self.dwell >= EMPTY_BIN_WAIT:
                    self.dwell, self.error = 0.0, ""
                    b["reserved"] -= 1
                    self.fleet.log("bin_count_corrected", self.i, job_id=self.job_id, bin=b["bin"], sku=b["sku"],
                                   zone=b["zone"], system_qty_was=b["expected"], physical_qty=b["qty"],
                                   note="scan found the bin empty; job abandoned, system count set to 0")
                    b["expected"] = 0             # the robot's scan corrects the system count
                    self.node_next = self.after_stop
                    self.new_job()
            elif self.dwell >= PICK_DWELL:
                b["reserved"] -= 1
                b["qty"] -= 1
                b["expected"] -= 1
                self.carry = {"sku": b["sku"], "qty": 1}
                self.fleet.picks += 1
                self.fleet.log("picked", self.i, job_id=self.job_id, sku=b["sku"], bin=b["bin"], zone=b["zone"],
                               qty_left=b["qty"], system_qty_left=b["expected"])
                self.dwell = 0.0
                self.node_next = self.after_stop
                self.go_drop()
        elif self.task == "carry" and self.slot is None:
            self.go_drop()                       # reached the doorstep without a slot: retry or loop again
        elif self.task == "carry":
            self.dwell += dt
            if self.dwell >= DROP_DWELL:
                self.fleet.log("delivered", self.i, job_id=self.job_id, sku=(self.carry or {}).get("sku"),
                               station=self.goal, zone=self.zone)
                self.dwell, self.carry = 0.0, None   # handed over at the pack station
                self.jobs += 1
                self.fleet.deliveries += 1
                self.node_next = _k(self.pos[:2])    # leave forward through the exit lane
                self.new_job()
        elif self.task == "charge":
            if self.dwell == 0.0:
                self.fleet.log("charging_started", self.i, job_id=self.job_id, dock=self.goal,
                               battery=round(self.battery))
            self.dwell += dt
            self.battery = min(100.0, self.battery + CHARGE_RATE * dt)
            if self.battery >= 95:
                self.fleet.log("charging_done", self.i, job_id=self.job_id, battery=round(self.battery),
                               took_s=round(self.dwell, 1))
                self.dwell = 0.0
                self.jobs += 1
                self.node_next = _k(self.pos[:2])
                self.new_job()
        else:
            self.new_job()

    def telem(self):
        stopped_for_traffic = self.waited > 0 and self.fault != "stuck"
        return {"id": self.i, "x": round(float(self.pos[0]), 2), "y": round(float(self.pos[1]), 2),
                "theta": round(math.degrees(self.yaw) % 360, 1), "vel": round(self.vel, 2),
                "task": self.task, "job_id": self.job_id, "goal": self.goal, "battery": round(self.battery),
                "temp": round(self.temp, 1), "zone": self.zone, "carrying": self.carry,
                "error": self.error, "ok": self.error == "",
                "yielding": stopped_for_traffic, "waiting_s": round(self.waited, 1)}


class Fleet:
    def __init__(self, n=20, seed=7):
        self.rng = random.Random(seed)
        self.net = Network()
        self.zones = build_zones()
        self.slots = Slots()
        self.inv = Inventory(random.Random(seed + 1))
        self.t = 0.0
        self.picks = self.deliveries = 0
        self.events, self.job_seq = [], 100      # event log (plain dicts, JSON-ready), job ids
        self.bots = []
        for i in range(n):
            aisle, row = i % AIS, i // AIS
            self.bots.append(Bot(i, aisle, -8.0 + 4.0 * row, self, random.Random(seed * 100 + i)))
        for b in self.bots:
            b.new_job()

    def log(self, type_, robot=None, **fields):
        self.events.append(dict({"t": round(self.t, 2), "type": type_, "robot": robot}, **fields))

    def next_job(self):
        self.job_seq += 1
        return f"J-{self.job_seq}"

    def step(self, dt):
        prev, self.t = self.t, self.t + dt
        if int(self.t / RESTOCK_EVERY) != int(prev / RESTOCK_EVERY):
            done = self.inv.restock(self.rng)
            if done:
                self.log("restocked", None, bins=done)
        for b in self.bots:
            b.step(dt)

    def min_spacing(self):
        best = 1e9
        for a in range(len(self.bots)):
            for b in range(a + 1, len(self.bots)):
                pa, pb = self.bots[a].pos, self.bots[b].pos
                best = min(best, math.hypot(pa[0] - pb[0], pa[1] - pb[1]))
        return best

    # ----- the contract served to the tool service (see sim_bridge.py) -----
    def telemetry(self):
        events = []
        for b in self.bots:
            if b.error:
                events.append({"type": "health" if b.error in ("OVERHEAT", "LOW_BATTERY") else "robot",
                               "robot": b.i, "zone": b.zone, "detail": b.error,
                               "sev": "critical" if b.error in ("DRIVE_FAULT", "LOW_BATTERY") else "warning",
                               "t": round(self.t, 1)})
        return {"t": round(self.t, 2), "picks": self.picks, "deliveries": self.deliveries,
                "robots": [b.telem() for b in self.bots], "events": events}

    def layout(self):
        return {"units": "metres", "aisles": [{"zone": z, "x": cx, "northbound": NORTHBOUND[z]}
                                              for z, cx in enumerate(centers)],
                "cross_lanes": {"bottom_y": YBOT, "bottom_flow": "east", "top_y": YTOP, "top_flow": "west"},
                "pack_stations": [list(Slots.pos(k)) for k in self.slots.owner if k[0] == "pick"],
                "charge_docks": [list(Slots.pos(k)) for k in self.slots.owner if k[0] == "dock"],
                "bin_y": BIN_Y}

    def inventory(self):
        return self.inv.view()

    def command(self, cmd):
        """Demo faults forwarded from the tool service's /demo/* endpoints."""
        res = self._command(cmd)
        self.log("demo_command", cmd.get("robot"), command={k: v for k, v in cmd.items() if k != "queued_at"},
                 result=res)
        return res

    def _command(self, cmd):
        kind = cmd.get("kind")
        if kind == "inject":
            b = self.bots[int(cmd["robot"])]
            b.fault = cmd.get("fault", "stuck")
            return {"ok": True, "robot": b.i, "fault": b.fault}
        if kind == "clear":
            for b in self.bots:
                b.fault = None
            return {"ok": True}
        if kind == "shrink":
            gone = self.inv.shrink(int(cmd["zone"]), int(cmd.get("units", 6)), self.rng)
            return {"ok": True, "zone": int(cmd["zone"]), "units_removed": gone}
        return {"ok": False, "error": f"unknown command {kind}"}
