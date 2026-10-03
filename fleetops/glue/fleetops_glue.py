#!/usr/bin/env python3
"""FleetOps glue: connects the real pieces on the GB10 without changing any of them.

  sim (fleet_runner / Isaac) --push--> sim_bridge :3001 --poll--> supervisor tool service :8090
                                             |                              |  ticket webhook
                                             v                              v
                                   fleetops_glue :7100  <-------------------+
                                     |   ^      |
            dashboard (live mode) <--+   |      +--> navbot :8787 (Discord cards, approvals, fleet log)
                                         +--------- navbot decisions (approve / deny)

Serves, from real data:
  dashboard sources  GET /clock /warehouse /reliability /commissioning /feeds /needs /events /messages
  live map           GET /feeds/map.svg     (top-down view of the real fleet, for the dashboard's feed panel)
  fleet log          GET /fleetlog?minutes=5 (same JSON as samples/fleet_log_5min.json, built live)
  webhooks           POST /hooks/scheduler  (tool service SCHEDULER_WEBHOOK_URL)
                     POST /decisions        (navbot SUPERVISOR_DECISION_URL)
Standard library only.  Settings come from the environment (see CONFIG).
"""
import json, math, os, threading, time, urllib.request, urllib.error
from collections import Counter, deque, defaultdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

E = os.environ.get
CONFIG = {
    "host": E("GLUE_HOST", "127.0.0.1"),          # localhost only: every client of the glue runs on the GB10
    "port": int(E("GLUE_PORT", "7100")),
    "tools": E("TOOLS_URL", "http://127.0.0.1:8090"),
    "bridge": E("BRIDGE_URL", "http://127.0.0.1:3001"),
    "navbot": E("NAVBOT_URL", ""),                 # e.g. http://127.0.0.1:8787 ; empty = don't forward
    "bot_key": E("BOT_SHARED_KEY", ""),
    "site": E("SITE_ID", "fleetops"),
    "site_name": E("SITE_NAME", "FleetOps DC"),
    "site_area": E("SITE_AREA", "Boston"),
    "isaac_stream": E("ISAAC_STREAM_URL", ""),     # optional iframe (e.g. a WebRTC client); empty = none
    "isaac_cams": E("ISAAC_CAMS_URL", "http://127.0.0.1:8212"),   # warehouse_live.py --cams camera server
    "fleetlog_every_s": float(E("FLEETLOG_EVERY_S", "300")),
    "approvals": E("GLUE_APPROVALS", "1") == "1",  # ask Discord to approve schedule changes
}
SCHEDULE_TYPES = {"stuck", "drive_fault", "deadlock", "overheat", "off_trajectory", "repeated_errors", "throughput_drop"}
LOCK = threading.Lock()
STATE = {"started": time.time(), "tickets": {}, "trans": [], "msgs": deque(maxlen=400),
         "snapshots": deque(maxlen=720), "events": deque(maxlen=20000), "event_seq": 0,
         "throughput": deque(maxlen=120), "fleet": None, "metrics": None, "layout": None,
         "telemetry": None, "requests": {}, "errors": deque(maxlen=50), "fleetlog_last": 0.0,
         "navbot_sent": 0, "navbot_failed": 0}


# ---------------- helpers ----------------
def get_json(base, path, timeout=2.0):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read())


def send_json(url, body, method="POST", headers=None, timeout=5.0, raw=None, ctype="application/json"):
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": ctype, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        txt = r.read()
        return r.status, (json.loads(txt) if txt[:1] in (b"{", b"[") else txt.decode(errors="replace"))


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def hms_iso(hms):
    """Tool service times are local HH:MM:SS of today: give them a date and an offset."""
    if not hms:
        return None
    try:
        h, m, s = (int(x) for x in str(hms).split(":"))
        d = datetime.now().astimezone()
        return d.replace(hour=h, minute=m, second=s, microsecond=0).isoformat(timespec="seconds")
    except ValueError:
        return hms


def robot_name(i):
    return f"AMR-{int(i):02d}" if i is not None else None


def note(channel, author, text):
    with LOCK:
        STATE["msgs"].append({"id": f"m{len(STATE['msgs'])}-{int(time.time()*1000)}", "channel": channel,
                              "author": author, "text": text, "at": now_iso(),
                              "bot": author in ("Fleet", "Supervisor", "Scheduler", "glue")})


