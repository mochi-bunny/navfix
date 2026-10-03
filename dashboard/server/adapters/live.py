"""Live adapters. Every endpoint path and field name the dashboard assumes is in
ENDPOINTS below and in INTERFACES.md. Paths can be overridden per source in
config.json under live.<source>.paths without touching this file.

Only local hosts are expected here; urllib is built without proxy support so a
stray HTTP(S)_PROXY env var can't route anything off the box.
"""
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

from .. import timeutil as T
from .base import CalendarSource, CellSource, ClockSource, MessageSource, SystemSource, TicketSource

ENDPOINTS = {
    "tickets": {
        "needs": "GET /needs",
        "events": "GET /events",
        "fire": "POST /demo/fire",            # body {"need_id": "T-12"}
        "reset": "POST /demo/reset",
    },
    "cell": {
        "reliability": "GET /reliability",
        "commissioning": "GET /commissioning",
        "warehouse": "GET /warehouse",
        "reset": "POST /demo/reset",
    },
    "clock": {
        "get": "GET /clock",
        "set": "POST /clock/set",             # body {"time": "13:25"} or {"time": ISO}
        "speed": "POST /clock/speed",         # body {"speed": 60}
        "reset": "POST /clock/reset",
    },
    "field": {
        "inject_delay": "POST /demo/inject-delay",  # body {"line": "Red", "minutes": 12}
        "create_event": "POST /events",             # body {"staff_id", "set": {...}, "reason", "by": "dashboard"}
        "update_event": "POST /events/{id}",        # body {"set": {...}, "reason": str, "by": "dashboard"}
        "revert_change": "POST /changes/{id}/revert",
        "reset": "POST /demo/reset",
    },
}

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _call(base_url, spec, body=None, timeout=1.5):
    method, path = spec.split(" ", 1)
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    with _opener.open(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw.strip() else None


def _paths(source, cfg):
    return {**ENDPOINTS[source], **(cfg.get("paths") or {})}


def _list(payload, key):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    return payload.get(key) or payload.get("items") or payload.get("data") or []


def _pick(d, *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _json_field(v, default=None):
    if v is None or isinstance(v, (list, dict)):
        return v if v is not None else default
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- tickets ---
class LiveTickets(TicketSource):
    def __init__(self, cfg):
        self.cfg, self.p = cfg, _paths("tickets", cfg)

    def read(self):
        needs = [self._need(n) for n in _list(_call(self.cfg["base_url"], self.p["needs"]), "needs")]
        events = [self._event(e) for e in _list(_call(self.cfg["base_url"], self.p["events"]), "events")]
        return {"needs": needs, "visit_events": events}

    @staticmethod
    def _need(n):
        slot = _pick(n, "booked_slot", "slot")
        return {
            **n,
            "need_id": _pick(n, "need_id", "id", "ticket_id"),
            "severity": _pick(n, "severity", "priority"),
            "duration_min": _pick(n, "duration_min", "duration"),
            "assigned_staff": _pick(n, "assigned_staff", "assignee", "staff_id"),
            "bring": _pick(n, "bring", "bring_list", default=[]),
            "evidence": _pick(n, "evidence", default={}),
            "booked_slot": slot if isinstance(slot, dict) else None,
            "opened_at": _pick(n, "opened_at", "created_at"),
            "updated_at": _pick(n, "updated_at"),
        }

    @staticmethod
    def _event(e):
        return {
            "id": _pick(e, "id", "event_id"),
            "need_id": _pick(e, "need_id", "ticket_id"),
            "type": _pick(e, "type", "event_type"),
            "at": _pick(e, "at", "ts", "timestamp", "created_at"),
            "by": _pick(e, "by", "actor", "agent"),
            "data": _pick(e, "data", "payload", default={}),
        }

    def fire_need(self, need_id):
        _call(self.cfg["base_url"], self.p["fire"], {"need_id": need_id})

    def reset(self):
        _call(self.cfg["base_url"], self.p["reset"])


# --------------------------------------------------------------- calendar ---
class LiveCalendar(CalendarSource):
    """Reads Field's SQLite read-only (tables per Field spec §8)."""

    def __init__(self, cfg, field_cfg, date_str):
        self.path = cfg["sqlite_path"]
        self.field = field_cfg
        self.p = _paths("field", field_cfg)
        self.date = date_str

    def _rows(self, con, table):
        try:
            return [dict(r) for r in con.execute(f"SELECT * FROM {table}")]
        except sqlite3.OperationalError:
            return []

    def read(self):
        if not Path(self.path).exists():
            raise FileNotFoundError(self.path)
        con = sqlite3.connect(f"file:{Path(self.path).resolve().as_posix()}?mode=ro", uri=True, timeout=1)
        con.row_factory = sqlite3.Row
        try:
            staff = self._rows(con, "staff")
            events = self._rows(con, "events")
            legs = self._rows(con, "legs")
            jobs = self._rows(con, "jobs")
            changes = self._rows(con, "changes")
            prefs_rows = self._rows(con, "prefs")
        finally:
            con.close()
        for s in staff:
            sk = s.get("skills")
            s["skills"] = _json_field(sk) if isinstance(sk, str) and sk.startswith("[") else \
                [x.strip() for x in (sk or "").split(",") if x.strip()]
        ev_staff = {}
        for e in events:
            c = e.get("coords")
            if isinstance(c, str):
                e["coords"] = _json_field(c) if c.startswith("[") else [float(x) for x in c.split(",")] if "," in c else None
            ev_staff[e["id"]] = e.get("staff_id")
        for l in legs:
            l.setdefault("staff_id", ev_staff.get(l.get("event_id")))
            l["lines"] = _json_field(l.get("lines"), [])
        for c in changes:
            c["before"], c["after"] = _json_field(c.get("before")), _json_field(c.get("after"))
            c["at"] = _pick(c, "at", "timestamp", "ts")
        if len(prefs_rows) == 1 and "key" not in prefs_rows[0]:
            prefs = prefs_rows[0]
        else:
            prefs = {r.get("key"): r.get("value") for r in prefs_rows}
        return {"staff": staff, "events": events, "legs": legs, "jobs": jobs, "changes": changes, "prefs": prefs}

    def inject_delay(self, line, minutes):
        _call(self.field["base_url"], self.p["inject_delay"], {"line": line, "minutes": int(minutes)})

    def update_event(self, event_id, fields, reason=None):
        spec = self.p["update_event"].replace("{id}", quote(str(event_id), safe=""))
        _call(self.field["base_url"], spec, {"set": fields, "reason": reason, "by": "dashboard"})

    def create_event(self, staff_id, fields, reason=None):
        _call(self.field["base_url"], self.p["create_event"],
              {"staff_id": staff_id, "set": fields, "reason": reason, "by": "dashboard"})

    def revert_change(self, change_id):
        spec = self.p["revert_change"].replace("{id}", quote(str(change_id), safe=""))
        _call(self.field["base_url"], spec, {"by": "dashboard"})

    def reset(self):
        _call(self.field["base_url"], self.p["reset"])


# ------------------------------------------------------------------- cell ---
class LiveCell(CellSource):
    def __init__(self, cfg):
        self.cfg, self.p = cfg, _paths("cell", cfg)

    def read(self):
        base = self.cfg["base_url"]
        rel = _list(_call(base, self.p["reliability"]), "reliability")
        com = _call(base, self.p["commissioning"])
        if isinstance(com, dict) and "commissioning" in com:
            com = com["commissioning"]
        wh = _list(_call(base, self.p["warehouse"]), "sites")
        return {"reliability": rel, "commissioning": com or None, "warehouse": wh}

    def clip_path(self, name):
        root = self.cfg.get("clips_root")
        if not root:
            return None
        root = Path(root).resolve()
        target = (root / name).resolve()
        if root != target and root not in target.parents:
            return None
        return str(target) if target.is_file() else None

    def reset(self):
        _call(self.cfg["base_url"], self.p["reset"])


# ------------------------------------------------------------------ clock ---
class LiveClock(ClockSource):
    def __init__(self, cfg, start_time):
        self.cfg, self.p, self.start = cfg, _paths("clock", cfg), start_time

    def read(self):
        c = _call(self.cfg["base_url"], self.p["get"], timeout=1.0)
        return {"sim_time": _pick(c, "sim_time", "now", "time", "iso"), "speed": float(_pick(c, "speed", default=1))}

    def set(self, sim_time=None, speed=None):
        if sim_time is not None:
            _call(self.cfg["base_url"], self.p["set"], {"time": sim_time})
        if speed is not None:
            _call(self.cfg["base_url"], self.p["speed"], {"speed": speed})

    def reset(self):
        try:
            _call(self.cfg["base_url"], self.p["reset"])
        except Exception:
            self.set(self.start, 1)


# --------------------------------------------------------------- messages ---
class LiveMessages(MessageSource):
    """Bots append one JSON object per line to a shared file, or expose GET url."""

    TAIL_BYTES = 512 * 1024

    def __init__(self, cfg):
        self.cfg = cfg

    def read(self):
        if self.cfg.get("url"):
            req = urllib.request.Request(self.cfg["url"], headers={"Accept": "application/json"})
            with _opener.open(req, timeout=1.5) as r:
                rows = _list(json.loads(r.read()), "messages")
        else:
            path = Path(self.cfg["jsonl_path"])
            if not path.exists():
                raise FileNotFoundError(str(path))
            with path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - self.TAIL_BYTES))
                chunk = f.read().decode("utf-8", "replace")
            lines = chunk.splitlines()
            if size > self.TAIL_BYTES:
                lines = lines[1:]
            rows = []
            for ln in lines:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
        out = []
        for m in rows:
            author = _pick(m, "author", "user", "from", default="?")
            fallback_id = "h" + hashlib.sha1(json.dumps(m, sort_keys=True, default=str).encode()).hexdigest()[:12]
            out.append({
                "id": _pick(m, "id", "message_id", default=fallback_id),
                "channel": str(_pick(m, "channel", default="")).lstrip("#"),
                "author": author,
                "bot": bool(_pick(m, "bot", default=author in ("Field", "Fleet"))),
                "text": _pick(m, "text", "content", default=""),
                "at": _pick(m, "at", "ts", "timestamp"),
            })
        return {"messages": out}


