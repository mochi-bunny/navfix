"""navbot adapters: build the dashboard's Fleet data from the Discord bot's SQLite (data/bot.db).

navbot records every supervisor event it posts (`events`), every approval (`approvals`), every
Discord action (`activity`) and every telemetry report (`telemetry_reports`, `robot_readings`,
`fleet_events`, `fleet_snapshots`). These adapters turn those rows into the shapes in INTERFACES.md
for three sources: tickets (Needs + visit timeline), cell (reliability, commissioning, warehouse)
and messages (channel mirror + ops log). What each metric is and how it's worked out is in METRICS.md.

The database is opened read-only; navbot stays the only writer.
"""
import json
import re
import sqlite3
from pathlib import Path

from .. import stats
from .base import CellSource, MessageSource, TicketSource

TARGET_RATE, GATE_THRESHOLD = 0.8, 0.95  # the gate rule navbot cards show: ≥80% success at 95% confidence
SITE_RE = re.compile(r"^\s*([^(]+?)\s*(?:\(([^)]*)\))?\s*$")


class NavbotDB:
    """Shared read-only access to navbot's database."""

    def __init__(self, cfg):
        self.path = Path(cfg["db_path"])

    def query(self, sql, args=()):
        if not self.path.exists():
            raise FileNotFoundError(str(self.path))
        con = sqlite3.connect(f"file:{self.path.resolve().as_posix()}?mode=ro", uri=True, timeout=1)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, args)]
        except sqlite3.OperationalError:  # table not created yet: navbot hasn't seen that kind of data
            return []
        finally:
            con.close()

    def events(self):
        """Every supervisor event navbot posted, oldest first, with its posting time."""
        out = []
        for r in self.query("SELECT event, payload, posted_at FROM events ORDER BY posted_at, id"):
            try:
                ev = json.loads(r["payload"] or "{}")
            except ValueError:
                continue
            ev.setdefault("event", r["event"])
            ev["_at"] = r["posted_at"]
            out.append(ev)
        return out


def site_id(site):
    """'Globex (Kendall Square)' -> ('globex', 'Globex', 'Kendall Square')."""
    m = SITE_RE.match(str(site or ""))
    name = (m.group(1) if m else str(site)).strip()
    return name.split()[0].lower() if name else "", name, (m.group(2) if m else None)


def confidence(ev):
    """P(true success rate ≥ 80%). The supervisor's figure when it sends one, else worked out from passed/run."""
    if ev.get("confidence_pct") is not None:
        return float(ev["confidence_pct"]) / 100
    try:
        return round(stats.p_rate_at_least(int(ev["passed"]), int(ev["run"]), TARGET_RATE), 4)
    except (KeyError, TypeError, ValueError):
        return None


def _ticket_by_request(events):
    return {e["request_id"]: e.get("ticket_id") for e in events
            if e["event"] == "APPROVAL_REQUEST" and e.get("request_id")}