def err(where, e):
    with LOCK:
        STATE["errors"].append({"at": now_iso(), "where": where, "error": repr(e)[:200]})


# ---------------- pollers ----------------
def poll_loop():
    last_snap, last_tp = 0.0, 0.0
    while True:
        t0 = time.time()
        try:
            fleet = get_json(CONFIG["tools"], "/fleet")
            metrics = get_json(CONFIG["tools"], "/metrics")
            with LOCK:
                STATE["fleet"], STATE["metrics"] = fleet, metrics
            if t0 - last_tp >= 10:
                last_tp = t0
                with LOCK:
                    STATE["throughput"].append({"t": now_iso(), "v": metrics["throughput"]["deliveries_last_min"]})
        except Exception as e:
            err("poll supervisor", e)
        try:
            tel = get_json(CONFIG["bridge"], "/api/telemetry")
            with LOCK:
                STATE["telemetry"] = tel
            if STATE["layout"] is None:
                STATE["layout"] = get_json(CONFIG["bridge"], "/api/layout")
            if t0 - last_snap >= 5:
                last_snap = t0
                inv = get_json(CONFIG["bridge"], "/api/inventory")
                snap = {"t": tel["t"], "picks": tel.get("picks"), "deliveries": tel.get("deliveries"),
                        "robots": [{k: r.get(k) for k in ("id", "zone", "x", "y", "theta", "vel", "task", "job_id",
                                                          "goal", "battery", "temp", "error", "yielding", "waiting_s")}
                                   | {"carrying": (r["carrying"] or {}).get("sku") if isinstance(r.get("carrying"), dict)
                                      else r.get("carrying")} for r in tel["robots"]],
                        "zone_stock": [{"zone": z["zone"], "total": z["total"], "expected_total": z["expected_total"],
                                        "bins_short": sum(1 for b in z["bins"] if b["qty"] != b["expected"])}
                                       for z in inv["zones"]]}
                with LOCK:
                    if STATE["snapshots"] and snap["t"] < STATE["snapshots"][-1]["t"]:   # sim restarted
                        STATE["snapshots"].clear(); STATE["events"].clear()
                    STATE["snapshots"].append(snap)
            ev = get_json(CONFIG["bridge"], f"/api/events?since={STATE['event_seq']}")
            with LOCK:
                if ev["last_seq"] < STATE["event_seq"]:                                 # bridge restarted
                    STATE["event_seq"] = 0
                for e in ev["events"]:
                    STATE["events"].append(e)
                    STATE["event_seq"] = max(STATE["event_seq"], e["seq"])
        except Exception as e:
            err("poll bridge", e)
        time.sleep(max(0.0, 1.0 - (time.time() - t0)))


def ticket_loop():
    """Turns ticket changes into dashboard visit events and ops-log lines."""
    while True:
        try:
            for tk in get_json(CONFIG["tools"], "/tickets")["tickets"]:
                with LOCK:
                    old = STATE["tickets"].get(tk["id"])
                    STATE["tickets"][tk["id"]] = tk
                if old is None:
                    if (time.time() - STATE["started"]) > 5:     # new since we started, not history
                        add_trans(tk, "need.opened", "Supervisor")
                elif old["status"] != tk["status"]:
                    add_trans(tk, f"ticket.{tk['status']}", last_actor(tk))
        except Exception as e:
            err("poll tickets", e)
        time.sleep(1.0)


def last_actor(tk):
    """Who made the latest change, from the ticket's own history (supervisor / scheduler / human)."""
    hist = tk.get("history") or []
    actor = (hist[-1].get("actor") if hist else None) or "unknown"
    return {"supervisor": "Supervisor", "scheduler": "Scheduler", "human": "Human"}.get(actor, actor)


def add_trans(tk, kind, by):
    with LOCK:
        STATE["trans"].append({"id": f"V{len(STATE['trans']) + 1}", "need_id": tk["id"], "type": kind,
                               "at": now_iso(), "by": by,
                               "data": {"status": tk["status"], "priority": tk.get("priority"),
                                        "event_type": tk.get("event_type")}})
    who = robot_name(tk.get("robot_id")) or (f"zone {tk['zone']}" if tk.get("zone") is not None else "")
    note("ops-log", by, f"{tk['id']} {tk['status']} · {tk.get('event_type')} · {who} · {tk.get('priority')}")