# ----------------------------------------------------------------- system ---
class LiveSystem(SystemSource):
    def __init__(self, cfg):
        self.cfg = cfg

    @staticmethod
    def _tcp(host, port, timeout=0.3):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _vllm(self):
        try:
            req = urllib.request.Request(self.cfg["vllm_url"].rstrip("/") + "/v1/models")
            with _opener.open(req, timeout=1.0) as r:
                models = [m.get("id") for m in json.loads(r.read()).get("data", [])]
            return {"up": True, "model": models[0] if models else None, "models": models}
        except Exception as e:
            return {"up": False, "model": None, "detail": str(e)[:120]}

    def _gpu(self):
        total = self.cfg.get("gpu_total_gb", 128)
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2)
            used, tot = [x.strip() for x in out.stdout.strip().splitlines()[0].split(",")]
            return {"used_gb": round(float(used) / 1024, 1), "total_gb": round(float(tot) / 1024), "source": "nvidia-smi"}
        except Exception:
            pass
        # GB10 has unified memory and nvidia-smi may report [N/A]; fall back to system memory.
        try:
            info = {}
            for ln in Path("/proc/meminfo").read_text().splitlines():
                k, v = ln.split(":", 1)
                info[k] = int(v.strip().split()[0])
            used = (info["MemTotal"] - info["MemAvailable"]) / 1024 / 1024
            return {"used_gb": round(used, 1), "total_gb": round(info["MemTotal"] / 1024 / 1024) or total,
                    "source": "unified memory (/proc/meminfo)"}
        except Exception:
            return {"used_gb": None, "total_gb": total, "source": "unavailable"}

    def _egress(self):
        try:
            if self.cfg.get("egress_counter_url"):
                req = urllib.request.Request(self.cfg["egress_counter_url"])
                with _opener.open(req, timeout=1.0) as r:
                    d = json.loads(r.read())
                return int(_pick(d, "cloud_model_calls", "count", "value")), "counter_url"
            if self.cfg.get("egress_counter_file"):
                raw = Path(self.cfg["egress_counter_file"]).read_text().strip()
                try:
                    return int(raw), "counter_file"
                except ValueError:
                    return int(_pick(json.loads(raw), "cloud_model_calls", "count")), "counter_file"
        except Exception:
            return None, "error"
        return None, "not wired"

    def read(self):
        services = []
        for s in self.cfg.get("services", []):
            up = self._tcp(s.get("host", "127.0.0.1"), s["port"])
            services.append({"name": s["name"], "port": s["port"], "up": up})
        calls, src = self._egress()
        return {"vllm": self._vllm(), "gpu": self._gpu(), "services": services,
                "cloud_model_calls": calls, "egress_source": src, "checked_at": T.iso(T.parse(time.time()))}
