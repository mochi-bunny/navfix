"""Hourly robot health check: parses the supervisor's telemetry CSV into a #monitor-bot card.
Every value shown comes from the CSV; code only decides OK / WARN / DOWN."""
import csv
import io
from datetime import datetime

import discord

import formatter

# Accepted header names for each column we understand (first match wins).
ALIASES = {
    "robot": ("robot", "robot_id", "robot_code", "robot_name", "name", "id"),
    "site": ("site", "location", "cell"),
    "status": ("status", "state"),
    "in_use": ("in_use", "active", "inuse", "in_service"),
    "battery": ("battery_pct", "battery", "battery_percent", "batt"),
    "temp": ("temp_c", "temp", "temperature", "temperature_c"),
    "errors": ("errors_1h", "errors", "error_count", "errs"),
    "last_seen": ("last_seen", "last_heartbeat", "heartbeat", "timestamp", "updated_at"),
}

DOWN_STATUSES = {"offline", "down", "error", "fault", "estop", "e-stop", "disconnected", "failed"}
WARN_STATUSES = {"degraded", "warning", "warn", "charging", "paused", "stuck"}
LOW_BATTERY, HOT_TEMP_C = 20, 70
MAX_ROWS = 40  # keeps the table inside Discord's 4096-character limit

ICON = {"OK": "🟢", "WARN": "🟡", "DOWN": "🔴"}


class TelemetryError(ValueError):
    """CSV is empty or has no robot column (the server returns 400)."""


def _num(value):
    try:
        return float(str(value).strip().rstrip("%").replace("°C", ""))
    except (TypeError, ValueError):
        return None


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y", "in_use", "active")


def parse(text: str) -> tuple[list[dict], list[str]]:
    """Returns (rows of robots in use, original header). Each row has our column keys plus 'raw'."""
    reader = csv.DictReader(io.StringIO(text.strip()))
    if not reader.fieldnames:
        raise TelemetryError("CSV is empty")
    header = [h.strip() for h in reader.fieldnames]
    lower = {h.lower(): h for h in header}
    cols = {key: next((lower[a] for a in names if a in lower), None) for key, names in ALIASES.items()}
    if cols["robot"] is None:
        raise TelemetryError(f"no robot column; expected one of {', '.join(ALIASES['robot'])}")

    rows = []
    for raw in reader:
        raw = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        row = {key: raw.get(col, "") if col else "" for key, col in cols.items()}
        if not row["robot"]:
            continue
        if cols["in_use"] and not _truthy(row["in_use"]):
            continue
        row["raw"] = raw
        rows.append(row)
    return rows, header


def health(row: dict) -> tuple[str, list[str]]:
    """OK / WARN / DOWN plus the reasons, from thresholds above."""
    reasons, level = [], "OK"
    status = row["status"].lower()
    if status in DOWN_STATUSES:
        return "DOWN", [f"status {row['status']}"]
    if status in WARN_STATUSES:
        level = "WARN"
        reasons.append(f"status {row['status']}")
    battery, temp, errors = _num(row["battery"]), _num(row["temp"]), _num(row["errors"])
    if battery is not None and battery < LOW_BATTERY:
        level = "WARN"
        reasons.append(f"battery {row['battery'].rstrip('%')}%")
    if temp is not None and temp >= HOT_TEMP_C:
        level = "WARN"
        reasons.append(f"temp {row['temp'].replace('°C', '')}°C")
    if errors is not None and errors > 0:
        level = "WARN"
        reasons.append(f"{row['errors']} errors in the last hour")
    return level, reasons


def _cell(value: str, width: int) -> str:
    value = value or "—"
    return value if len(value) <= width else value[: width - 1] + "…"


