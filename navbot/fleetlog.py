"""FleetOps fleet log (JSON from the supervisor / Isaac Sim) -> #monitor-bot health card + robot log rows.

The log has: meta (layout, error_codes, event_types), summary (per robot and per zone totals),
events (what happened, with t = seconds since the run started) and snapshots (every robot every 5 s).
The card shows the latest snapshot as a table, plus what happened during the window.
Every number on the card comes from the log; code only decides OK / WARN / DOWN.
"""
import json
from datetime import datetime, timedelta

import discord

import formatter

# From meta.error_codes: which codes stop a robot (DOWN) and which only need a look (WARN).
DOWN_ERRORS = {"DRIVE_FAULT": "can't move", "OVERHEAT": "motor overheating"}
WARN_ERRORS = {"LOW_BATTERY": "battery low", "BIN_EMPTY": "found an empty bin"}
CHARGE_SOON_PCT = 25   # LOW_BATTERY fires below 15%; warn a little earlier
HOT_TEMP_C = 78        # OVERHEAT fires above 80 C
STUCK_WAIT_S = 20      # normal traffic waits are 2-7 s
JAM_WAIT_S = 10        # a resume after this long counts towards a traffic jam

ICON = {"OK": "🟢", "WARN": "🟡", "DOWN": "🔴"}


class FleetLogError(ValueError):
    """Not a fleet log, or it has no snapshots (the server returns 400)."""


def robot_name(robot_id) -> str:
    return f"AMR-{int(robot_id):02d}"


def is_fleet_log(text: str) -> bool:
    return text.lstrip().startswith("{")


class Clock:
    """Turns t (seconds since the run started) into wall-clock time when the log says when it was made."""

    def __init__(self, meta: dict):
        self.start = None
        try:
            end = datetime.strptime(meta["generated_at"], "%Y-%m-%dT%H:%M:%S%z")
            self.start = end - timedelta(seconds=float(meta.get("sim_seconds", 0)))
        except (KeyError, TypeError, ValueError):
            pass

    def at(self, t) -> datetime | None:
        return self.start + timedelta(seconds=float(t)) if self.start else None

    def __call__(self, t) -> str:
        if self.start:
            return f"{self.at(t):%H:%M:%S}"
        minutes, seconds = divmod(int(float(t)), 60)
        return f"t+{minutes}:{seconds:02d}"


def parse(text: str) -> dict:
    try:
        log = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FleetLogError(f"bad JSON: {exc}") from exc
    if not isinstance(log, dict) or not log.get("snapshots"):
        raise FleetLogError("fleet log needs a 'snapshots' list")
    return log


# ---------- robot health (latest snapshot) ----------
def health(r: dict) -> tuple[str, list[str]]:
    error = (r.get("error") or "").strip()
    if error in DOWN_ERRORS:
        reason = f"{DOWN_ERRORS[error]} ({error})"
        if error == "OVERHEAT" and r.get("temp") is not None:
            reason += f", {r['temp']}°C"
        return "DOWN", [reason]
    reasons = []
    if error:
        reasons.append(f"{WARN_ERRORS.get(error, 'error')} ({error})")
    if r.get("battery") is not None and r["battery"] < CHARGE_SOON_PCT and r.get("task") != "charge":
        reasons.append(f"battery {r['battery']}%, not charging")
    if r.get("temp") is not None and r["temp"] >= HOT_TEMP_C:
        reasons.append(f"running hot, {r['temp']}°C")
    if r.get("yielding") and (r.get("waiting_s") or 0) >= STUCK_WAIT_S:
        reasons.append(f"stuck in traffic {r['waiting_s']:.0f}s")
    return ("WARN" if reasons else "OK"), reasons


def _state(r: dict) -> str:
    if r.get("error"):
        return "stopped" if r["error"] in DOWN_ERRORS and not r.get("vel") else "faulted"
    if r.get("yielding"):
        return f"wait {r.get('waiting_s', 0):.0f}s"
    return "moving" if r.get("vel") else "stopped"