def fleetlog_loop():
    while True:
        time.sleep(5)
        if CONFIG["navbot"] and time.time() - STATE["fleetlog_last"] >= CONFIG["fleetlog_every_s"]:
            STATE["fleetlog_last"] = time.time()
            push_fleetlog(5)


# ---------------- dashboard views ----------------
def robot_status(r):
    if r.get("error_code") == "DRIVE_FAULT":
        return "blocked"
    if r.get("error_code"):
        return "warning"
    if r.get("task") == "charge" and not r.get("moving"):
        return "charging"
    if r.get("waiting_for_traffic"):
        return "waiting"
    return "ok"


def warehouse_view():
    fleet, m = STATE["fleet"], STATE["metrics"]
    if not fleet or not m:
        return []
    robots = [{"id": robot_name(r["id"]), "zone": r["zone"], "task": r["task"], "battery": r["battery_pct"],
               "status": robot_status(r), "temp": r.get("motor_temp_c"), "location": r.get("location")}
              for r in fleet["robots"]]
    active = fleet.get("active_events") or []
    worst = next((e for e in active if e.get("severity") == "critical"), active[0] if active else None)
    open_tk = sorted((t for t in STATE["tickets"].values() if t["status"] not in ("resolved",)),
                     key=lambda t: t.get("created_at") or "")
    orders = m.get("orders", {})
    at_risk = orders.get("at_risk") or []
    return [{
        "site": CONFIG["site"], "name": CONFIG["site_name"], "area": CONFIG["site_area"],
        "health": "degraded" if active else "ok",
        "health_note": (worst or {}).get("detail") if worst else "all robots nominal",
        "need_id": open_tk[-1]["id"] if open_tk else None,
        "robots_active": sum(1 for r in robots if r["status"] not in ("blocked",) and r["task"] != "idle"),
        "robots_total": len(robots), "robots": robots,
        "throughput": {"unit": "deliveries/min", "current": m["throughput"]["deliveries_last_min"],
                       "series": list(STATE["throughput"])},
        "backlog": {"count": orders.get("open", 0),
                    "note": f"{len(at_risk)} at risk, {len(orders.get('late') or [])} late",
                    "need_id": open_tk[-1]["id"] if open_tk else None},
        "consumables": [],
    }]


def reliability_view():
    try:
        subjects = get_json(CONFIG["tools"], "/trust")["subjects"]
    except Exception as e:
        err("trust", e); return []
    return [{"site": CONFIG["site"], "skill": s["subject"], "policy": s.get("policy"), "trials": s.get("trials", 0),
             "successes": s.get("successes", 0), "confidence": s.get("p_rate_at_least_80"),
             "gate": "open" if s.get("trusted") else "closed", "threshold": 0.95, "target_rate": 0.8,
             "last_tested": hms_iso(s.get("last_commissioned")), "note": s.get("what")} for s in subjects]


def commissioning_view():
    try:
        job = get_json(CONFIG["tools"], "/commission/latest")
    except Exception as e:
        err("commission", e); return None
    if not job or job.get("status") in (None, "none"):
        return None
    batches = job.get("batches") or []
    arms = list(batches[-1]["counts"]) if batches else []
    trials, n = [], 0
    for b in batches:
        for arm, outs in (b.get("ran") or {}).items():
            for o in outs:
                n += 1
                trials.append({"n": n, "policy": arm, "success": bool(o), "at": None,
                               "confidence": (b.get("p_at_least") or {}).get(arm)})
    last = batches[-1] if batches else {}
    passed = job.get("status") == "done" and job.get("decision") == "pass"
    pols = []
    for a in arms:
        s_, n_ = (int(x) for x in last["counts"][a].split("/"))
        st = "passed" if passed and job.get("policy") == a else ("dropped" if job.get("status") == "done" else "active")
        pols.append({"id": a, "name": a, "trials": n_, "successes": s_,
                     "confidence": (last.get("p_at_least") or {}).get(a), "status": st})
    return {"need_id": None, "site": CONFIG["site"], "skill": job.get("subject"),
            "status": "running" if job.get("status") == "running" else ("gate_open" if passed else "gate_closed"),
            "target_rate": 0.8, "gate_threshold": 0.95, "started_at": hms_iso(job.get("started")),
            "gate_opened_at": hms_iso(job.get("finished")) if passed else None,
            "gate_trial": job.get("trials") if passed else None, "winner": job.get("policy") if passed else None,
            "policies": pols, "trials": trials, "id": job.get("id")}


