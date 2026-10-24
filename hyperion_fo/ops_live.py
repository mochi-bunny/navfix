#!/usr/bin/env python3
"""Hyperion x Fo live ops loop.

Reads the live fleet from sim_bridge.py (pushed by the Isaac Sim GUI or by fleet_runner.py), runs the four
Hyperion agents on it once a second, and sends their commands back. A hardware fault that the war room's code
rule sends to a technician goes to the Dispatcher, which gets a REAL person on the way through Fo (call,
calendar, email). The technician's ETA becomes the repair timer (demo clock: ETA_SCALE sim seconds per real
minute); while she is "on the way" the allocator keeps the fleet moving around the blocked aisle; on arrival the
robot is cleared and Fo sends the closing email.

  python3 ops_live.py --bridge http://127.0.0.1:3001 [--dry-run] [--eta-scale 2]
  panel: http://127.0.0.1:8300/        inject: curl -X POST localhost:8300/inject -d '{"robot":7}'
"""
import argparse, json, os, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llm import Brain
import agents
from agents import Surveillance, WarRoom, Allocator
# hyperion-fo: only a hardware fault that stops a robot pages a human. Low battery = the charger's job; overheat = watch.
agents.CODE_ACTION.update({"LOW_BATTERY": "none", "OVERHEAT": "none"})
PAGE_CODES = {"DRIVE_FAULT"}
import dispatch
import discord

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--bridge", default="http://127.0.0.1:3001")
ap.add_argument("--dry-run", action="store_true", help="print the Fo requests instead of sending them")
ap.add_argument("--eta-scale", type=float, default=2.0, help="sim seconds per real minute of technician ETA")
ap.add_argument("--port", type=int, default=8300)
ap.add_argument("--tag", default="")
args = ap.parse_args()

RUN = args.tag or time.strftime("run_%m%d_%H%M%S")
RUN_DIR = os.path.join(HERE, "runs", RUN); os.makedirs(RUN_DIR, exist_ok=True)
TRACE = open(os.path.join(RUN_DIR, "trace.jsonl"), "a")
LOCK = threading.Lock()
EVENTS, INCIDENTS, STATE = [], {}, {"tel": None, "run": RUN, "dry_run": args.dry_run, "backend": None,
                                    "eta_scale": args.eta_scale, "started": time.time()}
SIM_T = [0.0]


def emit(agent, kind, **payload):
    ev = {"t_wall": round(time.time(), 2), "t_sim": round(SIM_T[0], 1), "agent": agent, "kind": kind,
          **{k: v for k, v in payload.items() if k != "llm"}}
    if "llm" in payload:
        ev["llm"] = payload["llm"]
    with LOCK:
        EVENTS.append(ev); TRACE.write(json.dumps(ev) + "\n"); TRACE.flush()
        inc = payload.get("incident")
        if inc and inc in INCIDENTS:
            INCIDENTS[inc]["steps"].append({"agent": agent, "kind": kind, "t_wall": ev["t_wall"],
                                            **{k: payload[k] for k in ("status", "progress", "eta_min", "summary", "tool",
                                                                       "ok", "why", "seconds", "fallback_used", "decision") if k in payload}})
    print(f"[{SIM_T[0]:7.1f}s] {agent:<11} {kind:<16} " + json.dumps({k: v for k, v in payload.items() if k != 'llm'})[:170], flush=True)


