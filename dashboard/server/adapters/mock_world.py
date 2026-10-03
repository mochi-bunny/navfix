"""In-process stand-in for every service, driven by the sim clock.

State is a pure function of (fixtures, anchors, sim time): each call rebuilds
the day from scratch and applies every script beat whose time has passed. That
makes clock jumps in either direction safe while recording.
"""
import copy
import json
import threading
import time
from datetime import timedelta
from pathlib import Path

from .. import timeutil as T
from ..stats import p_rate_at_least


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _fmt(value, vars_):
    if isinstance(value, str):
        return value.format_map(vars_) if "{" in value else value
    if isinstance(value, dict):
        return {k: _fmt(v, vars_) for k, v in value.items()}
    if isinstance(value, list):
        return [_fmt(v, vars_) for v in value]
    return value


class SimClock:
    def __init__(self, start, speed=1.0):
        self._lock = threading.Lock()
        self.anchor_real = time.time()
        self.anchor_sim = start
        self.speed = speed

    def now(self):
        with self._lock:
            return self.anchor_sim + timedelta(seconds=(time.time() - self.anchor_real) * self.speed)

    def set(self, sim=None, speed=None):
        current = self.now()
        with self._lock:
            self.anchor_sim = sim or current
            self.anchor_real = time.time()
            if speed is not None:
                self.speed = float(speed)