def needs_view():
    sev = {"critical": "P1", "high": "P1", "medium": "P2", "low": "P3"}
    out = []
    for t in sorted(STATE["tickets"].values(), key=lambda t: t.get("created_at") or ""):
        ev = (t.get("evidence") or {}).get("event") or {}
        out.append({"need_id": t["id"], "source": "fleet.supervisor", "kind": t.get("event_type"),
                    "title": (t.get("reason") or t.get("event_type") or "")[:140],
                    "severity": sev.get(t.get("priority"), "P2"), "site": CONFIG["site"], "skill_req": None,
                    "deadline": hms_iso(t.get("needed_by")), "duration_min": 15, "bring": [],
                    "evidence": {"robot": robot_name(t.get("robot_id")), "zone": t.get("zone"),
                                 "event_id": t.get("event_id"), "detail": ev.get("detail")},
                    # the dashboard treats "closed" as finished; the supervisor calls that "resolved"
                    "status": "closed" if t["status"] == "resolved" else t["status"],
                    "supervisor_status": t["status"], "assigned_staff": t.get("assignee"),
                    "opened_at": hms_iso(t.get("created_at")), "updated_at": hms_iso(t.get("updated_at"))})
    return out


_cams_cache = {"at": 0.0, "info": None}


def isaac_cams():
    """The Isaac camera server's /cams, cached for 5 s; None when Isaac is not running with --cams."""
    if time.time() - _cams_cache["at"] > 5:
        try:
            _cams_cache["info"] = get_json(CONFIG["isaac_cams"], "/cams", timeout=0.5)
        except Exception:
            _cams_cache["info"] = None
        _cams_cache["at"] = time.time()
    return _cams_cache["info"]


def feeds_view(base):
    feeds = [{"id": "map", "label": "Floor map", "site": CONFIG["site"], "robot_id": None, "kind": "image",
              "url": f"{base}/feeds/map.svg", "refresh_ms": 500, "proxy": True}]
    cams = isaac_cams()
    if cams:   # real Isaac Sim cameras: fixed views, then one chase camera per robot
        for c in cams.get("fixed", []):
            feeds.append({"id": f"isaac-{c['id']}", "label": c["label"], "site": CONFIG["site"], "robot_id": None,
                          "kind": "mjpeg", "url": f"{CONFIG['isaac_cams']}/stream/{c['id']}", "proxy": True})
        for i in range(int(cams.get("robots", 0))):
            feeds.append({"id": f"isaac-robot-{i}", "label": f"{robot_name(i)} · chase camera", "site": CONFIG["site"],
                          "robot_id": robot_name(i), "kind": "mjpeg",
                          "url": f"{CONFIG['isaac_cams']}/stream/robot/{i}", "proxy": True})
    if CONFIG["isaac_stream"]:
        feeds.insert(0, {"id": "isaac", "label": "Isaac Sim", "site": CONFIG["site"], "robot_id": None,
                         "kind": "iframe", "url": CONFIG["isaac_stream"]})   # loaded by the viewer's browser directly
    return {"feeds": feeds}