# ---------------------------------------------------------------- tickets ---
class NavbotTickets(TicketSource):
    """Needs = tickets navbot has seen. Status moves open → proposed → booked → closed from the events."""

    def __init__(self, db):
        self.db = db

    def read(self):
        events = self.db.events()
        req_ticket = _ticket_by_request(events)
        needs, timeline = {}, []

        def step(ticket, step_type, ev, by, **data):
            timeline.append({"id": f"nb-{len(timeline) + 1}", "need_id": ticket, "type": step_type, "at": ev["_at"],
                             "by": by, "data": {k: v for k, v in data.items() if v not in (None, "", [])}})

        for ev in events:
            t, ticket = ev["event"], ev.get("ticket_id")
            if t == "APPROVAL_DECISION":
                ticket = req_ticket.get(ev.get("request_id"))
            if not ticket:
                continue
            n = needs.get(ticket)
            if t in ("SKILL_REFUSED", "TASK_FAILED"):
                sid, name, _ = site_id(ev.get("site"))
                skill = str(ev.get("skill", ""))
                refused = t == "SKILL_REFUSED"
                if n is None:
                    n = needs[ticket] = {"need_id": ticket, "opened_at": ev["_at"], "status": "open",
                                         "evidence": {}, "bring": [], "booked_slot": None}
                    step(ticket, "need.opened", ev, "Fleet", site=name, skill=skill,
                         result=f"{ev.get('passed')}/{ev.get('run')}", deadline=ev.get("deadline"))
                n.update(
                    source="fleet.reliability" if refused else "fleet.tasks",
                    kind="skill_unproven" if refused else "task_failed",
                    title=f"{name}: {skill.replace('_', ' ')} "
                          f"{'refused' if refused else 'failed'} ({ev.get('passed')}/{ev.get('run')})",
                    site=sid, skill_req=skill, deadline=ev.get("deadline") or n.get("deadline"),
                    robot=ev.get("robot"), error_code=ev.get("error_code"), updated_at=ev["_at"],
                    evidence={**n["evidence"], "trials_run": ev.get("run"), "successes": ev.get("passed"),
                              "note": ev.get("failure_note"), "clips": ev.get("clips") or []},
                )
                if n["status"] == "closed":  # failed again after closing: reopen
                    n["status"] = "open"
            elif n is None:
                continue
            elif t == "APPROVAL_REQUEST":
                n.update(status="proposed", updated_at=ev["_at"])
                step(ticket, "visit.proposed", ev, "Field", kind=ev.get("kind"),
                     plan=" / ".join(ev.get("context") or []))
            elif t == "APPROVAL_DECISION":
                ok = ev.get("choice") in ("book", "approve") or ev.get("status") == "approved"
                n.update(status="booked" if ok else "open", updated_at=ev["_at"],
                         assigned_staff=n.get("assigned_staff"))
                step(ticket, "visit.booked" if ok else "visit.declined", ev, ev.get("approved_by"),
                     choice=ev.get("choice"))
            elif t == "ETA_UPDATE":
                n.update(updated_at=ev["_at"], assigned_staff=ev.get("engineer"))
                step(ticket, "eta.updated", ev, "Field", engineer=ev.get("engineer"), eta=ev.get("eta"),
                     reason=ev.get("reason"))
            elif t == "GATE_OPEN":
                n.update(status="closed", updated_at=ev["_at"],
                         evidence={**n["evidence"], "trials_run": ev.get("run"), "successes": ev.get("passed"),
                                   "policy": ev.get("policy")})
                step(ticket, "visit.completed", ev, "Fleet", gate="open", policy=ev.get("policy"),
                     result=f"{ev.get('passed')}/{ev.get('run')}", confidence=f"{ev.get('confidence_pct')}%")
            elif t == "JOB_COMPLETED":
                step(ticket, "order.shipped", ev, "Fleet", order=ev.get("order_id"),
                     duration_s=ev.get("duration_s"))
        return {"needs": list(needs.values()), "visit_events": timeline}


