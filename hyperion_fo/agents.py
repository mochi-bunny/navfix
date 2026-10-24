"""Hyperion agents: surveillance, war room, scheduler, task allocation.

Design rule (same as the pitch): code enforces, models explain. Detectors, severity floors, action
validation, the technician capacity and the assignment maths are code. The LLM (Grok Bot client in
llm.py) writes the triage text and root cause, picks the order of waiting tickets and may veto an
allocation move. Every LLM answer is validated; an invalid or failed answer falls back to a code rule
and the fallback is logged, never hidden.
"""
import math

import numpy as np
from scipy.optimize import linear_sum_assignment

from fleet import AIS, centers

SEV_RANK = {"info": 0, "warning": 1, "critical": 2}
CODE_SEV = {"DRIVE_FAULT": "critical", "LOW_BATTERY": "critical", "OVERHEAT": "warning", "BIN_EMPTY": "warning"}
CODE_ACTION = {"DRIVE_FAULT": "request_technician", "LOW_BATTERY": "request_technician",
               "OVERHEAT": "request_technician", "BIN_EMPTY": "cycle_count"}


class Agent:
    name = "agent"

    def __init__(self, brain, emit):
        self.brain, self.emit = brain, emit

    def ask(self, system, user, **kw):
        obj, meta = self.brain.chat_json(system, user, **kw)
        return obj, meta

    def note(self, kind, meta=None, **payload):
        if meta:
            payload["llm"] = {k: meta[k] for k in ("backend", "model", "latency_s", "ok") if k in meta}
            if not meta.get("ok", True):
                payload["llm"]["error"] = meta.get("error", "")
        self.emit(self.name, kind, **payload)


class Surveillance(Agent):
    """Watches telemetry every PERIOD sim seconds, opens incidents for new problems."""
    name = "surveillance"
    PERIOD = 5.0
    WAIT_S = 20.0

    def __init__(self, brain, emit):
        super().__init__(brain, emit)
        self.open = {}          # key -> incident
        self.next_id = 1
        self.last = -1e9

    def detect(self, tel):
        found = {}
        for r in tel["robots"]:
            if r["error"]:
                found[(r["id"], r["error"])] = {"robot": r["id"], "zone": r["zone"], "code": r["error"]}
            elif r["waiting_s"] >= self.WAIT_S:
                found[(r["id"], "TRAFFIC_WAIT")] = {"robot": r["id"], "zone": r["zone"], "code": "TRAFFIC_WAIT"}
        return found

    def tick(self, t, tel):
        if t - self.last < self.PERIOD:
            return []
        self.last = t
        found = self.detect(tel)
        for key in [k for k in self.open if k not in found]:
            inc = self.open.pop(key)
            self.emit(self.name, "cleared", incident=inc["id"], robot=inc["robot"], code=inc["code"],
                      open_s=round(t - inc["opened_t"], 1))
        new = [dict(v, key=f"{k[0]}:{k[1]}") for k, v in found.items() if k not in self.open]
        if not new:
            return []
        by = {x["key"]: x for x in new}
        obj, meta = self.ask(
            "You are the surveillance agent of a warehouse robot fleet. For each alert give a severity "
            "(info, warning or critical), a one-line summary under 15 words, and whether to escalate to the war room.",
            'Alerts: %s\nFormat: {"alerts":[{"key":"..","severity":"..","summary":"..","escalate":true}]}' % new,
            max_tokens=350)
        answers = {}
        if obj and isinstance(obj.get("alerts"), list):
            for a in obj["alerts"]:
                if isinstance(a, dict) and a.get("key") in by:
                    answers[a["key"]] = a
        out, fallbacks = [], 0
        for key, al in by.items():
            a = answers.get(key)
            floor = CODE_SEV.get(al["code"], "info")
            raw_sev = str(a.get("severity", "")).strip().lower() if a else ""
            if raw_sev not in SEV_RANK:
                fallbacks += 1
            sev = raw_sev if raw_sev in SEV_RANK else floor
            sev = sev if SEV_RANK[sev] >= SEV_RANK[floor] else floor           # code sets the floor
            summary = (a or {}).get("summary") if a and isinstance(a.get("summary"), str) else ""
            if not summary:
                fallbacks += 1
                summary = f"Robot {al['robot']} in zone {al['zone']}: {al['code']}"
            esc = bool(a.get("escalate")) if a else False
            esc = esc or sev == "critical" or al["code"] in CODE_ACTION   # actionable codes always escalate
            inc = {"id": f"INC-{self.next_id}", "robot": al["robot"], "zone": al["zone"], "code": al["code"],
                   "severity": sev, "summary": summary[:140], "opened_t": t, "escalate": esc}
            self.next_id += 1
            self.open[(al["robot"], al["code"])] = inc
            out.append(inc)
        self.note("incidents_opened", meta, count=len(out), fallbacks=fallbacks,
                  incidents=[{k: v for k, v in i.items() if k != "opened_t"} for i in out])
        return [i for i in out if i["escalate"]]