class MockWorld:
    def __init__(self, fixtures_dir, demo_cfg):
        fx = Path(fixtures_dir)
        self.fixtures_dir = fx
        self.day = json.loads((fx / "demo_day.json").read_text(encoding="utf-8"))
        self.needs_fx = json.loads((fx / "needs.json").read_text(encoding="utf-8"))
        self.cell_fx = json.loads((fx / "cell.json").read_text(encoding="utf-8"))
        self.script = json.loads((fx / "script.json").read_text(encoding="utf-8"))
        self.date = demo_cfg.get("date") or self.day["date"]
        self.start_time = demo_cfg.get("start_time") or self.day["start_time"]
        self.clock = SimClock(self._t(self.start_time))
        self.overrides = {}
        self.manual = []  # edits and reverts made from the dashboard (what Field's write API would do)
        self._lock = threading.Lock()
        self._cache = (None, None)

    # ---- controls (what the services' /demo endpoints would do) ----------
    def fire(self, need_id):
        if need_id != "T-12":
            raise ValueError(f"mock can only fire T-12, not {need_id}")
        with self._lock:
            self.overrides["t12"] = {"at": self._minute(self.clock.now())}

    def inject_delay(self, line, minutes):
        spec = dict(self.script["anchors"]["delay"])
        spec.update({"line": line, "minutes": int(minutes), "injected": True})
        with self._lock:
            self.overrides["delay"] = {"at": self._minute(self.clock.now()), **spec}

    def reset(self):
        with self._lock:
            self.overrides.clear()
            self.manual.clear()
        self.clock.set(self._t(self.start_time), 1.0)

    EDITABLE = ("title", "place", "start", "end")

    def edit_event(self, event_id, fields, reason=None):
        st = self.state()
        ev = next((e for e in st["events"] if e["id"] == event_id), None)
        if not ev:
            raise KeyError(f"no event {event_id}")
        clean = {k: v for k, v in fields.items() if k in self.EDITABLE and v not in (None, "")}
        if not clean:
            raise ValueError("nothing to change")
        start = T.parse(clean.get("start"), self.date) if "start" in clean else ev["start"]
        end = T.parse(clean.get("end"), self.date) if "end" in clean else ev["end"]
        if end <= start:
            raise ValueError("end must be after start")
        with self._lock:
            self.manual.append({"op": "edit", "event_id": event_id, "set": clean, "reason": reason,
                                "at": self._minute(self.clock.now())})

    def create_event(self, staff_id, fields, reason=None):
        st = self.state()
        if not any(s["id"] == staff_id for s in st["staff"]):
            raise KeyError(f"no staff {staff_id}")
        title = (fields.get("title") or "").strip()
        if not title:
            raise ValueError("a title is needed")
        start, end = T.parse(fields.get("start"), self.date), T.parse(fields.get("end"), self.date)
        if not start or not end or end <= start:
            raise ValueError("end must be after start")
        # Field's conflict check: no overlap with this person's other events.
        clash = next((e for e in st["events"] if e["staff_id"] == staff_id and e["start"] < end and start < e["end"]), None)
        if clash:
            raise ValueError(f"overlaps {clash['title']} ({T.hhmm(clash['start'])}–{T.hhmm(clash['end'])})")
        ev = {"staff_id": staff_id, "title": title, "type": fields.get("type") or "meeting",
              "start": start, "end": end, "place": (fields.get("place") or "").strip() or "TBD"}
        with self._lock:
            self.manual.append({"op": "create", "event": ev, "reason": reason, "at": self._minute(self.clock.now())})

    def revert_change(self, change_id):
        ch = next((c for c in self.state()["changes"] if c["id"] == change_id), None)
        if not ch:
            raise KeyError(f"no change {change_id}")
        if not ch.get("revertable") or ch.get("reverted_by"):
            raise ValueError(f"change {change_id} can't be reverted")
        with self._lock:
            self.manual.append({"op": "revert", "change_id": change_id, "at": self._minute(self.clock.now())})

    # ---- helpers ----------------------------------------------------------
    def _t(self, hhmm):
        return T.parse(hhmm, self.date)

    @staticmethod
    def _minute(dt):
        return dt.replace(second=0, microsecond=0)

    def _place(self, key):
        p = self.day["places"].get(key)
        return {"key": key, "name": p["name"], "area": p["area"], "coords": p["coords"]} if p else {"key": key, "name": key}

    # ---- anchors ------------------------------------------------------------
    def _anchors(self):
        out = {"day": self._t("00:00")}
        specs = self.script["anchors"]
        params = {}
        for name, spec in specs.items():
            if name in self.overrides:
                out[name] = self.overrides[name]["at"]
                params[name] = self.overrides[name]
            elif self.script.get("auto", True) and spec.get("default") and not spec.get("requires"):
                out[name] = self._t(spec["default"])
                params[name] = spec
        arr = specs.get("arrived")
        if arr and "t12" in out and "arrived" not in self.overrides:
            out["arrived"] = max(self._t(arr["default"]), out["t12"] + timedelta(minutes=arr.get("min_after_anchor_min", 5)))
        return out, params

    def _delay_ctx(self, at, spec):
        leg = next(l for l in self.day["legs"] if l["event_id"] == spec["leg_event"])
        ev = next(e for e in self.day["events"] if e["id"] == spec["leg_event"])
        prev = max((e for e in self.day["events"] if e["staff_id"] == ev["staff_id"] and e["end"] <= ev["start"]),
                   key=lambda e: e["end"], default=None)
        line, minutes = spec["line"], int(spec["minutes"])
        leave_by = self._t(leg["leave_by"])
        leave_now = max(self._minute(at), self._t(prev["end"]) if prev else at)
        start = self._t(ev["start"])
        eta_late = leave_by + timedelta(minutes=leg["minutes"] + minutes)
        eta_now = leave_now + timedelta(minutes=leg["minutes"] + minutes)
        eta_taxi = leave_by + timedelta(minutes=leg.get("taxi_minutes", leg["minutes"]))
        return {
            "impact": line in leg.get("lines", []), "leg_event": spec["leg_event"],
            "line": line, "minutes": minutes,
            "simulated": " (simulated)" if spec.get("injected", True) else "",
            "leave_by": T.hhmm(leave_by), "leave_now": T.hhmm(leave_now),
            "eta_late": T.hhmm(eta_late), "eta_now": T.hhmm(eta_now), "eta_taxi": T.hhmm(eta_taxi),
            "eta_now_hhmm": eta_now.strftime("%H:%M"),
            "late_by": max(0, int((eta_late - start).total_seconds() // 60)),
            "_leave_now": leave_now, "_eta_late": eta_late, "_eta_now": eta_now,
        }

    def _commissioning_plan(self, anchors):
        if "arrived" not in anchors:
            return None
        cfg = self.cell_fx["commissioning"]
        target, threshold = self.cell_fx["target_rate"], self.cell_fx["gate_threshold"]
        start = anchors["arrived"] + timedelta(minutes=cfg["start_delay_min"])
        per = {p["id"]: {"n": 0, "s": 0, "active": True} for p in cfg["policies"]}
        trials, drop, gate, g = [], None, None, 0
        for pid, ok in cfg["sequence"]:
            st = per[pid]
            if not st["active"]:
                continue
            g += 1
            st["n"] += 1
            st["s"] += int(ok)
            conf = p_rate_at_least(st["s"], st["n"], target)
            at = start + timedelta(seconds=g * cfg["trial_interval_sec"])
            trials.append({"n": g, "policy": pid, "success": bool(ok), "at": at, "confidence": conf})
            if conf >= threshold:
                gate = {"at": at, "policy": pid, "trial": g, "trials": st["n"], "successes": st["s"], "confidence": conf}
                break
            if st["n"] >= cfg["min_trials_before_drop"] and conf < cfg["drop_below"]:
                st["active"] = False
                drop = {"at": at, "policy": pid, "trials": st["n"], "successes": st["s"]}
        return {"start": start, "trials": trials, "gate": gate, "drop": drop}

    # ---- state --------------------------------------------------------------
    def state(self):
        now = self.clock.now()
        key = (now.replace(microsecond=0), json.dumps([self.overrides, self.manual], default=str, sort_keys=True))
        if self._cache[0] == key:
            return self._cache[1]
        with self._lock:
            st = self._build(now)
        self._cache = (key, st)
        return st

    def _build(self, now):
        anchors, params = self._anchors()
        plan = self._commissioning_plan(anchors)
        vars_ = _Safe()
        if plan and plan["drop"]:
            anchors["drop"] = plan["drop"]["at"]
            vars_.update(drop_succ=plan["drop"]["successes"], drop_trials=plan["drop"]["trials"])
        if plan and plan["gate"]:
            anchors["gate"] = plan["gate"]["at"]
            vars_.update(gate_succ=plan["gate"]["successes"], gate_trials=plan["gate"]["trials"],
                         gate_conf=f"{plan['gate']['confidence']:.3f}")
        delay = self._delay_ctx(anchors["delay"], params["delay"]) if "delay" in anchors else None
        if delay:
            vars_.update({k: v for k, v in delay.items() if not k.startswith("_")})

        s = {
            "staff": [dict(x, home_base=self._place(x["home_base"])["name"],
                           current_location=self._place(x["current_location"])["name"]) for x in self.day["staff"]],
            "events": {}, "legs": {}, "needs": {}, "visit_events": [], "messages": [], "changes": [],
            "warehouse": {w["site"]: copy.deepcopy(w) for w in self.cell_fx["warehouse"]},
        }
        for e in self.day["events"]:
            self._add_event(s, e)
        for l in self.day["legs"]:
            s["legs"][l["event_id"]] = {**copy.deepcopy(l), "leave_by": self._t(l["leave_by"]),
                                        "delay_min": 0, "status": "planned", "alert": None, "last_checked": None}

        beats = []
        for i, b in enumerate(self.script["beats"]):
            a = b["anchor"]
            if a == "day":
                t = self._t(b["at"])
            elif a in anchors:
                t = anchors[a] + timedelta(minutes=b.get("offset_min", 0))
            else:
                continue
            beats.append((t, i, b))
        beats.sort(key=lambda x: (x[0], x[1]))
        seq = 0
        for t, _, b in beats:
            if t > now:
                break
            for op in b["do"]:
                req = op.get("requires")
                if req and (req not in anchors or anchors[req] > t):
                    continue
                when = op.get("when")
                if when and (not delay or ("impact" if delay["impact"] else "no_impact") != when):
                    continue
                seq += 1
                self._apply(s, _fmt(op, vars_), t, seq, delay)

        for i, m in enumerate(self.manual):
            if m["op"] == "edit":
                self._manual_edit(s, f"u{i + 1}", m)
            elif m["op"] == "create":
                eid = f"N{i + 1}"
                s["events"][eid] = {**m["event"], "id": eid, "coords": None, "area": None, "site": None,
                                    "need_id": None, "attendance": None}
                ev = s["events"][eid]
                self._change(s, f"u{i + 1}", m["at"], "added", "event", eid, None,
                             {"start": T.iso(ev["start"]), "end": T.iso(ev["end"]), "title": ev["title"], "place": ev["place"]},
                             "You", m.get("reason") or "Added from the dashboard")
            else:
                self._manual_revert(s, f"u{i + 1}", m)

        commissioning = self._commissioning_state(plan, now)
        reliability = self._reliability(plan, now, anchors)
        warehouse = self._warehouse(s, anchors, now)
        jobs = [{"id": f"J-{l['event_id']}", "type": "departure", "event_id": l["event_id"],
                 "fire_at": l["leave_by"] - timedelta(minutes=30),
                 "status": "done" if l["leave_by"] - timedelta(minutes=30) <= now else "scheduled"}
                for l in s["legs"].values()]
        return {
            "now": now, "speed": self.clock.speed,
            "staff": s["staff"], "events": list(s["events"].values()), "legs": list(s["legs"].values()),
            "jobs": jobs, "changes": s["changes"], "prefs": self.day["prefs"],
            "needs": list(s["needs"].values()), "visit_events": s["visit_events"],
            "messages": s["messages"],
            "reliability": reliability, "commissioning": commissioning, "warehouse": warehouse,
        }

    def _add_event(self, s, e):
        place = self._place(e["place"])
        s["events"][e["id"]] = {**copy.deepcopy(e), "start": self._t(e["start"]), "end": self._t(e["end"]),
                                "place": place["name"], "coords": place.get("coords"), "area": place.get("area"),
                                "site": e.get("site"), "need_id": e.get("need_id"), "attendance": None}

    # ---- change log ---------------------------------------------------------
    @staticmethod
    def _change(s, cid, t, kind, target, event_id, before, after, by, reason, revertable=True, need_id=None):
        ev = s["events"].get(event_id) or {}
        if cid.startswith("c"):  # scripted changes: stable id however the anchors move
            cid = f"c-{event_id}-{kind}-{t:%H%M}"
        s["changes"].append({
            "id": cid, "at": t, "kind": kind, "target": target, "event_id": event_id,
            "event_title": ev.get("title") or (after or {}).get("title"), "staff_id": ev.get("staff_id"),
            "before": before, "after": after, "by": by, "reason": reason,
            "revertable": revertable, "need_id": need_id or ev.get("need_id"), "reverted_by": None,
        })

    def _set_event_fields(self, s, event_id, fields):
        """Apply title/place/start/end; moving the start shifts the leg too (as Field would re-plan)."""
        ev = s["events"][event_id]
        before, after = {}, {}
        for k, v in fields.items():
            if k in ("start", "end"):
                v = T.parse(v, self.date)
                before[k], after[k] = T.iso(ev[k]), T.iso(v)
                if k == "start" and event_id in s["legs"]:
                    leg = s["legs"][event_id]
                    old_leave = leg["leave_by"]
                    leg["leave_by"] = old_leave + (v - ev["start"])
                    before["leave_by"], after["leave_by"] = T.iso(old_leave), T.iso(leg["leave_by"])
                ev[k] = v
            else:
                before[k], after[k] = ev.get(k), v
                ev[k] = v
        return before, after

    def _manual_edit(self, s, cid, m):
        if m["event_id"] not in s["events"]:
            return
        before, after = self._set_event_fields(s, m["event_id"], m["set"])
        self._change(s, cid, m["at"], "edited", "event", m["event_id"], before, after, "You",
                     m.get("reason") or "Edited from the dashboard")

    def _manual_revert(self, s, cid, m):
        ch = next((c for c in s["changes"] if c["id"] == m["change_id"]), None)
        if not ch or not ch["revertable"] or ch["reverted_by"]:
            return
        eid = ch["event_id"]
        if ch["target"] == "event" and ch["before"] is None:  # revert an added event = remove it
            ev = s["events"].pop(eid, None)
            s["legs"].pop(eid, None)
            before, after = ch["after"], None
            title = ev["title"] if ev else ch["event_title"]
        elif ch["target"] == "event" and eid in s["events"]:
            fields = {k: v for k, v in ch["before"].items() if k in ("start", "end", "title", "place", "need_id")}
            before, after = self._set_event_fields(s, eid, fields)
            title = s["events"][eid]["title"]
        elif ch["target"] == "leg" and eid in s["legs"]:
            leg = s["legs"][eid]
            before, after = ch["after"], ch["before"]
            if "leave_by" in ch["before"]:
                leg["leave_by"] = T.parse(ch["before"]["leave_by"])
            if "delay_min" in ch["before"]:
                leg["delay_min"] = ch["before"]["delay_min"]
            leg["status"] = "planned"
            title = s["events"].get(eid, {}).get("title")
        else:
            return
        ch["reverted_by"] = cid
        self._change(s, cid, m["at"], "reverted", ch["target"], eid, before, after, "You",
                     f"Reverted: {ch['kind']}", revertable=False)
        s["changes"][-1]["event_title"] = title

    def _apply(self, s, op, t, seq, delay):
        kind = op["op"]
        if kind == "msg":
            s["messages"].append({"id": f"m{seq}", "channel": op["channel"], "author": op["author"],
                                  "bot": op["author"] in ("Field", "Fleet"), "text": op["text"], "at": t})
        elif kind == "need.open":
            n = copy.deepcopy(self.needs_fx["needs"][op["need"]])
            n["deadline"] = T.parse(n["deadline"], self.date)
            n.update(status="open", opened_at=t, updated_at=t, assigned_staff=None, booked_slot=None)
            s["needs"][n["need_id"]] = n
            s["visit_events"].append({"id": f"v{seq}", "need_id": n["need_id"], "type": "need.opened",
                                      "at": t, "by": "Fleet", "data": {"severity": n["severity"]}})
        elif kind == "need.event":
            n = s["needs"].get(op["need"])
            if not n:
                return
            s["visit_events"].append({"id": f"v{seq}", "need_id": op["need"], "type": op["type"],
                                      "at": t, "by": op.get("by"), "data": op.get("data", {})})
            if op.get("status"):
                n["status"] = op["status"]
            for k, v in (op.get("set") or {}).items():
                if k == "booked_slot":
                    v = {**v, "start": T.parse(v["start"], self.date), "end": T.parse(v["end"], self.date)}
                n[k] = v
            n["updated_at"] = t
        elif kind == "booking.apply":
            bk = self.needs_fx["bookings"][op["need"]]
            ev = bk["event"]
            self._add_event(s, ev)
            n = s["needs"].get(op["need"])
            if n:
                n.update(assigned_staff=bk["staff_id"], score=bk.get("score"),
                         booked_slot={"event_id": ev["id"], "start": self._t(ev["start"]), "end": self._t(ev["end"])},
                         alternatives=bk.get("alternatives", []))
            new = s["events"][ev["id"]]
            self._change(s, f"c{seq}", t, "booked", "event", ev["id"], None,
                         {"start": T.iso(new["start"]), "end": T.iso(new["end"]), "title": new["title"]},
                         "Field", f"{op['need']} visit booked after a yes in #field", need_id=op["need"])
        elif kind == "event.update":
            ev = s["events"][op["id"]]
            before = {k: ev.get(k) for k in op["set"]}
            ev.update(op["set"])
            self._change(s, f"c{seq}", t, "attached" if "need_id" in op["set"] else "edited", "event", op["id"],
                         before, dict(op["set"]), op.get("by", "Field"), op.get("reason"),
                         need_id=op["set"].get("need_id"))
        elif kind == "event.reschedule":
            before, after = self._set_event_fields(s, op["id"], {"start": op["start"], "end": op["end"]})
            self._change(s, f"c{seq}", t, "rescheduled", "event", op["id"], before, after,
                         op.get("by", "Field"), op.get("reason"))
        elif kind == "delay.detect" and delay and delay["impact"]:
            leg = s["legs"][delay["leg_event"]]
            on_time = leg["leave_by"] + timedelta(minutes=leg["minutes"])
            leg.update(delay_min=delay["minutes"], status="late", last_checked=t,
                       alert=f"{delay['line']} Line +{delay['minutes']} min{delay['simulated']}")
            self._change(s, f"c{seq}", t, "delayed", "leg", delay["leg_event"],
                         {"delay_min": 0, "eta": T.iso(on_time)},
                         {"delay_min": delay["minutes"], "eta": T.iso(delay["_eta_late"])},
                         "Field", f"MBTA {leg['alert']}", revertable=False)
        elif kind == "delay.apply" and delay and delay["impact"]:
            leg = s["legs"][delay["leg_event"]]
            before = {"leave_by": T.iso(leg["leave_by"]), "eta": T.iso(delay["_eta_late"])}
            leg.update(leave_by=delay["_leave_now"], status="replanned", last_checked=t,
                       note=f"Leaving early ({delay['line']} +{delay['minutes']})")
            self._change(s, f"c{seq}", t, "replanned", "leg", delay["leg_event"], before,
                         {"leave_by": T.iso(leg["leave_by"]), "eta": T.iso(delay["_eta_now"])},
                         "Field", f"Leave early to absorb {delay['line']} +{delay['minutes']} (chosen in #field)")
        elif kind == "consumable.set":
            for c in s["warehouse"][op["site"]]["consumables"]:
                if c["item"] == op["item"]:
                    c.update(runout=op["runout"], need_id=op.get("need_id"))
        elif kind == "warehouse.set":
            s["warehouse"][op["site"]].update(op["set"])

    def _commissioning_state(self, plan, now):
        if not plan:
            return None
        cfg = self.cell_fx["commissioning"]
        done = [t for t in plan["trials"] if t["at"] <= now]
        gate = plan["gate"] if plan["gate"] and plan["gate"]["at"] <= now else None
        drop = plan["drop"] if plan["drop"] and plan["drop"]["at"] <= now else None
        policies = []
        for p in cfg["policies"]:
            mine = [t for t in done if t["policy"] == p["id"]]
            succ = sum(t["success"] for t in mine)
            status = "active"
            if drop and drop["policy"] == p["id"]:
                status = "dropped"
            if gate and gate["policy"] == p["id"]:
                status = "passed"
            policies.append({"id": p["id"], "name": p["name"], "trials": len(mine), "successes": succ,
                             "confidence": mine[-1]["confidence"] if mine else None, "status": status})
        status = "gate_open" if gate else ("running" if now >= plan["start"] else "scheduled")
        return {
            "need_id": cfg["need_id"], "site": cfg["site"], "skill": cfg["skill"], "status": status,
            "target_rate": self.cell_fx["target_rate"], "gate_threshold": self.cell_fx["gate_threshold"],
            "started_at": plan["start"], "gate_opened_at": gate["at"] if gate else None,
            "gate_trial": gate["trial"] if gate else None,
            "winner": next((p["name"] for p in cfg["policies"] if gate and p["id"] == gate["policy"]), None),
            "policies": policies,
            "trials": [dict(t) for t in done],
        }

    def _reliability(self, plan, now, anchors):
        target, threshold = self.cell_fx["target_rate"], self.cell_fx["gate_threshold"]
        rows = []
        for r in self.cell_fx["reliability"]:
            row = dict(r, last_tested=T.parse(r["last_tested"]), need_id=None, note=None)
            if r["site"] == "globex" and r["skill"] == "open_middle_drawer":
                row["need_id"] = "T-12" if anchors.get("t12") and anchors["t12"] <= now else None
                if plan:
                    done = [t for t in plan["trials"] if t["at"] <= now]
                    gate = plan["gate"] if plan["gate"] and plan["gate"]["at"] <= now else None
                    if gate:
                        row.update(policy=self._policy_name(gate["policy"]), trials=gate["trials"],
                                   successes=gate["successes"], last_tested=gate["at"], note="commissioned")
                    elif done:
                        row.update(last_tested=done[-1]["at"], note=f"commissioning · trial {done[-1]['n']}")
            row["confidence"] = p_rate_at_least(row["successes"], row["trials"], target)
            row["gate"] = "open" if row["confidence"] >= threshold else "closed"
            row["threshold"] = threshold
            row["target_rate"] = target
            rows.append(row)
        return rows

    def _policy_name(self, pid):
        return next(p["name"] for p in self.cell_fx["commissioning"]["policies"] if p["id"] == pid)

    def _warehouse(self, s, anchors, now):
        out = []
        t12 = anchors.get("t12") if anchors.get("t12") and anchors["t12"] <= now else None
        gate = anchors.get("gate") if anchors.get("gate") and anchors["gate"] <= now else None
        noon = self._t("12:00")
        degrade_from = self._t("12:15")
        for site, w in s["warehouse"].items():
            series, i = [], 0
            t = self._t("08:00")
            while t <= now:
                f = 1.0
                if site == "globex" and t >= degrade_from:
                    if anchors.get("gate") and t >= anchors["gate"]:
                        f = 1.12 if t < anchors["gate"] + timedelta(minutes=45) else 1.0
                    else:
                        f = 0.72
                noise = (((i * 9301 + 49297 + len(site) * 7) % 233280) / 233280 - 0.5) * 0.06
                series.append({"t": T.iso(t), "v": round(w["throughput_base"] * f * (1 + noise))})
                t += timedelta(minutes=15)
                i += 1
            backlog = w["backlog_base"]
            note, need_id = None, None
            if site == "globex":
                drawer = self._drawer_backlog(w, anchors, now, noon)
                backlog += drawer
                if drawer:
                    draining = gate is not None
                    note = f"{drawer} drawer orders {'draining' if draining else 'held'}"
                    need_id = "T-12" if t12 else None
            robots = self._robots(site, w, now)
            out.append({
                "site": site, "name": w["name"], "area": w["area"],
                "health": w.get("health", "ok"), "health_note": w.get("health_note"),
                "need_id": w.get("need_id") if w.get("health") != "ok" else None,
                "robots_active": sum(r["status"] in ("ok", "commissioning") for r in robots),
                "robots_total": w["robots_total"], "robots": robots,
                "throughput": {"unit": "orders/h", "current": series[-1]["v"] if series else None, "series": series},
                "backlog": {"count": backlog, "note": note, "need_id": need_id},
                "consumables": [dict(c, runout=T.parse(c["runout"])) for c in w["consumables"]],
            })
        return out

    def _robots(self, site, w, now):
        """Per-robot telemetry Fleet watches (deterministic for a given sim time)."""
        zones = ["pack-1", "pack-2", "drawer", "dock"]
        tasks = {"pack-1": "pick_place_box", "pack-2": "pick_place_box", "dock": "close_drawer",
                 "drawer": "open_middle_drawer" if site == "globex" else "open_top_drawer"}
        mins = max(0, int((now - self._t("08:00")).total_seconds() // 60))
        out = []
        for i in range(w["robots_total"]):
            zone = zones[i % len(zones)]
            battery = 100 - ((mins + i * 37 + len(site) * 11) % 80)
            status = "charging" if battery < 25 else "ok"
            if zone == "drawer" and site == "globex" and w.get("health") in ("degraded", "commissioning"):
                status = "blocked" if w["health"] == "degraded" else "commissioning"
            out.append({"id": f"{w['name'][0]}-{i + 1:02d}", "zone": zone, "task": tasks[zone],
                        "battery": battery, "status": status})
        return out

    def _drawer_backlog(self, w, anchors, now, noon):
        peak = w.get("drawer_backlog_at_need", 40)
        t12 = anchors.get("t12")
        ramp_end = t12 if t12 else noon + timedelta(minutes=40)
        ramp_start = min(noon, ramp_end - timedelta(minutes=40))
        if now <= ramp_start:
            return 0
        if now < ramp_end:
            return int(peak * (now - ramp_start) / (ramp_end - ramp_start))
        gate = anchors.get("gate")
        held_until = min(now, gate) if gate else now
        level = peak + int((held_until - ramp_end).total_seconds() // 600)
        if gate and now > gate:
            level = max(0, level - int((now - gate).total_seconds() // 30))
        return level
