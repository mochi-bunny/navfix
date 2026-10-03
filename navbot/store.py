"""SQLite records for navbot. The dashboard can read the same file.

  activity          every thing that happens through the bot (posts, messages, reactions, decisions, errors)
  approvals         one record per approval request, updated as it moves from pending to a decision
  telemetry_reports one row per hourly robot report received
  robot_readings    one row per robot per report (the robot monitor log, append-only)
  fleet_events      every event from a JSON fleet log (faults, picks, waits, ...), append-only
  fleet_snapshots   every robot at every snapshot in a JSON fleet log (position, battery, temp, error)
  events            every card posted and every decision sent (kept for the dashboard)
"""
import json
import os
import sqlite3
import time

import config

os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
_db = sqlite3.connect(config.DB_PATH, isolation_level=None)  # autocommit
_db.row_factory = sqlite3.Row
_db.execute("PRAGMA journal_mode=WAL")  # lets the dashboard read while the bot writes
_db.executescript(
    """
    CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY,
        event TEXT, payload TEXT, message_id INTEGER, posted_at REAL);
    CREATE TABLE IF NOT EXISTS approvals(
        request_id TEXT PRIMARY KEY,
        message_id INTEGER UNIQUE, channel_id INTEGER, options TEXT,
        status TEXT DEFAULT 'pending', choice TEXT,
        decided_by INTEGER, decided_at REAL, delivered INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS activity(
        id INTEGER PRIMARY KEY,
        at REAL, kind TEXT,
        channel_id INTEGER, channel TEXT,
        user_id INTEGER, user_name TEXT,
        message_id INTEGER, request_id TEXT,
        detail TEXT);
    CREATE INDEX IF NOT EXISTS activity_at ON activity(at);
    CREATE INDEX IF NOT EXISTS activity_request ON activity(request_id);
    CREATE TABLE IF NOT EXISTS telemetry_reports(
        id INTEGER PRIMARY KEY,
        received_at REAL, message_id INTEGER,
        robots INTEGER, ok INTEGER, warn INTEGER, down INTEGER,
        csv TEXT);
    CREATE TABLE IF NOT EXISTS robot_readings(
        id INTEGER PRIMARY KEY,
        report_id INTEGER REFERENCES telemetry_reports(id),
        received_at REAL, robot TEXT, site TEXT, status TEXT,
        battery_pct REAL, temp_c REAL, errors_1h REAL, last_seen TEXT,
        health TEXT, reasons TEXT);
    CREATE INDEX IF NOT EXISTS readings_robot ON robot_readings(robot, received_at);
    CREATE TABLE IF NOT EXISTS fleet_events(
        id INTEGER PRIMARY KEY,
        report_id INTEGER REFERENCES telemetry_reports(id),
        t REAL, at TEXT, type TEXT, robot TEXT, job_id TEXT, error TEXT, payload TEXT);
    CREATE INDEX IF NOT EXISTS fleet_events_robot ON fleet_events(robot, report_id);
    CREATE TABLE IF NOT EXISTS fleet_snapshots(
        id INTEGER PRIMARY KEY,
        report_id INTEGER REFERENCES telemetry_reports(id),
        t REAL, at TEXT, robot TEXT, zone INTEGER, x REAL, y REAL, vel REAL,
        task TEXT, job_id TEXT, battery REAL, temp REAL, error TEXT,
        carrying TEXT, yielding INTEGER, waiting_s REAL);
    CREATE INDEX IF NOT EXISTS fleet_snapshots_robot ON fleet_snapshots(robot, report_id, t);
    """
)

# Columns added to approvals after the first version; added in place so old rows are kept.
_APPROVAL_COLUMNS = {
    "source": "TEXT DEFAULT 'supervisor'",  # supervisor (POST /events) or discord (posted in #approvals)
    "kind": "TEXT",                         # SCHEDULE_CHANGE, TIME_OFF, BOOK_VISIT, ...
    "summary": "TEXT",
    "details": "TEXT",
    "requested_by": "TEXT",
    "requested_by_id": "INTEGER",
    "requested_at": "REAL",
    "request_message_id": "INTEGER",        # the requester's own message, for discord requests
    "choice_label": "TEXT",
    "decided_by_name": "TEXT",
    "updated_at": "REAL",
}
_have = {r["name"] for r in _db.execute("PRAGMA table_info(approvals)")}
for _col, _type in _APPROVAL_COLUMNS.items():
    if _col not in _have:
        _db.execute(f"ALTER TABLE approvals ADD COLUMN {_col} {_type}")
_db.execute("UPDATE approvals SET status='pending' WHERE status='open'")  # old name for pending


# ---------- activity ----------
def activity(kind: str, *, channel_id=None, channel=None, user_id=None, user_name=None,
             message_id=None, request_id=None, **detail) -> None:
    _db.execute(
        "INSERT INTO activity(at, kind, channel_id, channel, user_id, user_name, message_id, request_id, detail) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (time.time(), kind, channel_id, channel, user_id, user_name, message_id, request_id,
         json.dumps(detail, ensure_ascii=False, default=str) if detail else None),
    )