class WarRoom(Agent):
    """Takes escalated incidents, writes the root cause and debrief, proposes actions."""
    name = "war_room"
    AUDIT_COOLDOWN = 60.0

    def __init__(self, brain, emit):
        super().__init__(brain, emit)
        self.last_audit = {}

    def convene(self, t, inc, tel):
        zone_robots = [r for r in tel["robots"] if r["zone"] == inc["zone"]]
        ctx = {"incident": {k: inc[k] for k in ("id", "robot", "zone", "code", "severity", "summary")},
               "robot": next(r for r in tel["robots"] if r["id"] == inc["robot"]),
               "zone_robots": len(zone_robots), "zone_waiting": sum(1 for r in zone_robots if r["waiting_s"] > 5),
               "zone_bin_empty_now": sum(1 for r in zone_robots if r["error"] == "BIN_EMPTY")}
        ctx["robot"] = {k: ctx["robot"][k] for k in ("id", "task", "battery", "temp", "error", "waiting_s")}
        obj, meta = self.ask(
            "You are the war room agent of a warehouse robot fleet. Given an incident, state the most likely "
            "root cause in one sentence, one debrief sentence for the shift lead, and choose ONE action. "
            "Allowed actions: request_technician (robot hardware fault), cycle_count (shelf stock does not "
            "match the system, e.g. BIN_EMPTY), none.",
            'Incident context: %s\nFormat: {"root_cause":"..","debrief":"..","action":"request_technician|cycle_count|none"}' % ctx,
            max_tokens=300)
        expected = CODE_ACTION.get(inc["code"], "none")
        action, fallback = expected, False
        if obj and obj.get("action") in ("request_technician", "cycle_count", "none"):
            action = obj["action"]
        else:
            fallback = True
        # code enforces: a hardware fault must get a technician, a stock mismatch must get a count
        if inc["code"] in CODE_ACTION and action != expected:
            self.emit(self.name, "action_overridden", incident=inc["id"], proposed=action, enforced=expected)
            action = expected
        if action == "cycle_count" and t - self.last_audit.get(inc["zone"], -1e9) < self.AUDIT_COOLDOWN:
            action = "none"
        cmds = []
        if action == "cycle_count":
            self.last_audit[inc["zone"]] = t
            cmds.append({"kind": "audit", "zone": inc["zone"]})
        root = obj.get("root_cause") if obj and isinstance(obj.get("root_cause"), str) else ""
        deb = obj.get("debrief") if obj and isinstance(obj.get("debrief"), str) else ""
        if not root:
            fallback = True
            root = f"{inc['code']} on robot {inc['robot']} (no model answer, code rule applied)"
        self.note("debrief", meta, incident=inc["id"], robot=inc["robot"], zone=inc["zone"], code=inc["code"],
                  root_cause=root[:200], debrief=deb[:240], action=action, fallback_used=fallback)
        return action, cmds


class Scheduler(Agent):
    """Technician scheduling: 2 technicians, travel + repair time, LLM orders the waiting tickets."""
    name = "scheduler"
    TECHS = 2
    TRAVEL_S = 20.0
    REPAIR_S = 25.0

    def __init__(self, brain, emit):
        super().__init__(brain, emit)
        self.queue, self.busy = [], []      # busy: {"ticket","done_t","tech"}

    def request(self, t, inc):
        self.queue.append({"id": inc["id"], "robot": inc["robot"], "zone": inc["zone"], "code": inc["code"],
                           "severity": inc["severity"], "t": t})
        self.emit(self.name, "ticket_queued", incident=inc["id"], robot=inc["robot"], queue=len(self.queue))

    def tick(self, t):
        cmds = []
        for job in [b for b in self.busy if b["done_t"] <= t]:
            self.busy.remove(job)
            cmds.append({"kind": "clear", "robot": job["ticket"]["robot"]})
            self.emit(self.name, "repair_done", incident=job["ticket"]["id"], robot=job["ticket"]["robot"],
                      tech=job["tech"], waited_s=round(job["start_t"] - job["ticket"]["t"], 1))
        while self.queue and len(self.busy) < self.TECHS:
            tech = next(i for i in range(1, self.TECHS + 1) if i not in {b["tech"] for b in self.busy})
            order = self.order(t)
            tk = self.queue.pop(order[0])
            self.busy.append({"ticket": tk, "tech": tech, "start_t": t, "done_t": t + self.TRAVEL_S + self.REPAIR_S})
            self.emit(self.name, "technician_dispatched", incident=tk["id"], robot=tk["robot"], tech=tech,
                      eta_s=self.TRAVEL_S + self.REPAIR_S, still_waiting=len(self.queue))
        return cmds

    def order(self, t):
        """Indices into self.queue, best first. LLM chooses when there is a real choice; code validates."""
        rule = sorted(range(len(self.queue)),
                      key=lambda i: (-SEV_RANK[self.queue[i]["severity"]], self.queue[i]["t"]))
        if len(self.queue) < 2:
            return rule
        tickets = [{"i": i, "robot": q["robot"], "code": q["code"], "severity": q["severity"],
                    "age_s": round(t - q["t"])} for i, q in enumerate(self.queue)]
        obj, meta = self.ask(
            "You schedule maintenance technicians for a warehouse robot fleet. Order the waiting tickets, "
            "most urgent first. Critical before warning; among equals, the oldest first.",
            'Tickets: %s\nFormat: {"order":[0,1],"reason":".."}' % tickets, max_tokens=200)
        valid = (obj and isinstance(obj.get("order"), list)
                 and sorted(x for x in obj["order"] if isinstance(x, int)) == list(range(len(self.queue))))
        chosen = obj["order"] if valid else rule
        self.note("ticket_order", meta, tickets=len(tickets), order=chosen, rule_order=rule,
                  agrees_with_rule=(chosen == rule), fallback_used=not valid,
                  reason=(obj or {}).get("reason", "")[:160] if isinstance((obj or {}).get("reason", ""), str) else "")
        return chosen