def assess(log: dict) -> list[tuple[dict, str, list[str]]]:
    """[(row, OK/WARN/DOWN, reasons), ...] for every robot in the latest snapshot, problems first.
    Rows use the same keys as telemetry.py so store.add_telemetry() can log them."""
    snap = log["snapshots"][-1]
    clock = Clock(log.get("meta", {}))
    checked = []
    for r in snap["robots"]:
        level, reasons = health(r)
        row = {
            "robot": robot_name(r["id"]),
            "site": f"zone {r.get('zone')}",
            "status": f"{r.get('task', '')} · {_state(r)}",
            "battery": str(r.get("battery", "")),
            "temp": str(r.get("temp", "")),
            "errors": r.get("error") or "",
            "last_seen": clock(snap["t"]),
            "raw": r,
        }
        checked.append((row, level, reasons))
    order = {"DOWN": 0, "WARN": 1, "OK": 2}
    checked.sort(key=lambda c: (order[c[1]], c[0]["robot"]))
    return checked


# ---------- what happened during the window (events) ----------
def incidents(log: dict) -> list[dict]:
    """Each error_raised paired with its error_cleared, marked when a scripted/presenter demo caused it."""
    events = log.get("events", [])
    injected = {}  # robot -> t of the last demo inject
    open_errors, out = {}, []
    for e in events:
        if e["type"] == "demo_command" and e.get("command", {}).get("kind") == "inject":
            injected[e["command"].get("robot")] = e["t"]
        elif e["type"] == "error_raised":
            item = {"robot": e["robot"], "error": e["error"], "t": e["t"], "cleared_t": None,
                    "job_id": e.get("job_id"), "battery": e.get("battery"), "temp": e.get("temp"),
                    "at": e.get("at"), "demo": e["t"] - injected.get(e["robot"], -99) < 5}
            open_errors[(e["robot"], e["error"])] = item
            out.append(item)
        elif e["type"] == "error_cleared":
            item = open_errors.pop((e["robot"], e["error"]), None)
            if item:
                item["cleared_t"] = e["t"]
    return out


def jams(log: dict) -> list[dict]:
    """Groups of robots released together after long waits: a blocked aisle or junction."""
    long_waits = sorted((e for e in log.get("events", [])
                         if e["type"] == "resumed" and e.get("waited_s", 0) >= JAM_WAIT_S), key=lambda e: e["t"])
    groups = []
    for e in long_waits:
        if groups and e["t"] - groups[-1][-1]["t"] <= 1.0:
            groups[-1].append(e)
        else:
            groups.append([e])
    return [{"t": g[0]["t"], "robots": len(g), "max_wait": max(x["waited_s"] for x in g)}
            for g in groups if len(g) >= 3]


def window_facts(log: dict) -> dict:
    events = log.get("events", [])
    summary = log.get("summary", {})
    counts = summary.get("event_counts", {})
    snap = log["snapshots"][-1]
    return {
        "picks": summary.get("picks", counts.get("picked", 0)),
        "deliveries": summary.get("deliveries", counts.get("delivered", 0)),
        "charges": counts.get("charging_done", 0),
        "stations_full": counts.get("stations_full", 0),
        "waits": counts.get("waiting", 0),
        "wait_s": sum(r.get("logged_wait_s", 0) for r in summary.get("robots", [])),
        # A repeat visit to a bin already set to 0 isn't a new mismatch.
        "corrections": [e for e in events if e["type"] == "bin_count_corrected"
                        and e.get("system_qty_was") != e.get("physical_qty")],
        "short_zones": [z for z in snap.get("zone_stock", []) if z.get("total") != z.get("expected_total")],
        "restocks": counts.get("restocked", 0),
    }


# ---------- Discord card ----------
def _table(checked) -> str:
    cols = [("HEALTH", 6), ("ROBOT", 6), ("ZONE", 4), ("TASK", 6), ("STATE", 8), ("BATT", 4), ("TEMP", 4)]
    right = {"BATT", "TEMP"}

    def line(values):
        return " ".join(v.rjust(w) if h in right else v.ljust(w)
                        for (h, w), v in zip(cols, values)).rstrip()

    out = [line([h for h, _ in cols])]
    for row, level, _ in checked:
        r = row["raw"]
        out.append(line([
            level, row["robot"], str(r.get("zone", "")), str(r.get("task", ""))[:6], _state(r)[:8],
            f"{r.get('battery', '')}%", f"{r.get('temp', 0):.0f}C",
        ]))
    return "```\n" + "\n".join(out) + "\n```"