def _short_time(value: str) -> str:
    """2026-10-03T13:58:02-04:00 -> 13:58; anything else is shown as sent."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        return value


def _table(rows: list[dict], levels: list[str]) -> str:
    cols = [  # (heading, width, value)
        ("HEALTH", 6, lambda r, lv: lv),
        ("ROBOT", 9, lambda r, lv: r["robot"]),
        ("SITE", 11, lambda r, lv: r["site"]),
        ("STATUS", 9, lambda r, lv: r["status"]),
        ("BATT", 4, lambda r, lv: f"{r['battery'].rstrip('%')}%" if r["battery"] else ""),
        ("TEMP", 4, lambda r, lv: f"{r['temp'].replace('°C', '')}C" if r["temp"] else ""),
        ("ERR", 3, lambda r, lv: r["errors"]),
        ("SEEN", 5, lambda r, lv: _short_time(r["last_seen"])),
    ]
    right = {"BATT", "TEMP", "ERR"}

    def line(values):
        return " ".join(
            v.rjust(w) if h in right else v.ljust(w) for (h, w, _), v in zip(cols, values)
        ).rstrip()

    out = [line([h for h, _, _ in cols])]
    for r, lv in zip(rows, levels):
        out.append(line([_cell(fn(r, lv), w) for _, w, fn in cols]))
    return "```\n" + "\n".join(out) + "\n```"


def assess(text: str) -> list[tuple[dict, str, list[str]]]:
    """[(row, OK/WARN/DOWN, reasons), ...] for every robot in use, problems first."""
    rows, _ = parse(text)
    checked = [(r, *health(r)) for r in rows]
    order = {"DOWN": 0, "WARN": 1, "OK": 2}
    checked.sort(key=lambda c: (order[c[1]], c[0]["robot"]))
    return checked


def build(checked: list, received_at: datetime) -> discord.Embed:
    rows = [c[0] for c in checked]
    counts = {lv: sum(1 for _, l, _ in checked if l == lv) for lv in ("OK", "WARN", "DOWN")}
    color = formatter.RED if counts["DOWN"] else formatter.YELLOW if counts["WARN"] else formatter.GREEN
    e = discord.Embed(
        title=f"🩺 Fleet health · {received_at:%H:%M} · {len(rows)} robot{'s' if len(rows) != 1 else ''} in use",
        color=color,
    )
    e.set_author(name=formatter.FLEET)

    if not rows:
        e.description = "No robots marked in use in this report."
        return e

    summary = f"**{_summary(checked)}**\n" \
              f"🟢 **{counts['OK']}** healthy   🟡 **{counts['WARN']}** warning   🔴 **{counts['DOWN']}** down"
    shown = checked[:MAX_ROWS]
    table = _table([c[0] for c in shown], [c[1] for c in shown])
    more = f"\n+{len(checked) - MAX_ROWS} more not shown" if len(checked) > MAX_ROWS else ""
    e.description = f"{summary}\n{table}{more}"

    issues = [f"{ICON[lv]} **{r['robot']}** · {', '.join(why)}" for r, lv, why in checked if lv != "OK"]
    e.add_field(
        name="Needs attention",
        value=formatter._v("\n".join(issues[:15]) or "Nothing. All robots in use are healthy ✅"),
        inline=False,
    )
    e.set_footer(
        text=f"Healthy = not offline or degraded, battery ≥{LOW_BATTERY}%, temp <{HOT_TEMP_C}°C, "
             f"0 errors"
    )
    return e


def _summary(checked: list) -> str:
    """One-line plain summary for the top of the card, e.g. 'GR1-11 is down; GR1-03 and GR1-05 need attention.'"""
    def names(level):
        found = [r["robot"] for r, lv, _ in checked if lv == level]
        return found[0] if len(found) == 1 else ", ".join(found[:-1]) + " and " + found[-1] if found else ""

    down, warn = names("DOWN"), names("WARN")
    if not down and not warn:
        return "The robot in use is healthy." if len(checked) == 1 else f"All {len(checked)} robots in use are healthy."
    parts = []
    if down:
        parts.append(f"{down} {'is' if ' and ' not in down else 'are'} down")
    if warn:
        parts.append(f"{warn} need{'s' if ' and ' not in warn else ''} attention")
    return "; ".join(parts) + "."
