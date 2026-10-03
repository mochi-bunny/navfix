"""Reads every adapter in the background, normalises the data and derives the
few things the UI needs (leg ETAs, origins, links between Needs, events and
sites). The UI only formats what it is given; it computes nothing.
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import quote

from . import timeutil as T

NEED_RE = re.compile(r"\bT-\d+\b")
SLOW_SOURCES = {"system": 3.0}  # seconds between reads


def _jsonable(v):
    if isinstance(v, datetime):
        return T.iso(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _mins(a, b):
    return int(round((a - b).total_seconds() / 60))


class Snapshotter:
    def __init__(self, adapters, modes, cfg):
        self.adapters, self.modes, self.cfg = adapters, modes, cfg
        self.date = cfg["demo"]["date"]
        self.interval = max(0.25, cfg.get("poll_ms", 1000) / 2000)
        self.raw = {name: None for name in adapters}
        self.status = {name: {"mode": modes[name], "ok": False, "error": "not read yet", "last_ok": None}
                       for name in adapters}
        self.last_read = {name: 0.0 for name in adapters}
        self.clock = {"sim": datetime.now(T.tz()), "speed": 1.0, "real": time.time()}
        self.body = b"{}"
        self.pool = ThreadPoolExecutor(max_workers=len(adapters))
        self.lock = threading.Lock()
        self.seq = 0

    # ---- background loop ---------------------------------------------------
    def start(self):
        self.refresh()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            time.sleep(self.interval)
            try:
                self.refresh()
            except Exception as e:  # never let the loop die during a recording
                print("snapshot error:", e)

    def refresh(self):
        now = time.time()
        due = [n for n in self.adapters if now - self.last_read[n] >= SLOW_SOURCES.get(n, 0)]
        futures = {n: self.pool.submit(self.adapters[n].read) for n in due}
        for n, f in futures.items():
            st = self.status[n]
            try:
                self.raw[n] = f.result(timeout=4)
                self.last_read[n] = now
                st.update(ok=True, error=None, last_ok=now)
            except Exception as e:
                st.update(ok=False, error=f"{type(e).__name__}: {e}"[:200])
        if self.raw.get("clock"):
            c = self.raw["clock"]
            sim = T.parse(c.get("sim_time")) or datetime.now(T.tz())
            with self.lock:
                self.clock = {"sim": sim, "speed": float(c.get("speed") or 1), "real": now}
        snap = self._build()
        self.seq += 1
        snap["seq"] = self.seq
        body = json.dumps(_jsonable(snap), separators=(",", ":")).encode()
        with self.lock:
            self.body = body

    def sim_now(self):
        with self.lock:
            c = dict(self.clock)
        return c["sim"] + timedelta(seconds=(time.time() - c["real"]) * c["speed"]), c["speed"]

    def response(self):
        now, speed = self.sim_now()
        off = now.utcoffset() or timedelta(0)
        clock = {"sim_time": T.iso(now), "epoch_ms": T.epoch_ms(now), "speed": speed,
                 "tz": self.cfg["demo"]["tz"], "tz_offset_min": int(off.total_seconds() // 60),
                 "date": now.date().isoformat(), "mode": self.modes["clock"],
                 "ok": self.status["clock"]["ok"]}
        with self.lock:
            body = self.body
        return b'{"clock":' + json.dumps(clock).encode() + b"," + body[1:]

    # ---- normalisation -----------------------------------------------------
    def _p(self, v):
        try:
            return T.parse(v, self.date)
        except (ValueError, TypeError):
            return None

    def _build(self):
        now, _ = self.sim_now()
        cal = self.raw.get("calendar") or {}
        tk = self.raw.get("tickets") or {}
        cell = self.raw.get("cell") or {}
        msgs = (self.raw.get("messages") or {}).get("messages", [])
        system = dict(self.raw.get("system") or {})

        staff = [dict(s) for s in cal.get("staff", [])]
        staff_name = {s["id"]: s.get("name", s["id"]) for s in staff}
        spotlight = self.cfg["demo"].get("spotlight_staff")
        for s in staff:
            s["spotlight"] = bool(s.get("spotlight")) or s["id"] == spotlight

        events = []
        for e in cal.get("events", []):
            e = dict(e, start=self._p(e.get("start")), end=self._p(e.get("end")))
            if e["start"] and e["end"]:
                events.append(e)
        events.sort(key=lambda e: (e["start"], e["id"]))
        ev_by_id = {e["id"]: e for e in events}

        legs = []
        for l in cal.get("legs", []):
            ev = ev_by_id.get(l.get("event_id"))
            if not ev:
                continue
            l = dict(l, leave_by=self._p(l.get("leave_by")), staff_id=l.get("staff_id") or ev.get("staff_id"))
            minutes = int(l.get("minutes") or 0)
            delay = int(l.get("delay_min") or 0)
            if not l["leave_by"]:
                l["leave_by"] = ev["start"] - timedelta(minutes=minutes + 10)
            eta = self._p(l.get("eta")) or l["leave_by"] + timedelta(minutes=minutes + delay)
            prev = [p for p in events if p.get("staff_id") == ev.get("staff_id") and p["end"] <= ev["start"] and p["id"] != ev["id"]]
            origin = l.get("origin") or (prev[-1]["place"] if prev else None) or \
                next((s.get("home_base") for s in staff if s["id"] == ev.get("staff_id")), None)
            late_by = max(0, _mins(eta, ev["start"]))
            l.update(eta=eta, origin=origin, destination=ev.get("place"), event_title=ev.get("title"),
                     event_start=ev["start"], late_by=late_by, delay_min=delay,
                     status=l.get("status") or ("late" if late_by else "planned"))
            ev["leg"] = {"leave_by": l["leave_by"], "eta": eta, "mode": l.get("mode"), "minutes": minutes,
                         "delay_min": delay, "late_by": late_by}
            legs.append(l)
        legs.sort(key=lambda l: l["leave_by"])

        needs = []
        for n in tk.get("needs", []):
            n = dict(n)
            for k in ("deadline", "opened_at", "updated_at"):
                n[k] = self._p(n.get(k))
            slot = n.get("booked_slot")
            if isinstance(slot, dict):
                n["booked_slot"] = dict(slot, start=self._p(slot.get("start")), end=self._p(slot.get("end")))
            linked = [e for e in events if e.get("need_id") == n["need_id"]]
            if not slot and linked:
                n["booked_slot"] = {"event_id": linked[0]["id"], "start": linked[0]["start"], "end": linked[0]["end"]}
            n["event_ids"] = [e["id"] for e in linked]
            n["assigned_name"] = staff_name.get(n.get("assigned_staff"), n.get("assigned_staff"))
            ev = dict(n.get("evidence") or {})
            ev["clip_urls"] = [{"name": c, "url": "/media/clip/" + quote(str(c))} for c in ev.get("clips") or []]
            n["evidence"] = ev
            needs.append(n)
        sev_rank = {"P1": 0, "P2": 1, "P3": 2}
        needs.sort(key=lambda n: (n.get("status") == "closed", sev_rank.get(n.get("severity"), 9),
                                  n.get("opened_at") or now))

        visit_events = [dict(v, at=self._p(v.get("at"))) for v in tk.get("visit_events", [])]
        visit_events = [v for v in visit_events if v["at"]]
        visit_events.sort(key=lambda v: v["at"])

        channels = self.cfg["demo"]["channels"]
        messages = {c: [] for c in channels}
        for m in msgs:
            at = self._p(m.get("at"))
            if at and m.get("channel") in messages:
                messages[m["channel"]].append(dict(m, at=at))
        for c in messages:
            messages[c] = sorted(messages[c], key=lambda m: m["at"])[-80:]
        ops_log = [dict(m, need_ids=sorted(set(NEED_RE.findall(m.get("text", ""))))) for m in messages.get("ops-log", [])]

        reliability = [dict(r, last_tested=self._p(r.get("last_tested"))) for r in cell.get("reliability", [])]
        com = cell.get("commissioning")
        if com:
            com = dict(com, started_at=self._p(com.get("started_at")), gate_opened_at=self._p(com.get("gate_opened_at")),
                       trials=[dict(t, at=self._p(t.get("at"))) for t in com.get("trials", [])])
        warehouse = []
        for w in cell.get("warehouse", []):
            w = dict(w, consumables=[dict(c, runout=self._p(c.get("runout"))) for c in w.get("consumables", [])])
            w["need_ids"] = sorted({x for x in [w.get("need_id"), (w.get("backlog") or {}).get("need_id")] +
                                    [c.get("need_id") for c in w["consumables"]] if x})
            warehouse.append(w)

        changes = []
        for i, c in enumerate(cal.get("changes", [])):
            c = dict(c, at=self._p(c.get("at") or c.get("timestamp")))
            if not c["at"]:
                continue
            ev = ev_by_id.get(c.get("event_id")) or {}
            before, after = c.get("before") or {}, c.get("after") or {}
            c.update(
                id=str(c.get("id") or f"ch{i}"),
                kind=c.get("kind") or "changed",
                by=c.get("by") or "Field",
                event_title=c.get("event_title") or ev.get("title") or after.get("title") or before.get("title"),
                staff_id=c.get("staff_id") or ev.get("staff_id"),
                event_exists=bool(ev),
                added=c.get("before") is None, removed=c.get("after") is None,
                diff=[{"field": k, "before": before.get(k), "after": after.get(k)}
                      for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)],
            )
            c["revertable"] = bool(c.get("revertable", True)) and not c.get("reverted_by")
            changes.append(c)
        changes.sort(key=lambda c: c["at"])

        current_legs = {}
        for s in staff:
            mine = [l for l in legs if l.get("staff_id") == s["id"]]
            cur = next((l for l in mine if l["leave_by"] <= now < l["eta"]), None)
            state = "in_transit" if cur else None
            if not cur:
                cur = next((l for l in mine if l["leave_by"] > now), None)
                state = "next" if cur else "done"
            current_legs[s["id"]] = dict(cur, phase=state, leave_in_min=_mins(cur["leave_by"], now)) if cur else {"phase": "done"}

        system["sources"] = {n: {"mode": st["mode"], "ok": st["ok"], "error": st["error"],
                                 "age_s": round(time.time() - st["last_ok"], 1) if st["last_ok"] else None}
                             for n, st in self.status.items()}
        system["dashboard_llm_calls"] = 0
        return {
            "modes": self.modes,
            "any_mock": "mock" in self.modes.values(),
            "spotlight_staff": spotlight,
            "staff": staff, "events": events, "legs": legs, "current_legs": current_legs,
            "jobs": cal.get("jobs", []), "prefs": cal.get("prefs", {}), "changes": changes,
            "needs": needs, "visit_events": visit_events,
            "reliability": reliability, "commissioning": com, "warehouse": warehouse,
            "messages": messages, "ops_log": ops_log,
            "system": system,
        }