def _num(value) -> str:
    """10.0 -> 10, 2.5 -> 2.5"""
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def _duration(seconds) -> str:
    minutes, seconds = divmod(int(round(seconds)), 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def build(log: dict, checked: list, received_at: datetime) -> discord.Embed:
    meta = log.get("meta", {})
    clock = Clock(meta)
    counts = {lv: sum(1 for _, l, _ in checked if l == lv) for lv in ("OK", "WARN", "DOWN")}
    color = formatter.RED if counts["DOWN"] else formatter.YELLOW if counts["WARN"] else formatter.GREEN
    snap_t = log["snapshots"][-1]["t"]
    e = discord.Embed(
        title=f"🩺 Fleet health · {received_at:%H:%M} · {len(checked)} robots",
        color=color,
    )
    e.set_author(name=formatter.FLEET)

    down = [r["robot"] for r, lv, _ in checked if lv == "DOWN"]
    warn = [r["robot"] for r, lv, _ in checked if lv == "WARN"]
    if not down and not warn:
        headline = f"All {len(checked)} robots are working normally."
    else:
        bits = []
        if down:
            bits.append(f"{', '.join(down)} {'is' if len(down) == 1 else 'are'} down")
        if warn:
            bits.append(f"{', '.join(warn)} need{'s' if len(warn) == 1 else ''} a look")
        headline = "; ".join(bits) + "."
    e.description = (
        f"**{headline}**\n"
        f"🟢 **{counts['OK']}** healthy   🟡 **{counts['WARN']}** warning   🔴 **{counts['DOWN']}** down\n"
        f"Status at {clock(snap_t)}:\n{_table(checked)}"
    )

    issues = [f"{ICON[lv]} **{r['robot']}** · {', '.join(why)}" for r, lv, why in checked if lv != "OK"]
    e.add_field(name="Needs attention now", value=formatter._v("\n".join(issues[:15]) or "Nothing ✅"),
                inline=False)

    lines = []  # (t, text), sorted by time below
    for i in incidents(log):
        icon = ICON["DOWN"] if i["error"] in DOWN_ERRORS else ICON["WARN"]
        what = DOWN_ERRORS.get(i["error"]) or WARN_ERRORS.get(i["error"], i["error"])
        line = f"{icon} {clock(i['t'])} **{robot_name(i['robot'])}** {what} (`{i['error']}`)"
        if i["cleared_t"] is not None:
            line += f" · cleared after {_duration(i['cleared_t'] - i['t'])}"
        else:
            line += " · **still active**"
        if i["demo"]:
            line += " · demo fault"
        lines.append((i["t"], line))
    for j in jams(log):
        lines.append((j["t"], f"🚦 {clock(j['t'])} traffic jam cleared: **{j['robots']} robots** had waited "
                              f"up to {j['max_wait']:.0f}s"))
    lines = [text for _, text in sorted(lines, key=lambda x: x[0])]
    e.add_field(name=f"What happened ({_duration(meta.get('sim_seconds', snap_t))} window)",
                value=formatter._v("\n".join(lines) or "No faults ✅"), inline=False)

    f = window_facts(log)
    e.add_field(name="📦 Throughput",
                value=f"{f['picks']} picks · {f['deliveries']} delivered\n{f['charges']} charges", inline=True)
    e.add_field(name="🚦 Congestion",
                value=f"{f['waits']} traffic waits ({_duration(f['wait_s'])} total)\n"
                      f"pack stations full {f['stations_full']}×", inline=True)
    stock = [f"zone {c.get('zone')} bin ({_num(c['bin'][0])}, {_num(c['bin'][1])}) {c['sku']}: "
             f"system said {c['system_qty_was']}, robot found {c['physical_qty']}" for c in f["corrections"]]
    stock += [f"zone {z['zone']}: {z['total']} on shelf vs {z['expected_total']} expected" for z in f["short_zones"]]
    e.add_field(name="🧮 Stock mismatches", value=formatter._v("\n".join(stock[:8]) or "None ✅"), inline=False)

    e.set_footer(text=f"Down = DRIVE_FAULT or OVERHEAT · Warn = battery <{CHARGE_SOON_PCT}% and not charging, "
                      f"≥{HOT_TEMP_C}°C, BIN_EMPTY/LOW_BATTERY, or stuck {STUCK_WAIT_S}s+")
    return e