def map_svg():
    lay, tel = STATE["layout"], STATE["telemetry"]
    if not lay or not tel:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200"><text x="20" y="100" fill="#999">waiting for the sim</text></svg>'
    xs = [a["x"] for a in lay["aisles"]]
    ybot, ytop = lay["cross_lanes"]["bottom_y"], lay["cross_lanes"]["top_y"]
    x0, x1, y0, y1 = min(xs) - 5, max(xs) + 5, ybot - 5, ytop + 5
    W, H = x1 - x0, y1 - y0
    X = lambda x: round(x - x0, 2)
    Y = lambda y: round(y1 - y, 2)                     # north up
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.1f} {H:.1f}" preserveAspectRatio="xMidYMid meet">',
         f'<rect width="{W:.1f}" height="{H:.1f}" fill="#12161d"/>']
    for a in lay["aisles"]:
        for side in (-1, 1):
            p.append(f'<rect x="{X(a["x"] + side * 1.9 - 0.85)}" y="{Y(ytop - 2.5)}" width="1.7" '
                     f'height="{round(ytop - ybot - 5, 2)}" fill="#2a3240" rx="0.2"/>')
        p.append(f'<line x1="{X(a["x"])}" y1="{Y(ybot)}" x2="{X(a["x"])}" y2="{Y(ytop)}" stroke="#c9b13a" stroke-width="0.08"/>')
    for yy in (ybot, ytop):
        p.append(f'<line x1="{X(min(xs))}" y1="{Y(yy)}" x2="{X(max(xs))}" y2="{Y(yy)}" stroke="#c9b13a" stroke-width="0.08"/>')
    for (sx, sy) in lay.get("pack_stations", []):
        p.append(f'<rect x="{X(sx) - 0.5}" y="{Y(sy) - 0.5}" width="1" height="1" fill="#1f7a55" rx="0.15"/>')
    for (dx, dy) in lay.get("charge_docks", []):
        p.append(f'<rect x="{X(dx) - 0.5}" y="{Y(dy) - 0.5}" width="1" height="1" fill="#1d5c80" rx="0.15"/>')
    col = {"pick": "#4cc9f0", "carry": "#b388ff", "charge": "#22d3ee", "idle": "#8a94a6"}
    for r in tel["robots"]:
        c = "#ff5d5d" if r.get("error") else ("#ffb020" if r.get("yielding") else col.get(r.get("task"), "#cfd6e4"))
        th = math.radians(r.get("theta") or 0)
        hx, hy = X(r["x"] + 0.55 * math.cos(th)), Y(r["y"] + 0.55 * math.sin(th))
        p.append(f'<circle cx="{X(r["x"])}" cy="{Y(r["y"])}" r="0.42" fill="{c}"/>'
                 f'<line x1="{X(r["x"])}" y1="{Y(r["y"])}" x2="{hx}" y2="{hy}" stroke="#0b0e13" stroke-width="0.12"/>'
                 f'<text x="{X(r["x"])}" y="{Y(r["y"]) - 0.6}" font-size="0.55" fill="#e8edf5" text-anchor="middle" '
                 f'font-family="monospace">{r["id"]:02d}</text>')
    p.append(f'<text x="0.6" y="{H - 0.6:.1f}" font-size="0.8" fill="#8a94a6" font-family="monospace">'
             f'sim t={tel["t"]:.0f}s · {sum(1 for r in tel["robots"] if r.get("vel", 0) > 0.05)}/{len(tel["robots"])} moving'
             f' · red=fault amber=waiting</text>')
    p.append("</svg>")
    return "".join(p)


# ---------------- fleet log (live, same format as fleet_log_5min.json) ----------------
def build_fleetlog(minutes=5.0):
    with LOCK:
        snaps = list(STATE["snapshots"]); evs = list(STATE["events"])
    if not snaps:
        return None
    t_now = snaps[-1]["t"]
    t_from = t_now - minutes * 60
    snaps = [s for s in snaps if s["t"] >= t_from]
    evs = [{k: v for k, v in e.items() if k not in ("seq", "wall")} for e in evs if e["t"] >= t_from]
    per = defaultdict(Counter); wait = defaultdict(float)
    for e in evs:
        if e.get("robot") is not None and e["type"] != "demo_command":
            per[e["robot"]][e["type"]] += 1
        if e["type"] == "resumed":
            wait[e["robot"]] += e.get("waited_s", 0)
    last = snaps[-1]
    robots = [{"id": r["id"], "zone": r["zone"], "deliveries": per[r["id"]]["delivered"], "picks": per[r["id"]]["picked"],
               "charges": per[r["id"]]["charging_done"], "errors_raised": per[r["id"]]["error_raised"],
               "logged_waits": per[r["id"]]["waiting"], "logged_wait_s": round(wait[r["id"]], 1),
               "battery_end": r["battery"], "task_end": r["task"]} for r in last["robots"]]
    zones = [dict(z, robots=[r["id"] for r in last["robots"] if r["zone"] == z["zone"]],
                  deliveries=sum(1 for e in evs if e["type"] == "delivered" and e.get("zone") == z["zone"]))
             for z in last["zone_stock"]]
    return {"meta": {"what": "FleetOps live fleet log (real sim, via the GB10 sim bridge)",
                     "generated_by": "fleetops_glue.py", "generated_at": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
                     "sim_seconds": round(t_now, 1), "window_s": round(t_now - snaps[0]["t"], 1), "robots": len(last["robots"]),
                     "units": {"position": "metres", "theta": "degrees", "vel": "m/s", "temp": "C", "battery": "%",
                               "t": "seconds since the sim started"},
                     "layout": STATE["layout"], "snapshot_every_s": 5.0, "scripted_faults": []},
            "summary": {"picks": sum(1 for e in evs if e["type"] == "picked"),
                        "deliveries": sum(1 for e in evs if e["type"] == "delivered"),
                        "event_counts": dict(Counter(e["type"] for e in evs).most_common()),
                        "robots": robots, "zones": zones},
            "events": evs, "snapshots": snaps}