def http(method, path, body=None):
    req = urllib.request.Request(args.bridge.rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def command(cmd, source):
    try:
        res = http("POST", "/api/command", cmd)
    except OSError as e:
        res = {"ok": False, "error": str(e)}
    emit("fleet", "command", source=source, cmd=cmd, result=res)


class FleetView:
    """What the allocator needs from the fleet, rebuilt from live telemetry (it never touches simulator internals)."""
    class Bot:
        def __init__(self, r): self.i, self.zone, self.pending_zone = r["id"], r["zone"], r.get("pending_zone")
    def __init__(self, tel): self.bots = [self.Bot(r) for r in tel["robots"]]


class LiveTechnicianDesk:
    """Replaces the timer-technicians: a real person, reached through Fo, sets the repair time."""
    name = "scheduler"

    def __init__(self):
        self.jobs = {}          # incident id -> job

    def request(self, t, inc, root_cause):
        if inc["id"] in self.jobs or inc["code"] not in PAGE_CODES:
            return
        active = [j for j in self.jobs.values() if j["state"] in ("dispatching", "en_route", "needs_human")]
        if any(j["inc"]["robot"] == inc["robot"] for j in active) or len(active) >= 1:
            emit(self.name, "page_deferred", incident=inc["id"], robot=inc["robot"],
                 summary="one human page at a time; this one waits (logged)")
            return
        job = {"inc": inc, "state": "dispatching", "t_open": t, "eta_min": None, "due_t": None, "t_wall": time.time()}
        self.jobs[inc["id"]] = job
        cause = root_cause.split(". ")[0].split(", ")[0].rstrip(".")          # first clause only, never cut mid-word
        cause = cause if len(cause) <= 110 else cause[:110].rsplit(" ", 1)[0]
        brief = (f"Robot {inc['robot']} in aisle {inc['zone']} has a {inc['code'].replace('_', ' ').lower()} and has stopped. "
                 f"Likely cause: {cause}. Please bring a spare drive module.")
        emit(self.name, "technician_needed", incident=inc["id"], robot=inc["robot"], brief=brief)
        discord.post(f"🔴 **{inc['id']}** robot {inc['robot']} stopped in aisle {inc['zone']} ({inc['code']}). "
                     f"Hyperion is getting a technician through Fo.", emit, args.dry_run, inc["id"])

        def work():
            eta, out, who = dispatch.dispatch_technician(inc, brief, RUN_DIR, RUN, emit, dry=args.dry_run)
            job["fo"] = out
            if eta is None:                     # nobody committed: a human decides, the robot stays out of service
                job.update(state="needs_human")
                emit(self.name, "needs_human", incident=inc["id"], summary=(out or {}).get("said", ""))
                discord.post(f"🟠 **{inc['id']}** no technician could commit ({(out or {}).get('said', '')[:200]}). "
                             f"Needs a human decision on the ops panel. Nobody outside the approved list was contacted.", emit, args.dry_run, inc["id"])
                return
            job.update(eta_min=eta, due_t=SIM_T[0] + eta * args.eta_scale, state="en_route", who=who)
            discord.post(f"🟡 **{inc['id']}** {who} is on the way, ETA {eta} min (confirmed by phone). "
                         f"Calendar slot booked, incident email sent. Fleet is routing around aisle {inc['zone']}.", emit, args.dry_run, inc["id"])
            emit(self.name, "technician_en_route", incident=inc["id"], eta_min=eta,
                 repair_at_sim=round(job["due_t"], 1), demo_clock=f"{args.eta_scale:g} sim s per real minute")
        threading.Thread(target=work, daemon=True).start()

    def approve(self, iid, t):
        """Human decision from the panel: the ops lead takes the repair (manual). Only humans unlock this path."""
        job = self.jobs.get(iid)
        if not job or job["state"] != "needs_human":
            return False
        job.update(state="en_route", eta_min=dispatch.DEFAULT_ETA, due_t=t + dispatch.DEFAULT_ETA * args.eta_scale,
                   who=f"{dispatch.C['ops_lead']['name']} (ops lead, manual approval)")
        emit(self.name, "human_approved", incident=iid, eta_min=dispatch.DEFAULT_ETA, summary="ops lead takes the repair")
        return True

    def tick(self, t):
        for iid, job in self.jobs.items():
            if job["state"] == "en_route" and t >= job["due_t"]:
                job["state"] = "repaired"
                command({"kind": "clear", "robot": job["inc"]["robot"]}, "technician")
                emit(self.name, "repair_done", incident=iid, robot=job["inc"]["robot"], downtime_s=round(t - job["t_open"], 1))
                threading.Thread(target=lambda j=job, tt=t: (
                    dispatch.close_out(j["inc"], tt - j["t_open"], RUN_DIR, RUN, emit, dry=args.dry_run, who=j.get("who")),
                    j.update(state="closed"), emit(self.name, "incident_closed", incident=j["inc"]["id"]),
                    discord.post(f"🟢 **{j['inc']['id']}** robot {j['inc']['robot']} back in service. Downtime {tt - j['t_open']:.0f} s "
                                 f"(demo clock). Closing email sent.", emit, args.dry_run, j['inc']['id'])), daemon=True).start()


class Panel(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/panel"):
            return self.send(200, open(os.path.join(HERE, "panel.html"), "rb").read(), "text/html")
        if self.path.startswith("/state"):
            with LOCK:
                tel = STATE["tel"] or {}
                robots = tel.get("robots", [])
                return self.send(200, {"run": RUN, "dry_run": args.dry_run, "backend": STATE["backend"], "sim_t": SIM_T[0],
                                       "deliveries": tel.get("deliveries"), "picks": tel.get("picks"),
                                       "faulted": [r["id"] for r in robots if r["error"]],
                                       "waiting": sum(1 for r in robots if r["waiting_s"] > 5), "robots": len(robots),
                                       "incidents": list(INCIDENTS.values()), "events": EVENTS[-40:],
                                       "jobs": {k: {x: v.get(x) for x in ("state", "eta_min", "due_t")} for k, v in DESK.jobs.items()}})
        return self.send(404, {"error": "not found"})
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0); body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/approve":
            ok = DESK.approve(body.get("incident", ""), SIM_T[0]); return self.send(200, {"ok": ok})
        if self.path == "/inject":
            cmd = {"kind": "inject", "robot": int(body.get("robot", 7)), "fault": body.get("fault", "stuck")}
            command(cmd, "demo_operator"); return self.send(200, {"ok": True})
        return self.send(404, {"error": "not found"})


brain = Brain(); STATE["backend"] = f"{brain.backend}:{brain.model}"
surv, war, alloc, DESK = Surveillance(brain, emit), WarRoom(brain, emit), Allocator(brain, emit), LiveTechnicianDesk()
threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", args.port), Panel).serve_forever, daemon=True).start()
emit("ops", "start", run=RUN, bridge=args.bridge, dry_run=args.dry_run, backend=STATE["backend"], eta_scale=args.eta_scale,
     panel=f"http://127.0.0.1:{args.port}/")

last_t = -1.0
while True:
    try:
        tel = http("GET", "/api/telemetry")
    except OSError as e:
        print("bridge not reachable:", e, flush=True); time.sleep(1); continue
    with LOCK:
        STATE["tel"] = tel
    t = SIM_T[0] = float(tel["t"])
    if t - last_t >= 1.0:
        last_t = t
        for inc in surv.tick(t, tel):
            with LOCK:
                INCIDENTS[inc["id"]] = dict({k: inc[k] for k in ("id", "robot", "zone", "code", "severity", "summary")}, steps=[])
            emit("surveillance", "escalated", incident=inc["id"], robot=inc["robot"], code=inc["code"], severity=inc["severity"])
            action, cmds = war.convene(t, inc, tel)
            root = next((e.get("root_cause", "") for e in reversed(EVENTS) if e["kind"] == "debrief"), "")
            emit("war_room", "decision", incident=inc["id"], action=action, root_cause=root[:160])
            if action == "request_technician":
                DESK.request(t, inc, root or inc["summary"])
            for c in cmds:
                command(c, "war_room")
        DESK.tick(t)
        for c in alloc.tick(t, FleetView(tel), surv.open):
            command(c, "allocator")
    time.sleep(0.25)