def log_event(ev: dict, message_id) -> None:
    _db.execute(
        "INSERT INTO events(event, payload, message_id, posted_at) VALUES (?,?,?,?)",
        (ev.get("event"), json.dumps(ev), message_id, time.time()),
    )


# ---------- approvals ----------
def approval_exists(request_id: str) -> bool:
    row = _db.execute("SELECT 1 FROM approvals WHERE request_id=?", (request_id,)).fetchone()
    return row is not None


def add_approval(request_id: str, message_id: int, channel_id: int, options: list, *, source: str,
                 kind=None, summary=None, details=None, requested_by=None, requested_by_id=None,
                 request_message_id=None) -> None:
    now = time.time()
    _db.execute(
        "INSERT INTO approvals(request_id, message_id, channel_id, options, status, source, kind, summary, "
        "details, requested_by, requested_by_id, requested_at, request_message_id, updated_at) "
        "VALUES (?,?,?,?,'pending',?,?,?,?,?,?,?,?,?)",
        (request_id, message_id, channel_id, json.dumps(options), source, kind, summary, details,
         requested_by, requested_by_id, now, request_message_id, now),
    )


def next_request_id() -> str:
    """REQ-0001, REQ-0002, ... for requests people post in #approvals."""
    n = _db.execute("SELECT COUNT(*) FROM approvals WHERE source='discord'").fetchone()[0]
    while approval_exists(f"REQ-{n + 1:04d}"):
        n += 1
    return f"REQ-{n + 1:04d}"


def get_by_message(message_id: int):
    row = _db.execute("SELECT * FROM approvals WHERE message_id=?", (message_id,)).fetchone()
    if row is None:
        return None
    req = dict(row)
    req["options"] = json.loads(req["options"])
    return req


def decide(request_id: str, status: str, choice: str, label: str, user_id: int, user_name: str) -> bool:
    """Atomic: only the first decision can move a request out of 'pending'."""
    now = time.time()
    cur = _db.execute(
        "UPDATE approvals SET status=?, choice=?, choice_label=?, decided_by=?, decided_by_name=?, "
        "decided_at=?, updated_at=? WHERE request_id=? AND status='pending'",
        (status, choice, label, user_id, user_name, now, now, request_id),
    )
    return cur.rowcount == 1


def mark_delivered(request_id: str, delivered: bool) -> None:
    _db.execute(
        "UPDATE approvals SET delivered=?, updated_at=? WHERE request_id=?",
        (1 if delivered else 0, time.time(), request_id),
    )


# ---------- robot monitor ----------
def _float(value):
    try:
        return float(str(value).strip().rstrip("%").replace("°C", ""))
    except (TypeError, ValueError):
        return None


def add_telemetry(message_id: int, checked: list, csv_text: str) -> int:
    """checked: [(row, health, reasons), ...] from telemetry.assess(). Returns the report id."""
    now = time.time()
    counts = {lv: sum(1 for _, h, _ in checked if h == lv) for lv in ("OK", "WARN", "DOWN")}
    report_id = _db.execute(
        "INSERT INTO telemetry_reports(received_at, message_id, robots, ok, warn, down, csv) VALUES (?,?,?,?,?,?,?)",
        (now, message_id, len(checked), counts["OK"], counts["WARN"], counts["DOWN"], csv_text),
    ).lastrowid
    _db.executemany(
        "INSERT INTO robot_readings(report_id, received_at, robot, site, status, battery_pct, temp_c, "
        "errors_1h, last_seen, health, reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(report_id, now, r["robot"], r["site"], r["status"], _float(r["battery"]), _float(r["temp"]),
          _float(r["errors"]), r["last_seen"], health, "; ".join(reasons))
         for r, health, reasons in checked],
    )
    return report_id


def add_fleet_log(report_id: int, log: dict, robot_name, clock) -> tuple[int, int]:
    """Appends every event and every robot snapshot from a fleet log (fleetlog.py). Returns (events, snapshot rows)."""
    def stamp(t):
        at = clock.at(t)
        return at.isoformat(timespec="seconds") if at else None

    events = [
        (report_id, e.get("t"), stamp(e.get("t", 0)), e.get("type"),
         robot_name(e["robot"]) if e.get("robot") is not None else None,
         e.get("job_id"), e.get("error"), json.dumps(e, ensure_ascii=False))
        for e in log.get("events", [])
    ]
    _db.executemany(
        "INSERT INTO fleet_events(report_id, t, at, type, robot, job_id, error, payload) VALUES (?,?,?,?,?,?,?,?)",
        events,
    )
    snaps = [
        (report_id, s["t"], stamp(s["t"]), robot_name(r["id"]), r.get("zone"), r.get("x"), r.get("y"),
         r.get("vel"), r.get("task"), r.get("job_id"), r.get("battery"), r.get("temp"), r.get("error") or None,
         r.get("carrying"), 1 if r.get("yielding") else 0, r.get("waiting_s"))
        for s in log.get("snapshots", []) for r in s.get("robots", [])
    ]
    _db.executemany(
        "INSERT INTO fleet_snapshots(report_id, t, at, robot, zone, x, y, vel, task, job_id, battery, temp, "
        "error, carrying, yielding, waiting_s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        snaps,
    )
    return len(events), len(snaps)