def navbot_post(path, body=None, raw=None, ctype="application/json"):
    if not CONFIG["navbot"]:
        return None
    for attempt in range(3):
        try:
            code, resp = send_json(CONFIG["navbot"] + path, body, raw=raw, ctype=ctype,
                                   headers={"X-Bot-Key": CONFIG["bot_key"]}, timeout=10)
            STATE["navbot_sent"] += 1
            return code, resp
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:200]
            err(f"navbot {path}", f"HTTP {e.code} {msg}")
            if e.code < 500:
                break
        except Exception as e:
            err(f"navbot {path}", e)
        time.sleep(1 + attempt)
    STATE["navbot_failed"] += 1
    return None


def push_fleetlog(minutes=5.0):
    log = build_fleetlog(minutes)
    if log:
        r = navbot_post("/telemetry", raw=json.dumps(log).encode(), ctype="application/json")
        note("ops-log", "glue", f"fleet log ({len(log['events'])} events, {len(log['snapshots'])} snapshots) "
                                + ("sent to navbot" if r else "NOT delivered to navbot"))
        return r


# ---------------- supervisor -> navbot ----------------
def on_ticket_webhook(body):
    tk = body.get("ticket") or {}
    if not tk.get("id"):
        return {"ok": False, "error": "no ticket"}
    with LOCK:
        STATE["tickets"][tk["id"]] = {**STATE["tickets"].get(tk["id"], {}), **tk}
    if tk.get("status") != "open":
        return {"ok": True, "forwarded": False, "why": f"status {tk.get('status')}"}
    ev = (tk.get("evidence") or {}).get("event") or {}
    facts = ev.get("facts") or {}
    rname = robot_name(tk.get("robot_id"))
    where = facts.get("location") or (f"zone {tk['zone']}" if tk.get("zone") is not None else "")
    card = {"event": "TASK_FAILED", "site": CONFIG["site_name"], "cell": where,
            "skill": facts.get("task") or tk.get("event_type"), "passed": 0, "run": 1,
            "robot": rname, "error_code": facts.get("error_code") or (tk.get("event_type") or "").upper(),
            # the supervisor LLM's ticket text is the card's "Issue"; navbot's own LLM writes the debrief
            "failure_note": tk.get("reason"),
            "ticket_id": tk["id"], "deadline": tk.get("needed_by")}
    r1 = navbot_post("/events", card)
    r2 = None
    if CONFIG["approvals"] and tk.get("event_type") in SCHEDULE_TYPES | {"inventory_mismatch", "low_stock"}:
        rid = f"{tk['id']}-{int(time.time())}"
        with LOCK:
            STATE["requests"][rid] = tk["id"]
        # navbot stores the LAST context line as the request summary, so the problem itself goes last
        ctx = [f"{tk['id']} · {tk.get('event_type')} · {rname or where}",
               f"Priority {tk.get('priority')} · needed by {tk.get('needed_by') or 'n/a'}",
               "✅ approve: the Scheduler reassigns the work (ticket → rescheduled)",
               "❌ deny: hold for a person to decide (ticket → acknowledged)",
               tk.get("reason") or ""]
        r2 = navbot_post("/events", {"event": "APPROVAL_REQUEST", "request_id": rid, "kind": "SCHEDULE_CHANGE",
                                     "ticket_id": tk["id"], "context": ctx,
                                     "options": [{"emoji": "✅", "choice": "approve", "label": "Reassign work"},
                                                 {"emoji": "❌", "choice": "deny", "label": "Hold"}]})
    note("ops-log", "Supervisor", f"{tk['id']} opened · {tk.get('event_type')} · {rname or where} · "
                                  + ("posted to Discord" if r1 else "Discord not reached"))
    return {"ok": True, "forwarded": bool(r1), "approval_requested": bool(r2)}


def patch_ticket(tid, body):
    try:
        return send_json(f"{CONFIG['tools']}/tickets/{tid}", body, method="PATCH")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:300]