class Allocator(Agent):
    """Task allocation: keeps robots out of aisles blocked by a faulted robot (Hungarian assignment).

    A stuck robot in a one-way aisle stalls everything that enters behind it. Zones that hold a faulted
    robot are 'blocked'. Robots homed in a blocked zone are re-homed to the open zones, balanced by load
    and nearest aisle; when the zone is repaired they go home. Moves apply at the robot's next job, so
    nothing is teleported or turned around mid-aisle. Code builds and applies the moves; the LLM explains
    them and flags concerns for the shift lead (logged, not binding)."""
    name = "allocator"
    PERIOD = 5.0
    MAX_MOVES = 8

    def __init__(self, brain, emit, enabled=True):
        super().__init__(brain, emit)
        self.last = -1e9
        self.enabled = enabled
        self.home = {}

    def tick(self, t, fleet, open_incidents):
        if not self.enabled or t - self.last < self.PERIOD:
            return []
        self.last = t
        for b in fleet.bots:
            self.home.setdefault(b.i, b.zone)
        # blocked zones come from surveillance's open DRIVE_FAULT incidents (the pipeline is chained), not simulator internals
        blocked = {inc["zone"] for (_, code), inc in open_incidents.items() if code == "DRIVE_FAULT"}
        faulted = {inc["robot"] for (_, code), inc in open_incidents.items() if code in ("DRIVE_FAULT", "OVERHEAT", "LOW_BATTERY")}
        target = lambda b: b.pending_zone if b.pending_zone is not None else b.zone
        open_zones = [z for z in range(AIS) if z not in blocked]
        movers = [b for b in fleet.bots if b.i not in faulted and
                  (target(b) in blocked or (target(b) != self.home[b.i] and self.home[b.i] not in blocked))]
        if not movers or not open_zones:
            return []
        load = {z: sum(1 for b in fleet.bots if b.i not in faulted and target(b) == z and b not in movers) for z in range(AIS)}
        cap = math.ceil(len(fleet.bots) / len(open_zones)) + 1
        homeward = [b for b in movers if self.home[b.i] not in blocked]
        awayward = [b for b in movers if self.home[b.i] in blocked or target(b) in blocked]
        moves = [{"robot": b.i, "from_zone": target(b), "to_zone": self.home[b.i], "why": "home zone repaired"}
                 for b in homeward if target(b) != self.home[b.i]]
        for m_ in moves:
            load[m_["to_zone"]] += 1
        slots = [z for z in open_zones for _ in range(max(0, cap - load[z]))]
        away = [b for b in awayward if b not in homeward]
        if away and slots:
            cost = np.array([[abs(centers[target(b)] - centers[z]) for z in slots] for b in away], float)
            rows, cols = linear_sum_assignment(cost)
            moves += [{"robot": away[r].i, "from_zone": target(away[r]), "to_zone": slots[c], "why": "zone blocked by a stuck robot",
                       "cost_m": float(cost[r, c])} for r, c in zip(rows, cols) if slots[c] != target(away[r])]
        moves = moves[: self.MAX_MOVES]
        if not moves:
            return []
        obj, meta = self.ask(
            "You allocate warehouse robots to aisles (zones). Aisles with a stuck robot are blocked. "
            "Each proposed move re-homes a robot to an open aisle, or sends it back home after repair. "
            "Give one sentence explaining the reallocation to the shift lead. List the robot ids of any move "
            "you think is a mistake (usually none).",
            'Blocked zones: %s\nProposed moves: %s\nFormat: {"reason":"..","concerns":[robot ids]}' % (sorted(blocked), moves),
            max_tokens=200)
        concerns = sorted({x for x in (obj or {}).get("concerns", []) if isinstance(x, int)}) if isinstance((obj or {}).get("concerns", []), list) else []
        cmds = [{"kind": "reassign", "robot": m_["robot"], "zone": m_["to_zone"]} for m_ in moves]   # code applies the optimum
        self.note("reallocation", meta, blocked_zones=sorted(blocked), moves=moves, model_concerns=concerns,
                  fallback_used=obj is None, reason=(obj or {}).get("reason", "")[:200] if isinstance((obj or {}).get("reason", ""), str) else "")
        return cmds