# ------------------------------------------------------------------- cell ---
class NavbotCell(CellSource):
    def __init__(self, db):
        self.db = db

    def read(self):
        events = self.db.events()
        return {"reliability": self._reliability(events), "commissioning": self._commissioning(events),
                "warehouse": self._warehouse(events)}

    @staticmethod
    def _reliability(events):
        """One row per site × skill: the latest gate result (SKILL_REFUSED = closed, GATE_OPEN = open)."""
        rows = {}
        for ev in events:
            if ev["event"] not in ("SKILL_REFUSED", "GATE_OPEN"):
                continue
            sid, _, _ = site_id(ev.get("site"))
            rows[(sid, ev.get("skill"))] = {
                "site": sid, "skill": ev.get("skill"), "policy": ev.get("policy"),
                "trials": ev.get("run"), "successes": ev.get("passed"), "confidence": confidence(ev),
                "gate": "open" if ev["event"] == "GATE_OPEN" else "closed",
                "threshold": GATE_THRESHOLD, "target_rate": TARGET_RATE,
                "last_tested": ev["_at"], "need_id": ev.get("ticket_id"),
                "note": ev.get("failure_note") or ev.get("gate"),
            }
        return sorted(rows.values(), key=lambda r: (r["site"], str(r["skill"])))

    @staticmethod
    def _commissioning(events):
        """The latest GATE_OPEN. navbot only gets the result, not trial-by-trial data, so `trials` is empty."""
        ev = next((e for e in reversed(events) if e["event"] == "GATE_OPEN"), None)
        if not ev:
            return None
        sid, _, _ = site_id(ev.get("site"))
        name = ev.get("policy")
        return {"need_id": ev.get("ticket_id"), "site": sid, "skill": ev.get("skill"), "status": "gate_open",
                "target_rate": TARGET_RATE, "gate_threshold": GATE_THRESHOLD, "started_at": None,
                "gate_opened_at": ev["_at"], "gate_trial": ev.get("total_trials"), "winner": name,
                "baseline_trials": ev.get("baseline_trials"),
                "policies": [{"id": name, "name": f"Policy {name}", "trials": ev.get("run"),
                              "successes": ev.get("passed"), "confidence": confidence(ev), "status": "passed"}],
                "trials": []}

    def _warehouse(self, events):
        sites = {}

        def site(sid, name, area=None):
            return sites.setdefault(sid, {"site": sid, "name": name, "area": area, "health": "ok",
                                          "health_note": None, "need_id": None, "robots": [],
                                          "robots_active": 0, "robots_total": 0, "throughput": None,
                                          "backlog": None, "consumables": []})

        # Customer sites from the hourly telemetry CSV (latest report that isn't a fleet log).
        latest_csv = self.db.query(
            "SELECT id, received_at FROM telemetry_reports WHERE id NOT IN "
            "(SELECT DISTINCT report_id FROM fleet_events) ORDER BY id DESC LIMIT 1")
        if latest_csv:
            for r in self.db.query("SELECT * FROM robot_readings WHERE report_id=?", (latest_csv[0]["id"],)):
                w = site(*site_id(r["site"]))
                w["robots"].append({"id": r["robot"], "zone": None, "task": r["status"],
                                    "battery": r["battery_pct"], "temp_c": r["temp_c"],
                                    "status": {"DOWN": "blocked", "WARN": "degraded"}.get(r["health"], "ok"),
                                    "health": r["health"], "reasons": r["reasons"]})

        # The warehouse fleet from the latest JSON fleet log.
        rep = self.db.query("SELECT MAX(report_id) AS id FROM fleet_events")
        rid = rep[0]["id"] if rep else None
        if rid:
            w = site("fleetops", "FleetOps warehouse", "20 AMRs")
            snaps = self.db.query(
                "SELECT * FROM fleet_snapshots WHERE report_id=? AND t=(SELECT MAX(t) FROM fleet_snapshots "
                "WHERE report_id=?) ORDER BY robot", (rid, rid))
            for s in snaps:
                status = "blocked" if s["error"] else "charging" if s["task"] == "charge" else "ok"
                w["robots"].append({"id": s["robot"], "zone": s["zone"], "task": s["task"],
                                    "battery": s["battery"], "status": status, "error": s["error"]})
            # Throughput: deliveries per minute of the log, so the sparkline shows the trend.
            per_min = {}
            first = None
            for e in self.db.query("SELECT t, at FROM fleet_events WHERE report_id=? AND type='delivered' "
                                   "ORDER BY t", (rid,)):
                minute = int(e["t"] // 60)
                per_min[minute] = per_min.get(minute, 0) + 1
                first = first or e
            if per_min:
                series = [{"t": m, "v": per_min.get(m, 0)} for m in range(max(per_min) + 1)]
                w["throughput"] = {"unit": "deliveries/min", "current": series[-1]["v"], "series": series,
                                   "total": sum(per_min.values())}

        # Health, counts and open tickets per site.
        open_ticket = {}
        for ev in events:
            if ev["event"] in ("SKILL_REFUSED", "TASK_FAILED") and ev.get("ticket_id"):
                open_ticket[site_id(ev.get("site"))[0]] = ev["ticket_id"]
            elif ev["event"] == "GATE_OPEN" and ev.get("ticket_id"):
                sid = site_id(ev.get("site"))[0]
                if open_ticket.get(sid) == ev["ticket_id"]:
                    del open_ticket[sid]
        for ev in events:  # sites only mentioned in events still get a row
            if ev.get("site"):
                site(*site_id(ev["site"]))
        for sid, w in sites.items():
            w["robots_total"] = len(w["robots"])
            w["robots_active"] = sum(r["status"] != "blocked" for r in w["robots"])
            bad = [r for r in w["robots"] if r["status"] in ("blocked", "degraded")]
            w["need_id"] = open_ticket.get(sid)
            if w["need_id"]:
                w["health"], w["health_note"] = "degraded", f"{w['need_id']} open"
            if bad:
                w["health"] = "degraded"
                w["health_note"] = ", ".join(f"{r['id']} {r['status']}" for r in bad[:4])
        return sorted(sites.values(), key=lambda w: w["site"])


# --------------------------------------------------------------- messages ---
# navbot's Discord channels -> the dashboard's channels. Handoffs go to the ops log.
CHANNEL = {"DAILY_SUMMARY": "field", "DAILY_RECAP": "field", "APPROVAL_REQUEST": "field",
           "ETA_UPDATE": "cell-globex"}


def _line(ev, ticket_of):
    """One readable line per event, built only from its fields."""
    t, tk = ev["event"], ev.get("ticket_id") or ticket_of.get(ev.get("request_id"))
    pre = f"{tk} " if tk else ""
    _, site_name, _ = site_id(ev.get("site"))
    skill = str(ev.get("skill", "")).replace("_", " ")
    if t == "SKILL_REFUSED":
        return f"{pre}opened · {site_name} {skill} refused {ev.get('passed')}/{ev.get('run')}" + \
            (f" · engineer by {ev['deadline']}" if ev.get("deadline") else "")
    if t == "TASK_FAILED":
        return f"{pre}task failed · {site_name} {skill} {ev.get('passed')}/{ev.get('run')}"
    if t == "GATE_OPEN":
        return f"{pre}gate open · {site_name} {skill} · policy {ev.get('policy')} {ev.get('passed')}/{ev.get('run')}"
    if t == "JOB_DISPATCHED":
        return f"Order {ev.get('order_id')} dispatched · {site_name}"
    if t == "JOB_COMPLETED":
        m, s = divmod(int(ev.get("duration_s") or 0), 60)
        return f"Order {ev.get('order_id')} completed · {site_name} · {m}m {s:02d}s"
    if t == "APPROVAL_REQUEST":
        return f"{pre}approval needed · {ev.get('kind')} · " + " ".join(ev.get("context") or [])
    if t == "APPROVAL_DECISION":
        return f"{pre}{ev.get('status') or ev.get('choice')} · {ev.get('approved_by')}"
    if t == "ETA_UPDATE":
        return f"{pre}{ev.get('engineer')} now arriving {ev.get('eta')} at {site_name}" + \
            (f" · {ev['reason']}" if ev.get("reason") else "")
    if t == "INFRA_ERROR":
        return f"{ev.get('source')}: {ev.get('message')}"
    if t in ("DAILY_SUMMARY", "DAILY_RECAP"):
        counts = ", ".join(f"{n} {k}" for k, n in (ev.get("counts") or {}).items())
        return f"{'Morning brief' if t == 'DAILY_SUMMARY' else 'Recap'} · {ev.get('date')}" + \
            (f" · {counts}" if counts else "")
    if t == "TELEMETRY":
        return f"Robot health report #{ev.get('report_id')} ({ev.get('format')})"
    return t


class NavbotMessages(MessageSource):
    def __init__(self, db):
        self.db = db

    def read(self):
        events = self.db.events()
        ticket_of = _ticket_by_request(events)
        out = []
        for i, ev in enumerate(events):
            text = _line(ev, ticket_of)
            author = ev.get("approved_by") if ev["event"] == "APPROVAL_DECISION" else \
                "Field" if CHANNEL.get(ev["event"]) == "field" else "Fleet"
            out.append({"id": f"nb-{i}", "channel": CHANNEL.get(ev["event"], "ops-log"), "author": author,
                        "bot": ev["event"] != "APPROVAL_DECISION", "text": text, "at": ev["_at"]})
        # Requests people typed in #approvals, and their outcome.
        for a in self.db.query("SELECT * FROM approvals WHERE source='discord' ORDER BY requested_at"):
            out.append({"id": f"nb-req-{a['request_id']}", "channel": "field", "author": a["requested_by"],
                        "bot": False, "text": a["details"] or a["summary"] or "", "at": a["requested_at"]})
            if a["status"] in ("approved", "denied"):
                out.append({"id": f"nb-dec-{a['request_id']}", "channel": "ops-log",
                            "author": a["decided_by_name"], "bot": False, "at": a["decided_at"],
                            "text": f"{a['request_id']} {(a['kind'] or 'request').lower()} {a['status']} · "
                                    f"{a['decided_by_name']}"})
        return {"messages": out}