def on_decision(body):
    rid, status = body.get("request_id"), body.get("status")
    tid = STATE["requests"].get(rid) or (rid.rsplit("-", 1)[0] if rid and rid.startswith("T-") else None)
    if not tid:
        return 404, {"ok": False, "error": f"unknown request {rid}"}
    who = body.get("approved_by") or "approver"
    steps = [{"actor": "scheduler", "status": "acknowledged", "note": f"{status} in Discord by {who}"}]
    if status == "approved":
        steps.append({"actor": "scheduler", "status": "rescheduled", "assignee": "Scheduler",
                      "note": f"work reassigned (approved by {who})"})
    results = [patch_ticket(tid, s) for s in steps]
    note("ops-log", "Scheduler", f"{tid} {status} by {who} → " + ", ".join(str(r[0]) for r in results))
    return 200, {"ok": True, "ticket": tid, "results": [r[0] for r in results]}


# ---------------- http ----------------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def out(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else (body.encode() if isinstance(body, str) else json.dumps(body).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in (("Access-Control-Allow-Origin", "*"), ("Access-Control-Allow-Headers", "Content-Type, X-Bot-Key"),
                     ("Access-Control-Allow-Methods", "GET, POST, OPTIONS")):
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query); p = u.path
        base = f"http://{self.headers.get('Host', '127.0.0.1:%d' % CONFIG['port'])}"
        try:
            if p == "/health":
                return self.out(200, {"ok": STATE["fleet"] is not None and STATE["telemetry"] is not None,
                                      "tickets": len(STATE["tickets"]), "events_buffered": len(STATE["events"]),
                                      "snapshots": len(STATE["snapshots"]), "navbot": CONFIG["navbot"] or None,
                                      "navbot_sent": STATE["navbot_sent"], "navbot_failed": STATE["navbot_failed"],
                                      "errors": list(STATE["errors"])[-5:]})
            if p == "/clock":
                return self.out(200, {"sim_time": now_iso(), "speed": 1})
            if p == "/warehouse":
                return self.out(200, {"sites": warehouse_view()})
            if p == "/reliability":
                return self.out(200, {"reliability": reliability_view()})
            if p == "/commissioning":
                return self.out(200, {"commissioning": commissioning_view()})
            if p == "/feeds":
                return self.out(200, feeds_view(base))
            if p == "/feeds/map.svg":
                return self.out(200, map_svg(), "image/svg+xml")
            if p == "/needs":
                return self.out(200, {"needs": needs_view()})
            if p == "/events":
                return self.out(200, {"events": list(STATE["trans"])})
            if p == "/messages":
                return self.out(200, {"messages": list(STATE["msgs"])})
            if p == "/fleetlog":
                log = build_fleetlog(float(q.get("minutes", ["5"])[0]))
                return self.out(200 if log else 503, log or {"error": "no snapshots yet"})
            return self.out(404, {"error": "not found"})
        except Exception as e:
            err(f"GET {p}", e)
            return self.out(500, {"error": repr(e)[:200]})

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.out(400, {"error": "bad json"})
        if p == "/hooks/scheduler":
            return self.out(200, on_ticket_webhook(body))
        if p == "/decisions":
            if CONFIG["bot_key"] and self.headers.get("X-Bot-Key") != CONFIG["bot_key"]:
                return self.out(401, {"error": "bad key"})
            code, res = on_decision(body)
            return self.out(code, res)
        if p == "/fleetlog/push":
            r = push_fleetlog(float(body.get("minutes", 5)))
            return self.out(200, {"ok": bool(r), "navbot_response": r})
        if p in ("/demo/reset", "/demo/fire", "/clock/set", "/clock/speed", "/clock/reset"):
            return self.out(200, {"ok": True, "note": "the real sim runs on real time; nothing to reset"})
        return self.out(404, {"error": "not found"})


if __name__ == "__main__":
    STATE["fleetlog_last"] = time.time()     # first automatic fleet log after a full interval, not at startup
    for target in (poll_loop, ticket_loop, fleetlog_loop):
        threading.Thread(target=target, daemon=True).start()
    print(f"fleetops glue on {CONFIG['host']}:{CONFIG['port']} | supervisor {CONFIG['tools']} | bridge {CONFIG['bridge']} "
          f"| navbot {CONFIG['navbot'] or 'off'}", flush=True)
    ThreadingHTTPServer((CONFIG["host"], CONFIG["port"]), H).serve_forever()
